# agent/lab_bridge.py — the agent's only window into the AI4CM lab.
#
# Design rule (M-1 contract): the agent NEVER re-derives trust. It reads
# gate_passed / gate_reasons from the lab's SUMMARY.json, written by
# AI4CM/scripts/daily_summary.py, so the chatbot can never disagree with
# the audited pipeline about which models are trustworthy.
#
# Since the Lab published a formal artifact contract
# (AI4CM/docs/AGENT_ARTIFACT_CONTRACT.md) every field is read through
# agent/contract.py rather than out of a raw dict. That module is the only
# place allowed to touch `summary[...]`; it exists because `.get(k, 0)` and
# plain truthiness turn "absent" into "zero" and "never verified" into
# "failed", which the contract's §0 forbids in as many words.
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from agent.contract import (
    CoverageView, FamilyView, Gate, LeaderboardView, MetricsView, Presence,
    RunView, Value, known, read_coverage, read_leaderboard, read_metrics_long,
    read_predictions, read_summary, unknown,
)

# Where to look for the lab, in order:
#   1. AI4CM_RUNS_ROOT env var (points directly at .../forecast_runs)
#   2. AI4CM_REPO env var (points at the AI4CM repo root)
#   3. A sibling checkout: ../AI4CM next to this repo
_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    if os.getenv("AI4CM_RUNS_ROOT"):
        roots.append(Path(os.environ["AI4CM_RUNS_ROOT"]))
    if os.getenv("AI4CM_REPO"):
        roots.append(Path(os.environ["AI4CM_REPO"]) / "backend" / "forecast_runs")
    here = Path(__file__).resolve().parent.parent
    roots.append(here.parent / "AI4CM" / "backend" / "forecast_runs")
    return roots


@dataclass
class LabRun:
    run_dir: Path
    view: RunView                      # SUMMARY.json read through the contract
    summary_text: str = ""             # SUMMARY.txt, verbatim
    history: Optional[pd.Series] = None            # actuals, for charting
    predictions: dict = field(default_factory=dict)   # family -> DataFrame
    leaderboards: dict = field(default_factory=dict)  # family -> LeaderboardView
    metrics: dict = field(default_factory=dict)       # family -> MetricsView
    coverage: dict = field(default_factory=dict)      # family -> CoverageView
    read_defects: list = field(default_factory=list)  # per-file contract departures
    read_notes: list = field(default_factory=list)    # per-file anticipated ambiguities

    @property
    def summary(self) -> dict:
        """The raw dict, for provenance display only.

        Deliberately *not* the way to read a field: everything the Agent
        says must come off `self.view`, which knows what is unknown.
        """
        return self.view.raw

    # ── Convenience views used by the UI and the chat ──
    @property
    def run_date(self) -> str:
        # Contract §1: run_date is required, but if it is missing the folder
        # name is the sanctioned fallback — and it is labelled as one.
        return (str(self.view.run_date.value) if self.view.run_date.is_known
                else self.run_dir.name)

    @property
    def families(self) -> list[FamilyView]:
        return list(self.view.families)

    @property
    def presentable_families(self) -> list[FamilyView]:
        """Families that may be shown as a clean result.

        Passed the gate AND run_status SUCCESS. A `gate_passed: null` family
        is not here (never verified is not a pass) and neither is it in
        `withheld_families` (never verified is not a failure either).
        """
        return self.view.presentable_families

    @property
    def withheld_families(self) -> list[FamilyView]:
        return self.view.withheld_families

    @property
    def unverified_families(self) -> list[FamilyView]:
        return self.view.unverified_families

    def champion(self) -> Optional[FamilyView]:
        """Highest *known* skill among champion-eligible families.

        Eligible means: gate PASSED, run_status SUCCESS, a real skill figure,
        and not a reference baseline. A family with no skill figure is not
        ranked last — it is not ranked at all, because UNKNOWN is not a
        score. If nothing is eligible there is no champion today.
        """
        return self.view.champion()

    def family(self, name: str) -> Optional[FamilyView]:
        return self.view.family(name)

    def family_predictions(self, name: str) -> Optional[pd.DataFrame]:
        return self.predictions.get(str(name).upper())

    def family_leaderboard(self, name: str) -> Optional[LeaderboardView]:
        return self.leaderboards.get(str(name).upper())

    def family_coverage(self, name: str) -> CoverageView:
        return self.coverage.get(str(name).upper(), CoverageView(family=str(name)))

    @property
    def all_defects(self) -> list[str]:
        """Every contract departure found, in the summary and in the files."""
        return self.view.all_defects + list(self.read_defects)

    @property
    def all_notes(self) -> list[str]:
        """Ambiguities the contract anticipates — real, but not departures."""
        return self.view.all_notes + list(self.read_notes)


