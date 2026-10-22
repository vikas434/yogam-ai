"""Day 1 - offline tests: provider and loop against a fake OpenRouter server.

Run from the repo root: python3 -m unittest discover -s tests -v
No network and no real key are used; the fake key below is a test constant.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OPENROUTER_API_KEY"] = "test-key-not-real"  # env beats .env
from yogam import loop, provider  # noqa: E402


def _msg(content="", calls=None, reasoning=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": "call_1", "type": "function",
                            "function": {"name": n, "arguments": a}} for n, a in calls]
    if reasoning:
        m["reasoning_details"] = reasoning
    return {"choices": [{"message": m}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class Fake:
    """Scripted server: each POST pops the next (status, body) and is recorded."""

    def __init__(self, script):
        self.script, self.seen = list(script), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append((dict(self.headers), body))
                status, payload = outer.script.pop(0)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1/chat/completions"

    def close(self):
        self.srv.shutdown()


class Echo:
    spec = {"schema": {"name": "echo", "description": "d", "parameters": {"type": "object"}}}

    def run(self, **kw):
        return "echoed " + json.dumps(kw)


class Boom:
    spec = {"schema": {"name": "boom", "description": "d", "parameters": {"type": "object"}}}

    def run(self, **kw):
        raise ValueError("kaput")


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self._url, self._sleep = provider.API_URL, provider.time.sleep
        provider.time.sleep = lambda s: None

    def tearDown(self):
        provider.API_URL, provider.time.sleep = self._url, self._sleep
        self.fake.close()

    def serve(self, script):
        self.fake = Fake(script)
        provider.API_URL = self.fake.url

    def test_tool_round_trip_and_wire_format(self):
        self.serve([(200, _msg("", [("echo", '{"x": 1}')], [{"sig": "abc"}])),
                    (200, _msg("done"))])
        hist = [{"role": "user", "text": "go"}]
        events = []
        out = loop.run_loop("m", "sys", hist, {"echo": Echo()},
                            lambda k, p: events.append(k), lambda c: None)
        self.assertEqual(out, "done")
        self.assertEqual([m["role"] for m in hist], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(events, ["assistant", "tool_start", "tool_end", "assistant"])
        headers, first = self.fake.seen[0]
        self.assertEqual(headers["Authorization"], "Bearer test-key-not-real")
        self.assertEqual(first["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(first["tools"][0]["function"]["name"], "echo")
        second = self.fake.seen[1][1]["messages"]
        self.assertEqual(second[2]["reasoning_details"], [{"sig": "abc"}])  # echoed
        self.assertEqual(second[2]["tool_calls"][0]["function"]["arguments"], '{"x": 1}')
        self.assertEqual(second[3], {"role": "tool", "tool_call_id": "call_1",
                                     "content": 'echoed {"x": 1}'})

    def test_plain_prompt_returns_text_without_tools(self):
        self.serve([(200, _msg("<html>coffee</html>"))])
        hist = [{"role": "user", "text": "Build a landing page for a coffee shop"}]
        out = loop.run_loop("m", "s", hist, {}, lambda k, p: None, lambda c: None)
        self.assertEqual(out, "<html>coffee</html>")
        self.assertNotIn("tools", self.fake.seen[0][1])
        self.assertEqual(hist[-1]["tool_calls"], [])

    def test_blocked_unknown_and_failing_tools(self):
        self.serve([(200, _msg("", [("echo", "{}"), ("nope", "{}"), ("boom", "{}")])),
                    (200, _msg("ok"))])
        hist = [{"role": "user", "text": "x"}]
        loop.run_loop("m", "s", hist, {"echo": Echo(), "boom": Boom()},
                      lambda k, p: None, lambda c: "no echo" if c["name"] == "echo" else None)
        results = [m["text"] for m in hist if m["role"] == "tool"]
        self.assertEqual(results, ["BLOCKED: no echo", "ERROR: unknown tool nope",
                                   "ERROR: ValueError: kaput"])

    def test_turn_limit_wraps_up_without_tools(self):
        self.serve([(200, _msg("", [("echo", "{}")])), (200, _msg("wrapped"))])
        hist = [{"role": "user", "text": "x"}]
        out = loop.run_loop("m", "s", hist, {"echo": Echo()}, lambda k, p: None,
                            lambda c: None, max_turns=1)
        self.assertEqual(out, "wrapped")
        self.assertEqual(self.fake.seen[1][1]["messages"][-1]["content"],
                         "Turn limit reached; wrap up now.")
        self.assertNotIn("tools", self.fake.seen[1][1])

    def test_retry_then_success_and_hard_error(self):
        self.serve([(429, {"e": 1}), (503, {"e": 2}), (200, _msg("hi"))])
        r = provider.complete("m", "s", [{"role": "user", "text": "x"}], [])
        self.assertEqual((r["text"], r["usage"]), ("hi", {"input": 7, "output": 3}))
        self.fake.close()
        self.serve([(401, {"error": "bad key"})])
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            provider.complete("m", "s", [{"role": "user", "text": "x"}], [])
        self.fake.close()
        self.serve([(200, {"error": {"message": "credits"}})])
        with self.assertRaisesRegex(RuntimeError, "API error"):
            provider.complete("m", "s", [{"role": "user", "text": "x"}], [])

    def test_upstream_504_is_retried_both_as_http_status_and_in_body(self):
        gateway = {"error": {"message": "The operation was aborted", "code": 504}}
        self.serve([(504, gateway), (200, gateway), (200, _msg("recovered"))])
        r = provider.complete("m", "s", [{"role": "user", "text": "x"}], [])
        self.assertEqual(r["text"], "recovered")
        self.assertEqual(len(self.fake.seen), 3)

    def test_missing_key_message_never_echoes_a_key(self):
        saved = os.environ.pop("OPENROUTER_API_KEY")
        provider._REPO_ROOT, real = "/nonexistent", provider._REPO_ROOT
        self.serve([])
        try:
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY is not set"):
                provider.api_key()
        finally:
            provider._REPO_ROOT = real
            os.environ["OPENROUTER_API_KEY"] = saved


if __name__ == "__main__":
    unittest.main()
