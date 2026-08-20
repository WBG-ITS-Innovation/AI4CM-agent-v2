# tests/test_official_answers.py — the demo script, as assertions.
#
# Every test here pins a way the agent was wrong before `agent/official.py`
# existed, not merely a way it is right now. The failure these replaced was
# not fabrication — the contract layer never invented a figure — it was
# target-blindness: every answer came from whichever backtest run was newest,
# so a question about Expenditure was answered with a State budget balance
# run's family verdicts, in the right shape, with no target named. It read as
# an answer and was not one.
#
# The fixtures deliberately include the trap the real lab contains: an OLDER
# published issue that still carries full p10/p50/p90 rows for targets the
# gates have since withheld, over the same target dates as the current issue.
# A consumer that falls back to it answers with withdrawn numbers.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import official
from agent import published as PUB
from agent import registry_read as REG


def _json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gates(passed: bool, mase: float, sentinel: float) -> dict:
    return {
        "accuracy_vs_naive": {
            "passed": mase < 1.0,
            "name": "accuracy vs repeating the same weekday last week",
            "metric": "MASE", "measured": mase, "threshold": 1.0,
            "reason_plain": "ACCURACY_REASON",
        },
        "signal": {
            "passed": sentinel >= 1.15,
            "name": "signal (shuffled-target control)",
            "measured": sentinel, "threshold": 1.15,
            "reason_plain": "SIGNAL_REASON",
        },
        "coverage": {
            "passed": None, "name": "interval coverage",
            "measured": None, "threshold": None,
            "reason_plain": "No intervals were reported.",
        },
    }


def _recipe(target: str, rid: str, model: str, verdict: str,
            mase: float, sentinel: float, failing: list[str]) -> dict:
    return {
        "id": rid, "target": target, "family": "B_ML", "point_model": model,
        "status": "candidate -- pre-tuning", "approved_by": None,
        "dev_credentials": {
            "window": "DEV (2024)", "n": 262, "mase": mase,
            "sentinel_ratio": sentinel, "skill_vs_ruler_pct": 30.0,
            "gates": _gates(verdict == "publishable", mase, sentinel),
        },
        "publication": {
            "verdict": verdict,
            "reason_plain": f"{target.upper()}_VERDICT_REASON",
            "named_fix": f"{target.upper()}_NAMED_FIX",
            "failing_gates": failing,
        },
    }


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    """A lab with one publishable target, two withheld, and the stale-issue trap."""
    repo = tmp_path / "AI4CM"

    _json(repo / "registry" / "recipes.json", {
        "schema_version": 1,
        "gate_policy": {"mase_max": 1.0, "sentinel_min": 1.15},
        "recipes": [
            _recipe("Revenues", "rev-v1", "LightGBM_L1", "publishable",
                    0.757959, 1.2255, []),
            _recipe("Expenditure", "exp-v1", "LightGBM_L1", "withheld",
                    1.103854, 1.0882, ["accuracy_vs_naive", "signal"]),
            _recipe("State budget balance", "sbb-v1", "HistGBDT_L1", "withheld",
                    1.57832, 7.0058, ["accuracy_vs_naive"]),
        ],
    })

    # THE TRAP: an older issue publishing all three targets, over the same
    # target dates as the current one, from before the gates withheld two.
    old = repo / "forecasts" / "published" / "2025-08-06"
    _json(old / "manifest.json", {
        "issue_date": "2025-08-06",
        "targets": ["Expenditure", "Revenues", "State budget balance"],
        "horizons": [1, 2, 3, 4, 5],
        "target_dates": ["2025-08-07"],
    })
    _write(old / "forecast.csv",
           "target,horizon,origin_date,origin_value,target_date,p10,p50,p90\n"
           "Revenues,1,2025-08-06,38196836,2025-08-07,34867361,81838118,121428687\n"
           "Expenditure,1,2025-08-06,30000000,2025-08-07,18902761,53368062,144568755\n"
           "State budget balance,1,2025-08-06,1700000000,2025-08-07,"
           "1369827399,1426388816,1494185054\n")
    _json(old / "provenance.json",
          {"code": {"git_sha": "deadbeef" * 5, "git_dirty": True},
           "data": {"latest_data_date": "2025-08-06"}})

    # The current issue: Revenues only.
    new = repo / "forecasts" / "published" / "2026-08-16"
    _json(new / "manifest.json", {
        "issue_date": "2026-08-16", "targets": ["Revenues"],
        "horizons": [1, 2, 3, 4, 5],
        "target_dates": ["2025-08-07", "2025-08-08", "2025-08-11",
                         "2025-08-12", "2025-08-13"],
    })
    _write(new / "forecast.csv",
           "target,horizon,origin_date,origin_value,target_date,p10,p50,p90,point_model\n"
           "Revenues,1,2025-08-06,38196836,2025-08-07,34867361,81838118,121428687,LightGBM_L1\n"
           "Revenues,5,2025-08-06,38196836,2025-08-13,36241520,47915238,116579065,LightGBM_L1\n")
    _json(new / "provenance.json",
          {"code": {"git_sha": "4a0ff5087c690d7a229f6284b5349652e9b303fe",
                    "git_dirty": False},
           "data": {"latest_data_date": "2025-08-06"}})

    _write(repo / "forecasts" / "scorecard.csv",
           "issue_date,target,horizon,target_date,p10,p50,p90,y_true\n")
    return repo


