# tests/test_target_enumeration.py — what the lab can forecast is not a
# property of what it last forecast.
#
# The diagnosis these tests encode. The app offered only "Revenues" while the
# lab holds champion recipes for three targets. Two candidate explanations:
#
#   (a) a real property of the 2026-08-04 artifact — it genuinely forecast one
#       target, and `SUMMARY.json.target` is a single string, not a list;
#   (b) a bug in target enumeration.
#
# Both are true, and only (b) is the app's problem. The artifact does record one
# target, and that is correct: a run forecasts one series. But the picker is
# labelled "Run a NEW forecast" — it asks what the lab CAN forecast, and answered
# it from `SUMMARY.json.data_file`, a field the Lab's own contract documents as
# "written by current code; absent on every committed artifact". A single source,
# known-empty by documentation, with a silent degrade to `[run.target]`.
#
# So the menu could never have had more than one entry, on any artifact the Lab
# has ever committed — and the fallback made that look like an answer rather than
# a failure. The fix reads the Lab's registry, which is the file the Lab itself
# consults before it will issue an official forecast.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import lab_bridge as LB

OFFICIAL = ["Revenues", "Expenditure", "State budget balance"]


def _repo_of(run_dir: Path) -> Path:
    return run_dir.parent.parent.parent


def _write_registry(run_dir: Path, targets: list[str], *, blob=None) -> Path:
    path = _repo_of(run_dir) / "registry" / "recipes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = blob if blob is not None else {
        "schema_version": 1,
        "recipes": [{"id": f"r{i}", "target": t, "point_model": "LightGBM_L1"}
                    for i, t in enumerate(targets)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ─────────────────────── the artifact really does record one target ───────────────────────

def test_the_artifact_records_a_single_target_and_that_is_correct(clean_run):
    """Half (a) of the diagnosis: the run is not hiding two more targets."""
    from agent.contract import read_summary
    view = read_summary(clean_run)
    assert view.target.is_known
    assert view.target.value == "Revenues"
    assert isinstance(view.raw["target"], str)      # not a list, by design


def test_the_old_source_is_empty_on_an_artifact_that_records_no_data_file(legacy_run):
    """Half (b), preserved on a synthetic older artifact.

    With no `data_file`, the data-file path enumerates nothing and does not go
    looking for a plausible CSV. This is why the menu could never have had
    more than one entry on any artifact the Lab had committed at the time.
    """
    run = LB.load_run(legacy_run)
    assert run is not None
    assert run.view.data_file.is_unknown
    assert LB.available_targets(run) == []


def test_a_recorded_data_file_enumerates_columns_but_not_the_menu(real_run):
    """The prediction the old version of this test made, now come true.

    Its docstring said: "when the Lab starts writing `data_file` this will
    fail, and the extra columns become PART 5's problem rather than a silent
    behaviour change." The Lab writes it, so the columns are readable — 40-odd
    of them, including line items like `Income tax` that have no champion
    recipe, no persistence benchmark and no quality gate.

    The assertion that matters is the second one: the picker did **not**
    silently grow to 40 entries. Official targets still come from the
    registry, and offering an arbitrary column is Part 5's work, gated on the
    ExploratoryResult distinction — not something an artifact gained by
    recording its input file.
    """
    run = LB.load_run(real_run)
    columns = LB.available_targets(run)
    assert columns, (
        f"the run records {run.view.data_file.value} but no columns were "
        f"read from it; expected it under <repo>/backend/data/processed/")
    for official in OFFICIAL:
        assert official in columns
    assert len(columns) > len(OFFICIAL), "expected non-official columns too"

    menu = LB.target_choices(run)
    assert menu.targets == OFFICIAL
    assert menu.source == "registry"
    unofficial = [c for c in columns if c not in OFFICIAL]
    assert not any(c in menu.targets for c in unofficial), (
        f"non-official columns reached the target menu: "
        f"{[c for c in unofficial if c in menu.targets]}")


# ─────────────────────── the registry is the right source ───────────────────────

def test_official_targets_reads_every_recipe(clean_run):
    _write_registry(clean_run, OFFICIAL)
    targets, note = LB.official_targets(_repo_of(clean_run))
    assert targets == OFFICIAL
    assert note == ""


def test_all_official_targets_are_offered(clean_run):
    _write_registry(clean_run, OFFICIAL)
    menu = LB.target_choices(LB.load_run(clean_run))
    assert menu.targets == OFFICIAL
    assert menu.official == OFFICIAL
    assert menu.enumerated
    assert all(menu.is_official(t) for t in OFFICIAL)


def test_the_runs_own_target_survives_a_registry_that_omits_it(clean_run):
    _write_registry(clean_run, ["Expenditure", "State budget balance"])
    menu = LB.target_choices(LB.load_run(clean_run))
    assert "Revenues" in menu.targets            # the run used it; it is offerable
    assert not menu.is_official("Revenues")      # but it is not official
    assert "no champion recipe" in menu.note


def test_duplicate_recipes_for_one_target_are_offered_once(clean_run):
    _write_registry(clean_run, ["Revenues", "Revenues", "Expenditure"])
    targets, _ = LB.official_targets(_repo_of(clean_run))
    assert targets == ["Revenues", "Expenditure"]


# ─────────────────────── failure is reported, never guessed ───────────────────────

def test_a_missing_registry_yields_no_targets_and_a_reason(clean_run):
    targets, note = LB.official_targets(_repo_of(clean_run))
    assert targets == []
    assert "not found" in note


@pytest.mark.parametrize("blob,expected", [
    ({"recipes": "not a list"}, "no `recipes` list"),
    ({"recipes": []}, "lists no targets"),
    ({"recipes": [{"point_model": "X"}]}, "lists no targets"),
    ({"recipes": [{"target": "   "}]}, "lists no targets"),
])
def test_a_malformed_registry_is_reported_not_papered_over(clean_run, blob,
                                                           expected):
    _write_registry(clean_run, [], blob=blob)
    targets, note = LB.official_targets(_repo_of(clean_run))
    assert targets == []
    assert expected in note


def test_unparseable_registry_json_is_reported(clean_run):
    path = _repo_of(clean_run) / "registry" / "recipes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    targets, note = LB.official_targets(_repo_of(clean_run))
    assert targets == []
    assert "could not be read" in note


def test_no_registry_degrades_to_the_run_target_and_says_which(clean_run):
    menu = LB.target_choices(LB.load_run(clean_run))
    assert menu.targets == ["Revenues"]
    assert menu.official == []
    assert not menu.enumerated
    assert "only this run's own target" in menu.note


# ─────────────────────── the real lab ───────────────────────

def test_the_real_lab_offers_all_three_official_targets(real_run):
    """The behaviour the report is about, against the actual checkout."""
    menu = LB.target_choices(LB.load_run(real_run))
    assert menu.enumerated, menu.note
    for target in OFFICIAL:
        assert target in menu.targets, f"{target} missing from {menu.targets}"
    assert menu.official == OFFICIAL
