# agent/contract.py — the Agent's defensive reader for the Lab's published
# artifact contract (AI4CM/docs/AGENT_ARTIFACT_CONTRACT.md).
#
# The contract's §0 is the whole reason this module exists:
#
#   "a consumer should treat a missing or `n/a` value as UNKNOWN — never as
#    zero, never as a pass, and never as a failure."
#
# Python's `dict.get(k, 0)` and truthiness do exactly the forbidden thing:
# they turn absence into zero and null into false. So nothing above this
# module is allowed to touch a raw artifact dict. Every field arrives here
# and leaves as a `Value` that knows whether it is known, and if not, why.
#
# Design rules:
#   1. Absence is never silently repaired. It is carried, labelled, and
#      rendered as "not recorded" by agent/plain.py.
#   2. Numbers are parsed, never cast. The Lab publishes horizon as the
#      string "5" and skill as "27.51%" or "n/a (not produced)".
#   3. Structure is detected, never assumed. Three leaderboard schemas and
#      two `metrics_long.csv` shapes share their filenames.
#   4. Anything that cannot be parsed becomes a visible defect on the view,
#      not an exception and not a guess.
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


# ─────────────────────────── presence & values ───────────────────────────

class Presence(str, Enum):
    """Why a value is (or is not) usable. Contract §0."""

    KNOWN = "known"
    ABSENT = "absent"                # the key is not in the artifact at all
    NULL = "null"                    # the key is present and explicitly null
    NOT_PRODUCED = "not_produced"    # sentinel, e.g. "n/a (not produced)"
    UNPARSEABLE = "unparseable"      # present, but not the declared type