# ───────────────────── the stale-issue trap ─────────────────────

def test_only_the_latest_issue_is_ever_read(lab: Path):
    """Rule 1: no fallback to an older issue for a missing target."""
    issue = PUB.latest_issue(lab)
    assert issue.issue_date == "2026-08-16"
    assert issue.targets == ("Revenues",)
    assert not issue.publishes("Expenditure")
    assert not issue.publishes("State budget balance")


def test_a_withheld_target_never_reaches_the_older_issues_numbers(lab: Path):
    """The 2025-08-06 issue's Expenditure p50 must not appear in any answer."""
    withdrawn = "53,368,062"          # Expenditure p50, formatted as we format
    for question in ("Why is Expenditure not published?",
                     "What is the forecast for Expenditure?",
                     "Which model is best for Expenditure?"):
        target = official.resolve_target(question)
        answer = (official.why_not_published_answer(lab, target)
                  if "not published" in question else
                  official.forecast_answer(lab, target, question)
                  if "forecast" in question else
                  official.best_model_answer(lab, target))
        assert withdrawn not in answer
        assert "53368062" not in answer.replace(",", "")


def test_no_withheld_target_ever_emits_a_level(lab: Path):
    """Rule 2: a withheld target gets a verdict, never a number to act on."""
    for target in ("Expenditure", "State budget balance"):
        answer = official.forecast_answer(lab, target,
                                          f"forecast {target}")
        assert "withheld" in answer.lower()
        # No p10/p50/p90 table, and no 7+ digit money figure of any kind.
        assert "P50" not in answer
        import re
        big = [m for m in re.findall(r"\d[\d,]{6,}", answer)]
        assert not big, f"{target} answer leaked a level: {big}"


# ───────────────────── the six demo questions ─────────────────────

def test_a_latest_forecast_shows_the_current_issue_with_intervals(lab: Path):
    answer = official.forecast_answer(lab, "Revenues",
                                      "What is the latest forecast for Revenues?")
    # The issue quoted must be the current one, not the stale 2025-08-06 dir.
    assert "issue `2026-08-16`" in answer
    assert "issue `2025-08-06`" not in answer
    assert "34,867,361" in answer and "81,838,118" in answer
    assert "121,428,687" in answer
    assert "P10" in answer and "P50" in answer and "P90" in answer
    assert "rev-v1" in answer


def test_b_best_model_names_the_registry_champion_not_a_family_best(lab: Path):
    answer = official.best_model_answer(lab, "Revenues")
    assert "rev-v1" in answer and "LightGBM_L1" in answer
    assert "0.757959" in answer            # cited, not invented
    assert "1.2255" in answer
    # The contract §1 distinction must be stated, not merely respected.
    assert "not the same as the best model within a single model family" in answer


def test_c_expenditure_states_both_failing_gates_in_plain_language(lab: Path):
    answer = official.why_not_published_answer(lab, "Expenditure")
    assert "Expenditure" in answer
    assert "withheld" in answer.lower()
    assert "failed 2 quality checks" in answer
    assert "1.10385" in answer or "1.103854" in answer
    assert "1.0882" in answer
    assert "ACCURACY_REASON" in answer and "SIGNAL_REASON" in answer
    assert "EXPENDITURE_NAMED_FIX" in answer


