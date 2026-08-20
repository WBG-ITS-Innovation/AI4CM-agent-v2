# tests/test_action_confirmation.py — nothing runs without an explicit yes.
#
# This is the one rule in the session brief that is stated as an absolute:
# "No silent execution." So the central assertion here is negative and it is
# repeated from several directions — the subprocess launcher is replaced with a
# recorder that FAILS the test if it is called, and then every path that is not
# an explicit affirmative is driven through it.
#
# Testing the negative matters more than testing the positive because the
# positive is the path a developer exercises by hand a hundred times while
# building, and the negative is the one nobody notices is broken until an agent
# publishes a forecast during a demo.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent import run_intent as RI
from tests.test_app_rendering import (_plotly_stub, _Recorder, _streamlit_stub,
                                      _tools_stub)
from tests.test_official_routing import _lab, _load_app


def _runnable_lab(tmp_path: Path) -> Path:
    """The routing fixture's lab, plus the two things a run needs to exist.

    `plan_run` refuses to describe a runnable plan without the Lab's own
    interpreter and its data file, and that refusal is correct — but it means a
    fixture lab without them can only ever exercise the blocked path.
    """
    run_dir = _lab(tmp_path)
    lab = run_dir.parent.parent.parent

    venv = lab / "backend" / ".venv" / "bin"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    data = lab / "backend" / "data" / "processed"
    data.mkdir(parents=True, exist_ok=True)
    (data / "master_daily_clean_treasury.csv").write_text(
        "date,Revenues\n2025-08-05,1\n2025-08-06,2\n", encoding="utf-8")
    return run_dir


class _Launched(Exception):
    """Raised by the stub launcher. Reaching it is the failure."""


def _no_launch(*args, **kwargs):
    raise _Launched("a subprocess was launched without the user's consent")


def _app(tmp_path, monkeypatch):
    run_dir = _runnable_lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply=None)
    app.st.session_state["chat"] = []
    lab = run_dir.parent.parent.parent
    return app, lab


# ─────────────────── an instruction only ever asks first ───────────────────

def test_an_instruction_does_not_launch_anything(tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)

    handled = app.action_turn("run today's forecast", lab,
                              ["Revenues", "Expenditure"])

    assert handled is True, "the ACTION tier must own this turn"
    assert app.st.session_state.get(app.PENDING_RUN) is not None


def test_the_confirmation_says_nothing_has_started(tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)
    app.action_turn("run today's forecast", lab, ["Revenues"])

    said = app.st.session_state["chat"][-1]["content"]
    assert "nothing has started yet" in said.lower()
    assert "Shall I run it?" in said


def test_the_confirmation_states_mode_targets_horizon_and_publication(
        tmp_path, monkeypatch):
    """The user must be able to tell what they are agreeing to."""
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)
    app.action_turn("run today's forecast", lab,
                    ["Revenues", "Expenditure", "State budget balance"])

    said = app.st.session_state["chat"][-1]["content"]
    assert "official" in said.lower()
    assert "Revenues" in said and "Expenditure" in said
    assert "5 business days" in said
    assert "withheld" in said.lower()
    assert "vault" in said.lower(), "retention must be part of what is agreed"


# ───────────────────────── refusal and ambiguity ─────────────────────────

def test_a_no_cancels_and_launches_nothing(tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)
    app.action_turn("run today's forecast", lab, ["Revenues"])

    handled = app.action_turn("no", lab, ["Revenues"])

    assert handled is True
    assert app.st.session_state.get(app.PENDING_RUN) is None
    said = app.st.session_state["chat"][-1]["content"]
    assert "have not run anything" in said
    assert "lab is untouched" in said


@pytest.mark.parametrize("reply", [
    "what would that cost?",
    "which targets does that include?",
    "no, wait — what does the gate check?",
    "is that ok?",
    "hmm",
])
def test_an_ambiguous_reply_never_launches_a_run(tmp_path, monkeypatch, reply):
    """`unclear` is a real outcome and it does not run anything.

    A yes/no parser that guesses converts every ambiguous reply into whichever
    answer it defaults to, and one of those defaults publishes a forecast.
    Note "no, wait — ..." in particular: a substring search for "wait" or for
    "no" would resolve it, and both resolutions are wrong.
    """
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)
    app.action_turn("run today's forecast", lab, ["Revenues"])

    handled = app.action_turn(reply, lab, ["Revenues"])

    assert handled is False, "an ambiguous reply is answered, not executed"
    assert app.st.session_state.get(app.PENDING_RUN) is None, (
        "consent must not stay open across an unrelated turn")


def test_consent_does_not_survive_a_later_unrelated_question(
        tmp_path, monkeypatch):
    """The dangerous shape: ask, wander off, and say 'yes' to something else."""
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)
    app.action_turn("run today's forecast", lab, ["Revenues"])
    app.action_turn("what does MASE mean?", lab, ["Revenues"])

    handled = app.action_turn("yes", lab, ["Revenues"])

    assert handled is False, "there is no pending run left to confirm"


# ───────────────────────── the affirmative path ─────────────────────────

def _recording_launcher(calls: list):
    def stream_run(plan, published_root=None, vault_root=None, timeout=3600):
        calls.append(plan)
        from agent.run_exec import RunOutcome
        outcome = RunOutcome(returncode=0)
        outcome.events = [
            {"event": "start", "issue_date": "2026-08-17",
             "targets": list(plan.targets)},
            {"event": "target_refused", "target": "Expenditure",
             "stage": "publish", "reason": "verdict is 'withheld'"},
            {"event": "finish", "issue_date": "2026-08-17", "published": [],
             "refused": ["Expenditure"], "failed": []},
        ]
        yield ("event", outcome.events[0])
        yield ("done", outcome)
    return stream_run


