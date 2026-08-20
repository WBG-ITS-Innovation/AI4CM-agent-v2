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
# agent/runtime/alerts.py
from __future__ import annotations
import os, json
from datetime import datetime, UTC
from pathlib import Path

def notify(events: list[dict], channel: str = "file"):
    """
    events: [{"message": "..."}]
    channel: "file" (default) or "slack" if SLACK_WEBHOOK_URL is set.
    """
    if channel == "slack" and os.getenv("SLACK_WEBHOOK_URL"):
        try:
            import requests
            url = os.getenv("SLACK_WEBHOOK_URL")
            for e in events:
                requests.post(url, json={"text": e.get("message","(no message)")}, timeout=10)
            return
        except Exception:
            pass  # fall through to file

    Path("artifacts").mkdir(parents=True, exist_ok=True)
    log = Path("artifacts/alerts.log")
    with log.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **e}) + "\n")
