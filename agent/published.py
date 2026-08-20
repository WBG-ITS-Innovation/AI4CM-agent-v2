# agent/published.py — the published forward issue, read as a contract.
#
# Scope: `forecasts/published/<issue_date>/` in the Lab. Contract §7. This is
# the immutable record of what was said before the truth existed, and it is a
# different artifact from `backend/forecast_runs/<date>/`, which is a backtest.
# Conflating them is how a backtest prediction gets quoted as a forecast, so
# nothing here reads a run directory and nothing in lab_bridge reads an issue.
#
# Two rules carry the safety of this module, and both are about absence:
#
#   1. ONLY THE LATEST ISSUE IS EVER READ. A target missing from the latest
#      issue is *not published today*, full stop. Falling back to an older
#      issue would answer with numbers the gates have since withdrawn — the
#      committed `2025-08-06` issue still carries full p10/p50/p90 rows for
#      Expenditure and State budget balance over the *same target dates* as
#      the current issue, from before P2 re-decided both as withheld. A
#      fallback would surface exactly those.
#
#   2. ABSENCE IS NEVER A VALUE. A target absent from `manifest.targets` is
#      distinguishable from an unreadable issue, and both are distinguishable
#      from a published one. Callers branch on `Publication.code`, never on
#      whether a number happens to be None.
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: Issue directories are `YYYY-MM-DD`, optionally `-r2` for a same-day
#: re-issue (contract §7). The zero-padded form makes a lexicographic sort a
#: correct date sort, which is the same convention `lab_bridge._DATE_DIR` uses.
_ISSUE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}(-r\d+)?$")

#: Required columns in `forecast.csv`. Contract §7.
_REQUIRED_COLS = ("target", "horizon", "target_date", "origin_date",
                  "p10", "p50", "p90")


@dataclass(frozen=True)
class ForecastRow:
    """One horizon of one target, as published."""

    horizon: int
    origin_date: str
    target_date: str
    p10: float
    p50: float
    p90: float
    origin_value: Optional[float]
    point_model: str
    modelled_as: str

    @property
    def quantiles_ordered(self) -> bool:
        """Contract §7: `p10 <= p50 <= p90` on every row."""
        return self.p10 <= self.p50 <= self.p90


@dataclass(frozen=True)
class Issue:
    """A published issue, or the reason there is none to read."""

    issue_date: str = ""
    path: Optional[Path] = None
    targets: tuple[str, ...] = ()
    horizons: tuple[int, ...] = ()
    target_dates: tuple[str, ...] = ()
    rows: dict[str, tuple[ForecastRow, ...]] = field(default_factory=dict)
    git_sha: str = ""
    git_dirty: Optional[bool] = None
    generated_at: str = ""
    latest_data_date: str = ""
    #: `manifest.data_sha_at_issue` — the hash of the input file this issue was
    #: built from. Read here so the ACTION tier can tell "the data has moved on"
    #: from "you are about to re-issue the same forecast", which are different
    #: situations that look identical from the outside.
    data_sha_at_issue: str = ""
    note: str = ""          # why this issue is unusable; "" when readable
    defects: tuple[str, ...] = ()

    @property
    def is_readable(self) -> bool:
        return self.path is not None and not self.note

    @property
    def max_horizon(self) -> Optional[int]:
        return max(self.horizons) if self.horizons else None

    def publishes(self, target: str) -> bool:
        return any(t.casefold() == target.casefold() for t in self.targets)

    def rows_for(self, target: str) -> tuple[ForecastRow, ...]:
        for name, rows in self.rows.items():
            if name.casefold() == target.casefold():
                return rows
        return ()

    def canonical_target(self, target: str) -> str:
        """The issue's own spelling of `target`, or `target` unchanged."""
        for name in self.targets:
            if name.casefold() == target.casefold():
                return name
        return target


def _read_json(path: Path) -> tuple[Optional[dict], str]:
    if not path.exists():
        return None, f"`{path.name}` is missing from the issue"
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"`{path.name}` could not be read: {exc}"
    if not isinstance(blob, dict):
        return None, f"`{path.name}` is not a JSON object"
    return blob, ""