#: Sentinels the Lab writes in place of a number. Contract §1 `skill_pct`.
_NA_MARKERS = re.compile(
    r"^\s*(n/?a|none|null|nan|not\s+produced|not\s+reported|-{1,2}|)\s*"
    r"(\(.*\))?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Value:
    """A field that knows whether it is known — and if not, why not.

    Never compare a `Value` to a number and never let one reach a template.
    Ask `.is_known` first, or hand it to `agent.plain`.
    """

    presence: Presence
    value: Any = None
    raw: Any = None
    note: str = ""

    @property
    def is_known(self) -> bool:
        return self.presence is Presence.KNOWN

    @property
    def is_unknown(self) -> bool:
        return not self.is_known

    def or_none(self) -> Any:
        """The value if known, else None. For arithmetic that tolerates None."""
        return self.value if self.is_known else None

    def __bool__(self) -> bool:  # pragma: no cover - guard, not logic
        raise TypeError(
            "Value is not truthy — absence must not collapse to False. "
            "Use .is_known, or render it through agent.plain.")


def known(value: Any, raw: Any = None) -> Value:
    return Value(Presence.KNOWN, value, raw if raw is not None else value)


def unknown(presence: Presence, raw: Any = None, note: str = "") -> Value:
    return Value(presence, None, raw, note)


# ─────────────────────────── scalar parsing ───────────────────────────

def parse_number(raw: Any, *, absent_note: str = "") -> Value:
    """Parse a number the Lab may have published as a string.

    Handles `"5"`, `"27.51%"`, `"1,234,567"`, `36314513.01`, and every
    sentinel in `_NA_MARKERS`. Contract §1: "Parse, do not cast blindly".
    """
    if raw is None:
        return unknown(Presence.ABSENT, raw, absent_note or "not recorded")
    if isinstance(raw, bool):
        return unknown(Presence.UNPARSEABLE, raw, "boolean where a number was expected")
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
            return unknown(Presence.UNPARSEABLE, raw, "not a finite number")
        return known(float(raw), raw)

    text = str(raw).strip()
    if _NA_MARKERS.match(text):
        # "n/a (not produced)" — the contract is explicit that this single
        # marker covers several causes and the consumer cannot tell which.
        return unknown(Presence.NOT_PRODUCED, raw,
                       "published as a not-produced marker; the artifact does "
                       "not record which cause applies")
    cleaned = text.replace(",", "").replace("%", "").strip()
    # Strip a currency-ish or unit prefix but never a digit.
    cleaned = re.sub(r"^[^\d\-+.]+", "", cleaned)
    try:
        number = float(cleaned)
    except (TypeError, ValueError):
        return unknown(Presence.UNPARSEABLE, raw,
                       f"published as {text!r}, which is not a number")
    if math.isnan(number) or math.isinf(number):
        return unknown(Presence.UNPARSEABLE, raw, "not a finite number")
    return known(number, raw)


def parse_percent(raw: Any) -> Value:
    """`skill_pct`: `"27.51%"` **or** `"n/a (not produced)"`. Contract §1.

    Returns percentage points (27.51), not a fraction.
    """
    return parse_number(raw, absent_note="no skill figure was published")


def parse_int(raw: Any) -> Value:
    """`horizon` is a *string* in `SUMMARY.json`. Contract §1."""
    v = parse_number(raw)
    if not v.is_known:
        return v
    if abs(v.value - round(v.value)) > 1e-9:
        return unknown(Presence.UNPARSEABLE, raw,
                       f"published as {raw!r}, which is not a whole number")
    return known(int(round(v.value)), raw)


#: `best_model` embeds a display-formatted number in prose:
#: `"RandomForest (MAE 31,685,490)"`. Contract §1 hazard 2.
_BEST_MODEL_RE = re.compile(
    r"^\s*(?P<name>.+?)\s*\(\s*(?P<metric>[A-Za-z_][\w@]*)\s+"
    r"(?P<number>[-+]?[\d,]*\.?\d+(?:[eE][-+]?\d+)?)\s*\)\s*$")

#: B_ML decorates its baseline `⚡ Persistence (baseline)`. Contract §1 hazard 3.
_DECORATION_RE = re.compile(
    r"[\U0001F300-\U0001FAFF☀-➿️⬀-⯿]")

_BASELINE_RE = re.compile(r"\b(baseline|persistence|naive|na[iï]ve|ops)\b",
                          re.IGNORECASE)


def strip_decoration(name: Any) -> str:
    """Remove emoji/pictographs from a model name used as a join key."""
    if name is None:
        return ""
    return _DECORATION_RE.sub("", str(name)).strip()


def is_baseline_model(name: Any) -> bool:
    """Reference baselines are not competitors and can never be champion."""
    return bool(_BASELINE_RE.search(strip_decoration(name)))


@dataclass(frozen=True)
class BestModel:
    """`best_model` split back into the two facts it conflates."""

    name: Value
    metric_name: Value       # e.g. "MAE" — which metric the number is
    metric_value: Value
    raw: Any = None
    withheld_marker: bool = False

    @property
    def is_baseline(self) -> bool:
        return self.name.is_known and is_baseline_model(self.name.value)


def parse_best_model(raw: Any) -> BestModel:
    """Recover `(name, metric, value)` from `"<Model> (MAE 1,234,567)"`.

    The contract calls splitting on `" ("` "the only way to recover the
    name". We do that, but we refuse to guess when the shape does not
    match: an unparseable field yields UNKNOWNs, never a half-name.
    """
    if raw is None:
        absent = unknown(Presence.ABSENT, raw, "not recorded")
        return BestModel(absent, absent, absent, raw)

    text = str(raw).strip()
    withheld = text.upper().startswith("WITHHELD")
    if _NA_MARKERS.match(text):
        na = unknown(Presence.NOT_PRODUCED, raw, "no best model was published")
        return BestModel(na, na, na, raw, withheld)

    # `best_model_display` prepends "WITHHELD — <reasons>; <Model> (MAE ...)".
    # The model name lives after the last "; ". Contract §1 `best_model_display`.
    candidate = text
    if withheld:
        tail = text.rsplit(";", 1)[-1].strip()
        candidate = re.sub(r"\s+for diagnosis only\s*$", "", tail,
                           flags=re.IGNORECASE).strip()

    m = _BEST_MODEL_RE.match(candidate)
    if not m:
        # No embedded metric. The whole string is the name, if it looks like one.
        name = strip_decoration(candidate)
        if not name:
            na = unknown(Presence.UNPARSEABLE, raw, "no model name could be read")
            return BestModel(na, na, na, raw, withheld)
        return BestModel(
            known(name, raw),
            unknown(Presence.ABSENT, raw, "no metric was embedded in the field"),
            unknown(Presence.ABSENT, raw, "no metric was embedded in the field"),
            raw, withheld)

    return BestModel(
        known(strip_decoration(m.group("name")), raw),
        known(m.group("metric"), raw),
        parse_number(m.group("number")),
        raw, withheld)


# ─────────────────────────── tri-state verdicts ───────────────────────────

class Gate(str, Enum):
    """`gate_passed` is tri-state. Contract §1: null = never verified."""

    PASSED = "passed"
    WITHHELD = "withheld"
    UNVERIFIED = "unverified"   # null, absent, or non-boolean


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED_QUALITY = "FAILED_QUALITY"
    UNKNOWN = "UNKNOWN"


def parse_gate(raw: Any) -> Gate:
    if raw is True:
        return Gate.PASSED
    if raw is False:
        return Gate.WITHHELD
    return Gate.UNVERIFIED


def parse_run_status(raw: Any) -> RunStatus:
    text = str(raw).strip().upper() if raw is not None else ""
    if text in (RunStatus.SUCCESS.value, RunStatus.FAILED_QUALITY.value):
        return RunStatus(text)
    return RunStatus.UNKNOWN


def parse_tristate_bool(raw: Any) -> Value:
    """`leakage_flag` / `shift_flag`: absent means unknown, *not* false."""
    if isinstance(raw, bool):
        return known(raw, raw)
    if raw is None:
        return unknown(Presence.ABSENT, raw, "not recorded")
    if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        return known(raw.strip().lower() == "true", raw)
    return unknown(Presence.UNPARSEABLE, raw, f"published as {raw!r}")


# ─────────────────────────── family view ───────────────────────────

@dataclass
class FamilyView:
    """One `families[]` entry, read through the contract.

    Nothing here is a raw dict field. `defects` accumulates every place the
    artifact departed from the contract, so the UI can show them instead of
    the Agent silently papering over them.
    """

    name: str
    raw: dict = field(default_factory=dict)
    gate: Gate = Gate.UNVERIFIED
    gate_reasons: list[str] = field(default_factory=list)
    run_status: RunStatus = RunStatus.UNKNOWN
    skill_pct: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    best_model: BestModel = field(
        default_factory=lambda: parse_best_model(None))
    best_model_display: Value = field(
        default_factory=lambda: unknown(Presence.ABSENT))
    ok: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    integrity_verified: Value = field(
        default_factory=lambda: unknown(Presence.ABSENT))
    leakage_flag: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    shift_flag: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    models: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    #: `defects` are departures from the contract. `notes` are the
    #: ambiguities the contract itself anticipates and calls WARNINGs —
    #: real, worth saying, but not a sign the run is broken.
    defects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # ── presentation rules ──
    @property
    def is_presentable(self) -> bool:
        """May this family be shown as a clean result?

        Only a family that *passed* the gate **and** whose run_status is
        SUCCESS. `UNVERIFIED` is not a pass (contract §0) and
        `FAILED_QUALITY` is not clean, whatever the gate says.
        """
        return self.gate is Gate.PASSED and self.run_status is RunStatus.SUCCESS

    @property
    def is_champion_eligible(self) -> bool:
        """Presentable, with a real skill figure, and not a baseline."""
        return (self.is_presentable
                and self.skill_pct.is_known
                and not self.best_model.is_baseline)

    @property
    def withheld_reasons(self) -> list[str]:
        return list(self.gate_reasons)


def read_family(raw: Any) -> FamilyView:
    """Build a `FamilyView`, recording every contract departure it finds."""
    defects: list[str] = []
    notes: list[str] = []
    if not isinstance(raw, dict):
        return FamilyView(name="(unnamed)", raw={},
                          defects=["family entry is not an object"])

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        defects.append("family has no `name`; it cannot be identified")
        name = "(unnamed)"

    gate = parse_gate(raw.get("gate_passed"))
    if "gate_passed" not in raw:
        defects.append("`gate_passed` absent — treated as never verified, not as a pass")
    elif gate is Gate.UNVERIFIED and raw.get("gate_passed") is not None:
        defects.append(
            f"`gate_passed` is {raw.get('gate_passed')!r}, which is not "
            f"true/false/null — treated as never verified")

    reasons_raw = raw.get("gate_reasons")
    reasons: list[str] = []
    if reasons_raw is None:
        if gate is Gate.WITHHELD:
            defects.append("`gate_passed` is false but `gate_reasons` is absent — "
                           "the artifact does not say why it was withheld")
    elif isinstance(reasons_raw, (list, tuple)):
        reasons = [str(r) for r in reasons_raw if str(r).strip()]
    else:
        defects.append("`gate_reasons` is not a list")

    # Contract §1: reasons are non-empty iff gate_passed is false.
    if gate is Gate.WITHHELD and not reasons:
        defects.append("withheld with no recorded reason — inconsistent artifact")
    if gate is Gate.PASSED and reasons:
        defects.append("gate passed but reasons were recorded — inconsistent artifact")

    run_status = parse_run_status(raw.get("run_status"))
    if "run_status" not in raw:
        defects.append("`run_status` absent — treated as UNKNOWN")
    elif run_status is RunStatus.UNKNOWN and raw.get("run_status") is not None:
        defects.append(f"`run_status` is {raw.get('run_status')!r}, which is not "
                       f"SUCCESS or FAILED_QUALITY")

    if gate is Gate.PASSED and run_status is RunStatus.FAILED_QUALITY:
        defects.append("gate passed but run_status is FAILED_QUALITY — "
                       "inconsistent; not presented as a clean result")

    skill = parse_percent(raw.get("skill_pct"))
    best = parse_best_model(raw.get("best_model"))

    display_raw = raw.get("best_model_display")
    display = (known(str(display_raw), display_raw)
               if isinstance(display_raw, str) and display_raw.strip()
               else unknown(Presence.ABSENT, display_raw,
                            "no display string was published"))
    if display.is_unknown and best.name.is_known:
        # Contract §1: fall back to `best_model` **and** `gate_passed`.
        notes.append("`best_model_display` absent — falling back to "
                       "`best_model` plus the gate verdict")

    view = FamilyView(
        name=name,
        raw=raw,
        gate=gate,
        gate_reasons=reasons,
        run_status=run_status,
        skill_pct=skill,
        best_model=best,
        best_model_display=display,
        ok=parse_tristate_bool(raw.get("ok")),
        integrity_verified=parse_tristate_bool(raw.get("integrity_verified")),
        leakage_flag=parse_tristate_bool(raw.get("leakage_flag")),
        shift_flag=parse_tristate_bool(raw.get("shift_flag")),
        models=(known(str(raw["models"]), raw["models"])
                if isinstance(raw.get("models"), str)
                else unknown(Presence.ABSENT, raw.get("models"))),
        defects=defects,
        notes=notes,
    )
    return view


# ─────────────────────────── summary view ───────────────────────────

@dataclass
class Overall:
    """`overall` is derived and can contradict `families`. Contract §1.

    We keep the published values *and* the recomputation, and never render a
    published counter that disagrees with the families it claims to describe.
    """

    published: dict = field(default_factory=dict)
    recomputed: dict = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)

    def get(self, key: str) -> Value:
        """Recomputed value wins; UNKNOWN if it cannot be recomputed."""
        if key in self.recomputed:
            return known(self.recomputed[key], self.published.get(key))
        return parse_number(self.published.get(key))