def test_an_explicit_yes_is_what_launches_the_run(tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_run", _recording_launcher(calls))
    monkeypatch.setattr(app.RX, "LOCK_PATH", tmp_path / "run.lock")

    app.action_turn("run today's forecast", lab, ["Revenues", "Expenditure"])
    assert calls == [], "still nothing launched"

    handled = app.action_turn("yes", lab, ["Revenues", "Expenditure"])

    assert handled is True
    assert len(calls) == 1, "exactly one run, launched by the yes"
    assert app.st.session_state.get(app.PENDING_RUN) is None


@pytest.mark.parametrize("affirmative",
                         ["yes", "y", "go ahead", "do it", "run it", "proceed",
                          "confirm", "run anyway", "please do"])
def test_the_ordinary_ways_of_saying_yes_all_work(tmp_path, monkeypatch,
                                                  affirmative):
    app, lab = _app(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_run", _recording_launcher(calls))
    monkeypatch.setattr(app.RX, "LOCK_PATH", tmp_path / "run.lock")

    app.action_turn("run today's forecast", lab, ["Revenues"])
    app.action_turn(affirmative, lab, ["Revenues"])

    assert len(calls) == 1, f"{affirmative!r} is consent and must run"


def test_a_confirmed_run_reports_the_withheld_target_without_levels(
        tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _recording_launcher([]))
    monkeypatch.setattr(app.RX, "LOCK_PATH", tmp_path / "run.lock")

    app.action_turn("run today's forecast", lab, ["Revenues", "Expenditure"])
    app.action_turn("yes", lab, ["Revenues", "Expenditure"])

    said = app.st.session_state["chat"][-1]["content"]
    assert "Expenditure" in said and "withheld" in said.lower()


# ───────────────────────────── the lock ─────────────────────────────

def test_a_confirmed_run_is_refused_while_another_is_in_flight(
        tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    calls: list = []
    lock = tmp_path / "run.lock"
    monkeypatch.setattr(app.RX, "stream_run", _recording_launcher(calls))
    monkeypatch.setattr(app.RX, "LOCK_PATH", lock)

    app.RX.acquire_lock(("Revenues",), path=lock)     # something else is running

    app.action_turn("run today's forecast", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])

    assert calls == [], "a second concurrent run must not launch"
    said = app.st.session_state["chat"][-1]["content"]
    assert "already in progress" in said


def test_the_lock_is_released_even_when_the_run_raises(tmp_path, monkeypatch):
    """A crashed run must not lock the agent out of every later one."""
    app, lab = _app(tmp_path, monkeypatch)
    lock = tmp_path / "run.lock"
    monkeypatch.setattr(app.RX, "LOCK_PATH", lock)

    def explode(*a, **kw):
        raise RuntimeError("boom")
        yield  # pragma: no cover - generator marker

    monkeypatch.setattr(app.RX, "stream_run", explode)
    app.action_turn("run today's forecast", lab, ["Revenues"])
    with pytest.raises(RuntimeError):
        app.action_turn("yes", lab, ["Revenues"])

    assert app.RX.read_lock(lock) is None, "the lock must not outlive the run"


# ───────────────────── the unchanged-data guardrail ─────────────────────

def test_unchanged_data_is_disclosed_before_the_user_agrees(
        tmp_path, monkeypatch):
    """The guardrail fires on the real lab today, so it must be visible.

    The champion recipes forecast forward from the end of the data. If the
    data has not moved, a new run reproduces the same numbers under a new
    issue date — a second immutable record of one forecast, in a directory
    that IS the lab's track record.
    """
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)

    data = lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv"
    issue = lab / "forecasts" / "published" / "2026-08-16"
    manifest = json.loads((issue / "manifest.json").read_text())
    manifest["data_sha_at_issue"] = app.RX.sha256_of(data)
    (issue / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    app.action_turn("run today's forecast", lab, ["Revenues"])

    said = app.st.session_state["chat"][-1]["content"]
    assert "has not changed since the last issue" in said
    assert "run anyway" in said
    assert "same numbers under a new issue date" in said


def test_changed_data_carries_no_duplicate_warning(tmp_path, monkeypatch):
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)

    issue = lab / "forecasts" / "published" / "2026-08-16"
    manifest = json.loads((issue / "manifest.json").read_text())
    manifest["data_sha_at_issue"] = "a-different-hash-entirely"
    (issue / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    app.action_turn("run today's forecast", lab, ["Revenues"])

    said = app.st.session_state["chat"][-1]["content"]
    assert "has not changed since the last issue" not in said


# ───────────────────── questions still reach the answers ─────────────────────

@pytest.mark.parametrize("question", [
    "What is the latest forecast for Revenues?",
    "Which model is best for Revenues?",
    "Why is Expenditure not published?",
    "Why is State budget balance not published?",
    "How accurate have the forecasts been?",
    "Forecast Revenues 30 days ahead",
    "how do I run a forecast?",
])
def test_a_question_falls_through_to_the_answering_tiers(
        tmp_path, monkeypatch, question):
    """The Session 3/4 demo script, plus the trap, through the real app."""
    app, lab = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_run", _no_launch)

    assert app.action_turn(question, lab, ["Revenues", "Expenditure",
                                           "State budget balance"]) is False
    assert app.st.session_state.get(app.PENDING_RUN) is None
