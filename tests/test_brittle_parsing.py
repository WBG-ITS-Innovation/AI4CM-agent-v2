# tests/test_brittle_parsing.py — Part 2.
#
# The four fragilities the contract exposed:
#   1. numbers published as strings — horizon "5", skill_pct "27.51%"
#   2. `best_model` as one prose field combining a name and a formatted number
#   3. `metrics_long.csv` carrying two incompatible shapes under one filename
#   4. three leaderboards with three schemas, only `e_quantile` lacking
#      `target` and `horizon`
#
# The standard throughout is: parse defensively, and fail visibly rather
# than guessing.
from __future__ import annotations

import pytest

from agent import lab_bridge as LB
from agent.contract import (
    MetricsShape, Presence, is_baseline_model, parse_best_model, parse_int,
    parse_number, parse_percent, read_leaderboard, read_metrics_long,
    read_predictions, read_summary, strip_decoration,
)


# ─────────────────────── 1. numbers published as strings ───────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("5", 5.0),
    (5, 5.0),
    (5.0, 5.0),
    ("27.51%", 27.51),
    ("1,234,567", 1234567.0),
    ("36,314,513.01", 36314513.01),
    ("-3.5", -3.5),
    ("  12  ", 12.0),
])
def test_numbers_parse_from_their_published_forms(raw, expected):
    v = parse_number(raw)
    assert v.is_known
    assert v.value == pytest.approx(expected)


@pytest.mark.parametrize("raw,presence", [
    (None, Presence.ABSENT),
    ("n/a (not produced)", Presence.NOT_PRODUCED),
    ("n/a", Presence.NOT_PRODUCED),
    ("N/A", Presence.NOT_PRODUCED),
    ("", Presence.NOT_PRODUCED),
    ("not produced", Presence.NOT_PRODUCED),
    ("five", Presence.UNPARSEABLE),
    ("--", Presence.NOT_PRODUCED),
    (float("nan"), Presence.UNPARSEABLE),
    (True, Presence.UNPARSEABLE),
])
def test_non_numbers_are_unknown_with_a_reason(raw, presence):
    v = parse_number(raw)
    assert v.is_unknown
    assert v.presence is presence
    assert v.value is None


def test_horizon_string_five_parses_to_int(clean_run):
    view = read_summary(clean_run)
    assert view.horizon.is_known
    assert view.horizon.value == 5
    assert isinstance(view.horizon.value, int)


def test_unparseable_horizon_fails_visibly(inconsistent_run):
    """`horizon: "five"` must not become 0, and must be reported."""
    view = read_summary(inconsistent_run)
    assert view.horizon.is_unknown
    assert view.horizon.presence is Presence.UNPARSEABLE
    assert any("horizon" in d for d in view.defects)


def test_fractional_horizon_is_rejected():
    v = parse_int("5.5")
    assert v.is_unknown
    assert "whole number" in v.note


def test_skill_percent_parses_to_percentage_points(clean_run):
    view = read_summary(clean_run)
    assert view.family("A_STAT").skill_pct.value == pytest.approx(27.51)


def test_skill_sentinel_carries_its_own_explanation():
    v = parse_percent("n/a (not produced)")
    assert v.is_unknown
    assert "does not record which cause" in v.note


# ─────────────────────── 2. best_model as prose ───────────────────────

def test_best_model_splits_into_name_metric_and_value():
    b = parse_best_model("RandomForest (MAE 31,685,490)")
    assert b.name.value == "RandomForest"
    assert b.metric_name.value == "MAE"
    assert b.metric_value.value == pytest.approx(31685489.6)


def test_best_model_strips_decoration_from_the_name():
    """B_ML labels its baseline `⚡ Persistence (baseline)`. Contract §1."""
    b = parse_best_model("⚡ Persistence (baseline) (MAE 50,098,487)")
    assert "⚡" not in b.name.value
    assert b.name.value.startswith("Persistence")
    assert b.is_baseline


def test_best_model_display_withheld_prefix_is_understood():
    b = parse_best_model(
        "WITHHELD — no signal beyond shuffled targets (ratio 1.13), not "
        "usable; RandomForest (MAE 31,685,490) for diagnosis only")
    assert b.withheld_marker
    assert b.name.value == "RandomForest"
    assert b.metric_value.value == pytest.approx(31685489.6)


