# tests/test_action_execution.py — the boundary, driven against a stub Lab.
#
# The real Lab takes ~100 seconds and publishes at the end, so it is not what
# these tests drive. They drive a stub entry point that produces the same
# stdout shapes, which is what lets the failure paths be tested at all: a real
# Lab cannot be asked to fail on demand, and a failure path that has never run
# is a failure path that does not work.
#
# What is deliberately NOT stubbed is the subprocess itself. These launch a
# real process, read a real pipe, and read real stderr off a real file
# descriptor, because every bug this module can have lives in exactly there —
# buffering, interleaving, and the exit code being read before the stream is
# drained.
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent import run_exec as RX

# ─────────────────────────── stub entry points ───────────────────────────

#: Emits the same event vocabulary agent/lab_entry.py does, including the
#: `[config]` noise the Lab's own config module writes to stdout for stock
#: targets. That noise is not hypothetical — it appeared on the first real run
#: of this session and broke a strict parser.
_STUB_OK = """
import json, sys
def emit(event, **f):
    sys.stdout.write(json.dumps({"event": event, **f}) + "\\n"); sys.stdout.flush()
emit("start", issue_date="2026-08-17", targets=["Revenues", "Expenditure"],
     data="/data.csv", data_sha256="abc", horizon=5, mode="official")
emit("target_start", target="Revenues", recipe_id="rev-v1", model="LightGBM_L1")
emit("target_ran", target="Revenues", recipe_id="rev-v1", model="LightGBM_L1",
     horizon=5, n_rows=5, n_estimators=20)
emit("target_published", target="Revenues", dest="/pub/2026-08-17")
print("[config] Auto-enabled delta modeling for stock target 'Expenditure'")
emit("target_start", target="Expenditure", recipe_id="exp-v1", model="LightGBM_L1")
emit("target_ran", target="Expenditure", recipe_id="exp-v1", model="LightGBM_L1",
     horizon=5, n_rows=5, n_estimators=20)
emit("target_refused", target="Expenditure", stage="publish",
     reason="Refusing to publish 'Expenditure': its current verdict is 'withheld'")
emit("finish", issue_date="2026-08-17", published=["Revenues"],
     refused=["Expenditure"], failed=[])
"""

#: Dies the way a real Python process dies: a traceback on stderr, exit 1.
_STUB_BOOM = """
import json, sys
sys.stdout.write(json.dumps({"event": "start", "issue_date": "2026-08-17",
                             "targets": ["Revenues"]}) + "\\n")
sys.stdout.flush()
raise RuntimeError("lightgbm.basic.LightGBMError: bad allocation")
"""

#: Exits non-zero with a target-level failure, the way lab_entry does.
_STUB_TARGET_FAILS = """
import json, sys
def emit(event, **f):
    sys.stdout.write(json.dumps({"event": event, **f}) + "\\n"); sys.stdout.flush()
emit("start", issue_date="2026-08-17", targets=["Revenues"])
emit("target_failed", target="Revenues", stage="publish",
     error="FileExistsError: forecasts/published/2026-08-17 already exists",
     traceback="Traceback (most recent call last): ...")
sys.stderr.write("Traceback (most recent call last):\\n  FileExistsError\\n")
emit("finish", issue_date="2026-08-17", published=[], refused=[], failed=["Revenues"])
sys.exit(1)
"""


def _plan(tmp_path: Path, script: str, monkeypatch) -> RX.RunPlan:
    """A RunPlan wired to a stub entry point run by THIS interpreter.

    The stub needs no modelling stack, so `sys.executable` is correct here —
    and only here. `plan_run` against a real lab picks the Lab's venv, which
    test_the_agents_interpreter_is_never_used_for_a_real_run pins.
    """
    entry = tmp_path / "stub_entry.py"
    entry.write_text(script, encoding="utf-8")
    monkeypatch.setattr(RX, "entry_script", lambda: entry)
    data = tmp_path / "data.csv"
    data.write_text("date,Revenues\n2025-01-01,1\n", encoding="utf-8")
    return RX.RunPlan(
        lab=tmp_path, python=Path(sys.executable), data=data,
        data_sha="abc", targets=("Revenues", "Expenditure"), horizon=5,
        latest_data_date="2025-08-06", last_issue_date="2026-08-16",
        last_issue_data_sha="abc")


def _drive(plan: RX.RunPlan) -> tuple[list, RX.RunOutcome]:
    seen, outcome = [], None
    for kind, payload in RX.stream_run(plan):
        if kind == "done":
            outcome = payload
        else:
            seen.append((kind, payload))
    assert outcome is not None
    return seen, outcome


# ─────────────────────────── the success path ───────────────────────────

def test_a_successful_run_reports_published_and_refused_separately(
        tmp_path, monkeypatch):
    _, outcome = _drive(_plan(tmp_path, _STUB_OK, monkeypatch))
    assert outcome.ok
    finish = outcome.first("finish")
    assert finish["published"] == ["Revenues"]
    assert finish["refused"] == ["Expenditure"]
    assert finish["failed"] == []


