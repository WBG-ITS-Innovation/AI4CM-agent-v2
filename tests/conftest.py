# tests/conftest.py — three artifact sets, on purpose.
#
# Testing a consumer only against a well-formed artifact tests the happy path
# of a contract whose entire subject is what happens when things are missing
# or contradictory. So there are three fixtures:
#
#   clean        — contract-compliant. Everything present, nothing contradicts.
#   incomplete   — fields legitimately absent: no run_id, no schema_version,
#                  gate_passed null, skill_pct "n/a (not produced)", no
#                  coverage, a family listed with no folder on disk.
#   inconsistent — fields that contradict each other: a gate that passed with
#                  reasons attached, a pass with run_status FAILED_QUALITY,
#                  overall counters that disagree with the families they
#                  describe, a prediction whose origin is after its target.
#
# The rule under test is the contract's §0: a missing or "n/a" value is
# UNKNOWN — never zero, never a pass, never a failure.
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(path: Path, obj) -> None:
    _write(path, json.dumps(obj, indent=2))


# Valid rows: origin strictly before target, as the contract requires.
_PREDS_OK = (
    "date,target_date,origin_date,origin_value,target,horizon,model,y_true,y_pred,y_lo,y_hi\n"
    "2025-01-06,2025-01-06,2025-01-01,100.0,Revenues,5,ETS,120.0,118.0,,\n"
    "2025-01-07,2025-01-07,2025-01-02,105.0,Revenues,5,ETS,125.0,121.0,,\n"
    "2025-01-08,2025-01-08,2025-01-03,110.0,Revenues,5,ETS,130.0,127.0,,\n"
)


# ─────────────────────────── clean ───────────────────────────

@pytest.fixture
def clean_run(tmp_path: Path) -> Path:
    """A run that honours the contract in every respect."""
    run = tmp_path / "backend" / "forecast_runs" / "2026-08-10"

    _json(run / "SUMMARY.json", {
        "run_id": "run-2026-08-10-abc123",
        "schema_version": 2,
        "run_date": "2026-08-10",
        "target": "Revenues",
        "cadence": "Daily",
        "horizon": "5",                      # a string, per the contract
        "mode": "backtest",
        "freshness": {"line": "Latest data date: 2026-08-09 (1 day before run)",
                      "stale": False, "backtest": True},
        "families": [
            {"name": "A_STAT", "ok": True,
             "models": "ETS, Persistence (baseline)",
             "best_model": "ETS (MAE 36,314,513)",
             "best_model_display": "ETS (MAE 36,314,513)",
             "skill_pct": "27.51%", "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": True,
             "gate_reasons": [], "leakage_flag": False, "shift_flag": False},
            {"name": "B_ML", "ok": True,
             "models": "⚡ Persistence (baseline), RandomForest",
             "best_model": "RandomForest (MAE 31,685,490)",
             "best_model_display": ("WITHHELD — no signal beyond shuffled "
                                    "targets (ratio 1.13), not usable; "
                                    "RandomForest (MAE 31,685,490) for "
                                    "diagnosis only"),
             "skill_pct": "36.75%", "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": False,
             "gate_reasons": ["no signal beyond shuffled targets (ratio 1.13)"],
             "leakage_flag": False, "shift_flag": False},
            {"name": "E_QUANTILE", "ok": True,
             "models": "ResidualRF, GBQuantile",
             "best_model": "GBQuantile (MAE 27,905,122)",
             "best_model_display": "GBQuantile (MAE 27,905,122)",
             "skill_pct": "48.45%", "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": True,
             "gate_reasons": [], "leakage_flag": False, "shift_flag": False},
        ],
        "overall": {"families_requested": 3, "families_ok": 3,
                    "families_gate_passed": 2, "leakage_flags": 0,
                    "shift_flags": 0, "quality_gate_failures": 1},
    })

    _write(run / "a_stat" / "leaderboard.csv",
           "target,horizon,cadence,model,MAE,RMSE,rank\n"
           "Revenues,5,Daily,ETS,36314513.01,50714566.85,0\n"
           "Revenues,5,Daily,Persistence (baseline),50098486.77,80112233.44,1\n")
    _write(run / "a_stat" / "metrics_long.csv",
           "target,horizon,cadence,model,MAE,RMSE\n"
           "Revenues,5,Daily,ETS,36314513.01,50714566.85\n")
    _write(run / "a_stat" / "predictions_long.csv", _PREDS_OK)

    _write(run / "b_ml" / "leaderboard.csv",
           "target,horizon,model,MAE,rank\n"
           "Revenues,5,⚡ Persistence (baseline),50098486.77,0\n"
           "Revenues,5,RandomForest,31685489.57,1\n")
    _write(run / "b_ml" / "metrics_long.csv",
           "target,horizon,model,MAE,RMSE,PI_coverage@90\n"
           "Revenues,5,RandomForest,31685489.57,52000000.0,0.878\n")
    _write(run / "b_ml" / "predictions_long.csv",
           _PREDS_OK.replace("ETS", "RandomForest"))

    _write(run / "e_quantile" / "leaderboard.csv",
           "model,pinball_q10,pinball_q50,pinball_q90,coverage_p10_p90,MAE\n"
           "ResidualRF,5979107.44,13657399.30,10065808.80,0.6975,27314798.60\n"
           "GBQuantile,6875481.64,13952561.02,10211384.10,0.7804,27905122.04\n")
    _write(run / "e_quantile" / "metrics_long.csv",
           "model,fold,metric,quantile,value\n"
           "GBQuantile,1,pinball,0.1,8166516.18\n"
           "GBQuantile,1,coverage_p10_p90,0.8,0.80\n"
           "GBQuantile,2,coverage_p10_p90,0.8,0.76\n")
    _write(run / "e_quantile" / "predictions_long.csv",
           _PREDS_OK.replace("ETS", "GBQuantile"))
    # Contract §5: the level the interval was fitted for is data, not a guess.
    _json(run / "e_quantile" / "run.json",
          {"target": "Revenues", "horizon": 5, "quantiles": [0.1, 0.5, 0.9],
           "coverage_nominal": 0.8, "coverage_key": "coverage_p10_p90",
           "coverage_lower_quantile": 0.1, "coverage_upper_quantile": 0.9})

    _write(run / "SUMMARY.txt", "AI4CM daily run 2026-08-10\n")
    return run


