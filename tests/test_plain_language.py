# tests/test_plain_language.py — Part 3, and the model framing.
#
# The plain-language layer is where honesty is most easily lost: it is the
# layer whose job is to sound confident. These tests pin the three places
# the brief calls out —
#
#   * `skill_pct` is "n/a (not produced)"  → say the family produced no skill
#     figure and that the reason is not recorded. Do not invent one.
#   * a family is withheld                 → name the gate reason.
#   * coverage is unreported               → say so; do not imply a level.
#
# — plus the corrected framing of how many models there are.
from __future__ import annotations

import pytest

from agent import lab_bridge as LB
from agent import plain
from agent.contract import (
    CoverageView, Presence, known, read_coverage, read_leaderboard,
    read_metrics_long, read_summary, unknown,
)


# ─────────────────────── skill: no figure, no invented reason ───────────────────────

def test_na_skill_says_none_was_produced_and_why_is_unrecorded(incomplete_run):
    text = plain.say_skill(read_summary(incomplete_run).family("A_STAT"))
    assert "no skill figure" in text
    assert "produced none" in text
    assert "does not record why" in text


def test_na_skill_admits_the_marker_is_ambiguous(incomplete_run):
    """The contract says one marker covers several causes. Say that."""
    text = plain.say_skill(read_summary(incomplete_run).family("A_STAT"))
    assert "several possible causes" in text


def test_na_skill_never_invents_a_threshold(incomplete_run, inconsistent_run):
    """The old narrative asserted 'the lab requires at least 5%'.

    That number is nowhere in the contract or the artifacts. It must not
    reappear in any rendering of any family.
    """
    for run_dir in (incomplete_run, inconsistent_run):
        view = read_summary(run_dir)
        text = plain.describe_run(view, LB.FAMILY_LABELS)
        for invented in ("requires at least", "the lab requires",
                         "at least 5%", "barely beats", "minimum threshold"):
            assert invented not in text
        # A family with no skill figure must not acquire one in the prose.
        assert "n/a" not in text.lower() or "not produced" in text.lower()


def test_na_skill_is_never_rendered_as_a_percentage(incomplete_run):
    view = read_summary(incomplete_run)
    text = plain.describe_family(view.family("A_STAT"))
    assert "n/a (not produced)%" not in text
    assert "0%" not in text
    assert "0.00%" not in text


def test_known_skill_is_rendered_plainly(clean_run):
    text = plain.say_skill(read_summary(clean_run).family("A_STAT"))
    assert "27.51%" in text
    assert "tomorrow looks like today" in text


def test_unparseable_skill_quotes_what_was_published():
    from agent.contract import read_family
    fam = read_family({"name": "A_STAT", "gate_passed": True,
                       "gate_reasons": [], "run_status": "SUCCESS",
                       "skill_pct": "lots"})
    text = plain.say_skill(fam)
    assert "not a number" in text
    assert "lots" in text


# ─────────────────────── withheld: name the reason ───────────────────────

def test_withheld_family_names_its_gate_reason(clean_run):
    text = plain.say_gate(read_summary(clean_run).family("B_ML"))
    assert text.startswith("withheld:")
    assert "no signal beyond shuffled targets (ratio 1.13)" in text


def test_withheld_reason_survives_into_the_run_narrative(clean_run):
    text = plain.describe_run(read_summary(clean_run), LB.FAMILY_LABELS)
    assert "no signal beyond shuffled targets" in text


def test_withheld_family_is_labelled_diagnosis_only(clean_run):
    text = plain.describe_family(read_summary(clean_run).family("B_ML"))
    assert "diagnosis only" in text


def test_withheld_with_no_reason_says_so_instead_of_guessing(inconsistent_run):
    text = plain.say_gate(read_summary(inconsistent_run).family("C_DL"))
    assert "records no reason" in text
    assert "inconsistency" in text


def test_champion_narrative_lists_withheld_reasons(incomplete_run):
    """When there is no champion, say what happened to each family."""
    text = plain.describe_champion(read_summary(incomplete_run),
                                   LB.FAMILY_LABELS)
    assert "No model earned trust" in text


# ─────────────────────── coverage: never imply a level ───────────────────────

def test_unreported_coverage_says_so_and_is_not_zero():
    cov = CoverageView(family="a_stat")
    text = plain.say_coverage(cov)
    assert "not reported" in text
    assert "not a coverage of zero" in text
    assert "not a failed check" in text
    assert "0%" not in text