def recompute_overall(families: Iterable[FamilyView],
                      published: Any) -> Overall:
    fams = list(families)
    pub = published if isinstance(published, dict) else {}
    recomputed = {
        "families_requested": len(fams),
        "families_ok": sum(1 for f in fams if f.ok.is_known and f.ok.value),
        "families_gate_passed": sum(1 for f in fams if f.gate is Gate.PASSED),
        "quality_gate_failures": sum(
            1 for f in fams if f.gate is Gate.WITHHELD
            or f.run_status is RunStatus.FAILED_QUALITY),
    }
    # Flags are tri-state per family: a count is only meaningful when every
    # family recorded the flag. Otherwise the count itself is UNKNOWN.
    for key, attr in (("leakage_flags", "leakage_flag"),
                      ("shift_flags", "shift_flag")):
        vals = [getattr(f, attr) for f in fams]
        if vals and all(v.is_known for v in vals):
            recomputed[key] = sum(1 for v in vals if v.value)

    disagreements = []
    for key, mine in recomputed.items():
        theirs = parse_number(pub.get(key))
        if theirs.is_known and int(theirs.value) != int(mine):
            disagreements.append(
                f"`overall.{key}` says {int(theirs.value)}, the families say {mine}")
    return Overall(pub, recomputed, disagreements)


