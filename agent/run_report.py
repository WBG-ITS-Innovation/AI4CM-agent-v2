# agent/run_report.py — what actually happened, read off the disk.
#
# THE RULE THIS MODULE EXISTS TO ENFORCE
# --------------------------------------
# A subprocess that says it published is not evidence that it published. This
# module never reports a number, a target or a retention that it has not read
# back out of an artifact. The run's own `finish` event is used for one thing
# only — knowing WHICH issue directory to go and read — and even that is
# checked against the directory actually being there.
#
# Session 2 is the reason. Its run reported a successful publish, and the issue
# had retained nothing durable: `publish()` wrote to `forecasts/published/` and
# nothing mirrored it to the vault, which is gitignored, so the only record of
# the forecast would have vanished with the next clean checkout. The failure
# was invisible from the run's output and obvious from `ls`. Session 2.5 fixed
# `publish()` — and the fix is exactly the kind of thing that regresses
# silently, so this module looks rather than assumes.
#
# WHAT IS DELIBERATELY NOT REPORTED
# ---------------------------------
# Levels for a target the gates withheld. `agent/lab_entry.py` already keeps
# them off the pipe, and this module closes the other route in: every number
# here is read from the published issue's `forecast.csv`, which by construction
# contains only targets that cleared publication. There is no code path from a
# refused target to a p50.
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import published as PUB


# ─────────────────────────── retention, verified ───────────────────────────

@dataclass(frozen=True)
class RetentionCheck:
    """Whether the vault really holds a copy of the issue, checked by reading.

    `status` is deliberately four-valued rather than a boolean, because
    "the vault does not exist" and "the vault has a copy that differs" are
    different problems with different fixes, and collapsing them into False
    would report the wrong one.
    """

    issue_date: str
    published_dir: Optional[Path] = None
    vault_dir: Optional[Path] = None
    status: str = "unknown"          # verified | missing | differs | unknown
    n_files: int = 0
    missing_files: tuple[str, ...] = ()
    differing_files: tuple[str, ...] = ()
    note: str = ""

    @property
    def is_verified(self) -> bool:
        return self.status == "verified"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files_under(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p
            for p in sorted(root.rglob("*")) if p.is_file()}


def verify_retention(lab: Path, issue_date: str,
                     published_root: Optional[Path] = None,
                     vault_root: Optional[Path] = None) -> RetentionCheck:
    """Compare the published issue with its vault copy, file by file.

    Content, not just presence: a truncated or half-written mirror is a
    retention failure that a name-only check reports as success. The estimator
    blobs are included, because they are the reason retention exists — the
    forecast can be re-read from the CSV, but what produced it cannot.
    """
    lab = Path(lab)
    pub_root = Path(published_root) if published_root else PUB.issue_root(lab)
    vlt_root = (Path(vault_root) if vault_root
                else lab / "private_vault" / "published")

    pub_dir = pub_root / str(issue_date)
    vlt_dir = vlt_root / str(issue_date)

    if not pub_dir.exists():
        return RetentionCheck(issue_date, None, None, "missing",
                              note=f"there is no published issue at `{pub_dir}`")
    if not vlt_dir.exists():
        return RetentionCheck(
            issue_date, pub_dir, None, "missing",
            note=(f"the issue was published to `{pub_dir}` but there is no "
                  f"copy at `{vlt_dir}`. The published directory is gitignored, "
                  f"so this forecast would not survive a clean checkout"))

    left, right = _files_under(pub_dir), _files_under(vlt_dir)
    missing = tuple(sorted(set(left) - set(right)))
    differing = tuple(sorted(
        name for name in sorted(set(left) & set(right))
        if _sha256(left[name]) != _sha256(right[name])))

    if missing or differing:
        return RetentionCheck(issue_date, pub_dir, vlt_dir, "differs",
                              n_files=len(left), missing_files=missing,
                              differing_files=differing)
    return RetentionCheck(issue_date, pub_dir, vlt_dir, "verified",
                          n_files=len(left))