def test_best_model_without_a_metric_yields_a_name_and_an_unknown():
    """A bare name must not produce a fabricated error figure."""
    b = parse_best_model("RandomForest")
    assert b.name.value == "RandomForest"
    assert b.metric_value.is_unknown
    assert b.metric_name.is_unknown


def test_best_model_absent_is_all_unknown():
    b = parse_best_model(None)
    assert b.name.is_unknown and b.metric_value.is_unknown


def test_best_model_sentinel_is_not_produced():
    b = parse_best_model("n/a (not produced)")
    assert b.name.presence is Presence.NOT_PRODUCED


@pytest.mark.parametrize("name,expected", [
    ("Persistence (baseline)", True),
    ("⚡ Persistence (baseline)", True),
    ("Ops baseline", True),
    ("RandomForest", False),
    ("GBQuantile", False),
    ("ETS", False),
])
def test_baseline_detection(name, expected):
    assert is_baseline_model(name) is expected


def test_strip_decoration_leaves_plain_names_untouched():
    assert strip_decoration("RandomForest") == "RandomForest"


# ─────────────────────── 3. metrics_long: two shapes ───────────────────────

def test_long_shape_is_detected(clean_run):
    m = read_metrics_long(clean_run / "e_quantile" / "metrics_long.csv", "e_quantile")
    assert m.shape is MetricsShape.LONG


def test_wide_shape_is_detected(clean_run):
    """The filename says 'long' and two of three families are wide."""
    for fam in ("a_stat", "b_ml"):
        m = read_metrics_long(clean_run / fam / "metrics_long.csv", fam)
        assert m.shape is MetricsShape.WIDE


def test_metric_lookup_works_in_both_shapes(clean_run):
    wide = read_metrics_long(clean_run / "a_stat" / "metrics_long.csv", "a_stat")
    assert wide.metric("MAE").value == pytest.approx(36314513.01)

    long = read_metrics_long(clean_run / "e_quantile" / "metrics_long.csv",
                             "e_quantile")
    assert long.metric("pinball").value == pytest.approx(8166516.18)


def test_absent_metric_is_unknown_not_zero(clean_run):
    m = read_metrics_long(clean_run / "a_stat" / "metrics_long.csv", "a_stat")
    v = m.metric("PI_coverage@90")
    assert v.is_unknown
    assert v.value is None


def test_all_null_metric_column_is_not_produced_not_zero(incomplete_run):
    """`MAE_skill_vs_Ops` is all-null. An empty cell asserts nothing."""
    m = read_metrics_long(incomplete_run / "a_stat" / "metrics_long.csv", "a_stat")
    assert "MAE_skill_vs_Ops" in m.all_null_columns
    v = m.metric("MAE_skill_vs_Ops")
    assert v.is_unknown
    assert v.presence is Presence.NOT_PRODUCED
    assert "does not record why" in v.note


def test_metrics_defect_names_the_empty_columns(incomplete_run):
    m = read_metrics_long(incomplete_run / "a_stat" / "metrics_long.csv", "a_stat")
    assert any("entirely" in n and "MAE_skill_vs_Ops" in n for n in m.notes)


# ─────────────────────── 4. three leaderboard schemas ───────────────────────

def test_each_leaderboard_schema_is_identified(clean_run):
    view = read_summary(clean_run)
    for fam, expected in (("a_stat", "a_stat"), ("b_ml", "b_ml"),
                          ("e_quantile", "e_quantile")):
        lb = read_leaderboard(clean_run / fam / "leaderboard.csv", fam,
                              view.target, view.horizon)
        assert lb.is_readable
        assert lb.schema == expected


def test_e_quantile_target_comes_from_the_summary_and_says_so(clean_run):
    """It has no `target` column at all, so it cannot be keyed by target."""
    view = read_summary(clean_run)
    lb = read_leaderboard(clean_run / "e_quantile" / "leaderboard.csv",
                          "e_quantile", view.target, view.horizon)
    assert "target" not in lb.frame.columns
    assert lb.target.value == "Revenues"
    assert lb.target_source == "summary"
    assert any("does not identify its target" in n for n in lb.notes)


def test_other_leaderboards_identify_their_own_target(clean_run):
    view = read_summary(clean_run)
    lb = read_leaderboard(clean_run / "a_stat" / "leaderboard.csv", "a_stat",
                          view.target, view.horizon)
    assert lb.target_source == "file"


