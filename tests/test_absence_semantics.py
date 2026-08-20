# tests/test_absence_semantics.py — Part 1.
#
# The contract's §0 in executable form:
#
#   "a consumer should treat a missing or `n/a` value as UNKNOWN — never as
#    zero, never as a pass, and never as a failure."
#
# plus the two presentation rules the Agent owes its users: never narrate an
# absent field as a value, and never present a withheld or FAILED_QUALITY
# family as a clean result.
from __future__ import annotations

import pytest

from agent import lab_bridge as LB
from agent import plain
from agent.contract import Gate, Presence, RunStatus, read_summary


# ─────────────── null gate is neither a pass nor a failure ───────────────

def test_null_gate_is_unverified_not_passed(incomplete_run):
    """`gate_passed: null` must never render as a pass. Contract §0."""
    view = read_summary(incomplete_run)
    a_stat = view.family("A_STAT")
    assert a_stat.gate is Gate.UNVERIFIED
    assert not a_stat.is_presentable
    assert a_stat not in view.presentable_families


def test_null_gate_is_not_a_failure_either(incomplete_run):
    """The old code put null in `failed_families`. It is not a failure."""
    view = read_summary(incomplete_run)
    a_stat = view.family("A_STAT")
    assert a_stat not in view.withheld_families
    assert a_stat in view.unverified_families


def test_absent_gate_key_is_unverified(clean_run):
    """A family with no `gate_passed` key at all is also never-verified."""
    view = read_summary(clean_run)
    raw = dict(view.raw["families"][0])
    raw.pop("gate_passed")
    from agent.contract import read_family
    fam = read_family(raw)
    assert fam.gate is Gate.UNVERIFIED
    assert not fam.is_presentable
    assert any("never verified" in d for d in fam.defects)


def test_unverified_family_is_described_as_neither(incomplete_run):
    """The plain layer says 'no verdict', not 'failed'."""
    view = read_summary(incomplete_run)
    text = plain.say_gate(view.family("A_STAT"))
    assert "not verified" in text
    assert "no verdict" in text
    assert "withheld" not in text.lower()


# ─────────────── FAILED_QUALITY is never a clean result ───────────────

def test_failed_quality_is_not_presentable_even_when_gate_passed(inconsistent_run):
    """gate_passed true + run_status FAILED_QUALITY must not read as clean."""
    view = read_summary(inconsistent_run)
    a_stat = view.family("A_STAT")
    assert a_stat.gate is Gate.PASSED
    assert a_stat.run_status is RunStatus.FAILED_QUALITY
    assert not a_stat.is_presentable
    assert not a_stat.is_champion_eligible


def test_failed_quality_family_cannot_be_champion(inconsistent_run):
    """Its 27.51% skill must not win it the crown."""
    view = read_summary(inconsistent_run)
    champ = view.champion()
    assert champ is None or champ.name != "A_STAT"


def test_failed_quality_contradiction_is_reported(inconsistent_run):
    view = read_summary(inconsistent_run)
    a_stat = view.family("A_STAT")
    assert any("FAILED_QUALITY" in d for d in a_stat.defects)
    assert "inconsistent" in plain.say_gate(a_stat)


def test_withheld_family_is_never_a_champion(clean_run):
    """B_ML has the second-best skill and is withheld. It cannot win."""
    view = read_summary(clean_run)
    b_ml = view.family("B_ML")
    assert b_ml.gate is Gate.WITHHELD
    assert not b_ml.is_champion_eligible
    assert view.champion().name == "E_QUANTILE"


# ─────────────── missing / "n/a" is UNKNOWN, not zero ───────────────

def test_na_skill_is_not_produced_not_zero(incomplete_run):
    view = read_summary(incomplete_run)
    skill = view.family("A_STAT").skill_pct
    assert skill.is_unknown
    assert skill.presence is Presence.NOT_PRODUCED
    assert skill.value is None          # emphatically not 0.0


def test_null_skill_is_absent_not_zero(incomplete_run):
    view = read_summary(incomplete_run)
    skill = view.family("B_ML").skill_pct
    assert skill.is_unknown
    assert skill.value is None


def test_absent_overall_counters_do_not_become_zero(incomplete_run):
    """The old dashboard rendered `.get('leakage_flags', 0)` — a fabricated 0.

    With no `overall` block and families that record no flags, the counters
    must come back UNKNOWN so the UI can print an em dash.
    """
    view = read_summary(incomplete_run)
    assert view.overall.get("leakage_flags").is_unknown
    assert view.overall.get("shift_flags").is_unknown
    # Recomputable counters are still known — they come from the families.
    assert view.overall.get("families_requested").value == 3


def test_absent_flags_are_unknown_not_false(incomplete_run):
    view = read_summary(incomplete_run)
    a_stat = view.family("A_STAT")
    assert a_stat.leakage_flag.is_unknown
    assert a_stat.shift_flag.is_unknown
    assert "not recorded" in plain.say_flag(a_stat.leakage_flag, "leakage")


def test_value_cannot_be_used_as_a_boolean(incomplete_run):
    """The guard that makes 'absence collapses to False' impossible to write."""
    view = read_summary(incomplete_run)
    with pytest.raises(TypeError, match="not truthy"):
        bool(view.family("A_STAT").leakage_flag)


