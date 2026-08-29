"""Startup entry point: loaded by Python's site module before target import.

``guard_core.activate()`` re-verifies that this exact file is the sibling shim
that ran, so an import-order substitution of either guard file is refused.
"""
import os
import sys

from guard_core import activate

try:
    activate()
except BaseException as exc:
    # CPython otherwise prints sitecustomize failures and continues importing the
    # target. A future M53 launch must be impossible without activation proof.
    sys.stderr.write("M53 guard activation failed: %s\n" % type(exc).__name__)
    sys.stderr.flush()
    os._exit(78)