def test_library_noise_on_stdout_is_kept_as_log_not_parsed_as_a_result(
        tmp_path, monkeypatch):
    """The `[config]` line is real: it appeared on the first live run.

    A strict NDJSON parser either crashes on it or drops it silently. Neither
    is acceptable — the first loses the run, the second loses the trace.
    """
    seen, outcome = _drive(_plan(tmp_path, _STUB_OK, monkeypatch))
    assert any("[config]" in line for line in outcome.logs)
    assert all("[config]" not in json.dumps(e) for e in outcome.events)
    assert ("log", "[config] Auto-enabled delta modeling for stock target "
            "'Expenditure'") in seen


def test_progress_is_streamed_in_order_not_delivered_at_the_end(
        tmp_path, monkeypatch):
    seen, _ = _drive(_plan(tmp_path, _STUB_OK, monkeypatch))
    names = [p.get("event") for k, p in seen if k == "event"]
    assert names == ["start", "target_start", "target_ran", "target_published",
                     "target_start", "target_ran", "target_refused", "finish"]


def test_a_refused_target_carries_no_forecast_levels(tmp_path, monkeypatch):
    """agent/official.py rule 2, enforced on the pipe rather than downstream.

    `official_run` succeeds for a withheld target — the refusal happens later,
    in `publish_official` — so p10/p50/p90 exist in the Lab process at the
    moment the refusal is decided. They must never cross into the agent.
    """
    _, outcome = _drive(_plan(tmp_path, _STUB_OK, monkeypatch))
    blob = json.dumps(outcome.events)
    for level in ('"p10"', '"p50"', '"p90"', '"rows"'):
        assert level not in blob, f"{level} must not travel on the event stream"


# ─────────────────────────── the failure path ───────────────────────────

def test_a_crashed_run_surfaces_the_labs_real_stderr(tmp_path, monkeypatch):
    _, outcome = _drive(_plan(tmp_path, _STUB_BOOM, monkeypatch))
    assert not outcome.ok
    assert outcome.returncode != 0
    assert "LightGBMError: bad allocation" in outcome.stderr
    message = RX.failure_message(outcome)
    assert "LightGBMError: bad allocation" in message, (
        "the real error must reach the user verbatim, not as a paraphrase")
    assert "Nothing was published." in message


def test_a_failed_target_is_named_with_its_stage_and_error(tmp_path, monkeypatch):
    _, outcome = _drive(_plan(tmp_path, _STUB_TARGET_FAILS, monkeypatch))
    assert not outcome.ok
    message = RX.failure_message(outcome)
    assert "Revenues" in message and "publish" in message
    assert "FileExistsError" in message


def test_failure_never_claims_success_it_cannot_substantiate(
        tmp_path, monkeypatch):
    _, outcome = _drive(_plan(tmp_path, _STUB_BOOM, monkeypatch))
    message = RX.failure_message(outcome).lower()
    for word in ("published to", "successfully", "completed successfully"):
        assert word not in message


def test_a_missing_interpreter_blocks_before_anything_launches(tmp_path):
    plan = RX.RunPlan(lab=tmp_path, python=None, data=tmp_path / "d.csv",
                      data_sha="", targets=("Revenues",), horizon=5,
                      latest_data_date="", last_issue_date="",
                      last_issue_data_sha="",
                      blocked="the lab's own interpreter is not there")
    _, outcome = _drive(plan)
    assert not outcome.ok and outcome.returncode is None
    assert "interpreter" in RX.failure_message(outcome)


# ─────────────────────────── the lock ───────────────────────────

def test_a_second_run_is_refused_while_one_is_in_flight(tmp_path):
    lock = tmp_path / "run.lock"
    ok, holder = RX.acquire_lock(("Revenues",), path=lock)
    assert ok and holder is None

    ok2, holder2 = RX.acquire_lock(("Revenues",), path=lock)
    assert not ok2, "a second concurrent run must be refused"
    assert holder2 is not None and holder2.pid == os.getpid()
    assert "already in progress" in RX.lock_message(holder2)


def test_releasing_the_lock_lets_the_next_run_start(tmp_path):
    lock = tmp_path / "run.lock"
    assert RX.acquire_lock((), path=lock)[0]
    RX.release_lock(lock)
    assert RX.acquire_lock((), path=lock)[0]


def test_a_dead_holder_does_not_lock_the_agent_out_forever(tmp_path):
    """A crashed run must not require the user to delete a file to recover."""
    lock = tmp_path / "run.lock"
    dead_pid = _a_pid_that_is_not_running()
    lock.write_text(json.dumps({"pid": dead_pid, "started_at": time.time(),
                                "targets": ["Revenues"]}), encoding="utf-8")
    assert RX.read_lock(lock) is None
    assert RX.acquire_lock((), path=lock)[0]


def test_an_ancient_holder_is_treated_as_stale(tmp_path):
    lock = tmp_path / "run.lock"
    lock.write_text(json.dumps(
        {"pid": os.getpid(),
         "started_at": time.time() - RX.STALE_AFTER_SECONDS - 1,
         "targets": []}), encoding="utf-8")
    assert RX.read_lock(lock) is None


def test_an_unreadable_lock_is_not_treated_as_a_live_run(tmp_path):
    lock = tmp_path / "run.lock"
    lock.write_text("not json at all", encoding="utf-8")
    assert RX.read_lock(lock) is None


def _a_pid_that_is_not_running() -> int:
    for pid in range(99999, 40000, -7):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except OSError:
            continue
    pytest.skip("could not find a free pid to stand in for a dead process")
