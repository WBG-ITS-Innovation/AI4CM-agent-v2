# tests/test_official_routing.py — the LLM must not answer these questions.
#
# The bug this file pins was invisible in every local rehearsal, because it
# only appears when a language-model backend is REACHABLE — which is the demo
# case and not the test case.
#
# `app.py` used to compute a rule-based answer only when the model produced
# nothing:
#
#     stream = answer_stream(q, run, targets, history)
#     if stream is not None:
#         ans = st.write_stream(stream)
#     if not ans.strip():
#         ans = answer_lab_question(q, run)      # <- only on an empty stream
#
# `answer_stream` is grounded in `build_agent_context(run, ...)`, and a run
# covers ONE target's backtest. So with a working backend, "why is Expenditure
# not published?" was answered from whichever run was newest — a State budget
# balance run — while the artifact-sourced answer sat unused behind a branch
# that only a broken backend could reach. Rehearsing without a key passed; the
# demo would not have.
#
# So: for a question about a named target's official status, the artifact
# answer is the SOURCE and the model may only rewrite it. These tests assert
# the ordering, not the wording.
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from tests.test_app_rendering import (_plotly_stub, _Recorder, _streamlit_stub,
                                      _tools_stub)


def _lab(tmp_path: Path) -> Path:
    """A minimal lab: one run, a registry with three verdicts, one issue."""
    repo = tmp_path / "AI4CM"
    run = repo / "backend" / "forecast_runs" / "2026-08-12"
    run.mkdir(parents=True)
    (run / "SUMMARY.json").write_text(json.dumps({
        "run_id": "2026-08-12", "schema_version": 2,
        "run_date": "2026-08-12", "target": "State budget balance",
        "cadence": "daily", "horizon": "5", "mode": "production",
        "data_file": "master_daily_clean_treasury.csv",
        "freshness": {"line": "Latest data date: 2025-08-06", "stale": True,
                      "backtest": False},
        "families": [{
            "name": "B_ML", "ok": True,
            "models": "LightGBM_L1", "best_model": "Lasso (MAE 145,965,394)",
            "best_model_display": "WITHHELD — persistence-like",
            "skill_pct": "6.46%", "run_status": "SUCCESS",
            "integrity_verified": True, "gate_passed": False,
            "gate_reasons": ["forecast is persistence-like (shift diagnostic)"],
            "leakage_flag": False, "shift_flag": True,
        }],
        "overall": {"families_requested": 1, "families_ok": 1,
                    "families_gate_passed": 0, "leakage_flags": 0,
                    "shift_flags": 1, "quality_gate_failures": 1},
    }), encoding="utf-8")

    def _recipe(target, rid, model, verdict, mase, sentinel, failing):
        return {
            "id": rid, "target": target, "family": "B_ML",
            "point_model": model, "status": "candidate -- pre-tuning",
            "approved_by": None,
            "dev_credentials": {
                "window": "DEV (2024)", "n": 262, "mase": mase,
                "sentinel_ratio": sentinel,
                "gates": {"accuracy_vs_naive": {
                    "passed": mase < 1.0, "name": "accuracy vs naive",
                    "measured": mase, "threshold": 1.0,
                    "reason_plain": f"{target} accuracy reason"}},
            },
            "publication": {"verdict": verdict,
                            "reason_plain": f"{target} verdict reason",
                            "named_fix": f"{target} fix",
                            "failing_gates": failing},
        }

    (repo / "registry").mkdir(parents=True, exist_ok=True)
    (repo / "registry" / "recipes.json").write_text(json.dumps({"recipes": [
        _recipe("Revenues", "rev-v1", "LightGBM_L1", "publishable", 0.757959, 1.2255, []),
        _recipe("Expenditure", "exp-v1", "LightGBM_L1", "withheld", 1.103854, 1.0882,
                ["accuracy_vs_naive"]),
        _recipe("State budget balance", "sbb-v1", "HistGBDT_L1", "withheld",
                1.57832, 7.0058, ["accuracy_vs_naive"]),
    ]}), encoding="utf-8")

    issue = repo / "forecasts" / "published" / "2026-08-16"
    issue.mkdir(parents=True)
    (issue / "manifest.json").write_text(json.dumps({
        "issue_date": "2026-08-16", "targets": ["Revenues"],
        "horizons": [1, 5], "target_dates": ["2025-08-07", "2025-08-13"]}),
        encoding="utf-8")
    (issue / "forecast.csv").write_text(
        "target,horizon,origin_date,origin_value,target_date,p10,p50,p90\n"
        "Revenues,1,2025-08-06,38196836,2025-08-07,34867361,81838118,121428687\n",
        encoding="utf-8")
    (issue / "provenance.json").write_text(json.dumps(
        {"code": {"git_sha": "4a0ff5087c69", "git_dirty": False},
         "data": {"latest_data_date": "2025-08-06"}}), encoding="utf-8")
    return run


def _load_app(run_dir: Path, monkeypatch, llm_reply: str | None):
    """app.py with a backend that returns `llm_reply` for every chat call."""
    calls: list[dict] = []

    def chat_llm(messages, json_mode=False, temperature=0.2, stream=False):
        calls.append({"messages": messages, "stream": stream})
        if llm_reply is None:
            return None
        return iter([llm_reply]) if stream else llm_reply

    llm = type(sys)("agent.llm")
    llm.have_llm = lambda: llm_reply is not None
    llm.chat_llm = chat_llm
    llm.llm_healthcheck = lambda: (llm_reply is not None, "stub")

    rec = _Recorder(None)
    plotly, go = _plotly_stub()
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_stub(rec))
    monkeypatch.setitem(sys.modules, "plotly", plotly)
    monkeypatch.setitem(sys.modules, "plotly.graph_objects", go)
    monkeypatch.setitem(sys.modules, "agent.tools", _tools_stub())
    monkeypatch.setitem(sys.modules, "agent.llm", llm)
    monkeypatch.setenv("AI4CM_RUNS_ROOT", str(run_dir.parent))
    monkeypatch.delenv("AI4CM_REPO", raising=False)
    monkeypatch.setenv("AI4CM_CHAT_HISTORY", str(run_dir.parent / "chat.json"))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    sys.modules.pop("app", None)
    return module, calls