@dataclass
class RunView:
    """`SUMMARY.json` read through the contract."""

    run_dir: Path
    raw: dict = field(default_factory=dict)
    run_id: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    schema_version: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    run_date: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    target: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    cadence: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    horizon: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    mode: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    data_file: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    #: The Lab's derived composition sentence, and the pool size behind it.
    #: See `read_model_composition` for why the Agent reads rather than writes.
    client_framing: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    champion_pool_size: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    stale: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    freshness_line: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    families: list[FamilyView] = field(default_factory=list)
    overall: Overall = field(default_factory=Overall)
    defects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fatal: list[str] = field(default_factory=list)

    @property
    def is_readable(self) -> bool:
        return not self.fatal

    def family(self, name: str) -> Optional[FamilyView]:
        want = str(name).strip().upper()
        return next((f for f in self.families if f.name.upper() == want), None)

    @property
    def presentable_families(self) -> list[FamilyView]:
        return [f for f in self.families if f.is_presentable]

    @property
    def withheld_families(self) -> list[FamilyView]:
        return [f for f in self.families if f.gate is Gate.WITHHELD]

    @property
    def unverified_families(self) -> list[FamilyView]:
        """Neither passed nor withheld — the gate never ran. Never a failure."""
        return [f for f in self.families if f.gate is Gate.UNVERIFIED]

    def champion(self) -> Optional[FamilyView]:
        """Highest *known* skill among champion-eligible families.

        A family with no skill figure cannot win by default: an UNKNOWN is
        not a score. If nothing is eligible there is no champion today.
        """
        pool = [f for f in self.families if f.is_champion_eligible]
        return max(pool, key=lambda f: f.skill_pct.value) if pool else None

    @property
    def all_defects(self) -> list[str]:
        out = list(self.defects)
        for f in self.families:
            out.extend(f"{f.name}: {d}" for d in f.defects)
        out.extend(f"overall: {d}" for d in self.overall.disagreements)
        return out

    @property
    def all_notes(self) -> list[str]:
        out = list(self.notes)
        for f in self.families:
            out.extend(f"{f.name}: {n}" for n in f.notes)
        return out


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─────────────────── the model composition, read not restated ───────────────────
#
# The Lab derives its client-facing composition sentence from the model
# registry — `backend/model_reference.client_framing()` writes it from
# `composition()`'s counts, so adding a model changes the sentence or fails the
# Lab's test. The Agent used to carry its own copy of that sentence as a pinned
# literal, and when C_DL became enumerable the Lab's pool grew and the Agent's
# string did not. Both repos' suites passed the whole time, because each was
# internally consistent; they disagreed only with each other.
#
# The fix is not a better literal. It is that the Agent has no literal at all:
# it reads the sentence the Lab derived, or it says the composition is not
# recorded. A stale-but-plausible sentence is the failure mode, so there is
# deliberately no fallback to fall back to.
#
# Shape. The field is NOT in the published contract (docs/AGENT_ARTIFACT_
# CONTRACT.md §1), so the Lab is free to move it and free to stop writing it.
# This reader is therefore liberal about where it may sit and strict about what
# counts as present. Runs from 2026-08-04 on do carry it, flat; older ones do
# not, and both shapes still occur on disk:
#
#   {"client_framing": "<sentence>"}                              ← flat
#   {"model_composition": {"framing": "<sentence>",               ← nested
#                          "champion_pool_size": 13}}
#
# Anything else — absent, null, blank, wrong type — is UNKNOWN.
#
# Pool size, 2026-08-12. The Lab now publishes the champion pool as an explicit
# LIST of names (`model_composition.champion_pool`), so its size is recorded
# rather than inferred and `len()` is simply reading it. The size is not written
# down here on purpose. It has already changed once, from 13 to 21, and a number
# in a comment cannot be checked by anything. That is not the
# same as taking `counts[champion_pool_category]`, which would make us the author
# of a number under a name the Lab never used; that inference is still refused.
# An int `champion_pool_size` still wins if the Lab ever writes one.

_FRAMING_KEYS = ("framing", "client_framing", "sentence")