def retention_sentence(check: RetentionCheck) -> str:
    """One quotable line about durability, or the honest gap."""
    if check.status == "verified":
        return (f"**Retention verified.** I compared all {check.n_files} files "
                f"in the published issue with the vault copy at "
                f"`{check.vault_dir}` — every one matches by checksum, so the "
                f"forecast and the fitted models behind it survive a clean "
                f"checkout.")
    if check.status == "missing":
        return f"⚠️ **Retention could not be confirmed.** {check.note}."
    if check.status == "differs":
        bits = []
        if check.missing_files:
            bits.append(f"{len(check.missing_files)} file(s) absent from the "
                        f"vault ({', '.join(check.missing_files[:4])}"
                        f"{'…' if len(check.missing_files) > 4 else ''})")
        if check.differing_files:
            bits.append(f"{len(check.differing_files)} file(s) whose contents "
                        f"differ ({', '.join(check.differing_files[:4])}"
                        f"{'…' if len(check.differing_files) > 4 else ''})")
        return (f"⚠️ **The vault copy does not match what was published**: "
                f"{'; and '.join(bits)}. The published directory is gitignored, "
                f"so the mismatched part is the part that would be lost.")
    return ("I could not check whether this issue was retained to the vault, "
            "so I can't tell you it is durable and I won't imply it is.")


# ─────────────────────────── the post-run report ───────────────────────────

def _fmt(value: float) -> str:
    return f"{value:,.0f}"


def _published_table(issue: PUB.Issue, target: str) -> str:
    rows = issue.rows_for(target)
    if not rows:
        return (f"The manifest lists **{target}** but `forecast.csv` has no "
                f"rows for it — an inconsistency in the artifact, so I have no "
                f"numbers to quote and won't reconstruct any.")
    lines = ["| Date | Low (P10) | **Central (P50)** | High (P90) |",
             "|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row.target_date} | {_fmt(row.p10)} | "
                     f"**{_fmt(row.p50)}** | {_fmt(row.p90)} |")
    return "\n".join(lines)


def report(lab: Path, outcome, published_root: Optional[Path] = None,
           vault_root: Optional[Path] = None) -> str:
    """The story of a finished run, assembled from artifacts.

    `outcome` is a `run_exec.RunOutcome`. Only its `finish` event is trusted,
    and only for the issue date; everything the user reads about numbers,
    targets and durability is read back off the disk.
    """
    finish = outcome.first("finish") or {}
    issue_date = str(finish.get("issue_date") or "")
    refused = outcome.all("target_refused")
    failed = outcome.all("target_failed")

    claimed = list(finish.get("published") or [])
    issue = (PUB.issue_at(Path(lab), issue_date) if issue_date
             else PUB.Issue(note="the run recorded no issue date"))
    if published_root is not None and issue_date:
        path = Path(published_root) / issue_date
        issue = (PUB.read_issue(path) if (path / "manifest.json").exists()
                 else PUB.Issue(issue_date=issue_date,
                                note=f"no issue was written at `{path}`"))

    parts: list[str] = []

    # ── what was published, from the artifact ──
    if not issue.is_readable:
        if claimed:
            parts.append(
                f"⚠️ **The run reported publishing {', '.join(claimed)}, but I "
                f"cannot read the issue it claims to have written**: "
                f"{issue.note}. I won't report numbers I can't read back, and "
                f"a publish I can't verify is not one I'll call successful.")
        else:
            parts.append(f"**Nothing was published.** {issue.note.capitalize()}."
                         if issue.note else "**Nothing was published.**")
    else:
        parts.append(f"**Run complete — issue `{issue.issue_date}`.**")
        if issue.targets:
            heading = ("Published, and read back from the issue on disk:"
                       if len(issue.targets) == 1 else
                       "Published, and read back from the issue on disk:")
            parts.append(heading)
            for target in issue.targets:
                parts.append(f"### {target}\n\n{_published_table(issue, target)}")
            parts.append(
                "P10–P90 is the range the model expects the outcome to fall in "
                "four times out of five; P50 is the central estimate.")
        else:
            parts.append("The issue was written but names no published target.")

        # A target the run claimed and the artifact does not carry.
        ghosts = [t for t in claimed if not issue.publishes(t)]
        if ghosts:
            parts.append(
                f"⚠️ The run reported publishing **{', '.join(ghosts)}**, but "
                f"the issue's manifest does not list it. I am reporting the "
                f"artifact, not the claim.")

    # ── what was refused, in the lab's own words ──
    if refused:
        bullets = []
        for event in refused:
            target = event.get("target", "?")
            reason = str(event.get("reason") or "").strip()
            bullets.append(f"- **{target}** — {reason}")
        parts.append(
            "**Withheld by the lab's own publishing code**, not filtered out "
            "beforehand:\n\n" + "\n".join(bullets) + "\n\n"
            "No forecast numbers are shown for these, because the gates "
            "withheld them — quoting the levels 'for context' would republish "
            "exactly what the verdict withdrew.")

    # ── what failed ──
    if failed:
        bullets = "\n".join(
            f"- **{e.get('target')}** failed at the {e.get('stage')} stage: "
            f"`{e.get('error')}`" for e in failed)
        parts.append(f"**Failed:**\n\n{bullets}")

    # ── provenance and durability ──
    if issue.is_readable:
        parts.append(PUB.provenance_sentence(issue))
        if issue.git_dirty is True:
            parts.append(
                "⚠️ The lab's working tree had uncommitted changes when this "
                "ran, so the recorded code version does not fully identify "
                "what produced these numbers. The forecast is real; its "
                "reproducibility is not guaranteed. If that matters, commit "
                "the lab and run again.")
        check = verify_retention(lab, issue.issue_date, published_root, vault_root)
        parts.append(retention_sentence(check))
        if issue.defects:
            parts.append("**Defects found while reading the issue back:**\n\n"
                         + "\n".join(f"- {d}" for d in issue.defects))

    return "\n\n".join(p for p in parts if p)


