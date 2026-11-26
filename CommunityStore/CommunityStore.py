"""Stable Fusion entry point. Load the selected runtime before importing adsk."""
from pathlib import Path

from .bootstrap import launch, stop_runtime

ROOT = Path(__file__).absolute().parent
_runtime = None


def run(context):
    global _runtime
    if _runtime is None:
        _runtime = launch(ROOT, context)


def stop(context):
    global _runtime
    if _runtime is not None:
        runtime, _runtime = _runtime, None
        stop_runtime(runtime, context)