def _pool_size(container: Any) -> Value:
    """Pool size from an explicit int, else from the length of a published list."""
    if not isinstance(container, dict):
        return unknown(Presence.ABSENT, None,
                       "the run does not record the champion-eligible pool")
    explicit = parse_int(container.get("champion_pool_size"))
    if explicit.is_known:
        return explicit
    pool = container.get("champion_pool")
    if isinstance(pool, list) and pool and all(isinstance(m, str) for m in pool):
        return known(len(pool), pool)
    return unknown(Presence.ABSENT, pool,
                   "the run does not record the champion-eligible pool")


def read_model_composition(raw: dict) -> tuple[Value, Value]:
    """`(client_framing, champion_pool_size)` from a SUMMARY.json dict.

    Never synthesises a sentence from counts. If the Lab published counts but
    no sentence, that is still UNKNOWN here: writing the sentence ourselves
    would recreate the exact defect this reader exists to prevent.
    """
    def _sentence(value: Any) -> Value:
        if isinstance(value, str) and value.strip():
            return known(value.strip(), value)
        return unknown(Presence.ABSENT, value,
                       "the run does not record the model composition")

    flat = raw.get("client_framing")
    if isinstance(flat, str) and flat.strip():
        return _sentence(flat), _pool_size(raw.get("model_composition") or raw)

    nested = raw.get("model_composition")
    if isinstance(nested, dict):
        for key in _FRAMING_KEYS:
            if key in nested:
                return _sentence(nested[key]), _pool_size(nested)
        return _sentence(None), _pool_size(nested)
    if nested is not None:
        # Present but not an object. Rule 3: structure is detected, never
        # assumed. Reading a bare string here would accept whatever the Lab
        # happened to put under a key it never agreed to — including a raw
        # count, which is the one thing that must not reach a sentence.
        return (unknown(Presence.UNPARSEABLE, nested,
                        f"`model_composition` is {type(nested).__name__}, not "
                        f"an object with a framing sentence"),
                _pool_size(raw))

    return _sentence(flat), _pool_size(raw)


def read_summary(run_dir: Path) -> RunView:
    """Read `SUMMARY.json` defensively. Never raises; reports instead."""
    run_dir = Path(run_dir)
    view = RunView(run_dir=run_dir)
    path = run_dir / "SUMMARY.json"
    if not path.exists():
        view.fatal.append(f"no SUMMARY.json in {run_dir}")
        return view
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        view.fatal.append(f"SUMMARY.json could not be parsed: {exc}")
        return view
    if not isinstance(raw, dict):
        view.fatal.append("SUMMARY.json is not a JSON object")
        return view

    view.raw = raw

    # Contract §1: run_id / schema_version are absent on every committed
    # artifact. That is a known defect, not a reason to refuse the run.
    if isinstance(raw.get("run_id"), str) and raw["run_id"].strip():
        view.run_id = known(raw["run_id"])
    else:
        view.run_id = unknown(Presence.ABSENT, raw.get("run_id"),
                              "not recorded; falling back to the folder name")
        view.defects.append("`run_id` absent — the run is identified by its "
                            "folder name only")
    view.schema_version = parse_int(raw.get("schema_version"))
    if view.schema_version.is_unknown:
        view.defects.append("`schema_version` absent — assuming version 1, so "
                            "since-v2 fields may be missing")

    # Required-or-reject fields. Contract §1.
    for key, attr, parser in (("run_date", "run_date", None),
                              ("target", "target", None),
                              ("cadence", "cadence", None)):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            setattr(view, attr, known(val.strip(), val))
        else:
            setattr(view, attr, unknown(Presence.ABSENT, val, "not recorded"))
            view.defects.append(f"`{key}` absent or blank")
    if view.run_date.is_known and not _DATE_RE.match(str(view.run_date.value)):
        view.defects.append(f"`run_date` is {view.run_date.value!r}, "
                            f"not YYYY-MM-DD")

    view.horizon = parse_int(raw.get("horizon"))
    if view.horizon.is_unknown:
        view.defects.append("`horizon` could not be read as a whole number")

    mode = raw.get("mode")
    view.mode = (known(str(mode), mode) if isinstance(mode, str) and mode.strip()
                 else unknown(Presence.ABSENT, mode, "assuming production"))

    # `data_file` is not in the contract and is absent from every committed
    # SUMMARY.json — so it must never be rendered as a filename.
    df = raw.get("data_file")
    view.data_file = (known(str(df), df) if isinstance(df, str) and df.strip()
                      else unknown(Presence.ABSENT, df,
                                   "the run does not record its input file"))

    view.client_framing, view.champion_pool_size = read_model_composition(raw)
    if view.client_framing.is_unknown:
        # A note, not a defect: the field is not in the published contract, so
        # its absence is not a departure from it. But it is a real thing this
        # artifact cannot tell us, and the Agent must not fill the gap.
        view.notes.append(
            "the model composition is not recorded — the Lab derives it from "
            "its registry (`client_framing()`) and writes it on newer runs, but "
            "this run does not carry it, so no model counts can be quoted here")

    fresh = raw.get("freshness")
    if isinstance(fresh, dict):
        view.stale = parse_tristate_bool(fresh.get("stale"))
        line = fresh.get("line")
        view.freshness_line = (known(str(line), line)
                               if isinstance(line, str) and line.strip()
                               else unknown(Presence.ABSENT, line,
                                            "no freshness line was recorded"))
    else:
        view.defects.append("`freshness` absent — staleness is unknown")

    fams_raw = raw.get("families")
    if not isinstance(fams_raw, list) or not fams_raw:
        view.fatal.append("`families` is absent or empty")
        return view
    view.families = [read_family(f) for f in fams_raw]

    seen: set[str] = set()
    for f in view.families:
        key = f.name.upper()
        if key in seen:
            view.defects.append(f"duplicate family `{f.name}`")
        seen.add(key)

    view.overall = recompute_overall(view.families, raw.get("overall"))
    return view


