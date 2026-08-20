# agent/data_intake.py — reading a candidate data file, and refusing bad ones.
#
# This runs BEFORE anything is scored, forecast or installed, and it is entirely
# agent-side: no Lab code, no subprocess, no writes. It exists because the two
# actions this session enables both take the new file as their input, and an
# input that is wrong in a way nobody checked produces a scored track record and
# a published forecast that are both confidently wrong.
#
# THE FOUR REJECTIONS, AND WHAT EACH ONE PREVENTS
# -----------------------------------------------
# 1. SCHEMA. The champion recipes build features from named columns. A file
#    missing one dies deep inside pandas with a bare KeyError, long after the
#    point where a human could tell what was wrong with their file.
#
# 2. DOES NOT EXTEND. The recipes forecast forward from the end of the data.
#    A file whose last date is not beyond the current one adds no truth to score
#    against and no new origin to forecast from.
#
# 3. SAME CHECKSUM. Session 5's guardrail: identical bytes reproduce the same
#    forecast under a new issue date, and published issues are the lab's track
#    record.
#
# 4. TRUNCATED HISTORY — the dangerous one. A user who exports "the new rows"
#    rather than "the updated series" produces a perfectly well-formed CSV that
#    extends past the current end, has the right columns, and a different
#    checksum. It passes 1, 2 and 3. Installing it as canonical would destroy
#    ~4000 rows of Treasury history that the models train on, and the file it
#    replaced is gitignored, so git would not even show the loss. So the check
#    is containment, not length: every date the current file has, the candidate
#    must also have.
#
# A REVISION IS DISCLOSED, NOT REFUSED
# ------------------------------------
# Values for dates that already exist may legitimately change — Treasury data is
# revised. That is reported with a count and examples so the user decides, rather
# than rejected. It matters because a revision under an already-published
# forecast changes what that forecast will be scored against, and the Lab's own
# scorer reports the same thing from the other side (`baseline_disagreements`).
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from . import published as PUB

#: Columns that are calendar or index, never a forecastable series. Mirrors
#: `forecast_modes.NON_TARGET_COLUMNS`; duplicated rather than imported because
#: this module must run in the AGENT's interpreter, which has no Lab on its path.
NON_TARGET_COLUMNS = frozenset({"date", "is_weekend", "is_holiday"})

#: How many differing overlap values to name before summarising the rest.
_MAX_EXAMPLES = 5


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DataFile:
    """A CSV read as a daily series, or the reason it could not be."""

    path: Path
    sha256: str = ""
    n_rows: int = 0
    columns: tuple[str, ...] = ()
    first_date: str = ""
    last_date: str = ""
    targets: tuple[str, ...] = ()
    dates: frozenset = field(default_factory=frozenset, repr=False)
    frame: Optional[pd.DataFrame] = field(default=None, repr=False)
    note: str = ""              # why it is unreadable; "" when readable

    @property
    def is_readable(self) -> bool:
        return not self.note


def describe(path: Path) -> DataFile:
    """Read a candidate file. Never raises — the reason comes back as `note`.

    Parse errors are surfaced verbatim rather than summarised: "could not read
    your file" is not actionable, and the pandas message names the line.
    """
    path = Path(path)
    if not path.exists():
        return DataFile(path, note=f"there is no file at `{path}`")
    if path.is_dir():
        return DataFile(path, note=f"`{path}` is a directory, not a CSV file")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:                              # noqa: BLE001
        return DataFile(path, note=f"it could not be parsed as CSV: {exc}")

    # The date-column check comes FIRST, before the empty check, because pandas
    # does not raise on a file that is not a CSV at all: handed raw bytes it
    # returns one column named after the garbage and zero rows. Reporting that
    # as "a header with no rows" is true and useless. Reporting it as "no date
    # column, and here are the columns I did find" puts the garbage on screen,
    # which is what tells the user they sent the wrong file.
    date_col = next((c for c in frame.columns if str(c).lower() == "date"), None)
    if date_col is None:
        return DataFile(path, note=(
            f"it has no `date` column (its columns are: "
            f"{', '.join(str(c) for c in frame.columns[:8])}"
            f"{'…' if len(frame.columns) > 8 else ''})"))

    if frame.empty:
        return DataFile(path, note="it has a header but no rows")

    parsed = pd.to_datetime(frame[date_col], errors="coerce")
    bad = int(parsed.isna().sum())
    if bad:
        first_bad = frame.loc[parsed.isna(), date_col].iloc[0]
        return DataFile(path, note=(
            f"{bad} row(s) have a `date` that is not a date — the first is "
            f"`{first_bad}`"))

    frame = frame.assign(**{date_col: parsed}).sort_values(date_col)
    dates = [str(d.date()) for d in frame[date_col]]
    duplicated = pd.Index(dates)[pd.Index(dates).duplicated()].unique().tolist()
    if duplicated:
        return DataFile(path, note=(
            f"{len(duplicated)} date(s) appear more than once — the first is "
            f"`{duplicated[0]}`. A daily series with a repeated date has no "
            f"single value for that day"))

    targets = tuple(
        str(c) for c in frame.columns
        if str(c).lower() not in NON_TARGET_COLUMNS
        and pd.api.types.is_numeric_dtype(frame[c]))

    return DataFile(
        path=path, sha256=sha256_of(path), n_rows=len(frame),
        columns=tuple(str(c) for c in frame.columns),
        first_date=dates[0], last_date=dates[-1], targets=targets,
        dates=frozenset(dates), frame=frame)


