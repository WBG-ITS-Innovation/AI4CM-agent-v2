# tests/test_real_artifact.py — the rules, against the artifact they describe.
#
# The fixtures prove the semantics. This file proves they survive contact
# with the lab's actual committed run — the one the contract's §8 says has
# 3 errors and 10 warnings. It skips cleanly when the lab is not checked out
# beside this repo.
from __future__ import annotations

import pytest

from agent import lab_bridge as LB
from agent import plain
from agent.contract import Gate, MetricsShape, RunStatus, read_summary


def test_the_real_run_loads(real_run):
    run = LB.load_run(real_run)
    assert run is not None
    assert run.run_date == "2026-08-04"


def test_the_real_runs_string_fields_parse(real_run):
    """horizon "5" and skill_pct "27.51%" — the two the brief names."""
    view = read_summary(real_run)
    assert view.horizon.value == 5
    assert view.family("A_STAT").skill_pct.value == pytest.approx(27.51)
    assert view.family("E_QUANTILE").skill_pct.value == pytest.approx(48.45)


def test_the_real_runs_best_model_prose_splits(real_run):
    """The prose splits into name, metric and number — not which number.

    This test is about PARSING `"RandomForest (MAE <amount>)"` into its three
    parts, so it asserts the parse, not the amount. The amount is a real
    measured figure for a named treasury, and this repository is public: under
    the Lab's own exposure rules absolute lari figures are redacted while the
    reasoning stays. Pinning the value here would have re-published it, and
    would not have tested anything the shape assertions below miss.
    """
    view = read_summary(real_run)
    best = view.family("B_ML").best_model
    assert best.name.value == "RandomForest"
    assert best.metric_name.value == "MAE"
    assert best.metric_value.is_known
    assert isinstance(best.metric_value.value, float)
    assert best.metric_value.value > 0


def test_the_real_champion_is_e_quantile_not_b_ml(real_run):
    """B_ML's 36.75% beats A_STAT's 27.51%, but B_ML is withheld."""
    view = read_summary(real_run)
    assert view.family("B_ML").gate is Gate.WITHHELD
    assert view.champion().name == "E_QUANTILE"


def test_the_real_runs_withheld_families_keep_their_reasons(real_run):
    view = read_summary(real_run)
    assert view.family("B_ML").gate_reasons == [
        "no signal beyond shuffled targets (ratio 1.13)"]
    assert view.family("C_DL").gate_reasons == [
        "forecast is persistence-like (shift diagnostic)"]


def test_the_real_run_records_run_id_and_schema_version(real_run):
    """Contract §1's known defect, now fixed on the Lab side.

    These two were absent on every committed artifact, and the Agent reported
    that as a defect. The Lab writes them, so the assertion flips: they are
    read, and — the half that would otherwise go unnoticed — the Agent stops
    reporting a departure it no longer has grounds for. A reader that keeps
    filing the same defect after the artifact is fixed is as wrong as one
    that never filed it.
    """
    view = read_summary(real_run)
    assert view.run_id.is_known
    assert view.run_id.value == view.raw["run_id"]
    assert view.schema_version.is_known
    assert view.schema_version.value == view.raw["schema_version"]
    assert not any("run_id" in d for d in view.defects), view.defects
    assert not any("schema_version" in d for d in view.defects), view.defects


def test_the_real_run_records_its_input_file_and_it_reaches_the_narrative(real_run):
    """Live bug, twice over: this used to render as 'Input data: `None`', and
    then — once absence was handled — as 'not recorded'. The artifact now
    names the file, so the name has to survive the whole way to the prose."""
    view = read_summary(real_run)
    assert view.data_file.is_known
    assert view.data_file.value == view.raw["data_file"]
    assert str(view.data_file.value).endswith(".csv")
    text = plain.describe_run(view, LB.FAMILY_LABELS)
    assert str(view.data_file.value) in text
    assert "None" not in text


def test_an_artifact_from_before_the_lab_wrote_those_fields_still_reads(legacy_run):
    """The absent path, kept alive on a synthetic older artifact.

    Every assertion here was true of the real run until the Lab started
    writing these four fields. Runs in that shape still exist on disk, and
    the semantics they exercise — absent is UNKNOWN, never zero, never a
    default, and reported — are the ones the whole contract is about. Pinning
    them to a fixture rather than to a checkout means the next Lab change
    cannot quietly retire them.
    """
    view = read_summary(legacy_run)

    assert view.run_id.is_unknown
    assert view.schema_version.is_unknown
    assert any("run_id" in d for d in view.defects)
    assert any("schema_version" in d for d in view.defects)

    assert view.data_file.is_unknown
    assert view.client_framing.is_unknown
    assert "not recorded" in plain.say_model_framing(view)

    text = plain.describe_run(view, LB.FAMILY_LABELS)
    assert "None" not in text
    assert LB.available_targets(LB.load_run(legacy_run)) == []


def test_the_real_a_stat_leaderboard_is_partially_identified(real_run):
    """Contract §8: the three errors on this run are all this one defect."""
    view = read_summary(real_run)
    from agent.contract import read_leaderboard
    lb = read_leaderboard(real_run / "a_stat" / "leaderboard.csv", "a_stat",
                          view.target, view.horizon)
    assert set(lb.partially_identified) == {"target", "horizon", "cadence"}
    assert "RMSE" in lb.all_null_columns


def test_the_real_b_ml_leaderboard_has_decoration_in_its_join_key(real_run):
    from agent.contract import read_leaderboard
    lb = read_leaderboard(real_run / "b_ml" / "leaderboard.csv", "b_ml")
    assert lb.baseline_models == ["Persistence (baseline)"]
    assert any("decoration in the join key" in n for n in lb.notes)


def test_the_real_metrics_files_span_both_shapes(real_run):
    from agent.contract import read_metrics_long
    assert read_metrics_long(real_run / "a_stat" / "metrics_long.csv",
                             "a_stat").shape is MetricsShape.WIDE
    assert read_metrics_long(real_run / "b_ml" / "metrics_long.csv",
                             "b_ml").shape is MetricsShape.WIDE
    assert read_metrics_long(real_run / "e_quantile" / "metrics_long.csv",
                             "e_quantile").shape is MetricsShape.LONG


def test_the_real_b_ml_ops_skill_column_is_all_null(real_run):
    """Contract §4: all-null, and indistinguishable from a failure."""
    from agent.contract import read_metrics_long
    m = read_metrics_long(real_run / "b_ml" / "metrics_long.csv", "b_ml")
    assert "MAE_skill_vs_Ops" in m.all_null_columns
    assert m.metric("MAE_skill_vs_Ops").is_unknown


def test_the_real_e_quantile_coverage_has_no_recorded_level(real_run):
    """82 coverage rows with a blank `quantile`, and no coverage_nominal.

    So the Agent reports the measurement and refuses to name a level.
    """
    run = LB.load_run(real_run)
    cov = run.family_coverage("E_QUANTILE")
    assert cov.is_reported
    assert not cov.level_is_recorded
    text = plain.say_coverage(cov)
    assert "not recorded" in text
    assert "cannot say what it was aiming at" in text


def test_the_real_run_surfaces_its_defects(real_run):
    run = LB.load_run(real_run)
    assert run.all_defects, "the committed run has known contract departures"


def test_the_real_narrative_never_invents(real_run):
    text = plain.describe_run(read_summary(real_run), LB.FAMILY_LABELS)
    assert "requires at least" not in text
    assert "None" not in text
    for fam in ("A_STAT", "B_ML", "E_QUANTILE", "C_DL"):
        assert fam in text