# ───────────────── the ordering, with a working backend ─────────────────

def test_an_official_question_is_answered_from_artifacts_not_the_run(
        tmp_path, monkeypatch):
    """The regression: with a backend up, this used to answer from the run."""
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="IGNORED")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    # The run is a State budget balance backtest; the question is not.
    assert run.view.target.value == "State budget balance"

    answer = app.answer_official_question(
        "Why is Expenditure not published?", run)
    assert answer is not None, "an official question must not fall through"
    assert "Expenditure" in answer
    assert "exp-v1" in answer
    assert "1.10385" in answer or "1.103854" in answer
    # The run's own family verdict must not be what answers this.
    assert "persistence-like" not in answer
    assert "B_ML" not in answer


def test_the_official_answer_is_the_source_and_the_model_only_rewrites(
        tmp_path, monkeypatch):
    """`official_narrative_stream` must receive the artifact answer verbatim."""
    run_dir = _lab(tmp_path)
    app, calls = _load_app(run_dir, monkeypatch, llm_reply="rephrased")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    factual = app.answer_official_question("Why is Expenditure not published?", run)
    calls.clear()
    stream = app.official_narrative_stream("Why is Expenditure not published?",
                                           run, ["Revenues", "Expenditure"], factual)
    assert stream is not None
    assert len(calls) == 1
    prompt = calls[0]["messages"][-1]["content"]
    # The artifact answer is handed to the model, not reconstructed by it.
    assert factual in prompt
    # And the instructions forbid it moving a figure or softening the verdict.
    assert "Keep EVERY number exactly as written" in prompt
    assert "keep the word 'withheld'" in prompt


def test_a_non_official_question_still_reaches_the_general_llm_path(
        tmp_path, monkeypatch):
    """Only target-scoped official questions are diverted; the rest are not."""
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="x")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    for question in ("what does skill mean?",
                     "is the data fresh?",
                     "what are you?"):
        assert app.answer_official_question(question, run) is None, question


def test_an_unnamed_target_asks_rather_than_answering_about_the_run(
        tmp_path, monkeypatch):
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply=None)
    import agent.lab_bridge as LB
    run = LB.load_latest()

    answer = app.answer_official_question("why is it not published?", run)
    assert answer is not None
    assert "Which line do you mean" in answer
    # All three offered, so the user can pick rather than be guessed at.
    for name in ("Revenues", "Expenditure", "State budget balance"):
        assert name in answer


# ───────────────── the model's context is grounded ─────────────────

def test_the_context_carries_every_targets_verdict_not_just_the_runs(
        tmp_path, monkeypatch):
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="x")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    ctx = app.official_context(run)
    assert ctx["latest_issue_date"] == "2026-08-16"
    assert ctx["targets_in_latest_issue"] == ["Revenues"]
    assert ctx["validated_horizon_business_days"] == 5
    per = ctx["per_target"]
    assert per["Revenues"]["publication_verdict"] == "publishable"
    assert per["Expenditure"]["publication_verdict"] == "withheld"
    assert per["State budget balance"]["publication_verdict"] == "withheld"
    assert per["Revenues"]["in_latest_published_issue"] is True
    assert per["Expenditure"]["in_latest_published_issue"] is False


def test_the_context_carries_no_forecast_level_for_a_withheld_target(
        tmp_path, monkeypatch):
    """A number in the context window is a number that can be echoed."""
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="x")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    ctx = app.official_context(run)
    for target in ("Expenditure", "State budget balance"):
        entry = ctx["per_target"][target]
        assert "WITHHELD" in entry["forecast_numbers"]
        assert "Do not quote" in entry["forecast_numbers"]
        for key in ("p10", "p50", "p90", "forecast_rows"):
            assert key not in entry


def test_the_context_states_that_nothing_is_approved(tmp_path, monkeypatch):
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="x")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    ctx = app.official_context(run)
    assert "null on every recipe" in ctx["approval_rule"]
    for entry in ctx["per_target"].values():
        assert entry["approved"] is False
        assert entry["approved_by"] is None


def test_the_context_states_no_realized_accuracy_exists(tmp_path, monkeypatch):
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="x")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    ctx = app.official_context(run)
    assert ctx["realized_scored_rows"] == 0
    assert "2025 holdout has never been evaluated" in ctx["accuracy_rule"]


def test_the_context_is_valid_json_and_within_budget(tmp_path, monkeypatch):
    """`build_agent_context` truncates at a byte budget; the block must fit."""
    run_dir = _lab(tmp_path)
    app, _ = _load_app(run_dir, monkeypatch, llm_reply="x")
    import agent.lab_bridge as LB
    run = LB.load_latest()

    raw = app.build_agent_context(run, ["Revenues", "Expenditure"])
    assert len(raw) <= 16000
    parsed = json.loads(raw)          # must not be truncated mid-structure
    assert "official_publication_status" in parsed
    assert parsed["official_publication_status"]["latest_issue_date"] == "2026-08-16"
