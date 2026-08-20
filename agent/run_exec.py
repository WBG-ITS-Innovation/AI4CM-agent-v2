# agent/run_exec.py — launching the Lab, and the three things that stop it.
#
# The agent does not forecast. This module starts a subprocess in the LAB's
# interpreter (agent/lab_entry.py), streams what it says, and reports what
# actually happened. Everything here is about the boundary, not the modelling.
#
# THREE GUARDS, AND WHY EACH ONE IS NOT OPTIONAL
# ----------------------------------------------
# 1. THE LOCK. A forward run takes about a hundred seconds and publishes at the
#    end. Two of them racing would have both call `publish()` on the same issue
#    date; the loser gets a FileExistsError, and which one that is depends on
#    scheduling. Streamlit makes this easy to trigger by accident — a browser
#    refresh re-runs the script, and an impatient second click is one gesture.
#    The lock is a file, not a process-local flag, because two browser tabs are
#    two sessions and a session-state flag is invisible across them.
#
# 2. THE UNCHANGED-DATA CHECK. The champion recipes forecast forward from the
#    end of the data. If the data has not moved since the last issue, a new run
#    produces the SAME NUMBERS under a NEW issue date — a second immutable
#    record of a forecast that was already made. That is not a harmless
#    duplicate: the published issues are the track record, and two entries for
#    one forecast makes the record say something false about how often the lab
#    forecast. So the agent says so and asks, rather than quietly minting one.
#
# 3. HONEST FAILURE. `stderr` goes to its own file and is read back verbatim.
#    The temptation with a subprocess is to catch the exception and render
#    something reassuring; a run that failed must say what the Lab said,
#    including the traceback, because the alternative is an agent that reports
#    success it cannot substantiate.
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from . import published as PUB

#: Where the concurrent-run lock lives. Overridable so a test — or a second
#: checkout on the same machine — does not contend with a real run.
LOCK_PATH = Path(os.getenv("AI4CM_RUN_LOCK", "artifacts/run.lock"))

#: A run that has been holding the lock longer than this is assumed dead even
#: if its pid is somehow still alive. The real forward run is ~100s for three
#: targets; an hour is far outside anything legitimate.
STALE_AFTER_SECONDS = 3600


# ─────────────────────────── locating the Lab ───────────────────────────