def test_target_is_unknown_when_neither_file_nor_summary_has_one(clean_run):
    lb = read_leaderboard(clean_run / "e_quantile" / "leaderboard.csv",
                          "e_quantile", None, None)
    assert lb.target.is_unknown
    assert lb.target_source == "unknown"


def test_partially_identified_leaderboard_is_reported(inconsistent_run):
    """Identity columns filled on the baseline row, blank on the winner."""
    view = read_summary(inconsistent_run)
    lb = read_leaderboard(inconsistent_run / "a_stat" / "leaderboard.csv",
                          "a_stat", view.target, view.horizon)
    assert set(lb.partially_identified) == {"target", "horizon", "cadence"}
    assert any("blank on others" in d for d in lb.defects)


def test_all_null_leaderboard_column_is_reported(inconsistent_run):
    lb = read_leaderboard(inconsistent_run / "a_stat" / "leaderboard.csv", "a_stat")
    assert "RMSE" in lb.all_null_columns
    assert any("not as zero" in n for n in lb.notes)


def test_decorated_join_key_is_reported_and_stripped(clean_run):
    lb = read_leaderboard(clean_run / "b_ml" / "leaderboard.csv", "b_ml")
    assert any("decoration in the join key" in n for n in lb.notes)
    assert "⚡ Persistence (baseline)" not in set(lb.frame["model_key"])


def test_baselines_are_identified_and_excluded_from_competitors(clean_run):
    lb = read_leaderboard(clean_run / "b_ml" / "leaderboard.csv", "b_ml")
    assert lb.baseline_models == ["Persistence (baseline)"]
    assert set(lb.competitors["model_key"]) == {"RandomForest"}


def test_leaderboard_without_model_column_is_rejected_not_guessed(tmp_path):
    path = tmp_path / "leaderboard.csv"
    path.write_text("target,horizon,MAE\nRevenues,5,1234\n", encoding="utf-8")
    lb = read_leaderboard(path, "a_stat")
    assert not lb.is_readable
    assert any("lacks required column" in f for f in lb.fatal)


def test_unknown_leaderboard_schema_is_reported(tmp_path):
    path = tmp_path / "leaderboard.csv"
    path.write_text("model,wibble,wobble\nETS,1,2\n", encoding="utf-8")
    lb = read_leaderboard(path, "a_stat")
    assert lb.schema == "unknown"
    assert any("matches no known leaderboard schema" in d for d in lb.defects)


def test_empty_leaderboard_is_rejected(tmp_path):
    path = tmp_path / "leaderboard.csv"
    path.write_text("model,MAE\n", encoding="utf-8")
    lb = read_leaderboard(path, "a_stat")
    assert not lb.is_readable


# ─────────────────────── predictions: the time-order rule ───────────────────────

def test_origin_on_or_after_target_is_dropped_and_reported(inconsistent_run):
    """The model would have been predicting a date it could already see."""
    frame, defects, notes = read_predictions(
        inconsistent_run / "a_stat" / "predictions_long.csv", "A_STAT")
    assert len(frame) == 1                      # 2 of 3 rows dropped
    assert any("origin_date >= target_date" in d for d in defects)


def test_valid_predictions_are_kept(clean_run):
    frame, defects, notes = read_predictions(
        clean_run / "a_stat" / "predictions_long.csv", "A_STAT")
    assert len(frame) == 3
    assert not any("origin_date >=" in d for d in defects)


def test_predictions_missing_required_columns_are_rejected(tmp_path):
    path = tmp_path / "predictions_long.csv"
    path.write_text("date,y_pred\n2025-01-01,1.0\n", encoding="utf-8")
    frame, defects, notes = read_predictions(path, "A_STAT")
    assert frame is None
    assert any("lacks required column" in d for d in defects)


def test_all_null_interval_columns_are_reported_as_ambiguous(clean_run):
    """`y_lo`/`y_hi` empty: no native PI, or PI failed — indistinguishable."""
    _, defects, notes = read_predictions(
        clean_run / "a_stat" / "predictions_long.csv", "A_STAT")
    assert any("y_lo" in n and "does not distinguish" in n for n in notes)


def test_baseline_missing_from_predictions_is_reported(clean_run):
    """The persistence row is in the leaderboard but not in predictions."""
    run = LB.load_run(clean_run)
    assert any("have no rows in predictions_long.csv" in n
               for n in run.read_notes)