# ─────────────────────────── downloads ───────────────────────────

@dataclass(frozen=True)
class Download:
    """One offerable file, named so it cannot be confused for another issue."""

    label: str
    filename: str
    data: bytes = b""
    mime: str = "text/csv"
    note: str = ""

    @property
    def is_available(self) -> bool:
        return bool(self.data)


#: Filenames carry the issue date because a forecast is only meaningful with
#: the date it was issued on. A file called `forecast.csv` in a downloads
#: folder is indistinguishable from every other one, and a treasury comparing
#: two of them has no way to tell which came first.
_OFFERED = (
    ("Forecast (CSV)", "forecast.csv", "ai4cm-forecast-{issue}.csv", "text/csv"),
    ("Quality gates (JSON)", "gates.json", "ai4cm-gates-{issue}.json",
     "application/json"),
    ("Provenance (JSON)", "provenance.json", "ai4cm-provenance-{issue}.json",
     "application/json"),
)


def downloads(lab: Path, issue_date: str,
              published_root: Optional[Path] = None) -> list[Download]:
    """Offerable files from one published issue, read at call time.

    Absent files are returned as unavailable entries with a reason rather than
    omitted, so the UI can say why something is not on offer instead of
    quietly showing a shorter list.
    """
    root = (Path(published_root) if published_root
            else PUB.issue_root(Path(lab)))
    issue_dir = root / str(issue_date)

    out: list[Download] = []
    for label, name, pattern, mime in _OFFERED:
        path = issue_dir / name
        target = pattern.format(issue=issue_date)
        if not path.exists():
            out.append(Download(label, target, b"", mime,
                                note=f"`{name}` is not in issue `{issue_date}`"))
            continue
        try:
            out.append(Download(label, target, path.read_bytes(), mime))
        except OSError as exc:
            out.append(Download(label, target, b"", mime,
                                note=f"`{name}` could not be read: {exc}"))
    return out


# ════════════════════════ scoring: what actually happened ════════════════════
#
# Same discipline as the forecast report above, and one addition that is
# specific to scoring: PENDING ROWS EXIST NOWHERE ON DISK.
#
# `score_published` rewrites the scorecard from scratch and writes only rows
# whose truth has arrived, so a scorecard with three rows is indistinguishable
# — from the file alone — between "three forecasts were made" and "twenty-five
# were made and twenty-two are still waiting". The pending count lives only in
# the Lab's return value, which travels on the event stream. Reporting scored
# rows without it would present a partial scoring as a complete one, which is
# the scoring equivalent of quoting a withheld level.
#
# The column set is READ, never assumed. It changed under this session: the
# Lab added the four Ops comparison columns on 2026-08-18, taking the schema
# from 27 fields to 31. A report that hardcoded the field list would have
# either crashed or silently dropped the new comparator. So every optional
# figure is emitted only where its column is present AND populated, and its
# absence is reported as absence.

#: Figures a row may carry, as `(column, label, formatter)`. Ops is not special-
#: cased anywhere: it is simply one more entry that may or may not be present.
_SKILL_FIELDS = (
    ("skill_vs_ruler_pct", "vs the naive ruler"),
    ("skill_vs_ops", "vs the Treasury's current method"),
)