# ─────────────────────────── incomplete ───────────────────────────

@pytest.fixture
def incomplete_run(tmp_path: Path) -> Path:
    """A run whose fields are legitimately absent — nothing contradicts.

    Every absence here is one the contract explicitly anticipates, so the
    Agent must degrade to "not recorded" and never to zero, pass or failure.
    """
    run = tmp_path / "backend" / "forecast_runs" / "2026-08-09"

    _json(run / "SUMMARY.json", {
        # no run_id, no schema_version, no data_file, no freshness
        "run_date": "2026-08-09",
        "target": "Revenues",
        "cadence": "Daily",
        "horizon": "5",
        "mode": "backtest",
        "families": [
            # gate never returned a verdict; no skill figure was produced.
            {"name": "A_STAT", "ok": True,
             "models": "ETS, Persistence (baseline)",
             "best_model": "ETS (MAE 36,314,513)",
             "best_model_display": "ETS (MAE 36,314,513)",
             "skill_pct": "n/a (not produced)",
             "integrity_verified": True,
             "gate_passed": None, "gate_reasons": []},
             # ^ no run_status, no leakage_flag, no shift_flag
            # passed cleanly, but publishes no skill figure at all.
            {"name": "B_ML", "ok": True,
             "models": "RandomForest",
             "best_model": "RandomForest",       # no embedded metric
             "skill_pct": None,
             "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": True,
             "gate_reasons": [], "leakage_flag": False, "shift_flag": False},
            # listed here, but there is no e_quantile/ folder on disk.
            {"name": "E_QUANTILE", "ok": False,
             "models": "", "best_model": "n/a (not produced)",
             "skill_pct": "n/a (not produced)",
             "run_status": "SUCCESS", "integrity_verified": False,
             "gate_passed": None, "gate_reasons": []},
        ],
        # no `overall` block at all
    })

    # RMSE and rank entirely absent; an all-null MAE_skill column present.
    _write(run / "a_stat" / "leaderboard.csv",
           "target,horizon,cadence,model,MAE,RMSE\n"
           "Revenues,5,Daily,ETS,36314513.01,\n"
           "Revenues,5,Daily,Persistence (baseline),50098486.77,\n")
    _write(run / "a_stat" / "metrics_long.csv",
           "target,horizon,cadence,model,MAE,RMSE,MAE_skill_vs_Ops\n"
           "Revenues,5,Daily,ETS,36314513.01,50714566.85,\n")
    _write(run / "a_stat" / "predictions_long.csv", _PREDS_OK)

    # A point-model family: no coverage columns anywhere. Legitimate.
    _write(run / "b_ml" / "leaderboard.csv",
           "target,horizon,model,MAE,rank\n"
           "Revenues,5,RandomForest,31685489.57,1\n")
    _write(run / "b_ml" / "predictions_long.csv",
           _PREDS_OK.replace("ETS", "RandomForest"))

    _write(run / "SUMMARY.txt", "AI4CM daily run 2026-08-09\n")
    return run