def find_latest_run(runs_root: Optional[Path] = None) -> Optional[Path]:
    """Newest dated run folder that contains a SUMMARY.json (M-1 output)."""
    roots = [runs_root] if runs_root else _candidate_roots()
    for root in roots:
        if root is None or not root.exists():
            continue
        dated = sorted(
            (d for d in root.iterdir() if d.is_dir() and _DATE_DIR.match(d.name)),
            reverse=True,
        )
        for d in dated:
            if (d / "SUMMARY.json").exists():
                return d
    return None


def _load_history(view: RunView, run_dir: Path) -> Optional[pd.Series]:
    """Actual observed series, for plotting next to the forecast.

    The lab records only the data file's *name* in SUMMARY.json — and in
    fact no committed SUMMARY.json records it at all. When it is absent we
    return None and the caller says so; we do not go looking for a
    plausible-looking CSV and present it as the run's input.
    """
    if not (view.data_file.is_known and view.target.is_known):
        return None
    candidate = run_dir.parent.parent / "data" / "processed" / str(view.data_file.value)
    if not candidate.exists():
        return None
    try:
        df = pd.read_csv(candidate)
        date_col = next((c for c in df.columns if c.lower() == "date"), df.columns[0])
        target = view.target.value
        if target not in df.columns:
            return None
        s = (df[[date_col, target]].dropna()
             .assign(**{date_col: pd.to_datetime(df[date_col], errors="coerce")})
             .dropna(subset=[date_col])
             .set_index(date_col)[target].astype(float).sort_index())
        return s
    except (OSError, ValueError, KeyError):
        return None


def _family_dir(run_dir: Path, name: str) -> Optional[Path]:
    """The family's folder, tolerating the Lab's nested `<fam>/daily/` layout."""
    d = run_dir / str(name).lower()
    return d if d.exists() else None


def load_run(run_dir: Path) -> Optional[LabRun]:
    """Load everything the UI needs from one lab run directory.

    Returns None only when `SUMMARY.json` is unreadable — a run whose
    *files* are defective still loads, with the defects attached, because
    the contract's whole point is that a consumer reports them rather than
    silently dropping the artifact.
    """
    run_dir = Path(run_dir)
    view = read_summary(run_dir)
    if not view.is_readable:
        return None

    run = LabRun(run_dir=run_dir, view=view)
    txt = run_dir / "SUMMARY.txt"
    run.summary_text = txt.read_text(encoding="utf-8") if txt.exists() else ""
    run.history = _load_history(view, run_dir)

    for fam in view.families:
        key = fam.name.upper()
        fam_dir = _family_dir(run_dir, fam.name)
        if fam_dir is None:
            run.read_defects.append(
                f"{fam.name}: SUMMARY.json lists this family but there is no "
                f"`{fam.name.lower()}/` folder in the run")
            continue

        preds = sorted(fam_dir.rglob("predictions_long.csv"))
        if preds:
            df, defects, notes = read_predictions(preds[0], fam.name)
            if df is not None:
                run.predictions[key] = df
            run.read_defects.extend(f"{fam.name}: {d}" for d in defects)
            run.read_notes.extend(f"{fam.name}: {n}" for n in notes)

        lb_files = sorted(fam_dir.rglob("leaderboard.csv"))
        lb_view: Optional[LeaderboardView] = None
        if lb_files:
            lb_view = read_leaderboard(lb_files[0], fam.name,
                                       summary_target=view.target,
                                       summary_horizon=view.horizon)
            run.leaderboards[key] = lb_view
            run.read_defects.extend(f"{fam.name}: {d}"
                                    for d in lb_view.defects + lb_view.fatal)
            run.read_notes.extend(f"{fam.name}: {n}" for n in lb_view.notes)

        m_files = sorted(fam_dir.rglob("metrics_long.csv"))
        m_view: Optional[MetricsView] = None
        if m_files:
            m_view = read_metrics_long(m_files[0], fam.name)
            run.metrics[key] = m_view
            run.read_defects.extend(f"{fam.name}: {d}"
                                    for d in m_view.defects + m_view.fatal)
            run.read_notes.extend(f"{fam.name}: {n}" for n in m_view.notes)

        cov = read_coverage(fam.name, leaderboard=lb_view, metrics=m_view,
                            extras=_coverage_extras(fam_dir))
        run.coverage[key] = cov
        run.read_defects.extend(f"{fam.name}: {d}" for d in cov.defects)

        # Contract §2, known-not-fixed: the persistence baseline is in the
        # leaderboard but has no rows in predictions_long. A consumer joining
        # the two on `model` silently loses it — so we say it out loud.
        if lb_view is not None and lb_view.is_readable and key in run.predictions:
            preds_models = {str(m).strip()
                            for m in run.predictions[key].get("model", [])}
            lost = [b for b in lb_view.baseline_models if b not in preds_models]
            if lost:
                run.read_notes.append(
                    f"{fam.name}: baseline row(s) {lost} appear in the "
                    f"leaderboard but have no rows in predictions_long.csv — "
                    f"they are derived from origin_value, not predicted")
    return run