# ─────────────────────────── leaderboards ───────────────────────────

#: Contract §2 — three families, three schemas. C_DL is undocumented there
#: but is written by the Lab and matches B_ML's shape; we detect, not assume.
_LEADERBOARD_SCHEMAS: dict[str, set[str]] = {
    "a_stat": {"target", "horizon", "cadence", "model", "MAE", "RMSE", "rank"},
    "b_ml": {"target", "horizon", "model", "MAE", "rank"},
    "c_dl": {"target", "horizon", "model", "MAE", "rank"},
    "e_quantile": {"model", "pinball_q10", "pinball_q50", "pinball_q90",
                   "coverage_p10_p90", "MAE"},
}

#: The only column guaranteed across all schemas. Contract §2.
_LEADERBOARD_REQUIRED = {"model"}


@dataclass
class LeaderboardView:
    """One `leaderboard.csv`, with its schema identified rather than assumed."""

    family: str
    path: Optional[Path] = None
    frame: Optional[pd.DataFrame] = None
    schema: str = "unknown"
    target_source: str = "unknown"     # "file" | "summary" | "unknown"
    target: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    horizon: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    all_null_columns: list[str] = field(default_factory=list)
    partially_identified: list[str] = field(default_factory=list)
    baseline_models: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fatal: list[str] = field(default_factory=list)

    @property
    def is_readable(self) -> bool:
        return not self.fatal and self.frame is not None

    @property
    def competitors(self) -> pd.DataFrame:
        """Rows excluding reference baselines. Contract: baselines are not
        competitors and must never be ranked as winners."""
        if self.frame is None:
            return pd.DataFrame()
        f = self.frame
        return f[~f["model_key"].map(is_baseline_model)]


def read_leaderboard(path: Path, family: str,
                     summary_target: Optional[Value] = None,
                     summary_horizon: Optional[Value] = None) -> LeaderboardView:
    """Read a leaderboard without assuming a schema.

    `e_quantile` carries neither `target` nor `horizon`, so it *cannot be
    keyed by target* from the file (contract §2). We take those from
    `SUMMARY.json` and record that we did, so nothing downstream can claim
    the file said it.
    """
    view = LeaderboardView(family=str(family).lower(), path=Path(path))
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        view.fatal.append(f"{path.name} could not be parsed: {exc}")
        return view
    if frame.empty:
        view.fatal.append(f"{path.name} has no rows")
        return view

    cols = set(frame.columns)
    missing_required = _LEADERBOARD_REQUIRED - cols
    if missing_required:
        view.fatal.append(
            f"{path.name} lacks required column(s) {sorted(missing_required)} — "
            f"rejected rather than guessed")
        return view

    # Identify the schema by best overlap, and say so when it matches none.
    best_name, best_score = "unknown", 0.0
    for name, expected in _LEADERBOARD_SCHEMAS.items():
        score = len(expected & cols) / len(expected | cols)
        if score > best_score:
            best_name, best_score = name, score
    view.schema = best_name if best_score >= 0.6 else "unknown"
    if view.schema == "unknown":
        view.defects.append(
            f"{path.name} matches no known leaderboard schema "
            f"(columns: {sorted(cols)}) — only `model` is relied on")
    elif view.schema != view.family and view.family in _LEADERBOARD_SCHEMAS:
        view.defects.append(
            f"{path.name} looks like the {view.schema} schema, not "
            f"{view.family} — reading it by its columns, not its folder")

    frame = frame.copy()
    frame["model_key"] = frame["model"].map(strip_decoration)
    if frame["model_key"].eq("").any():
        view.defects.append(f"{path.name} has row(s) with a blank `model`")
    decorated = [str(m) for m in frame["model"]
                 if strip_decoration(m) != str(m).strip()]
    if decorated:
        view.notes.append(
            f"{path.name} carries decoration in the join key "
            f"({', '.join(sorted(set(decorated)))}) — matched on the stripped name")

    # All-null columns assert nothing (contract §0) — never render them as 0.
    for col in frame.columns:
        if col != "model_key" and frame[col].isna().all():
            view.all_null_columns.append(col)
    if view.all_null_columns:
        view.notes.append(
            f"{path.name}: column(s) {sorted(view.all_null_columns)} are entirely "
            f"empty — reported as not recorded, not as zero")

    # Contract §2 "partially-identified leaderboard": identity columns filled
    # on some rows and blank on others.
    for col in ("target", "horizon", "cadence"):
        if col in frame.columns:
            filled = frame[col].notna()
            if filled.any() and not filled.all():
                view.partially_identified.append(col)
    if view.partially_identified:
        view.defects.append(
            f"{path.name}: {sorted(view.partially_identified)} filled on some rows "
            f"and blank on others — the file cannot answer 'which model won for "
            f"target X' on its own")

    # Target / horizon: prefer an unambiguous file value, else the summary.
    view.target, view.target_source = _identify(frame, "target", summary_target)
    view.horizon, _ = _identify(frame, "horizon", summary_horizon, as_int=True)
    if view.target_source == "summary":
        view.notes.append(
            f"{path.name} does not identify its target — taken from SUMMARY.json")

    view.baseline_models = sorted({m for m in frame["model_key"]
                                   if is_baseline_model(m)})
    view.frame = frame
    return view