def test_point_model_family_reports_no_coverage(incomplete_run):
    """B_ML here has no coverage column anywhere. That is legitimate."""
    lb = read_leaderboard(incomplete_run / "b_ml" / "leaderboard.csv", "b_ml")
    cov = read_coverage("b_ml", leaderboard=lb, metrics=None)
    assert not cov.is_reported
    assert "not reported" in plain.say_coverage(cov)


def test_coverage_with_a_recorded_level_states_both(clean_run):
    lb = read_leaderboard(clean_run / "e_quantile" / "leaderboard.csv", "e_quantile")
    m = read_metrics_long(clean_run / "e_quantile" / "metrics_long.csv", "e_quantile")
    cov = read_coverage("e_quantile", leaderboard=lb, metrics=m,
                        extras={"coverage_nominal": 0.8})
    assert cov.is_reported and cov.level_is_recorded
    text = plain.say_coverage(cov)
    assert "73.9%" in text or "%" in text
    assert "fitted level of 80%" in text


def test_coverage_without_a_level_refuses_to_supply_one(clean_run):
    """Contract §5: do not infer the level from the key name."""
    lb = read_leaderboard(clean_run / "e_quantile" / "leaderboard.csv", "e_quantile")
    cov = read_coverage("e_quantile", leaderboard=lb, metrics=None)
    assert cov.is_reported
    assert not cov.level_is_recorded
    text = plain.say_coverage(cov)
    assert "not recorded" in text
    assert "cannot say what it was aiming at" in text
    assert "80%" not in text          # the key name must not leak in as a level
    assert any("coverage_nominal" in d for d in cov.defects)


def test_coverage_outside_the_valid_range_is_rejected(inconsistent_run):
    m = read_metrics_long(inconsistent_run / "c_dl" / "metrics_long.csv", "c_dl")
    cov = read_coverage("c_dl", leaderboard=None, metrics=m)
    assert not cov.is_reported
    assert any("outside [0, 1]" in d for d in cov.defects)


def test_coverage_nominal_is_read_from_the_family_json(clean_run):
    run = LB.load_run(clean_run)
    cov = run.family_coverage("E_QUANTILE")
    assert cov.level_is_recorded
    assert cov.nominal.value == pytest.approx(0.8)


def test_unavailable_reason_is_quoted_when_present():
    cov = read_coverage("e_quantile", extras={
        "coverage_unavailable_reason": "only one quantile was configured"})
    text = plain.say_coverage(cov)
    assert "only one quantile was configured" in text


# The model framing moved to tests/test_model_framing.py when the sentence
# stopped being a literal in this module and became a field read from the
# artifact. The tests that used to live here asserted the literal verbatim,
# which is precisely what let it go stale.


# ─────────────────────── counters and defects ───────────────────────

def test_unknown_counter_renders_as_not_recorded():
    assert plain.say_counter(unknown(Presence.ABSENT), "leakage") == \
        "leakage: not recorded"
    assert plain.say_counter(known(3), "leakage") == "leakage: 3"


def test_flag_counts_are_labelled_a_floor_when_families_are_silent(incomplete_run):
    text = plain.describe_flags(read_summary(incomplete_run))
    assert "not recorded" in text
    assert "a floor, not a total" in text


def test_defects_are_shown_rather_than_absorbed(inconsistent_run):
    text = plain.describe_defects(read_summary(inconsistent_run))
    assert "depart" in text
    assert "None of them are guesses on my part" in text


def test_clean_run_reports_no_defects(clean_run):
    view = read_summary(clean_run)
    assert view.all_defects == []
    assert "No contract departures" in plain.describe_defects(view)


def test_clean_run_narrative_reads_normally(clean_run):
    text = plain.describe_run(read_summary(clean_run), LB.FAMILY_LABELS)
    assert "E_QUANTILE" in text and "48.45%" in text
    assert "not recorded" not in text
    assert "UNKNOWN" not in text


def test_departures_and_anticipated_ambiguities_are_separate(clean_run):
    """A compliant run has zero departures but still has things to note.

    Collapsing the two makes the defect panel cry wolf on a good artifact,
    which trains people to ignore it. The contract's own vocabulary already
    separates ERROR from WARNING; so does this.
    """
    run = LB.load_run(clean_run)
    assert run.all_defects == []
    assert run.all_notes, "a compliant run still has contract-anticipated gaps"
    assert any("y_lo" in n for n in run.all_notes)
    assert any("decoration in the join key" in n for n in run.all_notes)


def test_an_inconsistent_run_has_real_departures(inconsistent_run):
    run = LB.load_run(inconsistent_run)
    assert run.all_defects
    assert any("FAILED_QUALITY" in d for d in run.all_defects)
    assert any("origin_date >= target_date" in d for d in run.all_defects)