def test_d_state_budget_balance_states_one_failing_gate(lab: Path):
    answer = official.why_not_published_answer(lab, "State budget balance")
    assert "State budget balance" in answer
    assert "failed 1 quality check" in answer
    assert "1.57832" in answer
    # Its signal gate PASSES; the answer must not list it as a failure.
    assert "SIGNAL_REASON" not in answer


def test_c_and_d_do_not_produce_the_same_text(lab: Path):
    """The regression that would sink the demo: one answer for two targets."""
    c = official.why_not_published_answer(lab, "Expenditure")
    d = official.why_not_published_answer(lab, "State budget balance")
    assert c != d
    assert "Expenditure" in c and "Expenditure" not in d
    assert "State budget balance" in d


def test_e_accuracy_labels_withheld_figures_as_reasons_not_estimates(lab: Path):
    answer = official.accuracy_answer(lab)
    assert "0.757959" in answer and "1.10385" in answer and "1.57832" in answer
    assert "DEV (2024)" in answer
    # The load-bearing claim: no realized accuracy exists.
    assert "not been scored against a real outcome" in answer.lower() or \
           "no published forecast has been scored" in answer.lower()
    assert "2025 holdout" in answer
    # Each withheld figure must be marked as the reason, not an estimate.
    assert answer.count("withheld") >= 2
    assert "not an estimate you can use" in answer


def test_f_a_horizon_beyond_the_issue_is_refused_not_extrapolated(lab: Path):
    answer = official.forecast_answer(lab, "Revenues",
                                      "Forecast Revenues 30 days ahead")
    assert "can't give you an official" in answer.lower()
    assert "5 business days" in answer
    # It must not answer with the 5-day numbers as though they were the 30-day.
    assert "81,838,118" not in answer


def test_a_horizon_inside_the_issue_is_answered(lab: Path):
    answer = official.forecast_answer(lab, "Revenues",
                                      "Forecast Revenues 5 days ahead")
    assert "81,838,118" in answer
    assert "can't give you an official" not in answer.lower()


# ───────────────────── target resolution ─────────────────────

@pytest.mark.parametrize("question,expected", [
    ("Why is State budget balance not published?", "State budget balance"),
    ("what about the budget balance", "State budget balance"),
    ("show me revenues", "Revenues"),
    ("revenue outlook", "Revenues"),
    ("expenditure please", "Expenditure"),
    ("how is spending doing", "Expenditure"),
])
def test_target_resolution_prefers_the_longest_alias(question, expected):
    assert official.resolve_target(question) == expected


def test_state_budget_balance_is_not_shortened_to_balance():
    """`intents.parse_intent_rules` keeps the LAST match, which gets this wrong."""
    assert official.resolve_target("state budget balance") == "State budget balance"


def test_an_unnamed_target_resolves_to_none_rather_than_a_guess():
    assert official.resolve_target("what is the forecast?") is None
    assert official.resolve_target("") is None


@pytest.mark.parametrize("question,days", [
    ("Forecast Revenues 30 days ahead", 30),
    ("next 6 weeks", 30),
    ("3 months out", 63),
    ("forecast 5 business days", 5),
])
def test_requested_horizon_converts_at_business_day_rates(question, days):
    got = official.requested_horizon(question)
    assert got is not None and got[0] == days


def test_no_horizon_phrase_returns_none():
    assert official.requested_horizon("what is the forecast for Revenues") is None


# ───────────────────── approval and absence ─────────────────────

def test_nothing_renders_as_approved_while_approved_by_is_null(lab: Path):
    """Contract §7. `approved_by` is null on every real recipe."""
    for target in ("Revenues", "Expenditure", "State budget balance"):
        recipe = REG.champion_for(lab, target)
        assert recipe.approved_by is None
        assert not recipe.is_approved
    for answer in (official.forecast_answer(lab, "Revenues", "forecast Revenues"),
                   official.best_model_answer(lab, "Revenues")):
        assert "Nobody has approved this model" in answer
        # The words that would overclaim, none of which may appear.
        for overclaim in ("signed off", "production-ready", "validated and approved",
                          "approved for use"):
            assert overclaim not in answer.lower()
        assert "candidate" in answer.lower()


def test_a_dirty_tree_is_disclosed_not_hidden(lab: Path):
    """The older issue was built from a dirty tree; that must be sayable."""
    dirty = PUB.Issue(issue_date="x", path=Path("/x"),
                      git_sha="deadbeef" * 5, git_dirty=True)
    assert "uncommitted changes" in PUB.provenance_sentence(dirty)
    clean = PUB.Issue(issue_date="y", path=Path("/y"),
                      git_sha="4a0ff5087c69", git_dirty=False)
    assert "clean working tree" in PUB.provenance_sentence(clean)
    unknown = PUB.Issue(issue_date="z", path=Path("/z"), git_sha="abc123")
    assert "does not record whether" in PUB.provenance_sentence(unknown)