def read_scorecard(path: Path) -> tuple[list[dict], str]:
    """Rows from the Lab's scorecard, or the reason there are none.

    Parsed with `csv.DictReader` rather than pandas so a column the agent does
    not know about travels through untouched instead of being coerced.
    """
    import csv
    path = Path(path)
    if not path.exists():
        return [], f"there is no scorecard at `{path}`"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"the scorecard could not be read: {exc}"
    rows = list(csv.DictReader(text.splitlines()))
    return rows, ""


def _num(row: dict, key: str) -> Optional[float]:
    """A number, or None. A blank cell is absent — never zero."""
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value == value else None


def _flag(row: dict, key: str) -> Optional[bool]:
    raw = (row.get(key) or "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return None


def score_report(scorecard_path: Path, outcome) -> str:
    """What the scoring run found, read from the scorecard it wrote.

    `outcome` is a `run_exec.RunOutcome` over `agent/lab_score.py`'s stream. It
    supplies the pending count and the Lab's own per-target summary; every
    per-row figure comes from the artifact.
    """
    scored_event = outcome.first("scored") or {}
    refused = outcome.first("refused")
    if refused:
        return (f"**The lab refused to score.** {refused.get('reason')}\n\n"
                f"Nothing was written to the scorecard.")

    n_scored = int(scored_event.get("scored") or 0)
    n_pending = int(scored_event.get("pending") or 0)
    n_issues = int(scored_event.get("issues") or 0)
    summary = scored_event.get("summary") or {}
    pending_dates = scored_event.get("pending_dates") or []
    disagreements = scored_event.get("baseline_disagreements") or []

    rows, note = read_scorecard(scorecard_path)
    parts: list[str] = []

    if n_scored == 0:
        # ROWS and TARGET-DATES are different counts and the first real
        # production run made the difference visible: 25 pending ROWS but 15
        # distinct target-dates, because Revenues is published in three issues
        # and each issue's row for a given date is separately pending. The Lab
        # returns `pending` as a row count and `pending_dates` as a SET, so
        # printing the first and listing the second reads as arithmetic that
        # does not add up.
        n_distinct = len({(str(e[0]), str(e[1])) for e in pending_dates
                          if isinstance(e, (list, tuple)) and len(e) >= 2})
        covering = (f", covering **{n_distinct} distinct target-date(s)** — the "
                    f"same date is pending separately in each issue that "
                    f"forecast it" if n_distinct and n_distinct != n_pending else "")
        parts.append(
            f"**Nothing could be scored yet** — and that is the expected state "
            f"for a forecast issued before its outcome exists, not a failure.\n\n"
            f"Across {n_issues} published issue(s), **{n_pending} forecast "
            f"row(s) are still waiting for truth to arrive** in the data"
            f"{covering}. A forecast is scored only once the day it forecast "
            f"has actually happened and that day's value is in the lab's input "
            f"file.")
        if pending_dates:
            parts.append(_pending_block(pending_dates))
        return "\n\n".join(parts)

    if note:
        return (f"The run reported {n_scored} scored row(s), but I cannot read "
                f"the scorecard it wrote: {note}. I won't report figures I "
                f"can't read back.")

    parts.append(
        f"**Scored {n_scored} published forecast(s)** across {n_issues} issue(s)"
        + (f", with **{n_pending} row(s) still pending**." if n_pending else "."))

    # ── per target, from the Lab's own summary ──
    if summary:
        lines = ["| Series | Scored | Mean error | Ruler's error | Skill vs ruler | Inside the band |",
                 "|---|---:|---:|---:|---:|---:|"]
        for target, s in summary.items():
            skill = s.get("skill_vs_ruler_pct")
            hit = s.get("interval_hit_rate")
            nominal = s.get("nominal_coverage")
            lines.append(
                f"| **{target}** | {s.get('n')} | {_fmt(s.get('realized_mae', 0))} | "
                f"{_fmt(s.get('persistence_mae', 0))} | "
                f"{f'{skill:+.1f}%' if isinstance(skill, (int, float)) and skill == skill else 'not recorded'} | "
                f"{f'{hit:.0%}' if isinstance(hit, (int, float)) else 'not recorded'} "
                f"(target {nominal}) |")
        parts.append("\n".join(lines))
        parts.append(
            "*Skill vs ruler* is how much better than simply repeating the "
            "origin value the forecast was. A negative figure means the naive "
            "rule was closer, which is a real result and not a reporting error.")

    # ── per issue and target, from the rows ──
    for issue_date in sorted({r.get("issue_date", "") for r in rows}, reverse=True):
        block = _issue_block(issue_date, [r for r in rows
                                          if r.get("issue_date") == issue_date])
        if block:
            parts.append(block)

    if n_pending:
        parts.append(_pending_block(pending_dates))

    if disagreements:
        bullets = "\n".join(
            f"- **{d.get('target')}** h{d.get('horizon')} on "
            f"{d.get('target_date')}: the issue recorded "
            f"{_fmt(float(d.get('artifact_origin_value') or 0))}, the new data "
            f"gives {_fmt(float(d.get('recomputed_from_actuals') or 0))}"
            for d in disagreements[:6])
        parts.append(
            f"⚠️ **The actuals were revised underneath {len(disagreements)} "
            f"already-published forecast(s).** The rows are still scored against "
            f"what was published, because that is what the forecast was "
            f"committed against — but the comparison moved after the fact:\n\n"
            f"{bullets}")

    parts.append(
        "Every figure above is read from the lab's own scorecard, which its "
        "scoring code wrote. Nothing here was recomputed by me.")
    return "\n\n".join(parts)


def _issue_block(issue_date: str, rows: list[dict]) -> str:
    """One published issue's rows, per target, with whatever figures exist."""
    if not rows:
        return ""
    out = [f"### Issue `{issue_date}`"]
    for target in sorted({r.get("target", "") for r in rows}):
        trows = sorted((r for r in rows if r.get("target") == target),
                       key=lambda r: int(_num(r, "horizon") or 0))
        head = ["| Date | Forecast (P50) | Actual | Error | In band |",
                "|---|---:|---:|---:|:--:|"]
        for r in trows:
            p50, y = _num(r, "p50"), _num(r, "y_true")
            err = _num(r, "abs_error")
            inside = _flag(r, "inside_interval")
            head.append(
                f"| {r.get('target_date')} | {_fmt(p50) if p50 is not None else 'not recorded'} | "
                f"{_fmt(y) if y is not None else 'not recorded'} | "
                f"{_fmt(err) if err is not None else 'not recorded'} | "
                f"{'yes' if inside else 'no' if inside is not None else '—'} |")
        out.append(f"**{target}**\n\n" + "\n".join(head))

        extras = _skill_lines(trows)
        if extras:
            out.append(extras)
    return "\n\n".join(out)


def _skill_lines(rows: list[dict]) -> str:
    """Skill comparisons present in these rows, and honest absence for the rest.

    A comparator is reported only where its column exists AND carries a value.
    Where the column exists and is blank, the row's own reason column is quoted
    if it has one — the Lab records, for instance, that the Ops figure is *not
    defined* for a stock target, which is a different statement from "missing".
    """
    lines = []
    for column, label in _SKILL_FIELDS:
        if not any(column in r for r in rows):
            continue
        values = [v for v in (_num(r, column) for r in rows) if v is not None]
        if values:
            mean = sum(values) / len(values)
            lines.append(f"- Skill {label}: **{mean:+.1f}%** across "
                         f"{len(values)} horizon(s).")
            continue
        reason = next((str(r.get("ops_source") or "").strip() for r in rows
                       if column.startswith("skill_vs_ops")
                       and str(r.get("ops_source") or "").strip()), "")
        lines.append(f"- Skill {label}: not recorded"
                     + (f" — {reason}." if reason else "."))
    return "\n".join(lines)


def _pending_block(pending_dates: list) -> str:
    """Target-dates still awaiting truth. Their only record is the run's output."""
    by_target: dict[str, list[str]] = {}
    for entry in pending_dates:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            by_target.setdefault(str(entry[0]), []).append(str(entry[1]))
    if not by_target:
        return ("Some rows are still pending, but the run did not record which "
                "target-dates they were.")
    bullets = "\n".join(
        f"- **{target}** — {len(dates)} date(s), "
        f"{min(dates)} to {max(dates)}"
        for target, dates in sorted(by_target.items()))
    return (f"**Still awaiting truth** (no value for these dates in the data "
            f"yet, so they are neither right nor wrong). Listed as distinct "
            f"target-dates; a date forecast by several issues is pending once "
            f"per issue:\n\n{bullets}")