def _coverage_extras(fam_dir: Path) -> dict:
    """Coverage side-channel fields the Lab may write beside the CSVs.

    Contract §5 names `coverage_nominal`, `coverage_key`,
    `legacy_coverage_key_omitted` and `coverage_unavailable_reason`. They
    live in the family's own JSON, so we look there — and shrug, rather than
    invent, when the file is absent.
    """
    out: dict = {}
    for name in ("run.json", "integrity_report.json"):
        for path in sorted(fam_dir.rglob(name)):
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(blob, dict):
                for key in ("coverage_nominal", "coverage_key",
                            "legacy_coverage_key_omitted",
                            "coverage_unavailable_reason",
                            "coverage_lower_quantile", "coverage_upper_quantile"):
                    if key in blob and key not in out:
                        out[key] = blob[key]
    return out


def load_latest() -> Optional[LabRun]:
    latest = find_latest_run()
    return load_run(latest) if latest else None


# ── Orchestration: the agent drives the lab, it never models on its own ──

def repo_root(run_dir: Path) -> Path:
    """backend/forecast_runs/<date> -> AI4CM repo root."""
    return run_dir.parent.parent.parent


def _data_file_path(view: RunView, run_dir: Path) -> Optional[Path]:
    if not view.data_file.is_known:
        return None
    p = run_dir.parent.parent / "data" / "processed" / str(view.data_file.value)
    return p if p.exists() else None


def available_targets(run: "LabRun") -> list[str]:
    """Numeric columns in the lab's master data file, if the run names one.

    No committed SUMMARY.json records `data_file`, so this is usually empty.
    That is a real gap and it is left visible: this function does not go
    looking for a plausible CSV, and it is no longer the only source the
    target menu has (see `target_choices`).
    """
    p = _data_file_path(run.view, run.run_dir)
    if p is None:
        return []
    try:
        df = pd.read_csv(p, nrows=50)
        return [c for c in df.columns
                if c.lower() != "date" and pd.api.types.is_numeric_dtype(df[c])]
    except (OSError, ValueError):
        return []


# ── Official targets: the registry, not a past run's input file ──
#
# The bug this replaces: the "Run a new forecast" menu asked what the *lab can
# forecast* and answered it from `SUMMARY.json.data_file` — a field the Lab's
# own contract documents as "absent on every committed artifact". One source,
# known-empty, so the menu always degraded to a single entry and the app
# offered only Revenues while the Lab has champion recipes for three targets.
#
# Two distinct questions were being conflated:
#   * "what did this run forecast?"  — a property of the artifact. One target.
#   * "what can the lab forecast?"   — a property of the LAB. Not in the run.
#
# The Lab's authority on the second is `registry/recipes.json`: one champion
# recipe per official target, version-controlled, always present, and the same
# file `backend/forecast_modes.recipe_status()` consults before it will issue
# an official forecast. So that is what is read here.