# ─────────────────────────── inconsistent ───────────────────────────

@pytest.fixture
def inconsistent_run(tmp_path: Path) -> Path:
    """A run whose fields contradict each other.

    Nothing here is merely missing — each defect is two recorded facts that
    cannot both be true. The Agent must surface the contradiction and refuse
    to present the family as clean, rather than picking whichever field
    happens to be read first.
    """
    run = tmp_path / "backend" / "forecast_runs" / "2026-08-08"

    _json(run / "SUMMARY.json", {
        "run_id": "run-2026-08-08-def456",
        "schema_version": 2,
        "run_date": "2026-08-08",
        "target": "Revenues",
        "cadence": "Daily",
        "horizon": "five",                  # not a number at all
        "mode": "backtest",
        "freshness": {"line": "Latest data date: 2026-08-07",
                      "stale": False, "backtest": True},
        "families": [
            # gate says passed, run_status says the run failed quality.
            {"name": "A_STAT", "ok": True, "models": "ETS",
             "best_model": "ETS (MAE 36,314,513)",
             "best_model_display": "ETS (MAE 36,314,513)",
             "skill_pct": "27.51%", "run_status": "FAILED_QUALITY",
             "integrity_verified": True, "gate_passed": True,
             "gate_reasons": [], "leakage_flag": False, "shift_flag": False},
            # gate says passed, yet reasons were recorded against it.
            {"name": "B_ML", "ok": True, "models": "RandomForest",
             "best_model": "RandomForest (MAE 31,685,490)",
             "best_model_display": "RandomForest (MAE 31,685,490)",
             "skill_pct": "36.75%", "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": True,
             "gate_reasons": ["leakage detected in fold 2"],
             "leakage_flag": True, "shift_flag": False},
            # withheld, but the artifact records no reason why.
            {"name": "C_DL", "ok": True, "models": "MLP",
             "best_model": "MLP (MAE 38,793,513)",
             "best_model_display": "WITHHELD — not usable",
             "skill_pct": "10.84%", "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": False,
             "gate_reasons": [], "leakage_flag": False, "shift_flag": True},
            # a duplicate family name.
            {"name": "C_DL", "ok": True, "models": "LSTM",
             "best_model": "LSTM (MAE 45,831,668)",
             "best_model_display": "LSTM (MAE 45,831,668)",
             "skill_pct": "2.10%", "run_status": "SUCCESS",
             "integrity_verified": True, "gate_passed": True,
             "gate_reasons": [], "leakage_flag": False, "shift_flag": False},
        ],
        # every counter disagrees with the families above.
        "overall": {"families_requested": 9, "families_ok": 9,
                    "families_gate_passed": 4, "leakage_flags": 0,
                    "shift_flags": 0, "quality_gate_failures": 0},
    })

    # Partially identified: identity columns on one row, blank on the other.
    _write(run / "a_stat" / "leaderboard.csv",
           "target,horizon,cadence,model,MAE,RMSE,rank\n"
           ",,,ETS,36314513.01,,0\n"
           "Revenues,5.0,Daily,Persistence (baseline),50098486.77,,1\n")
    # origin_date on or after target_date — a prediction of a visible date.
    _write(run / "a_stat" / "predictions_long.csv",
           "date,target_date,origin_date,origin_value,target,horizon,model,y_true,y_pred\n"
           "2025-01-06,2025-01-06,2025-01-01,100.0,Revenues,5,ETS,120.0,118.0\n"
           "2025-01-07,2025-01-07,2025-01-07,105.0,Revenues,5,ETS,125.0,121.0\n"
           "2025-01-08,2025-01-08,2025-01-09,110.0,Revenues,5,ETS,130.0,127.0\n")

    _write(run / "b_ml" / "leaderboard.csv",
           "target,horizon,model,MAE,rank\n"
           "Revenues,5,RandomForest,31685489.57,1\n")
    _write(run / "b_ml" / "predictions_long.csv",
           _PREDS_OK.replace("ETS", "RandomForest"))

    _write(run / "c_dl" / "leaderboard.csv",
           "target,horizon,model,MAE,rank\n"
           "Revenues,5,MLP,38793512.64,1\n")
    # Coverage outside [0, 1], and no coverage_nominal to explain the level.
    _write(run / "c_dl" / "metrics_long.csv",
           "model,fold,metric,quantile,value\n"
           "MLP,1,coverage_p10_p90,,1.7\n")
    _write(run / "c_dl" / "predictions_long.csv",
           _PREDS_OK.replace("ETS", "MLP"))

    _write(run / "SUMMARY.txt", "AI4CM daily run 2026-08-08\n")
    return run


