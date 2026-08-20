# tests/test_data_intake.py — every way a candidate data file can be wrong.
#
# The weighting here is deliberate. A file that is obviously broken (not a CSV,
# no date column) fails loudly wherever it is used, so those tests are cheap
# insurance. The ones that earn their keep are the files that look FINE:
#
#   * an export of only the new rows — right columns, later dates, different
#     checksum, and installing it destroys four thousand rows of history that
#     nothing in git would record the loss of;
#   * the same file sent twice — Session 5's guardrail, from the other side;
#   * a file with revised values in the overlap — legitimate, and dangerous to
#     swallow silently because it changes what an already-published forecast
#     will be scored against.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import data_intake as DI

_COLS = ["date", "Revenues", "Expenditure", "State budget balance", "is_weekend"]


def _csv(path: Path, rows: list[tuple], cols: list[str] | None = None) -> Path:
    cols = cols or _COLS
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(cols)]
    lines += [",".join(str(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rows(start_day: int, n: int, bump: float = 0.0) -> list[tuple]:
    return [(f"2025-08-{start_day + i:02d}", 100.0 + i + bump, 200.0 + i,
             -100.0 + i, 0) for i in range(n)]


def _lab(tmp_path: Path, issue_sha: str = "sha-of-the-published-issue") -> Path:
    lab = tmp_path / "AI4CM"
    issue = lab / "forecasts" / "published" / "2026-08-16"
    issue.mkdir(parents=True)
    (issue / "manifest.json").write_text(json.dumps({
        "issue_date": "2026-08-16", "targets": ["Revenues"],
        "horizons": [1], "target_dates": ["2025-08-07"],
        "data_sha_at_issue": issue_sha}), encoding="utf-8")
    (issue / "forecast.csv").write_text(
        "target,horizon,origin_date,target_date,p10,p50,p90\n"
        "Revenues,1,2025-08-06,2025-08-07,1,2,3\n", encoding="utf-8")
    _csv(lab / "backend" / "data" / "processed"
         / "master_daily_clean_treasury.csv", _rows(1, 6))
    return lab


def _current(lab: Path) -> Path:
    return lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv"


# ─────────────────────────── unreadable files ───────────────────────────

def test_a_missing_file_is_rejected_by_path(tmp_path):
    v = DI.validate(tmp_path / "nope.csv", _lab(tmp_path))
    assert not v.accepted and "no file at" in v.problems[0]


def test_a_file_that_is_not_csv_surfaces_the_real_parser_error(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"\x00\x01\x02 not a csv at all")
    v = DI.validate(bad, _lab(tmp_path))
    assert not v.accepted
    assert "could not be parsed" in v.problems[0] or "no `date` column" in v.problems[0]


def test_a_file_with_no_date_column_names_the_columns_it_does_have(tmp_path):
    p = _csv(tmp_path / "c.csv", [(1, 2)], cols=["Revenues", "Expenditure"])
    v = DI.validate(p, _lab(tmp_path))
    assert not v.accepted
    assert "no `date` column" in v.problems[0] and "Revenues" in v.problems[0]


def test_an_unparseable_date_names_the_first_bad_value(tmp_path):
    p = _csv(tmp_path / "c.csv",
             [("2025-08-01", 1, 2, 3, 0), ("not-a-date", 1, 2, 3, 0)])
    v = DI.validate(p, _lab(tmp_path))
    assert not v.accepted and "not-a-date" in v.problems[0]


def test_a_repeated_date_is_rejected(tmp_path):
    p = _csv(tmp_path / "c.csv",
             [("2025-08-01", 1, 2, 3, 0), ("2025-08-01", 9, 9, 9, 0)])
    v = DI.validate(p, _lab(tmp_path))
    assert not v.accepted and "more than once" in v.problems[0]


def test_a_header_with_no_rows_is_rejected(tmp_path):
    p = _csv(tmp_path / "c.csv", [])
    v = DI.validate(p, _lab(tmp_path))
    assert not v.accepted and "no rows" in v.problems[0]


# ─────────────────────────── schema ───────────────────────────

def test_a_missing_column_is_rejected_and_named(tmp_path):
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv",
             [("2025-08-0%d" % i, 1.0, 0) for i in range(1, 9)],
             cols=["date", "Revenues", "is_weekend"])
    v = DI.validate(p, lab)
    assert not v.accepted
    assert "Expenditure" in v.problems[0]
    assert "fail partway through rather than at the start" in v.problems[0]


def test_an_extra_column_is_disclosed_but_accepted(tmp_path):
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv",
             [(f"2025-08-{d:02d}", 1.0, 2.0, 3.0, 0, 7.0) for d in range(1, 9)],
             cols=_COLS + ["NewSeries"])
    v = DI.validate(p, lab)
    assert v.accepted
    assert v.extra_columns == ("NewSeries",)
    assert "NewSeries" in DI.intake_message(v)


# ─────────────── the file that looks fine and is not ───────────────

def test_an_export_of_only_the_new_rows_is_rejected(tmp_path):
    """Right columns, later dates, different checksum — and it destroys history.

    This is the failure the containment check exists for. Length alone would
    not catch it either: a short file of NEW rows is exactly what a user gets
    from "export since last update".
    """
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv", _rows(7, 3))     # only 2025-08-07..09
    v = DI.validate(p, lab)
    assert not v.accepted
    problem = " ".join(v.problems)
    assert "does not contain" in problem
    assert "only the new rows" in problem
    assert "2025-08-01" in problem                 # names the earliest lost date


def test_the_same_file_twice_is_rejected_as_unchanged(tmp_path):
    lab = _lab(tmp_path)
    same = tmp_path / "same.csv"
    same.write_bytes(_current(lab).read_bytes())
    v = DI.validate(same, lab)
    assert not v.accepted
    assert any("identical to the lab's current data file" in p for p in v.problems)


def test_a_file_matching_the_published_issue_checksum_hits_the_guardrail(tmp_path):
    """Session 5's guardrail, reached through intake rather than through a run."""
    p = _csv(tmp_path / "c.csv", _rows(1, 9))
    lab = _lab(tmp_path, issue_sha=DI.sha256_of(p))
    v = DI.validate(p, lab)
    assert not v.accepted
    problem = " ".join(v.problems)
    assert "2026-08-16" in problem
    assert "same numbers under a new issue date" in problem


def test_a_file_that_does_not_extend_the_series_is_rejected(tmp_path):
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv", _rows(1, 6, bump=1.0))   # same dates, new values
    v = DI.validate(p, lab)
    assert not v.accepted
    assert any("does not go past" in x for x in v.problems)


# ─────────────────────────── the good path ───────────────────────────

def test_a_valid_extension_is_accepted_and_described(tmp_path):
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv", _rows(1, 9))     # 6 existing + 3 new
    v = DI.validate(p, lab)
    assert v.accepted
    assert v.n_new == 3
    assert v.new_dates == ("2025-08-07", "2025-08-08", "2025-08-09")
    assert v.candidate.last_date == "2025-08-09"

    said = DI.intake_message(v)
    assert "nothing has been run or installed yet" in said.lower()
    assert "Shall I take this file?" in said
    assert "2025-08-09" in said


def test_the_forecastable_series_are_counted_not_the_calendar_flags(tmp_path):
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv", _rows(1, 9))
    v = DI.validate(p, lab)
    assert "is_weekend" not in v.candidate.targets
    assert set(v.candidate.targets) == {"Revenues", "Expenditure",
                                        "State budget balance"}


def test_a_revision_in_the_overlap_is_disclosed_not_refused(tmp_path):
    """Legitimate, and it changes what a published forecast is scored against."""
    lab = _lab(tmp_path)
    rows = _rows(1, 9)
    rows[2] = ("2025-08-03", 999.0, 202.0, -98.0, 0)      # revised value
    p = _csv(tmp_path / "c.csv", rows)
    v = DI.validate(p, lab)

    assert v.accepted, "a revision must not block the file"
    assert v.n_revised == 1
    said = DI.intake_message(v)
    assert "1 value(s) changed" in said
    assert "Revenues on 2025-08-03" in said
    assert "scored against" in said


def test_an_unchanged_overlap_reports_no_revisions(tmp_path):
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv", _rows(1, 9))
    v = DI.validate(p, lab)
    assert v.n_revised == 0
    assert "changed for dates that already existed" not in DI.intake_message(v)


def test_a_rejection_says_nothing_was_touched(tmp_path):
    lab = _lab(tmp_path)
    v = DI.validate(_csv(tmp_path / "c.csv", _rows(7, 3)), lab)
    said = DI.intake_message(v)
    assert "lab's data is untouched" in said
    assert "Nothing has been scored, run or installed" in said


# ─────────────────────────── installing ───────────────────────────

def test_installing_keeps_a_timestamped_backup_of_what_it_replaced(tmp_path):
    lab = _lab(tmp_path)
    before = _current(lab).read_bytes()
    p = _csv(tmp_path / "c.csv", _rows(1, 9))

    result = DI.install(p, lab, stamp="20260818T120000Z")

    assert result.ok
    assert result.installed_to == _current(lab)
    assert result.backup_at is not None and result.backup_at.exists()
    assert result.backup_at.read_bytes() == before, (
        "the backup must be the file that was replaced, byte for byte")
    assert _current(lab).read_bytes() == p.read_bytes()
    assert "20260818T120000Z" in result.backup_at.name


def test_installing_never_overwrites_an_existing_backup(tmp_path):
    """The backup is the only copy of the data being replaced."""
    lab = _lab(tmp_path)
    p = _csv(tmp_path / "c.csv", _rows(1, 9))
    DI.install(p, lab, stamp="20260818T120000Z")

    p2 = _csv(tmp_path / "c2.csv", _rows(1, 10))
    result = DI.install(p2, lab, stamp="20260818T120000Z")

    assert not result.ok
    assert "refusing to overwrite" in result.note


def test_a_failed_backup_leaves_the_current_data_in_place(tmp_path):
    lab = _lab(tmp_path)
    before = _current(lab).read_bytes()
    result = DI.install(tmp_path / "does-not-exist.csv", lab)
    assert not result.ok
    assert _current(lab).read_bytes() == before