def official_targets(repo: Path) -> tuple[list[str], str]:
    """`(targets, note)` — every target with a champion recipe in the Lab.

    `note` is empty on success and carries the reason on failure. An
    unreadable registry yields no targets and a reason, never a guessed list.
    """
    path = Path(repo) / "registry" / "recipes.json"
    if not path.exists():
        return [], (f"the lab's target registry was not found at `{path}`, so "
                    f"I can't list the targets it has champion recipes for")
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"the lab's target registry could not be read: {exc}"
    recipes = blob.get("recipes") if isinstance(blob, dict) else None
    if not isinstance(recipes, list):
        return [], ("the lab's target registry has no `recipes` list, so I "
                    "can't tell which targets are official")
    targets: list[str] = []
    for r in recipes:
        t = r.get("target") if isinstance(r, dict) else None
        if isinstance(t, str) and t.strip() and t.strip() not in targets:
            targets.append(t.strip())
    if not targets:
        return [], "the lab's target registry lists no targets"
    return targets, ""


@dataclass(frozen=True)
class TargetMenu:
    """What the target picker may offer, and where each entry came from."""

    targets: list[str]          # everything offerable, official first
    official: list[str]         # has a champion recipe in the lab's registry
    source: str                 # "registry" | "run" | "none"
    note: str                   # why the menu is what it is; "" when complete

    @property
    def enumerated(self) -> bool:
        """True when the menu is the lab's real set, not one run's leftovers."""
        return self.source == "registry"

    def is_official(self, target: str) -> bool:
        return str(target) in self.official


def target_choices(run: "LabRun") -> TargetMenu:
    """The target menu, from the lab's registry, with the run's own target kept.

    The run's target is always offered even if the registry does not list it:
    a run that happened is evidence the lab can forecast that series, and
    dropping it would make the app unable to repeat its own last run.
    """
    official, note = official_targets(repo_root(run.run_dir))
    own = str(run.view.target.value) if run.view.target.is_known else None

    if official:
        targets = list(official) + ([own] if own and own not in official else [])
        extra = ("" if not own or own in official else
                 f" This run's own target, `{own}`, has no champion recipe in "
                 f"the registry — it is offered because the run used it, not "
                 f"because it is official.")
        return TargetMenu(targets, official, "registry", extra.strip())

    if own:
        return TargetMenu([own], [], "run",
                          f"{note} I'm offering only this run's own target, "
                          f"`{own}`, because that is the one thing the artifact "
                          f"does record.")
    return TargetMenu([], [], "none",
                      f"{note} This run does not record a target either, so I "
                      f"have nothing to offer.")


#: The families the Agent's own DEMO mode can run locally, via agent/tools.py.
#: Not a picture of the Lab's shelf, and it must not be read as one: it is a
#: capability list for this repo. It reaches app.py's LLM context under the name
#: `runnable_families`, where the system prompt separately forbids quoting any
#: model count from anything but `CONTEXT.model_composition`.
KNOWN_FAMILIES = ["A_STAT", "B_ML", "E_QUANTILE", "C_DL"]

#: Human names for the lab's internal family codenames, used everywhere the
#: agent speaks, so users aren't expected to know that B_ML means "the
#: machine-learning family".
#:
#: A LOOKUP, not an inventory. `plain.describe_run` and the Learn tab read which
#: families exist from the run's own artifacts and label them from here, so a
#: family the Lab adds appears without this dict changing. F_FOUNDATION is
#: listed because it is enumerable in the Lab's registry, not because it can
#: appear in a daily run. It cannot: the Lab keeps it out of the daily family
#: list and it writes no run artifacts. If it ever does appear, a reader gets a
#: description instead of a bare code.
FAMILY_LABELS = {
    "A_STAT": "classical statistical models (ETS, ARIMA-style)",
    "B_ML": "machine learning (Lasso, Ridge, tree ensembles, gradient boosting)",
    "E_QUANTILE": "quantile models with uncertainty bands",
    "C_DL": "deep learning (neural networks — much slower to train)",
    "F_FOUNDATION": ("pretrained forecasters, exploratory only. They never "
                     "compete on a target and never produce a daily forecast"),
}