# ─────────────────────────── framed ───────────────────────────

#: The sentence the Lab's `client_framing()` derived at Lab commit e8c26a2, which
#: is the wording the committed 2026-08-04 run carries. The Lab's live registry
#: has moved on since: it now describes a 44-model shelf and adds a second
#: sentence about how many entries have a recorded result. That is deliberate,
#: and it is why this value is NOT refreshed to match. It is a FIXTURE VALUE
#: standing in for an artifact, and artifacts on disk keep the wording they were
#: written with. `test_a_two_sentence_framing_renders_whole` covers the newer
#: shape. Nothing in agent/ or app.py may contain either sentence —
#: tests/test_model_framing.py enforces that.
LAB_DERIVED_FRAMING = (
    "13 machine-learning models, 5 deep-learning models and 4 statistical "
    "models compete on each target; prediction intervals come from 3 quantile "
    "methods; 3 further entries are reference baselines, not competitors.")


@pytest.fixture
def framed_run(clean_run: Path) -> Path:
    """A run that records the composition the Lab derived from its registry.

    No committed artifact carries this field yet, so without a fixture the
    "render what the artifact says" path would never be exercised and the only
    tested behaviour would be the absent one.
    """
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["model_composition"] = {"framing": LAB_DERIVED_FRAMING,
                                 "champion_pool_size": 13}
    _json(path, blob)
    return clean_run


@pytest.fixture
def flat_framed_run(clean_run: Path) -> Path:
    """The same field written flat, which is the other shape the Lab might pick."""
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["client_framing"] = LAB_DERIVED_FRAMING
    _json(path, blob)
    return clean_run


# ─────────────────────────── the older shape ───────────────────────────

@pytest.fixture
def legacy_run(clean_run: Path) -> Path:
    """An artifact in the shape every committed run had before Lab `e8c26a2`.

    `run_id`, `schema_version`, `data_file` and the model composition were all
    absent then, and the Agent's whole absence semantics were written against
    that. The Lab now writes all four, so `real_run` no longer exercises the
    absent path — and dropping it would mean the rules that survive contact
    with reality are only the *present* ones.

    Deliberately synthetic rather than a snapshot of the old artifact: it is
    derived from `clean_run`, so the rest of the run stays in one place, and
    it keeps working on a machine with no lab checked out at all. Older runs
    do still exist on disk in the wild — `backend/forecast_runs/2026-07-29`
    and `2026-07-30` predate the change — and the app must keep reading them.
    """
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    for key in ("run_id", "schema_version", "data_file", "client_framing",
                "model_composition"):
        blob.pop(key, None)
    _json(path, blob)
    return clean_run


@pytest.fixture
def real_run() -> Path:
    """The lab's actual committed run, if this machine has the lab checked out.

    Fixtures prove the rules; this proves the rules survive contact with the
    artifact the contract was written about.
    """
    candidate = (Path(__file__).resolve().parent.parent.parent
                 / "AI4CM" / "backend" / "forecast_runs" / "2026-08-04")
    if not (candidate / "SUMMARY.json").exists():
        pytest.skip("the AI4CM lab is not checked out beside this repo")
    return candidate