def test_absent_run_status_is_unknown(incomplete_run):
    view = read_summary(incomplete_run)
    assert view.family("A_STAT").run_status is RunStatus.UNKNOWN
    assert "not recorded" in plain.say_status(view.family("A_STAT"))


# ─────────────── absent fields are never narrated as values ───────────────

def test_absent_data_file_is_never_rendered_as_a_name(incomplete_run):
    """This was live on every real run: 'Input data: `None`'."""
    view = read_summary(incomplete_run)
    assert view.data_file.is_unknown
    text = plain.describe_run(view, LB.FAMILY_LABELS)
    assert "None" not in text
    assert "does not record which input file" in text


def test_absent_freshness_says_so_rather_than_implying_fresh(incomplete_run):
    view = read_summary(incomplete_run)
    assert view.stale.is_unknown
    assert "freshness is not recorded" in plain.describe_run(view, LB.FAMILY_LABELS)


def test_narrative_never_prints_a_bare_none(incomplete_run, inconsistent_run):
    for run_dir in (incomplete_run, inconsistent_run):
        text = plain.describe_run(read_summary(run_dir), LB.FAMILY_LABELS)
        assert "None" not in text
        assert "nan" not in text.lower()


def test_missing_run_id_falls_back_to_folder_name_and_says_so(incomplete_run):
    view = read_summary(incomplete_run)
    assert view.run_id.is_unknown
    assert any("run_id" in d for d in view.defects)


# ─────────────── the champion rule ───────────────

def test_no_champion_when_nothing_is_eligible(incomplete_run):
    """B_ML passed the gate but published no skill, so it cannot be ranked."""
    view = read_summary(incomplete_run)
    b_ml = view.family("B_ML")
    assert b_ml.is_presentable
    assert not b_ml.is_champion_eligible      # UNKNOWN is not a score
    assert view.champion() is None


def test_no_champion_explains_each_family_by_its_actual_state(incomplete_run):
    text = plain.describe_champion(read_summary(incomplete_run), LB.FAMILY_LABELS)
    assert "No model earned trust" in text
    assert "Never verified" in text and "not a failure" in text
    assert "published no skill figure" in text


def test_baseline_can_never_be_champion():
    """A reference baseline is not a competitor. Contract framing."""
    from agent.contract import read_family
    fam = read_family({
        "name": "A_STAT", "ok": True, "gate_passed": True, "gate_reasons": [],
        "run_status": "SUCCESS", "skill_pct": "99.00%",
        "best_model": "⚡ Persistence (baseline) (MAE 1,000)",
        "leakage_flag": False, "shift_flag": False})
    assert fam.is_presentable
    assert fam.best_model.is_baseline
    assert not fam.is_champion_eligible


# ─────────────── derived counters never override the families ───────────────

def test_overall_disagreement_is_detected_and_the_families_win(inconsistent_run):
    view = read_summary(inconsistent_run)
    assert view.overall.disagreements
    # 4 families listed, not the 9 the summary claims.
    assert view.overall.get("families_requested").value == 4
    # 2 gates recorded a pass, not the 4 claimed... and one of those two is
    # FAILED_QUALITY, so only one family is actually presentable.
    # Three entries record a gate pass, not the 4 claimed.
    assert view.overall.get("families_gate_passed").value == 3
    # But A_STAT's run_status is FAILED_QUALITY, so only two of those three
    # may be presented as clean — and one of the two is the duplicate C_DL
    # entry, which is itself reported as a defect.
    assert len(view.presentable_families) == 2
    assert {f.name for f in view.presentable_families} == {"B_ML", "C_DL"}


def test_overall_disagreement_is_surfaced_to_the_reader(inconsistent_run):
    text = plain.describe_run(read_summary(inconsistent_run), LB.FAMILY_LABELS)
    assert "disagree" in text
    assert "used the family records" in text


def test_duplicate_family_is_reported(inconsistent_run):
    view = read_summary(inconsistent_run)
    assert any("duplicate family" in d for d in view.defects)


def test_withheld_with_no_reason_is_flagged_not_explained(inconsistent_run):
    """C_DL is withheld with an empty reasons list. Don't invent one."""
    view = read_summary(inconsistent_run)
    c_dl = view.family("C_DL")
    assert c_dl.gate is Gate.WITHHELD
    assert not c_dl.gate_reasons
    assert any("no recorded reason" in d for d in c_dl.defects)
    assert "records no reason" in plain.say_gate(c_dl)


def test_pass_with_reasons_is_flagged(inconsistent_run):
    view = read_summary(inconsistent_run)
    assert any("passed but reasons were recorded" in d
               for d in view.family("B_ML").defects)


# ─────────────── the run still loads, defects and all ───────────────

def test_incomplete_run_still_loads(incomplete_run):
    run = LB.load_run(incomplete_run)
    assert run is not None
    assert run.all_defects


def test_inconsistent_run_still_loads(inconsistent_run):
    run = LB.load_run(inconsistent_run)
    assert run is not None
    assert run.all_defects


def test_family_listed_with_no_folder_is_reported(incomplete_run):
    run = LB.load_run(incomplete_run)
    assert any("no `e_quantile/` folder" in d for d in run.read_defects)


def test_unreadable_summary_returns_none(tmp_path):
    (tmp_path / "SUMMARY.json").write_text("{ not json", encoding="utf-8")
    assert LB.load_run(tmp_path) is None