def lab_python(lab: Path) -> Optional[Path]:
    """The Lab's own interpreter, which is the only one that has the models.

    Deliberately not `sys.executable`. The agent's environment has streamlit
    and pandas; the modelling stack (lightgbm, xgboost, catboost, sklearn)
    lives in the Lab's venv and belongs there. Running the entry script under
    the agent's interpreter would fail on the first import, and — worse — a
    partially-satisfied import could fail somewhere less obvious.
    """
    for candidate in (Path(lab) / "backend" / ".venv" / "bin" / "python",
                      Path(lab) / "backend" / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return None


def entry_script() -> Path:
    """The script the Lab's interpreter runs. Ships with this repo."""
    return Path(__file__).resolve().parent / "lab_entry.py"


def score_entry_script() -> Path:
    """The scoring script the Lab's interpreter runs. Ships with this repo."""
    return Path(__file__).resolve().parent / "lab_score.py"


#: Master switch for writes into the REAL lab tree — installing data, scoring
#: into the tracked scorecard, publishing a live issue. Default OFF.
#:
#: It exists because "the lab is busy" is a real and recurring state: a lab
#: session regenerating artifacts shares every path this agent writes to, and
#: neither side can see the other's work in progress. A flag the operator sets
#: deliberately is the only honest way to represent "the lab is clear" — the
#: agent cannot detect it, and inferring it from a clean `git status` would be
#: wrong, since the lab's data file and published issues are gitignored.
LAB_WRITES_ENV = "AI4CM_ALLOW_LAB_WRITES"


def lab_writes_allowed() -> bool:
    return os.getenv(LAB_WRITES_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def hold_message(what: str) -> str:
    """Why a real-tree write did not happen, and how to permit it."""
    return (f"**I'm holding off {what}.** Writes into the lab's real tree are "
            f"switched off, so I won't install data, write the lab's scorecard, "
            f"or publish a live issue.\n\n"
            f"This is deliberate: a lab session regenerating artifacts writes to "
            f"the same paths I would, and neither of us can see the other's work "
            f"in progress. I can't detect that from here — the lab's data file "
            f"and published issues are gitignored, so a clean `git status` "
            f"proves nothing.\n\n"
            f"Set `{LAB_WRITES_ENV}=1` when the lab is clear, and ask me again. "
            f"Everything else I can do without touching the real tree, and I "
            f"will say exactly where it wrote.")


def sha256_of(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ─────────────────────────── the plan, and consent ───────────────────────────

@dataclass(frozen=True)
class RunPlan:
    """Exactly what a run would do, assembled before anything is launched.

    Exists so the confirmation the user sees is generated from the same values
    the run will use, rather than written by hand alongside them. A
    confirmation that describes a different run than the one that executes is
    worse than no confirmation at all.
    """

    lab: Path
    python: Optional[Path]
    data: Path
    data_sha: str
    targets: tuple[str, ...]
    horizon: int
    latest_data_date: str
    last_issue_date: str
    last_issue_data_sha: str
    blocked: str = ""            # why this cannot run at all; "" when runnable

    @property
    def is_runnable(self) -> bool:
        return not self.blocked

    @property
    def data_unchanged(self) -> bool:
        """True when a new issue would repeat the last one's inputs exactly."""
        return bool(self.data_sha
                    and self.data_sha == self.last_issue_data_sha)


def plan_run(lab: Path, targets: Optional[tuple[str, ...]] = None,
             data: Optional[Path] = None) -> RunPlan:
    """Everything the confirmation step needs, with nothing launched yet."""
    lab = Path(lab)
    data_path = Path(data) if data else (
        lab / "backend" / "data" / "processed" / "master_daily_clean_treasury.csv")

    python = lab_python(lab)
    issue = PUB.latest_issue(lab)

    names = tuple(targets or ())
    if not names:
        from .lab_bridge import official_targets
        found, note = official_targets(lab)
        names = tuple(found)
        if not found:
            return RunPlan(lab, python, data_path, "", (), 5, "", "", "",
                           blocked=f"I can't tell which targets are official: {note}.")

    blocked = ""
    if python is None:
        blocked = (f"the lab's own interpreter (`backend/.venv`) is not at "
                   f"`{lab}`, and it is the only environment with the "
                   f"forecasting models installed — I won't substitute mine")
    elif not data_path.exists():
        blocked = f"the lab's input data file is missing (`{data_path}`)"
    elif not entry_script().exists():
        blocked = f"my own runner script is missing (`{entry_script()}`)"

    return RunPlan(
        lab=lab, python=python, data=data_path,
        data_sha=sha256_of(data_path) if data_path.exists() else "",
        targets=names, horizon=5,
        latest_data_date=issue.latest_data_date if issue.is_readable else "",
        last_issue_date=issue.issue_date if issue.is_readable else "",
        last_issue_data_sha=issue.data_sha_at_issue if issue.is_readable else "",
        blocked=blocked,
    )


def confirmation_text(plan: RunPlan) -> str:
    """What the agent says before it runs anything. Never auto-confirmed.

    States the mode, the targets, the horizon, the data it will use, and what
    publication means — then asks. The brief's rule is "no silent execution",
    and the reason is that this is the one agent action a user cannot undo: a
    published issue is the only record of what was said, so it is never
    rewritten, only added to.
    """
    if not plan.is_runnable:
        return (f"**I can't run a forecast right now.** {plan.blocked}.\n\n"
                f"Nothing has been started.")

    targets = ", ".join(f"**{t}**" for t in plan.targets)
    through = plan.latest_data_date or "an unrecorded date"

    warning = ""
    if plan.data_unchanged:
        warning = (
            f"\n\n⚠️ **The input data has not changed since the last issue.** "
            f"Its checksum is identical to the one recorded in issue "
            f"`{plan.last_issue_date}`, so this run would reproduce the same "
            f"numbers under a new issue date. Published issues are immutable "
            f"and are the lab's track record, so a duplicate would make that "
            f"record overstate how often a forecast was actually made. Say "
            f"**run anyway** if you want it regardless.")
    elif not plan.last_issue_data_sha:
        warning = (
            f"\n\nNote: the last issue does not record the checksum of the "
            f"data it used, so I can't tell you whether the inputs have moved "
            f"since. That is a gap in the artifact, not a clean bill of health.")

    return (
        f"Here is what I would do — **nothing has started yet.**\n\n"
        f"- **Mode:** official. The champion recipe from the lab's registry for "
        f"each target, not a model I picked.\n"
        f"- **Targets:** {targets}\n"
        f"- **Horizon:** {plan.horizon} business days — the only horizon at "
        f"which the lab's benchmark, recipe selection and gates were measured. "
        f"The lab refuses any other.\n"
        f"- **Data:** the lab's master file, through {through}.\n"
        f"- **Publication:** each target is published only if its registry "
        f"verdict allows it. A withheld target is run and then refused by the "
        f"lab's own publishing code — I don't filter the list beforehand, and "
        f"I won't show you numbers for anything it withholds.\n"
        f"- **Retention:** publishing writes to the lab's published directory "
        f"and mirrors to its private vault. I'll verify that on disk "
        f"afterwards rather than take the run's word for it."
        f"{warning}\n\n"
        f"**Shall I run it?**")


# ─────────────────────────── the lock ───────────────────────────

@dataclass(frozen=True)
class LockHolder:
    pid: int = 0
    started_at: float = 0.0
    targets: tuple[str, ...] = ()

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)


def _process_alive(pid: int) -> bool:
    """Whether `pid` still exists. A dead holder must not block forever.

    `os.kill(pid, 0)` raises ProcessLookupError for a dead process and
    PermissionError for one owned by another user — which is alive, so that
    case returns True rather than stealing the lock.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_lock(path: Optional[Path] = None) -> Optional[LockHolder]:
    """The live holder of the lock, or None if it is free.

    A lock file whose process is gone, or which is older than
    `STALE_AFTER_SECONDS`, is treated as free: a crashed run must not lock the
    agent out permanently, since the only recovery would be deleting a file the
    user has no reason to know about.
    """
    path = Path(path or LOCK_PATH)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                      # unreadable lock is not a live run
    holder = LockHolder(pid=int(blob.get("pid") or 0),
                        started_at=float(blob.get("started_at") or 0.0),
                        targets=tuple(blob.get("targets") or ()))
    if not _process_alive(holder.pid) or holder.age_seconds > STALE_AFTER_SECONDS:
        return None
    return holder


def acquire_lock(targets: tuple[str, ...] = (),
                 path: Optional[Path] = None) -> tuple[bool, Optional[LockHolder]]:
    """`(True, None)` on success, `(False, holder)` when a run is in flight.

    Written with `O_CREAT | O_EXCL` so two processes arriving together cannot
    both succeed. A stale file is removed first — `read_lock` has already
    decided it is stale — which leaves a small window, but the exclusive create
    closes it: the loser of that race gets FileExistsError and is told a run is
    in flight, which is the correct answer either way.
    """
    path = Path(path or LOCK_PATH)
    holder = read_lock(path)
    if holder is not None:
        return False, holder

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            return False, read_lock(path)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False, read_lock(path)
    except OSError:
        return False, None
    with os.fdopen(fd, "w") as fh:
        json.dump({"pid": os.getpid(), "started_at": time.time(),
                   "targets": list(targets)}, fh)
    return True, None


def release_lock(path: Optional[Path] = None) -> None:
    try:
        Path(path or LOCK_PATH).unlink()
    except OSError:
        pass


def lock_message(holder: LockHolder) -> str:
    scope = (", ".join(holder.targets) if holder.targets
             else "every champion target")
    return (f"**A forecast run is already in progress** — started "
            f"{int(holder.age_seconds)}s ago (process {holder.pid}), covering "
            f"{scope}. I won't start a second one: two runs publishing to the "
            f"same issue date would collide, and which one survived would come "
            f"down to timing. Wait for it to finish and ask me again.")


# ─────────────────────────── running it ───────────────────────────

@dataclass
class RunOutcome:
    """What the subprocess actually did, as distinct from what it claimed."""

    returncode: Optional[int] = None
    events: list = field(default_factory=list)   # parsed NDJSON, in order
    logs: list = field(default_factory=list)     # everything else on stdout
    stderr: str = ""
    launch_error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.launch_error

    def first(self, event: str) -> Optional[dict]:
        return next((e for e in self.events if e.get("event") == event), None)

    def all(self, event: str) -> list:
        return [e for e in self.events if e.get("event") == event]


def stream_run(plan: RunPlan, published_root: Optional[Path] = None,
               vault_root: Optional[Path] = None,
               timeout: int = 3600) -> Iterator[tuple[str, object]]:
    """Run the Lab and yield progress as it happens.

    Yields `("event", dict)` for each NDJSON line, `("log", str)` for anything
    else the Lab's libraries print — the Lab's config module writes `[config]`
    lines to stdout for stock targets, and a strict parser would either crash
    on them or silently drop them — and finally `("done", RunOutcome)`.

    The caller gets the outcome object rather than a boolean because "did it
    work" is not one question: the process can exit 0 having refused every
    target, and that is a success whose report says nothing was published.
    """
    outcome = RunOutcome()
    if not plan.is_runnable:
        outcome.launch_error = plan.blocked
        yield ("done", outcome)
        return

    cmd = [str(plan.python), str(entry_script()), "--lab", str(plan.lab),
           "--data", str(plan.data)]
    for target in plan.targets:
        cmd += ["--target", target]
    if published_root is not None:
        cmd += ["--published-root", str(published_root)]
    if vault_root is not None:
        cmd += ["--vault-root", str(vault_root)]

    yield from _stream_process(cmd, plan.lab, outcome, timeout)


def stream_score(lab: Path, python: Path, data: Path,
                 published_root: Optional[Path] = None,
                 scorecard: Optional[Path] = None,
                 timeout: int = 3600) -> Iterator[tuple[str, object]]:
    """Run the Lab's scorer and yield progress. Same contract as `stream_run`.

    `published_root` and `scorecard` are passed through to
    `agent/lab_score.py`. Omitting BOTH scores the lab's real issues into its
    real, git-tracked scorecard — which is why the caller checks
    `lab_writes_allowed()` before it gets here.
    """
    outcome = RunOutcome()
    if python is None or not Path(python).exists():
        outcome.launch_error = (
            "the lab's own interpreter (`backend/.venv`) was not found, and it "
            "is the only environment that can run the lab's scorer")
        yield ("done", outcome)
        return
    if not score_entry_script().exists():
        outcome.launch_error = f"my scoring script is missing ({score_entry_script()})"
        yield ("done", outcome)
        return

    cmd = [str(python), str(score_entry_script()), "--lab", str(lab),
           "--data", str(data)]
    if published_root is not None:
        cmd += ["--published-root", str(published_root)]
    if scorecard is not None:
        cmd += ["--scorecard", str(scorecard)]

    yield from _stream_process(cmd, Path(lab), outcome, timeout)


def _stream_process(cmd: list, cwd: Path, outcome: "RunOutcome",
                    timeout: int) -> Iterator[tuple[str, object]]:
    """Launch, stream stdout, capture stderr separately, report the outcome.

    Shared by both doors so there is one implementation of the thing that is
    actually hard here — reading a live pipe without deadlocking, keeping
    stderr distinguishable, and not reading the exit code before the stream is
    drained. A second copy of this would be a second place for those to be
    subtly wrong.
    """
    # stderr to its own file rather than merged into stdout: merging would put
    # a traceback in the middle of the NDJSON stream, and the whole point of
    # keeping it separate is to be able to show the real error verbatim.
    err_file = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed below
        mode="w+", suffix=".stderr", delete=False)
    try:
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                stderr=err_file, text=True, bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except OSError as exc:
            outcome.launch_error = f"could not start the lab's interpreter: {exc}"
            yield ("done", outcome)
            return

        started = time.time()
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parsed = _parse_event(line)
            if parsed is None:
                outcome.logs.append(line)
                yield ("log", line)
            else:
                outcome.events.append(parsed)
                yield ("event", parsed)
            if time.time() - started > timeout:
                proc.kill()
                outcome.launch_error = (
                    f"the run exceeded {timeout}s and was stopped")
                break
        outcome.returncode = proc.wait()
    finally:
        try:
            err_file.flush()
            err_file.seek(0)
            outcome.stderr = err_file.read()
        except OSError:
            pass
        err_file.close()
        try:
            os.unlink(err_file.name)
        except OSError:
            pass

    yield ("done", outcome)


def _parse_event(line: str) -> Optional[dict]:
    """One NDJSON event, or None when the line is something else.

    Requires both that the line parses AND that it carries an `event` key, so
    a stray JSON blob printed by a library is treated as a log line rather
    than as a result the agent will act on.
    """
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        blob = json.loads(text)
    except ValueError:
        return None
    if not isinstance(blob, dict) or "event" not in blob:
        return None
    return blob


def failure_message(outcome: RunOutcome) -> str:
    """The real error, verbatim. Never a reassuring paraphrase.

    Shows the Lab's own stderr because a forecast run that failed is a fact the
    user has to act on, and a summary written by the agent is a summary of
    something it did not understand.
    """
    if outcome.launch_error:
        head = f"**The forecast run could not start.** {outcome.launch_error}."
    else:
        head = (f"**The forecast run failed** (exit code "
                f"{outcome.returncode}).")

    fatal = outcome.first("fatal")
    if fatal:
        head += f"\n\nThe lab reported: {fatal.get('reason')}"

    failures = outcome.all("target_failed")
    if failures:
        bullets = "\n".join(
            f"- **{f.get('target')}** failed at the {f.get('stage')} stage: "
            f"`{f.get('error')}`" for f in failures)
        head += f"\n\n{bullets}"

    tail = (outcome.stderr or "").strip()
    if tail:
        lines = tail.splitlines()
        shown = "\n".join(lines[-40:])
        head += (f"\n\nThe lab's own error output"
                 f"{f' (last 40 of {len(lines)} lines)' if len(lines) > 40 else ''}:\n\n"
                 f"```\n{shown}\n```")
    if not tail and not fatal and not failures:
        head += ("\n\nThe lab produced no error output, which is itself odd — "
                 "I have nothing further to tell you about the cause, and I "
                 "won't invent one.")
    return head + "\n\nNothing was published."