def _identify(frame: pd.DataFrame, col: str, fallback: Optional[Value],
              as_int: bool = False) -> tuple[Value, str]:
    """Take an identity column from the file if it is unambiguous, else the
    summary — and report which, so the caller never misattributes it."""
    if col in frame.columns:
        vals = {str(v).strip() for v in frame[col].dropna().tolist()
                if str(v).strip() != ""}
        if len(vals) == 1:
            raw = vals.pop()
            return (parse_int(raw) if as_int else known(raw)), "file"
        if len(vals) > 1:
            return unknown(Presence.UNPARSEABLE, sorted(vals),
                           f"the file carries several `{col}` values"), "file"
    if fallback is not None and fallback.is_known:
        return fallback, "summary"
    return unknown(Presence.ABSENT, None, f"no `{col}` in the file or the summary"), "unknown"


# ─────────────────────────── metrics_long.csv ───────────────────────────

class MetricsShape(str, Enum):
    """Contract §4: two incompatible shapes share one filename."""

    LONG = "long"
    WIDE = "wide"
    UNKNOWN = "unknown"


@dataclass
class MetricsView:
    family: str
    path: Optional[Path] = None
    shape: MetricsShape = MetricsShape.UNKNOWN
    frame: Optional[pd.DataFrame] = None
    all_null_columns: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fatal: list[str] = field(default_factory=list)

    @property
    def is_readable(self) -> bool:
        return not self.fatal and self.frame is not None

    def metric(self, name: str, model: Optional[str] = None) -> Value:
        """Mean of a metric, whichever shape the file is in.

        Returns UNKNOWN — never 0 — when the metric is absent or all-null.
        """
        if self.frame is None:
            return unknown(Presence.ABSENT, None, "no metrics file was read")
        f = self.frame
        if model is not None and "model" in f.columns:
            f = f[f["model"].map(strip_decoration) == strip_decoration(model)]
            if f.empty:
                return unknown(Presence.ABSENT, None,
                               f"no rows for model {model!r}")
        if self.shape is MetricsShape.LONG:
            rows = f[f["metric"].astype(str) == name]
            if rows.empty:
                return unknown(Presence.ABSENT, None,
                               f"metric {name!r} is not in the file")
            series = pd.to_numeric(rows["value"], errors="coerce").dropna()
        elif self.shape is MetricsShape.WIDE:
            if name not in f.columns:
                return unknown(Presence.ABSENT, None,
                               f"column {name!r} is not in the file")
            series = pd.to_numeric(f[name], errors="coerce").dropna()
        else:
            return unknown(Presence.UNPARSEABLE, None,
                           "the file's shape could not be determined")
        if series.empty:
            return unknown(Presence.NOT_PRODUCED, None,
                           f"{name} is present but entirely empty — "
                           f"the artifact does not record why")
        return known(float(series.mean()))


def read_metrics_long(path: Path, family: str) -> MetricsView:
    """Detect LONG vs WIDE with `{'metric','value'} <= columns`. Contract §4."""
    view = MetricsView(family=str(family).lower(), path=Path(path))
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        view.fatal.append(f"{path.name} could not be parsed: {exc}")
        return view
    if frame.empty:
        view.fatal.append(f"{path.name} has no rows")
        return view

    cols = set(frame.columns)
    if {"metric", "value"} <= cols:
        view.shape = MetricsShape.LONG
    elif "model" in cols:
        view.shape = MetricsShape.WIDE
    else:
        view.shape = MetricsShape.UNKNOWN
        view.defects.append(
            f"{path.name} is neither the long nor the wide shape "
            f"(columns: {sorted(cols)})")

    for col in frame.columns:
        if frame[col].isna().all():
            view.all_null_columns.append(col)
    if view.all_null_columns:
        view.notes.append(
            f"{path.name}: column(s) {sorted(view.all_null_columns)} are entirely "
            f"empty — an empty cell asserts nothing, so these read as not recorded")

    view.frame = frame
    return view


# ─────────────────────────── predictions_long.csv ───────────────────────────

