# tests/test_data_flow.py — the three-step data flow, and the hold on it.
#
# Session 5 established that nothing runs without an explicit yes. This file
# extends that to two more actions with different consequences — taking a data
# file, and scoring — and adds the one thing Session 5 did not have: a switch
# that refuses to write into the lab's real tree at all.
#
# The switch is not a nicety. A lab session regenerating artifacts writes to
# every path this agent writes to, and neither side can see the other's work in
# progress. The agent cannot detect that state: the lab's data file and its
# published issues are gitignored, so a clean `git status` proves nothing. So
# the hold is explicit, defaults to ON, and these tests pin that a held run
# still does everything it safely can rather than refusing outright.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import run_intent as RI
from tests.test_action_confirmation import _app, _no_launch, _runnable_lab

_COLS = ["date", "Revenues", "Expenditure", "State budget balance", "is_weekend"]


def _write_csv(path: Path, rows: list[tuple]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(_COLS)] + [",".join(str(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rows(start: int, n: int) -> list[tuple]:
    return [(f"2025-08-{start + i:02d}", 100.0 + i, 200.0 + i, -100.0 + i, 0)
            for i in range(n)]


def _lab_with_data(tmp_path: Path, monkeypatch):
    """The Session 5 fixture lab, with a real master data file to extend."""
    app, lab = _app(tmp_path, monkeypatch)
    _write_csv(lab / "backend" / "data" / "processed"
               / "master_daily_clean_treasury.csv", _rows(1, 6))
    monkeypatch.setattr(app.RX, "LOCK_PATH", tmp_path / "run.lock")
    monkeypatch.setattr(app, "UPLOAD_DIR", tmp_path / "uploads")
    return app, lab


def _said(app) -> str:
    return app.st.session_state["chat"][-1]["content"]


# ─────────────────────────── intent ───────────────────────────

@pytest.mark.parametrize("message", [
    "here is the new data",
    "here's the new actuals",
    "I have new data",
    "new actuals have arrived",
    "the updated figures came in",
    "I've got fresh data for you",
    "take these new actuals",
])
def test_an_announcement_of_data_reaches_the_action_tier(message):
    assert RI.classify_data(message) is not None


@pytest.mark.parametrize("message", [
    "is the data fresh?",
    "what data does the lab use?",
    "how new is the data?",
    "when did the data last update?",
    "which actuals are missing?",
    "why is the data stale?",
])
def test_a_question_about_data_never_reaches_the_action_tier(message):
    """These are Tier-2 questions and share every noun with the instruction."""
    assert RI.classify_data(message) is None


def test_a_bare_csv_path_is_read_as_an_instruction():
    ask = RI.classify_data("/tmp/new_actuals.csv")
    assert ask is not None and ask.path == "/tmp/new_actuals.csv"


def test_a_path_inside_a_sentence_is_picked_up():
    ask = RI.classify_data("here is the new data: /tmp/aug.csv")
    assert ask is not None and ask.path == "/tmp/aug.csv"


# ────────────────── validation happens before anything runs ──────────────────

def test_announcing_data_without_a_path_points_at_the_uploader(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)

    assert app.action_turn("here is the new data", lab, ["Revenues"]) is True
    assert "uploader" in _said(app)
    assert app.st.session_state.get(app.PENDING_DATA) is None


def test_a_bad_file_is_rejected_and_nothing_is_staged(tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    bad = _write_csv(tmp_path / "only_new.csv", _rows(7, 3))   # truncated history

    app.action_turn(f"here is the new data: {bad}", lab, ["Revenues"])

    said = _said(app)
    assert "I can't use that file" in said
    assert "only the new rows" in said
    assert app.st.session_state.get(app.PENDING_DATA) is None, (
        "a rejected file must not become a pending action")


def test_a_good_file_is_described_and_waits(tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])

    said = _said(app)
    assert "nothing has been run or installed yet" in said.lower()
    assert "Shall I take this file?" in said
    assert app.st.session_state.get(app.PENDING_DATA) is not None


# ─────────────────── no subprocess without confirmation ───────────────────

def test_no_scoring_subprocess_is_launched_without_a_yes(tmp_path, monkeypatch):
    """The central negative, for the scoring action specifically."""
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])          # yes to TAKE, not to score

    assert app.st.session_state.get(app.PENDING_SCORE) is not None, (
        "scoring must be offered, not performed")
    assert "Shall I score" in _said(app)


def test_declining_the_score_launches_nothing(tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])
    app.action_turn("no", lab, ["Revenues"])

    assert "nothing was scored" in _said(app)
    assert app.st.session_state.get(app.PENDING_SCORE) is None


