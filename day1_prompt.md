You are building day 1 of Yogam, a minimal agent harness in Python. Standard
library only, no third-party packages, Python 3.10+. The model provider is
OpenRouter (OpenAI-style chat completions), so the harness can swap models by
changing one string.

Set up the repository first. Create a project folder, run git init, add a
.gitignore with __pycache__/, *.pyc, .DS_Store, and .env, and commit after each
working milestone. All harness code lives in a package folder named yogam/ at
the repo root — create an empty yogam/__init__.py now (day 4 fills it) — and
demos live in demos/. By day 4, python3 -m yogam will run from the root.

API key handling. The key lives only on this machine, in a git-ignored .env file
at the repo root. It already exists and contains OPENROUTER_API_KEY=<key> (plus
an unrelated YOGAM_API_KEY line you must ignore). Do not overwrite, print, log,
hard-code or commit any key. Create .env.example with the placeholder line
OPENROUTER_API_KEY=your-key-here (this file is committed). Before the first
commit, confirm with `git check-ignore .env` that .env is ignored. If
OPENROUTER_API_KEY is missing when you need to run the verification steps, stop
and ask me for it.

Code standard, which applies today and every following day. This build is the
reference implementation students keep for years, not scratch code. Every file
opens with a module docstring naming the day, the concept the file teaches, and
the design rules it embodies. Every public function carries a docstring. Where
behavior is subtle — the reasoning echo, the edit uniqueness rule, the
torn-line tolerance — a short comment states the constraint the code serves.
Size targets, within roughly ten percent and including documentation:
provider.py 120 lines, loop.py 65, tools.py 155, security.py 60, context.py 50,
memory.py 50, skills.py 50, session.py 65, subagent.py 27, harness.py 125,
cli.py 80, fleet.py 36 — about 909 lines for the finished package. Landing far
under a target means documentation was skipped, not that elegance was achieved.


Write yogam/provider.py to this exact contract.
- Constants: API_URL = "https://openrouter.ai/api/v1/chat/completions",
  DEFAULT_MODEL = "google/gemini-3.1-pro-preview".
- _load_env(path=".env"): a tiny stdlib .env reader. Resolve the path relative to
  the repo root (the parent of the yogam/ package), skip blank lines and #
  comments, split each line on the first "=", strip whitespace and optional
  surrounding quotes, and set the variable with os.environ.setdefault so a real
  environment variable always wins. A missing file is silently fine.
- api_key(): call _load_env(), then return OPENROUTER_API_KEY; raise
  RuntimeError with a clear message (naming .env and the variable, never echoing
  any key) when it is not set.
- complete(model, system, messages, tools) returns {"text": str, "tool_calls":
  [{"id", "name", "args"}], "reasoning": <opaque or None>, "usage": {"input":
  int, "output": int}}. It posts {"model": model, "messages": [{"role":
  "system", "content": system}] + _to_wire(messages), "temperature": 0.4,
  "max_tokens": 16000} and, when tools are given (a list of spec dicts, each
  {"schema": ...}), "tools": [{"type": "function", "function": t["schema"]} for
  t in tools].
- The neutral message format: {"role": "user", "text"}; {"role": "assistant",
  "text", "tool_calls", "reasoning"}; {"role": "tool", "id", "name", "text"}.
  _to_wire maps user to {"role": "user", "content": text}; assistant to {"role":
  "assistant", "content": text or None, "tool_calls": [{"id": id, "type":
  "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
  (omit tool_calls when empty), plus "reasoning_details": reasoning when it is
  not None — some models (Gemini 3) reject follow-up turns unless their
  reasoning is echoed back verbatim, so keep this round-trip; tool to {"role":
  "tool", "tool_call_id": id, "content": text}.
- Response parsing: take choices[0].message; text is its content or ""; each
  entry of its tool_calls becomes {"id", "name": function.name, "args":
  json.loads(function.arguments or "{}")}, falling back to {} if the arguments
  are not valid JSON; reasoning is message.get("reasoning_details"); usage maps
  prompt_tokens and completion_tokens to input and output.
- _post(url, body, retries=5): urllib, headers Authorization "Bearer <key>"
  (header only, never in the URL or in error messages), Content-Type
  application/json and X-Title "Yogam", timeout 600. On HTTP 408/429/500/502/503/504/524/529:
  sleep 2**attempt * 2 seconds and retry. On other HTTP errors: raise
  RuntimeError with the status and the first 400 characters of the error body.
  Retry URLError/TimeoutError the same way. Also handle a top-level "error" key in
  the response JSON (OpenRouter can return one with HTTP 200): retry it when its
  code is one of the retryable statuses above, otherwise raise RuntimeError.

Write yogam/loop.py to this exact contract.
- run_loop(model, system, messages, tools, on_event, before_tool, max_turns=80,
  before_turn=None) -> str. tools is a dict name -> Tool, where a Tool exposes
  .spec (a {"schema": ...} dict) and .run (a callable taking keyword
  arguments); the loop hands the provider [t.spec for t in tools.values()]. on_event(kind, payload) fires with "assistant" after
  each model reply, and "tool_start" / "tool_end" around each execution.
  before_tool(call) returns None to allow or a reason string to block; a blocked
  call gets the tool result "BLOCKED: <reason>". before_turn, when provided, is
  called with the message list before each model call and its return value
  replaces the list in place — leave it unused today; day 3 plugs compaction in
  here.
- Each turn: provider.complete, append {"role": "assistant", "text",
  "tool_calls", "reasoning"}, emit the event. No tool calls: return the text.
  Otherwise run each call in order and append {"role": "tool", "id": call["id"],
  "name", "text": str(result)}.
- Unknown tool: result "ERROR: unknown tool <name>". Tool exception: result
  "ERROR: <ExceptionType>: <message>". The loop never crashes because a tool did.
- When max_turns runs out: append a user message "Turn limit reached; wrap up
  now.", make one final call with no tools, append and return its text.

Write demos/day1_dice.py: a hand-written roll_dice tool — a small object with
.spec = {"schema": {"name": "roll_dice", "description": "Roll count six-sided
dice", "parameters": {"type": "object", "properties": {"count": {"type":
"string", "description": "How many dice"}}, "required": ["count"]}}} and .run
returning the rolls — plus a printing on_event, an always-allow before_tool, and
the task "Roll 3 dice and tell me whether the total beats 10".

Testing. This build environment may not be able to reach openrouter.ai. If a
live call fails with a proxy or network error, do not retry in a loop: test
provider.py and loop.py offline against a small fake OpenRouter server
(http.server on localhost, URL injected by monkeypatching API_URL) that returns
one tool call and then a final answer, and report that the live checks remain
for me to run on my machine.

Verify before finishing: (1) the dice transcript shows user, assistant tool call,
tool result, assistant answer; (2) the prompt "Build a landing page for a coffee
shop" returns text and no tool calls; (3) git log shows the day's commits;
(4) `git ls-files` lists .env.example but not .env, and no file in the repo or
its history contains a real key.
