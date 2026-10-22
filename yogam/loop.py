"""Day 1 - loop.py: the agent loop, the heart of every harness.

Concept: an agent is a model called in a loop - ask, run the tools it requests,
feed the results back, repeat until it answers in plain text.

Design rules:
  * The loop never crashes because a tool did: failures become tool results.
  * Policy lives outside the loop: before_tool may veto any call, and
    before_turn may rewrite history (day 3 plugs compaction in there).
  * A turn budget guarantees termination, with one tool-free wrap-up call.
"""
from . import provider


def _execute(call, tools, on_event, before_tool):
    """Run one tool call and return its result string (errors included)."""
    on_event("tool_start", call)
    tool = tools.get(call["name"])
    reason = before_tool(call)
    if reason is not None:
        result = f"BLOCKED: {reason}"
    elif tool is None:
        result = f"ERROR: unknown tool {call['name']}"
    else:
        try:
            result = str(tool.run(**call["args"]))
        except Exception as exc:  # deliberate: a tool bug must not end the run
            result = f"ERROR: {type(exc).__name__}: {exc}"
    on_event("tool_end", {"call": call, "result": result})
    return result


def _record(messages, reply):
    """Append the model's reply to history in neutral form."""
    messages.append({"role": "assistant", "text": reply["text"],
                     "tool_calls": reply["tool_calls"], "reasoning": reply["reasoning"]})


def run_loop(model, system, messages, tools, on_event, before_tool,
             max_turns=80, before_turn=None):
    """Drive the model until it answers without tools; return that final text.

    `tools` maps name -> Tool (.spec dict, .run callable). `messages` is mutated
    in place so the caller keeps the full transcript.
    """
    specs = [t.spec for t in tools.values()]
    for _ in range(max_turns):
        if before_turn is not None:
            messages[:] = before_turn(messages)
        reply = provider.complete(model, system, messages, specs)
        _record(messages, reply)
        on_event("assistant", reply)
        if not reply["tool_calls"]:
            return reply["text"]
        for call in reply["tool_calls"]:
            result = _execute(call, tools, on_event, before_tool)
            messages.append({"role": "tool", "id": call["id"],
                             "name": call["name"], "text": result})
    messages.append({"role": "user", "text": "Turn limit reached; wrap up now."})
    reply = provider.complete(model, system, messages, [])
    _record(messages, reply)
    return reply["text"]