def _as_float(raw: object) -> Optional[float]:
    """Parse, never cast. A blank cell is absent, not zero."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _read_forecast_csv(path: Path) -> tuple[dict[str, list[ForecastRow]], list[str]]:
    """`{target: [ForecastRow]}` plus any contract defects found."""
    defects: list[str] = []
    out: dict[str, list[ForecastRow]] = {}
    if not path.exists():
        return out, ["`forecast.csv` is missing from the issue"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return out, [f"`forecast.csv` could not be read: {exc}"]

    reader = csv.DictReader(text.splitlines())
    cols = set(reader.fieldnames or ())
    missing = [c for c in _REQUIRED_COLS if c not in cols]
    if missing:
        return out, [f"`forecast.csv` is missing required column(s): "
                     f"{', '.join(missing)}"]
    # Contract §7: a forecast issued before its truth existed cannot have one.
    if "y_true" in cols:
        defects.append("`forecast.csv` carries a `y_true` column, which a "
                       "forward issue must never have")

    for raw in reader:
        target = (raw.get("target") or "").strip()
        p10, p50, p90 = (_as_float(raw.get(k)) for k in ("p10", "p50", "p90"))
        horizon = _as_float(raw.get("horizon"))
        if not target or None in (p10, p50, p90) or horizon is None:
            defects.append("a `forecast.csv` row is missing its target, "
                           "horizon or one of p10/p50/p90, and was dropped")
            continue
        row = ForecastRow(
            horizon=int(horizon),
            origin_date=(raw.get("origin_date") or "").strip(),
            target_date=(raw.get("target_date") or "").strip(),
            p10=p10, p50=p50, p90=p90,
            origin_value=_as_float(raw.get("origin_value")),
            point_model=(raw.get("point_model") or "").strip(),
            modelled_as=(raw.get("modelled_as") or "").strip(),
        )
        if not row.quantiles_ordered:
            defects.append(f"{target} h{row.horizon}: published quantiles are "
                           f"crossed (p10 <= p50 <= p90 does not hold), so the "
                           f"interval is not quotable")
            continue
        if row.origin_date and row.target_date and row.origin_date >= row.target_date:
            defects.append(f"{target} h{row.horizon}: origin_date is not before "
                           f"target_date, so the row is not a forecast")
            continue
        out.setdefault(target, []).append(row)

    for rows in out.values():
        rows.sort(key=lambda r: r.horizon)
    return out, defects


def issue_root(repo: Path) -> Path:
    return Path(repo) / "forecasts" / "published"


def latest_issue(repo: Path) -> Issue:
    """The newest readable published issue, or an `Issue` carrying the reason.

    Deliberately does **not** search backwards past the newest issue that has
    a `manifest.json`. If the newest issue is unreadable the answer is "I
    cannot read today's issue", never a quietly older one — see rule 1.
    """
    root = issue_root(repo)
    if not root.exists():
        return Issue(note=f"the lab has published no forecasts yet — "
                          f"`{root}` does not exist")

    candidates = sorted(
        (p for p in root.iterdir()
         if p.is_dir() and _ISSUE_DIR.match(p.name)
         and (p / "manifest.json").exists()),
        key=lambda p: p.name, reverse=True)
    if not candidates:
        return Issue(note=f"no published issue under `{root}` has a "
                          f"`manifest.json`, so none can be read")

    return read_issue(candidates[0])


def issue_at(repo: Path, issue_date: str) -> Issue:
    """One named issue, for when the caller knows which one it means.

    `latest_issue` is right for answering ("what does the lab say today?") and
    wrong for reporting on a run just finished, which must describe the issue
    it produced even if something else has since landed. Same parsing, same
    defect discipline — only the choice of directory differs.
    """
    path = issue_root(repo) / str(issue_date)
    if not (path / "manifest.json").exists():
        return Issue(issue_date=str(issue_date), path=None,
                     note=f"there is no published issue `{issue_date}` to read")
    return read_issue(path)


def read_issue(path: Path) -> Issue:
    """Parse one issue directory. Callers pick the directory; this reads it."""
    path = Path(path)
    manifest, err = _read_json(path / "manifest.json")
    if manifest is None:
        return Issue(issue_date=path.name, path=path, note=err)

    targets = tuple(str(t).strip() for t in (manifest.get("targets") or [])
                    if str(t).strip())
    horizons = tuple(int(h) for h in (manifest.get("horizons") or [])
                     if isinstance(h, (int, float)))
    target_dates = tuple(str(d) for d in (manifest.get("target_dates") or []))

    rows, defects = _read_forecast_csv(path / "forecast.csv")

    # Provenance is not required to read the numbers, but a consumer must be
    # able to say how reproducible they are. Contract §7: the committed
    # 2025-08-06 issue was built from a dirty tree.
    git_sha, git_dirty, generated_at, latest_data = "", None, "", ""
    prov, prov_err = _read_json(path / "provenance.json")
    if prov is None:
        defects.append(f"provenance is unavailable ({prov_err}), so I cannot "
                       f"say which code version produced these numbers")
    else:
        code = prov.get("code") if isinstance(prov.get("code"), dict) else {}
        git_sha = str(code.get("git_sha") or "")
        raw_dirty = code.get("git_dirty")
        git_dirty = raw_dirty if isinstance(raw_dirty, bool) else None
        generated_at = str(prov.get("generated_at_utc") or "")
        data = prov.get("data") if isinstance(prov.get("data"), dict) else {}
        latest_data = str(data.get("latest_data_date") or "")

    # A target named by the manifest with no rows in the CSV is an
    # inconsistency, not an empty forecast.
    for t in targets:
        if not any(k.casefold() == t.casefold() for k in rows):
            defects.append(f"the manifest lists `{t}` but `forecast.csv` has "
                           f"no rows for it")

    return Issue(
        issue_date=str(manifest.get("issue_date") or path.name),
        path=path,
        targets=targets,
        horizons=tuple(sorted(horizons)),
        target_dates=target_dates,
        rows={k: tuple(v) for k, v in rows.items()},
        git_sha=git_sha,
        git_dirty=git_dirty,
        generated_at=generated_at,
        latest_data_date=latest_data,
        data_sha_at_issue=str(manifest.get("data_sha_at_issue") or ""),
        defects=tuple(defects),
    )


def provenance_sentence(issue: Issue) -> str:
    """One quotable sentence about reproducibility, or the honest gap."""
    if not issue.git_sha:
        return ("This issue does not record the code version that produced "
                "it, so it cannot be exactly reproduced.")
    short = issue.git_sha[:10]
    if issue.git_dirty is True:
        return (f"Produced by code version `{short}`, but from a working tree "
                f"with uncommitted changes — so it cannot be reproduced "
                f"exactly from that version alone.")
    if issue.git_dirty is False:
        return (f"Produced by code version `{short}` from a clean working "
                f"tree, so it can be reproduced exactly.")
    return (f"Produced by code version `{short}`; the issue does not record "
            f"whether the working tree was clean.")