def list_runs(repo: Path) -> list[tuple[Path, RunView]]:
    """All dated run folders (newest first), each read through the contract.

    Unreadable runs are still listed — with their fatal reason attached —
    because a run that silently vanishes from the history is a worse answer
    than one shown as unreadable.
    """
    root = repo / "backend" / "forecast_runs"
    out: list[tuple[Path, RunView]] = []
    if not root.exists():
        return out
    for d in sorted((p for p in root.iterdir()
                     if p.is_dir() and _DATE_DIR.match(p.name)), reverse=True):
        if (d / "SUMMARY.json").exists():
            out.append((d, read_summary(d)))
    return out


def run_lab_stream(repo: Path, target: str, horizon: int, families: list[str]):
    """Like run_lab, but yields log lines as they happen so the UI can show
    a live trace of what the lab is doing. Yields ('line', text) events and
    finally ('done', returncode)."""
    import subprocess
    script = repo / "scripts" / "run_daily_forecast.sh"
    if not script.exists():
        yield ("line", f"Runner not found: {script}")
        yield ("done", 1)
        return
    env = dict(os.environ,
               FAMILIES=" ".join(families),
               TG_TARGET=target,
               TG_HORIZON=str(int(horizon)))
    proc = subprocess.Popen(["bash", str(script)], cwd=str(repo), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        yield ("line", line.rstrip("\n"))
    proc.wait()
    yield ("done", proc.returncode)


def run_lab(repo: Path, target: str, horizon: int,
            families: list[str], timeout: int = 3600) -> tuple[bool, str]:
    """Launch the lab's own daily runner with the requested settings.

    All modeling, preprocessing, baselines, and integrity checks happen in
    the lab's audited code — the agent only sets the dials and reads back
    the gated summary. Returns (success, combined log text).
    """
    import subprocess
    script = repo / "scripts" / "run_daily_forecast.sh"
    if not script.exists():
        return False, f"Runner not found: {script}"
    env = dict(os.environ,
               FAMILIES=" ".join(families),
               TG_TARGET=target,
               TG_HORIZON=str(int(horizon)))
    try:
        proc = subprocess.run(["bash", str(script)], cwd=str(repo), env=env,
                              capture_output=True, text=True, timeout=timeout)
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return proc.returncode == 0, log
    except subprocess.TimeoutExpired:
        return False, f"Run exceeded {timeout}s and was stopped."



EXPLANATIONS = {
    "gate": ("Every model family must pass a quality gate before its results "
             "are shown as trustworthy. The gate fails if the run's own "
             "integrity checks failed (run_status), or if leakage or shift "
             "flags fired. A withheld result is still visible for diagnosis — "
             "it is just never presented as a clean winner."),
    "skill": ("Skill vs persistence answers: how much better is this model "
              "than simply assuming 'the next days look like today'? 0% means "
              "no better than that naive rule; higher is better. It keeps a "
              "small-looking error number honest."),
    "leakage": ("Leakage means the model accidentally saw information from "
                "the future during training — its test scores look great but "
                "mean nothing. The lab checks this by shuffling and by "
                "verifying no prediction's origin date is on or after its "
                "target date."),
    "shift": ("A shift flag means the 'forecast' is suspiciously similar to "
              "the real series just moved a few days — i.e. the model may "
              "only be copying yesterday's value, not predicting."),
    "stale": ("Stale data means the newest observation is older than the "
              "allowed gap, so today's forecast would be based on out-of-date "
              "inputs."),
    # No counts here, on purpose. This dict is handed to the LLM as `glossary`,
    # so a number written here is a number the model will happily quote. The
    # counts belong to the Lab's derived `client_framing`, read per-run through
    # the contract; this entry explains the *kinds* only, which never go stale.
    "models": ("Several kinds of entry appear on a leaderboard and they are "
               "not comparable: point competitors (machine-learning, deep-"
               "learning and classical statistical models) are ranked against "
               "each other; quantile methods produce prediction intervals "
               "rather than competing on point accuracy; and reference "
               "baselines exist to be beaten, not to win. There is no honest "
               "single headline count, because adding those together loses the "
               "distinction that matters. How many of each competed on a given "
               "run is recorded — when it is recorded at all — in that run's "
               "own artifacts, and is never quoted from memory."),
    "unknown": ("Some fields are simply not recorded in a run's artifacts. "
                "The lab's contract is explicit that a missing value must be "
                "read as UNKNOWN — never as zero, never as a pass, never as a "
                "failure — so where you see 'not recorded' that is what the "
                "artifact says, not something I failed to look up."),
}
