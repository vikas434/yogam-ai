"""Day 1 - demo: one hand-written tool driven by the loop.

Concept: a tool is just a spec the model reads plus a function we run. Run from
the repo root: python3 demos/day1_dice.py [model]
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yogam import loop, provider  # noqa: E402


class RollDice:
    """A tool object: .spec is what the model sees, .run is what we execute."""

    spec = {"schema": {
        "name": "roll_dice", "description": "Roll count six-sided dice",
        "parameters": {"type": "object", "required": ["count"],
                       "properties": {"count": {"type": "string",
                                                "description": "How many dice"}}}}}

    def run(self, count):
        """Roll `count` dice and report the rolls and their total."""
        rolls = [random.randint(1, 6) for _ in range(int(count))]
        return f"rolls={rolls} total={sum(rolls)}"


def show(kind, payload):
    """Print each loop event as it happens."""
    if kind == "assistant":
        print(f"[assistant] {payload['text']!r} calls={payload['tool_calls']}")
    elif kind == "tool_end":
        print(f"[tool] {payload['call']['name']} -> {payload['result']}")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else provider.DEFAULT_MODEL
    history = [{"role": "user", "text": "Roll 3 dice and tell me whether the total beats 10"}]
    answer = loop.run_loop(model, "You are a helpful assistant. Use tools when useful.",
                           history, {"roll_dice": RollDice()}, show, lambda call: None)
    print("\nTranscript roles:", [m["role"] for m in history])
    print("Final answer:", answer)