@pytest.mark.parametrize("reply", ["what does that involve?", "hmm", "is that ok?"])
def test_an_ambiguous_reply_to_the_score_offer_scores_nothing(
        tmp_path, monkeypatch, reply):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])
    handled = app.action_turn(reply, lab, ["Revenues"])

    assert handled is False
    assert app.st.session_state.get(app.PENDING_SCORE) is None


# ─────────────────────────── the hold ───────────────────────────

def test_under_a_hold_the_file_is_not_installed(tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.delenv(app.RX.LAB_WRITES_ENV, raising=False)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    current = lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv"
    before = current.read_bytes()
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])

    assert current.read_bytes() == before, "the lab's data must not be replaced"
    said = _said(app)
    assert "holding off" in said
    assert app.RX.LAB_WRITES_ENV in said, "the message must say how to permit it"


def test_a_hold_still_offers_scoring_because_it_writes_nothing_of_theirs(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.delenv(app.RX.LAB_WRITES_ENV, raising=False)
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])

    assert app.st.session_state.get(app.PENDING_SCORE) is not None
    assert "Shall I score" in _said(app)


def test_with_writes_permitted_the_file_is_installed_with_a_backup(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setenv(app.RX.LAB_WRITES_ENV, "1")
    monkeypatch.setattr(app.RX, "stream_score", _no_launch)
    current = lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv"
    before = current.read_bytes()
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))

    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])

    assert current.read_bytes() == good.read_bytes()
    said = _said(app)
    assert "Installed to" in said and "backed up to" in said
    backups = list(current.parent.glob("*.bak.csv"))
    assert len(backups) == 1 and backups[0].read_bytes() == before


# ─────────────────── scoring, against a stub lab ───────────────────

_SCORECARD = (
    "schema_version,issue_date,target,recipe_id,horizon,origin_date,origin_value,"
    "target_date,p10,p50,p90,interval_nominal,y_true,abs_error,inside_interval,"
    "persistence_pred,persistence_abs_error,skill_vs_ruler_pct,persistence_source,"
    "ops_pred,ops_abs_error,skill_vs_ops,ops_source,scored_in_window,"
    "publication_verdict,point_model,interval_model,target_transform,"
    "data_sha_at_issue,git_sha_at_issue,scored_at_data_sha\n"
    "2,2026-08-16,Revenues,rev-v1,1,2025-08-06,38196835.92,2025-08-07,"
    "34867361,81838118,121428687,0.8,39342741,42495377,True,"
    "38196835.92,1145905,-3608.46,artifact: origin_value,"
    "64782196,25439455,-67.05,Treasury planning method,live,"
    "publishable,LightGBM_L1,GBQuantile,ratio,abc,def,ghi\n")


def _score_launcher(calls: list, scorecard_body: str = _SCORECARD):
    def stream_score(lab, python, data, published_root=None, scorecard=None,
                     timeout=3600):
        calls.append({"data": Path(data), "scorecard": scorecard})
        from agent.run_exec import RunOutcome
        Path(scorecard).parent.mkdir(parents=True, exist_ok=True)
        Path(scorecard).write_text(scorecard_body, encoding="utf-8")
        outcome = RunOutcome(returncode=0)
        outcome.events = [
            {"event": "start", "issues": 3},
            {"event": "scored", "scored": 1, "pending": 4, "issues": 3,
             "scorecard": str(scorecard),
             "summary": {"Revenues": {"n": 1, "realized_mae": 42495377.0,
                                      "persistence_mae": 1145905.0,
                                      "skill_vs_ruler_pct": -3608.46,
                                      "interval_hit_rate": 1.0,
                                      "nominal_coverage": 0.8,
                                      "issues_covered": 1}},
             "pending_dates": [["Revenues", "2025-08-08"],
                               ["Revenues", "2025-08-11"],
                               ["Expenditure", "2025-08-08"],
                               ["Expenditure", "2025-08-11"]],
             "baseline_disagreements": []},
            {"event": "finish", "scored": 1, "pending": 4, "refused": False},
        ]
        yield ("event", outcome.events[0])
        yield ("done", outcome)
    return stream_score


def _drive_to_score(app, lab, tmp_path, launcher):
    good = _write_csv(tmp_path / "extended.csv", _rows(1, 9))
    app.action_turn(f"here is the new data: {good}", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])
    app.action_turn("yes", lab, ["Revenues"])


