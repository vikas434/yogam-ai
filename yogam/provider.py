"""Day 1 - provider.py: turn a neutral message list into one model reply.

Concept: the provider is the only file that knows the wire format. Everything
above it speaks a neutral message list, so changing model or vendor touches this
file alone.

Design rules:
  * Standard library only (urllib, json, os, time).
  * The API key comes from the environment or a git-ignored .env and travels only
    in a request header - never in a URL, a log line or an error message.
  * Transient failures (HTTP 429/5xx, network) retry with exponential backoff;
    permanent failures raise RuntimeError with a bounded excerpt of the body.
"""
import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"
# Transient statuses worth retrying: timeouts, rate limits and upstream 5xx
# (504 is common when OpenRouter's backing provider aborts a long request).
_RETRYABLE = (408, 429, 500, 502, 503, 504, 524, 529)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env(path=".env"):
    """Load KEY=VALUE lines from `path` (relative to the repo root) into os.environ.

    Real environment variables win (setdefault); a missing file is fine.
    """
    try:
        with open(os.path.join(_REPO_ROOT, path), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def api_key():
    """Return OPENROUTER_API_KEY from the environment or .env, or raise RuntimeError."""
    _load_env()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set: export it or put it in .env")
    return key


def _to_wire(messages):
    """Convert neutral messages (user / assistant / tool) to chat-completions form."""
    wire = []
    for m in messages:
        if m["role"] == "user":
            wire.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m["text"] or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in m["tool_calls"]]
            # Constraint: some models (Gemini 3) reject the next turn unless their
            # reasoning is echoed back verbatim, so it must round-trip untouched.
            if m.get("reasoning") is not None:
                msg["reasoning_details"] = m["reasoning"]
            wire.append(msg)
        else:  # tool result
            wire.append({"role": "tool", "tool_call_id": m["id"], "content": m["text"]})
    return wire


def _post(url, body, retries=5):
    """POST `body` as JSON and return the decoded response, retrying transient errors."""
    data = json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + api_key(),
               "Content-Type": "application/json", "X-Title": "Yogam"}
    last = "no attempt made"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            # OpenRouter can report failures inside an HTTP 200 body; transient
            # upstream codes (e.g. a Google 504) are retried like HTTP errors.
            if "error" in result:
                err = result["error"]
                code = err.get("code") if isinstance(err, dict) else None
                if code not in _RETRYABLE:
                    raise RuntimeError("API error: " + str(err)[:400])
                last = "API error: " + str(err)[:400]
            else:
                return result
        except urllib.error.HTTPError as err:  # must precede URLError (its parent)
            text = err.read().decode("utf-8", "replace")[:400]
            if err.code not in _RETRYABLE:
                raise RuntimeError(f"HTTP {err.code}: {text}") from None
            last = f"HTTP {err.code}: {text}"
        except (urllib.error.URLError, TimeoutError) as err:
            last = f"network error: {err}"
        if attempt < retries - 1:
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"gave up after {retries} attempts; last error: {last}")


def complete(model, system, messages, tools):
    """Ask the model for its next move.

    `tools` is a list of spec dicts, each {"schema": ...}. Returns {"text",
    "tool_calls": [{"id", "name", "args"}], "reasoning", "usage": {"input", "output"}}.
    """
    body = {"model": model, "temperature": 0.4, "max_tokens": 16000,
            "messages": [{"role": "system", "content": system}] + _to_wire(messages)}
    if tools:
        body["tools"] = [{"type": "function", "function": t["schema"]} for t in tools]
    data = _post(API_URL, body)
    msg = data["choices"][0]["message"]
    calls = []
    for c in msg.get("tool_calls") or []:
        try:
            args = json.loads(c["function"].get("arguments") or "{}")
        except ValueError:
            args = {}  # malformed arguments: let the tool report the missing fields
        calls.append({"id": c["id"], "name": c["function"]["name"], "args": args})
    usage = data.get("usage") or {}
    return {"text": msg.get("content") or "", "tool_calls": calls,
            "reasoning": msg.get("reasoning_details"),
            "usage": {"input": usage.get("prompt_tokens", 0),
                      "output": usage.get("completion_tokens", 0)}}
