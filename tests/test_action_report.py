# tests/test_action_report.py — the report describes the disk, not the run.
#
# The distinction these tests defend: a subprocess that says it published is
# not evidence that it published. Session 2's run reported success and had
# retained nothing durable — `publish()` wrote to the gitignored
# `forecasts/published/` and nothing mirrored it to the vault, so the only
# record of the forecast would have vanished at the next clean checkout. The
# run's output looked identical in both worlds.
#
# So every test here sets up a disagreement between what the run CLAIMS and
# what is actually on disk, and asserts the report follows the disk.
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent import run_report as RR
from agent.run_exec import RunOutcome

_FORECAST = (
    "target,horizon,origin_date,origin_value,target_date,p10,p50,p90,"
    "point_model,modelled_as\n"
    "Revenues,1,2025-08-06,38196835.92,2025-08-07,34867361,81838118,121428687,"
    "LightGBM_L1,level\n"
    "Revenues,2,2025-08-06,38196835.92,2025-08-08,38041278,62732573,89377695,"
    "LightGBM_L1,level\n"
)

#: The number a withheld target would have had, if anything ever showed it.
#: It exists only in this fixture, never in an artifact the report reads.
WITHHELD_P50 = "888888888"


def _lab(tmp_path: Path, *, issue: str = "2026-08-17", dirty: bool = False,
         retain: bool = True) -> Path:
    lab = tmp_path / "AI4CM"
    pub = lab / "forecasts" / "published" / issue
    pub.mkdir(parents=True)
    (pub / "forecast.csv").write_text(_FORECAST, encoding="utf-8")
    (pub / "manifest.json").write_text(json.dumps({
        "issue_date": issue, "targets": ["Revenues"], "horizons": [1, 2],
        "target_dates": ["2025-08-07", "2025-08-08"],
        "data_sha_at_issue": "abc123",
    }), encoding="utf-8")
    (pub / "gates.json").write_text(json.dumps(
        {"revenues-v1": {"target": "Revenues", "status": "candidate"}}),
        encoding="utf-8")
    (pub / "provenance.json").write_text(json.dumps({
        "code": {"git_sha": "b022666c0350a6d7835293ec4d1b472b557292e2",
                 "git_dirty": dirty},
        "generated_at_utc": "2026-08-17T09:00:00Z",
        "data": {"latest_data_date": "2025-08-06", "sha256": "abc123"},
    }), encoding="utf-8")
    (pub / "estimators").mkdir()
    (pub / "estimators" / "h1_point.joblib").write_bytes(b"\x00fitted-model")

    if retain:
        vault = lab / "private_vault" / "published" / issue
        vault.parent.mkdir(parents=True)
        shutil.copytree(pub, vault)
    return lab


def _outcome(issue: str = "2026-08-17", published=("Revenues",),
             refused=(), failed=()) -> RunOutcome:
    out = RunOutcome(returncode=0)
    for target in refused:
        out.events.append({
            "event": "target_refused", "target": target, "stage": "publish",
            "reason": (f"Refusing to publish {target!r}: its current verdict "
                       f"is 'withheld', which means a documented trivial "
                       f"benchmark is more accurate than this model.")})
    for target in failed:
        out.events.append({
            "event": "target_failed", "target": target, "stage": "publish",
            "error": "FileExistsError: already exists"})
    out.events.append({"event": "finish", "issue_date": issue,
                       "published": list(published), "refused": list(refused),
                       "failed": list(failed)})
    return out


# ─────────────────────────── retention, on disk ───────────────────────────

def test_retention_is_verified_by_comparing_checksums(tmp_path):
    lab = _lab(tmp_path)
    check = RR.verify_retention(lab, "2026-08-17")
    assert check.is_verified
    assert check.n_files == 5
    assert "Retention verified" in RR.retention_sentence(check)


def test_a_missing_vault_copy_is_reported_not_assumed(tmp_path):
    """Session 2's actual failure: published, retained nothing."""
    lab = _lab(tmp_path, retain=False)
    check = RR.verify_retention(lab, "2026-08-17")
    assert check.status == "missing"
    sentence = RR.retention_sentence(check)
    assert "could not be confirmed" in sentence
    assert "clean checkout" in sentence


def test_a_vault_copy_missing_the_estimators_is_not_verified(tmp_path):
    """A name-only check passes here; the forecast's models are gone."""
    lab = _lab(tmp_path)
    (lab / "private_vault" / "published" / "2026-08-17" / "estimators"
     / "h1_point.joblib").unlink()
    check = RR.verify_retention(lab, "2026-08-17")
    assert check.status == "differs"
    assert "estimators/h1_point.joblib" in check.missing_files
    assert "does not match" in RR.retention_sentence(check)