def test_a_confirmed_score_reports_figures_from_the_scorecard(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_score", _score_launcher(calls))
    monkeypatch.chdir(tmp_path)

    _drive_to_score(app, lab, tmp_path, calls)

    assert len(calls) == 1
    said = _said(app)
    assert "Scored 1 published forecast(s)" in said
    assert "81,838,118" in said and "39,342,741" in said     # P50 and actual
    assert "42,495,377" in said                              # abs error
    assert "recomputed by me" in said


def test_pending_rows_are_stated_because_nothing_on_disk_records_them(
        tmp_path, monkeypatch):
    """The scorecard holds only SCORED rows; pending exists only in the run."""
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_score", _score_launcher(calls))
    monkeypatch.chdir(tmp_path)

    _drive_to_score(app, lab, tmp_path, calls)

    said = _said(app)
    assert "4 row(s) still pending" in said
    assert "Still awaiting truth" in said
    assert "Expenditure" in said


def test_the_ops_comparator_is_reported_when_its_columns_exist(
        tmp_path, monkeypatch):
    """The lab added these columns mid-session; the report must not ignore them."""
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_score", _score_launcher(calls))
    monkeypatch.chdir(tmp_path)

    _drive_to_score(app, lab, tmp_path, calls)

    assert "Treasury's current method" in _said(app)


def test_an_absent_ops_figure_is_reported_with_the_labs_own_reason(
        tmp_path, monkeypatch):
    """Absence is never a value — and the lab says WHY it is absent."""
    body = _SCORECARD.replace(
        "64782196,25439455,-67.05,Treasury planning method",
        ",,,not defined: a balance level has no annual total")
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_score", _score_launcher(calls, body))
    monkeypatch.chdir(tmp_path)

    _drive_to_score(app, lab, tmp_path, calls)

    said = _said(app)
    assert "not recorded — not defined: a balance level has no annual total" in said


def test_under_a_hold_scoring_writes_a_staging_scorecard_not_the_labs(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.delenv(app.RX.LAB_WRITES_ENV, raising=False)
    calls: list = []
    monkeypatch.setattr(app.RX, "stream_score", _score_launcher(calls))
    monkeypatch.chdir(tmp_path)

    _drive_to_score(app, lab, tmp_path, calls)

    assert calls[0]["scorecard"] is not None, (
        "a held run must redirect the scorecard, never write the lab's")
    assert "staging" in str(calls[0]["scorecard"])
    assert "not the lab's tracked scorecard" in _said(app)


def test_with_writes_permitted_scoring_targets_the_labs_own_scorecard(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    monkeypatch.setenv(app.RX.LAB_WRITES_ENV, "1")
    calls: list = []

    def stream_score(lab_, python, data, published_root=None, scorecard=None,
                     timeout=3600):
        calls.append({"scorecard": scorecard})
        from agent.run_exec import RunOutcome
        out = RunOutcome(returncode=0)
        out.events = [{"event": "scored", "scored": 0, "pending": 25,
                       "issues": 3, "scorecard": "", "summary": {},
                       "pending_dates": [], "baseline_disagreements": []},
                      {"event": "finish", "scored": 0, "pending": 25}]
        yield ("done", out)

    monkeypatch.setattr(app.RX, "stream_score", stream_score)
    _drive_to_score(app, lab, tmp_path, calls)

    assert calls[0]["scorecard"] is None, (
        "permitted writes score into the lab's real scorecard")


def test_nothing_scoreable_yet_is_reported_as_expected_not_as_failure(
        tmp_path, monkeypatch):
    app, lab = _lab_with_data(tmp_path, monkeypatch)
    calls: list = []

    def stream_score(lab_, python, data, published_root=None, scorecard=None,
                     timeout=3600):
        from agent.run_exec import RunOutcome
        out = RunOutcome(returncode=0)
        out.events = [{"event": "scored", "scored": 0, "pending": 25,
                       "issues": 3, "scorecard": str(scorecard), "summary": {},
                       "pending_dates": [["Revenues", "2025-08-07"]],
                       "baseline_disagreements": []},
                      {"event": "finish", "scored": 0, "pending": 25}]
        yield ("done", out)

    monkeypatch.setattr(app.RX, "stream_score", stream_score)
    monkeypatch.chdir(tmp_path)
    _drive_to_score(app, lab, tmp_path, calls)

    said = _said(app)
    assert "expected state" in said
    assert "not a failure" in said
    assert "25 forecast row(s) are still waiting" in said
    assert "distinct target-date(s)" in said