def read_predictions(
        path: Path,
        family: str) -> tuple[Optional[pd.DataFrame], list[str], list[str]]:
    """Read the row-level artifact and check what the contract says to check.

    Contract §3: `origin_date`, `target_date`, `y_true` and `model` are
    required, and `origin_date >= target_date` on any row is an **error** —
    the model would have been predicting a date it could already see.

    Returns `(frame_or_None, defects, notes)`. A file that fails a
    required-column check is rejected, not partially used.
    """
    defects: list[str] = []
    notes: list[str] = []
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return None, [f"{path.name} could not be parsed: {exc}"], notes
    if frame.empty:
        return None, [f"{path.name} has no rows"], notes

    required = {"origin_date", "target_date", "model"}
    missing = required - set(frame.columns)
    if missing:
        return None, [f"{path.name} lacks required column(s) {sorted(missing)} — "
                      f"rejected rather than guessed"], notes

    frame = frame.copy()
    for col in ("origin_date", "target_date"):
        frame[col] = pd.to_datetime(frame[col], errors="coerce")
        if frame[col].isna().any():
            defects.append(f"{path.name}: {int(frame[col].isna().sum())} row(s) "
                           f"have an unreadable `{col}`")

    both = frame["origin_date"].notna() & frame["target_date"].notna()
    bad = both & (frame["origin_date"] >= frame["target_date"])
    if bad.any():
        defects.append(
            f"{path.name}: {int(bad.sum())} row(s) have origin_date >= "
            f"target_date — the model would have been predicting a date it "
            f"could already see. These rows are dropped, not charted")
        frame = frame[~bad]

    if "y_true" not in frame.columns:
        # Legitimate for a published forward forecast; stated, not assumed.
        notes.append(f"{path.name}: no `y_true` column — legitimate for a "
                     f"forward forecast, but nothing here can be scored")

    # y_lo / y_hi are all-null for models with no native intervals. The
    # contract is explicit that this is indistinguishable from a failure.
    for col in ("y_lo", "y_hi"):
        if col in frame.columns and frame[col].isna().all():
            notes.append(
                f"{path.name}: `{col}` is entirely empty — this family may have "
                f"no native prediction intervals, or they may have failed; the "
                f"artifact does not distinguish the two")

    return frame, defects, notes


# ─────────────────────────── coverage ───────────────────────────

_COVERAGE_KEY_RE = re.compile(r"^coverage_p(?P<lo>\d+)_p(?P<hi>\d+)$")


@dataclass
class CoverageView:
    """Contract §5. The interval's *level* is data, not something to infer.

    Point-model runs log no coverage at all. That is legitimate: consumers
    render "not reported" — never 0, never "failed".
    """

    family: str
    measured: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    nominal: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    key: Value = field(default_factory=lambda: unknown(Presence.ABSENT))
    unavailable_reason: Value = field(
        default_factory=lambda: unknown(Presence.ABSENT))
    legacy_key_omitted: Value = field(
        default_factory=lambda: unknown(Presence.ABSENT))
    defects: list[str] = field(default_factory=list)

    @property
    def is_reported(self) -> bool:
        return self.measured.is_known

    @property
    def level_is_recorded(self) -> bool:
        return self.nominal.is_known


def read_coverage(family: str,
                  leaderboard: Optional[LeaderboardView] = None,
                  metrics: Optional[MetricsView] = None,
                  extras: Optional[dict] = None) -> CoverageView:
    """Collect coverage from wherever the Lab published it, without inferring
    the level from a key name."""
    view = CoverageView(family=str(family).lower())
    extras = extras if isinstance(extras, dict) else {}

    for src in (extras,):
        if "coverage_unavailable_reason" in src:
            view.unavailable_reason = known(str(src["coverage_unavailable_reason"]))
        if "legacy_coverage_key_omitted" in src:
            view.legacy_key_omitted = known(str(src["legacy_coverage_key_omitted"]))
        if "coverage_nominal" in src:
            view.nominal = parse_number(src.get("coverage_nominal"))
        if "coverage_key" in src:
            view.key = known(str(src["coverage_key"]))

    # A `coverage_p<lo>_p<hi>` column on the leaderboard.
    if leaderboard is not None and leaderboard.frame is not None:
        for col in leaderboard.frame.columns:
            m = _COVERAGE_KEY_RE.match(str(col))
            if not m:
                continue
            series = pd.to_numeric(leaderboard.frame[col],
                                   errors="coerce").dropna()
            if series.empty:
                continue
            view.measured = known(float(series.mean()))
            if view.key.is_unknown:
                view.key = known(str(col))
            break

    # Long-form metrics rows named coverage_*.
    if view.measured.is_unknown and metrics is not None \
            and metrics.shape is MetricsShape.LONG and metrics.frame is not None:
        rows = metrics.frame[
            metrics.frame["metric"].astype(str).str.startswith("coverage")]
        if not rows.empty:
            series = pd.to_numeric(rows["value"], errors="coerce").dropna()
            if not series.empty:
                view.measured = known(float(series.mean()))
                view.key = known(str(rows["metric"].iloc[0]))
            # Since item 5 `quantile` carries the nominal level on these rows.
            if view.nominal.is_unknown and "quantile" in rows.columns:
                q = pd.to_numeric(rows["quantile"], errors="coerce").dropna()
                if not q.empty:
                    view.nominal = known(float(q.iloc[0]))

    if view.measured.is_known and view.nominal.is_unknown:
        # Contract §5 calls this an ERROR at publication. As a consumer we
        # keep the measurement but refuse to attach a level to it.
        view.defects.append(
            "coverage was measured but no `coverage_nominal` was published — "
            "the level the interval was fitted for is not recorded, so the "
            "measurement is reported without one")

    if view.measured.is_known and not (0.0 <= view.measured.value <= 1.0):
        view.defects.append(
            f"coverage {view.measured.value} is outside [0, 1]")
        view.measured = unknown(Presence.UNPARSEABLE, view.measured.raw,
                                "published outside the valid range [0, 1]")
    return view
