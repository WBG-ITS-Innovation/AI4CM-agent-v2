# ─────────────────────────────────────────────────────────────────────────────
# NOT REACHABLE FROM app.py. NO TEST COVERS THIS FILE.
#
# `agent/runtime/` is an unwired side package: nothing in `app.py` or `agent/`
# imports it, and the test suite neither exercises nor stubs it. It is kept
# because it sketches a capability the project may still want — watching for
# new lab runs and alerting on them — not because anything depends on it.
#
# Two consequences a reader should hold onto:
#   * Nothing here has been checked against the Lab's artifact contract, so it
#     does not carry the absence semantics the rest of `agent/` is built on.
#     Do not copy patterns out of this package into live code.
#   * `requests`, imported by alerts.py, is deliberately NOT in requirements.txt
#     for this reason. Install it yourself if you wire this up.
# ─────────────────────────────────────────────────────────────────────────────
"""Unwired side package. See the banner above before using anything here."""