def test_a_vault_copy_with_altered_contents_is_not_verified(tmp_path):
    """Presence is not enough: a truncated mirror is a retention failure."""
    lab = _lab(tmp_path)
    (lab / "private_vault" / "published" / "2026-08-17"
     / "forecast.csv").write_text("target,p50\ntruncated,1\n", encoding="utf-8")
    check = RR.verify_retention(lab, "2026-08-17")
    assert check.status == "differs"
    assert "forecast.csv" in check.differing_files


# ─────────────────────────── the report ───────────────────────────

def test_published_numbers_come_from_the_artifact(tmp_path):
    lab = _lab(tmp_path)
    text = RR.report(lab, _outcome())
    assert "issue `2026-08-17`" in text
    assert "81,838,118" in text and "34,867,361" in text
    assert "Retention verified" in text


def test_a_withheld_target_gets_the_reason_and_never_a_level(tmp_path):
    """agent/official.py rule 2, at the reporting boundary.

    The refusal text is quoted in the lab's own words; no number for the
    withheld series appears anywhere, because the only source of numbers is
    the published forecast.csv and a withheld target is not in it.
    """
    lab = _lab(tmp_path)
    text = RR.report(lab, _outcome(refused=("Expenditure",)))
    assert "Expenditure" in text
    assert "withheld" in text
    assert WITHHELD_P50 not in text
    assert "republish" in text.lower() or "withdrew" in text.lower()


def test_a_publish_the_artifact_does_not_confirm_is_flagged_not_repeated(
        tmp_path):
    """The run claims two targets; the manifest lists one."""
    lab = _lab(tmp_path)
    text = RR.report(lab, _outcome(published=("Revenues", "Expenditure")))
    assert "Expenditure" in text
    assert "manifest does not list it" in text
    assert "reporting the artifact, not the claim" in text


def test_a_claimed_publish_with_no_issue_on_disk_is_never_called_success(
        tmp_path):
    lab = _lab(tmp_path)
    shutil.rmtree(lab / "forecasts" / "published" / "2026-08-17")
    text = RR.report(lab, _outcome())
    assert "cannot read the issue" in text
    assert "not one I'll call successful" in text


def test_a_dirty_lab_tree_is_disclosed_as_a_reproducibility_limit(tmp_path):
    """A run launched mid-edit produces a forecast that cannot be reproduced.

    Not hypothetical: the first live run of this session recorded
    git_dirty=true because a Lab commit was in progress at that moment.
    """
    lab = _lab(tmp_path, dirty=True)
    text = RR.report(lab, _outcome())
    assert "uncommitted changes" in text
    assert "reproducibility is not guaranteed" in text
    assert "The forecast is real" in text


def test_a_failed_target_names_its_stage_and_error(tmp_path):
    lab = _lab(tmp_path)
    text = RR.report(lab, _outcome(published=(), failed=("Revenues",)))
    assert "FileExistsError" in text and "publish" in text


# ─────────────────────────── downloads ───────────────────────────

def test_every_download_filename_carries_the_issue_date(tmp_path):
    lab = _lab(tmp_path)
    for item in RR.downloads(lab, "2026-08-17"):
        assert "2026-08-17" in item.filename, (
            "a bare forecast.csv is indistinguishable from any other issue's")


def test_the_forecast_csv_is_offered_byte_for_byte(tmp_path):
    lab = _lab(tmp_path)
    csv = next(d for d in RR.downloads(lab, "2026-08-17")
               if d.filename.endswith(".csv"))
    assert csv.is_available
    assert csv.data == _FORECAST.encode("utf-8"), (
        "the download must be the published artifact, not a re-rendering")


def test_gates_and_provenance_are_offered_too(tmp_path):
    lab = _lab(tmp_path)
    names = {d.filename for d in RR.downloads(lab, "2026-08-17")}
    assert names == {"ai4cm-forecast-2026-08-17.csv",
                     "ai4cm-gates-2026-08-17.json",
                     "ai4cm-provenance-2026-08-17.json"}


def test_a_missing_file_is_offered_as_unavailable_with_a_reason(tmp_path):
    lab = _lab(tmp_path)
    (lab / "forecasts" / "published" / "2026-08-17" / "gates.json").unlink()
    gates = next(d for d in RR.downloads(lab, "2026-08-17")
                 if "gates" in d.filename)
    assert not gates.is_available
    assert "not in issue" in gates.note


def test_downloads_for_an_issue_that_does_not_exist_are_all_unavailable(tmp_path):
    lab = _lab(tmp_path)
    items = RR.downloads(lab, "1999-01-01")
    assert items and not any(d.is_available for d in items)