@dataclass(frozen=True)
class IntakeVerdict:
    """Whether a candidate may be used, and everything the user should know."""

    candidate: DataFile
    current: Optional[DataFile] = None
    last_issue_date: str = ""
    last_issue_sha: str = ""

    problems: tuple[str, ...] = ()          # hard refusals
    missing_columns: tuple[str, ...] = ()
    extra_columns: tuple[str, ...] = ()
    missing_dates: tuple[str, ...] = ()
    revised: tuple[str, ...] = ()           # human-readable examples
    n_revised: int = 0
    new_dates: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.problems

    @property
    def n_new(self) -> int:
        return len(self.new_dates)


def validate(candidate_path: Path, lab: Path,
             current_path: Optional[Path] = None) -> IntakeVerdict:
    """Everything that must be true before a file may be used, checked here.

    `lab` is read for the current data file and the latest issue's recorded
    checksum. Nothing is written and no Lab code runs.
    """
    lab = Path(lab)
    candidate = describe(candidate_path)
    if not candidate.is_readable:
        return IntakeVerdict(candidate,
                             problems=(f"I can't read that file: {candidate.note}",))

    current_path = Path(current_path) if current_path else (
        lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv")
    current = describe(current_path)

    issue = PUB.latest_issue(lab)
    last_issue_date = issue.issue_date if issue.is_readable else ""
    last_issue_sha = issue.data_sha_at_issue if issue.is_readable else ""

    problems: list[str] = []
    missing_cols: tuple[str, ...] = ()
    extra_cols: tuple[str, ...] = ()
    missing_dates: tuple[str, ...] = ()
    revised: list[str] = []
    n_revised = 0
    new_dates: tuple[str, ...] = ()

    if current.is_readable:
        missing_cols = tuple(c for c in current.columns
                             if c not in set(candidate.columns))
        extra_cols = tuple(c for c in candidate.columns
                           if c not in set(current.columns))
        if missing_cols:
            problems.append(
                f"it is missing {len(missing_cols)} column(s) the lab's current "
                f"data file has: {', '.join(missing_cols[:6])}"
                f"{'…' if len(missing_cols) > 6 else ''}. The champion recipes "
                f"build their features from these by name, so a run would fail "
                f"partway through rather than at the start")

        # Rejection 4: containment, not length. See the module header.
        absent = sorted(current.dates - candidate.dates)
        if absent:
            missing_dates = tuple(absent)
            problems.append(
                f"it does not contain {len(absent)} date(s) that the current "
                f"data file has — the earliest missing is `{absent[0]}` and the "
                f"latest is `{absent[-1]}`. This looks like an export of only "
                f"the new rows rather than the updated series. Installing it "
                f"would discard that history, and the file it replaced is "
                f"gitignored, so nothing would record the loss")

        if candidate.last_date <= current.last_date:
            problems.append(
                f"its last date is `{candidate.last_date}`, which does not go "
                f"past the current data file's `{current.last_date}`. There "
                f"would be no new actuals to score against and no new origin "
                f"to forecast from")
        else:
            new_dates = tuple(sorted(candidate.dates - current.dates))

        if candidate.sha256 == current.sha256:
            problems.append(
                "it is byte-for-byte identical to the lab's current data file")

        # A revision is disclosed, never refused.
        if (candidate.frame is not None and current.frame is not None
                and not missing_cols):
            n_revised, revised = _revisions(current, candidate)

    if last_issue_sha and candidate.sha256 == last_issue_sha:
        problems.append(
            f"its checksum is identical to the one recorded in the latest "
            f"published issue `{last_issue_date}`, so it is the same data that "
            f"issue was built from. Running on it would reproduce the same "
            f"numbers under a new issue date")

    return IntakeVerdict(
        candidate=candidate, current=current if current.is_readable else None,
        last_issue_date=last_issue_date, last_issue_sha=last_issue_sha,
        problems=tuple(problems), missing_columns=missing_cols,
        extra_columns=extra_cols, missing_dates=missing_dates[:20],
        revised=tuple(revised), n_revised=n_revised, new_dates=new_dates)


def _revisions(current: DataFile, candidate: DataFile) -> tuple[int, list[str]]:
    """Overlap cells whose value changed, as `(count, examples)`.

    Compared on the dates both files share and the columns both carry. NaN on
    both sides counts as unchanged: absent is not a value, and a column that is
    blank in both files has not been revised.
    """
    date_col = next(c for c in current.frame.columns if str(c).lower() == "date")
    cand_col = next(c for c in candidate.frame.columns if str(c).lower() == "date")

    left = current.frame.assign(_d=[str(d.date()) for d in current.frame[date_col]])
    right = candidate.frame.assign(_d=[str(d.date()) for d in candidate.frame[cand_col]])
    shared = sorted(set(left["_d"]) & set(right["_d"]))
    if not shared:
        return 0, []

    left = left[left["_d"].isin(shared)].set_index("_d").sort_index()
    right = right[right["_d"].isin(shared)].set_index("_d").sort_index()
    cols = [c for c in current.columns
            if c in set(candidate.columns) and str(c).lower() != "date"
            and pd.api.types.is_numeric_dtype(left[c])
            and pd.api.types.is_numeric_dtype(right[c])]

    total, examples = 0, []
    for col in cols:
        a, b = left[col], right[col]
        differs = ~((a == b) | (a.isna() & b.isna()))
        n = int(differs.sum())
        if not n:
            continue
        total += n
        for day in a.index[differs][:_MAX_EXAMPLES - len(examples)]:
            examples.append(f"{col} on {day}: {a[day]:,.2f} → {b[day]:,.2f}")
        if len(examples) >= _MAX_EXAMPLES:
            break
    return total, examples


def intake_message(verdict: IntakeVerdict) -> str:
    """What the agent says about a candidate file. Asks; never installs."""
    cand = verdict.candidate

    if not verdict.accepted:
        bullets = "\n".join(f"- {p}" for p in verdict.problems)
        return (f"**I can't use that file.**\n\n{bullets}\n\n"
                f"Nothing has been scored, run or installed, and the lab's data "
                f"is untouched.")

    lines = [
        f"**That file looks usable.** Here is what it contains — "
        f"**nothing has been run or installed yet.**",
        "",
        f"- **Rows:** {cand.n_rows:,}",
        f"- **Date range:** {cand.first_date} → {cand.last_date}",
        f"- **New dates beyond the current data:** {verdict.n_new} "
        f"({', '.join(verdict.new_dates[:6])}"
        f"{'…' if verdict.n_new > 6 else ''})",
        f"- **Columns:** {len(cand.columns)}, matching the lab's current file",
        f"- **Forecastable series present:** {len(cand.targets)}",
        f"- **Checksum:** `{cand.sha256[:16]}…`, which differs from issue "
        f"`{verdict.last_issue_date or 'not recorded'}`",
    ]
    if verdict.extra_columns:
        lines.append(
            f"- **Extra columns not in the current file:** "
            f"{', '.join(verdict.extra_columns[:6])}"
            f"{'…' if len(verdict.extra_columns) > 6 else ''}. Harmless — the "
            f"recipes select by name — but worth knowing they are there.")
    if verdict.n_revised:
        examples = "; ".join(verdict.revised)
        lines += [
            "",
            f"⚠️ **{verdict.n_revised} value(s) changed for dates that already "
            f"existed.** That is a revision, not an error, and I am reporting "
            f"it rather than refusing it — but a revision under an "
            f"already-published forecast changes what that forecast gets scored "
            f"against. Examples: {examples}.",
        ]
    lines += ["", "**Shall I take this file?**"]
    return "\n".join(lines)


# ─────────────────────────── installing it ───────────────────────────

@dataclass(frozen=True)
class InstallResult:
    installed_to: Optional[Path] = None
    backup_at: Optional[Path] = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.installed_to is not None and not self.note


def install(candidate: Path, lab: Path, current_path: Optional[Path] = None,
            stamp: str = "") -> InstallResult:
    """Make `candidate` the lab's canonical data file, keeping the old one.

    WHY INSTALL AT ALL, rather than just passing the path to each run: a
    published issue records the checksum of the data it used, and Session 5's
    principle is that an agent-run forecast is byte-equivalent to a terminal-run
    one. If the canonical file still ended at the old date, the next run from
    the lab's own state would disagree with the issue the agent just published,
    and the issue would not be reproducible from the repository.
    """
    lab = Path(lab)
    candidate = Path(candidate)
    target = Path(current_path) if current_path else (
        lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv")

    if not candidate.exists():
        return InstallResult(note=f"there is no file at `{candidate}`")

    backup: Optional[Path] = None
    if target.exists():
        stamp = stamp or pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.stem}.{stamp}.bak{target.suffix}")
        if backup.exists():
            return InstallResult(note=(
                f"a backup already exists at `{backup}` — refusing to overwrite "
                f"it, because that file is the only copy of the data being "
                f"replaced"))
        try:
            backup.write_bytes(target.read_bytes())
        except OSError as exc:
            return InstallResult(note=f"the current data could not be backed "
                                      f"up ({exc}), so nothing was replaced")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(candidate.read_bytes())
    except OSError as exc:
        return InstallResult(backup_at=backup,
                             note=f"the new data could not be written: {exc}")
    return InstallResult(installed_to=target, backup_at=backup)
