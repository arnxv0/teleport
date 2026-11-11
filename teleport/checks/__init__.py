"""The verification pipeline. Each step is fn(record, steps_so_far) -> JSON-able dict, so the worker
can checkpoint after every step and a different machine can resume from the next one.
Add a check by adding a step here."""
from . import judge, keenable, probe

PIPELINE = [
    ("probe", probe.run),        # is the recorded homepage alive?
    ("search", keenable.search),  # what does the live web say the official site is?
    ("fetch", keenable.fetch),    # read the official page for country / type
    ("judge", judge.run),         # LLM proposes changes; gate.py decides what is verified
]
STEPS = [name for name, _ in PIPELINE]