def test_a_missing_registry_yields_a_reason_never_a_guess(tmp_path: Path):
    recipe = REG.champion_for(tmp_path, "Revenues")
    assert not recipe.is_known
    assert "was not found" in recipe.note
    answer = official.best_model_answer(tmp_path, "Revenues")
    assert "I can't name a best model" in answer


def test_a_missing_published_root_yields_a_reason_never_a_backtest(tmp_path: Path):
    _json(tmp_path / "registry" / "recipes.json", {
        "recipes": [_recipe("Revenues", "rev-v1", "LightGBM_L1",
                            "publishable", 0.75, 1.3, [])]})
    answer = official.forecast_answer(tmp_path, "Revenues", "forecast Revenues")
    assert "can't read the lab's latest published forecast" in answer
    assert "won't substitute a backtest figure" in answer


def test_an_unrecognised_verdict_is_unknown_not_coerced(tmp_path: Path):
    raw = _recipe("Revenues", "rev-v1", "LightGBM_L1", "publishable", 0.75, 1.3, [])
    raw["publication"]["verdict"] = "probably fine"
    _json(tmp_path / "registry" / "recipes.json", {"recipes": [raw]})
    recipe = REG.champion_for(tmp_path, "Revenues")
    assert recipe.verdict == ""
    assert not recipe.verdict_known
    assert not recipe.is_publishable and not recipe.is_withheld
    answer = official.why_not_published_answer(tmp_path, "Revenues")
    assert "no publication verdict" in answer
    assert "Unknown is not the same as failed" in answer


def test_crossed_quantiles_are_dropped_with_a_defect_not_published(tmp_path: Path):
    issue = tmp_path / "forecasts" / "published" / "2026-08-16"
    _json(issue / "manifest.json", {
        "issue_date": "2026-08-16", "targets": ["Revenues"],
        "horizons": [1], "target_dates": ["2025-08-07"]})
    _write(issue / "forecast.csv",
           "target,horizon,origin_date,origin_value,target_date,p10,p50,p90\n"
           "Revenues,1,2025-08-06,100,2025-08-07,900,500,100\n")
    read = PUB.latest_issue(tmp_path)
    assert read.rows_for("Revenues") == ()
    assert any("crossed" in d for d in read.defects)


def test_a_forward_issue_carrying_y_true_is_flagged(tmp_path: Path):
    issue = tmp_path / "forecasts" / "published" / "2026-08-16"
    _json(issue / "manifest.json", {
        "issue_date": "2026-08-16", "targets": ["Revenues"],
        "horizons": [1], "target_dates": ["2025-08-07"]})
    _write(issue / "forecast.csv",
           "target,horizon,origin_date,origin_value,target_date,p10,p50,p90,y_true\n"
           "Revenues,1,2025-08-06,100,2025-08-07,100,200,300,250\n")
    read = PUB.latest_issue(tmp_path)
    assert any("y_true" in d for d in read.defects)


# ───────────────────── against the real lab ─────────────────────

@pytest.fixture
def real_lab() -> Path:
    candidate = Path(__file__).resolve().parent.parent.parent / "AI4CM"
    if not (candidate / "registry" / "recipes.json").exists():
        pytest.skip("the AI4CM lab is not checked out beside this repo")
    return candidate


def test_the_real_lab_answers_the_demo_script_correctly(real_lab: Path):
    """The fixtures prove the rules; this proves they survive the real artifacts."""
    rev = official.forecast_answer(real_lab, "Revenues",
                                   "What is the latest forecast for Revenues?")
    assert "2026-08-16" in rev
    assert "Nobody has approved" in rev

    exp = official.why_not_published_answer(real_lab, "Expenditure")
    sbb = official.why_not_published_answer(real_lab, "State budget balance")
    assert exp != sbb
    assert "failed 2 quality checks" in exp
    assert "failed 1 quality check" in sbb
    assert "1.57832" in sbb

    thirty = official.forecast_answer(real_lab, "Revenues",
                                      "Forecast Revenues 30 days ahead")
    assert "5 business days" in thirty
