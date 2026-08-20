# agent/registry_read.py — the Lab's publication verdicts, read as a contract.
#
# Scope: `registry/recipes.json`. This is where the Lab records, per target,
# which recipe is the champion and whether its numbers may be published at
# all. `lab_bridge.official_targets()` already reads this file for the target
# *names*; this module reads the verdict beside each name, which is the part a
# consumer needs to answer "why is this not published?".
#
# Why a separate module from lab_bridge: lab_bridge is organised around a run
# directory, and a verdict is not a property of a run. The registry is fixed
# across runs by design — `champion_policy.reselection` is `"none"` — so
# binding a verdict to whichever run happens to be newest would imply the
# verdict was re-decided when it was not.
#
# The distinction this module exists to protect (contract §1):
#
#   * THE CHAMPION RECIPE — one `point_model` per target, promoted here, and
#     what an official published forecast actually uses.
#   * `families[].best_model` in a run's SUMMARY.json — the best model *within
#     one family*, written for all four families.
#
# They are different things and the second is not the champion. A consumer
# that answers "which model is best for X" from a family leaderboard is
# ranking across four families, not across the registry's champion pool. So
# `champion_for()` reads the registry and nothing else.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: The verdicts `publication.verdict` may carry. Anything else is unknown —
#: never coerced to one of these, because a verdict a consumer invented is
#: worse than admitting the file said something unrecognised.
PUBLISHABLE = "publishable"
WITHHELD = "withheld"


@dataclass(frozen=True)
class Gate:
    """One quality gate, as the registry recorded it."""

    key: str
    name: str
    passed: Optional[bool]      # tri-state: None means never verified
    measured: object = None
    threshold: object = None
    reason_plain: str = ""

    @property
    def verified(self) -> bool:
        return self.passed is not None


@dataclass(frozen=True)
class Recipe:
    """A target's champion recipe and publication verdict, or the gap."""

    target: str = ""
    recipe_id: str = ""
    family: str = ""
    point_model: str = ""
    status: str = ""
    approved_by: Optional[str] = None
    verdict: str = ""                       # "" when the file records none
    reason_plain: str = ""
    failing_gates: tuple[str, ...] = ()
    named_fix: str = ""
    mase: Optional[float] = None
    sentinel_ratio: Optional[float] = None
    skill_vs_ruler_pct: Optional[float] = None
    dev_window: str = ""
    dev_n: Optional[int] = None
    gates: dict[str, Gate] = field(default_factory=dict)
    note: str = ""                          # why this recipe is unusable

    @property
    def is_known(self) -> bool:
        return bool(self.recipe_id) and not self.note

    @property
    def is_publishable(self) -> bool:
        return self.verdict == PUBLISHABLE

    @property
    def is_withheld(self) -> bool:
        return self.verdict == WITHHELD

    @property
    def verdict_known(self) -> bool:
        return self.verdict in (PUBLISHABLE, WITHHELD)

    @property
    def is_approved(self) -> bool:
        """Contract §7: nothing may render as approved while this is null."""
        return bool(self.approved_by)

    def failing(self) -> tuple[Gate, ...]:
        """The gates this recipe failed, in the order the registry named them."""
        out = [self.gates[k] for k in self.failing_gates if k in self.gates]
        # A gate recorded as failed but absent from `failing_gates` is still a
        # failure; the two lists disagreeing is itself worth surfacing.
        for key, gate in self.gates.items():
            if gate.passed is False and key not in self.failing_gates:
                out.append(gate)
        return tuple(out)


def _as_float(raw: object) -> Optional[float]:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw == raw else None
    try:
        return float(str(raw).strip().replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _parse_gates(blob: object) -> dict[str, Gate]:
    if not isinstance(blob, dict):
        return {}
    out: dict[str, Gate] = {}
    for key, raw in blob.items():
        if not isinstance(raw, dict):
            continue
        passed = raw.get("passed")
        out[str(key)] = Gate(
            key=str(key),
            name=str(raw.get("name") or key),
            passed=passed if isinstance(passed, bool) else None,
            measured=raw.get("measured"),
            threshold=raw.get("threshold"),
            reason_plain=str(raw.get("reason_plain") or ""),
        )
    return out


def _parse_recipe(raw: dict) -> Recipe:
    target = str(raw.get("target") or "").strip()
    creds = raw.get("dev_credentials")
    creds = creds if isinstance(creds, dict) else {}
    pub = raw.get("publication")
    pub = pub if isinstance(pub, dict) else {}

    verdict = str(pub.get("verdict") or "").strip().lower()
    if verdict not in (PUBLISHABLE, WITHHELD):
        verdict = ""    # unrecognised is unknown, never coerced

    failing = tuple(str(g) for g in (pub.get("failing_gates") or [])
                    if str(g).strip())

    approved = raw.get("approved_by")
    return Recipe(
        target=target,
        recipe_id=str(raw.get("id") or "").strip(),
        family=str(raw.get("family") or "").strip(),
        point_model=str(raw.get("point_model") or "").strip(),
        status=str(raw.get("status") or "").strip(),
        approved_by=approved if isinstance(approved, str) and approved.strip() else None,
        verdict=verdict,
        reason_plain=str(pub.get("reason_plain") or "").strip(),
        failing_gates=failing,
        named_fix=str(pub.get("named_fix") or "").strip(),
        mase=_as_float(creds.get("mase")),
        sentinel_ratio=_as_float(creds.get("sentinel_ratio")),
        skill_vs_ruler_pct=_as_float(creds.get("skill_vs_ruler_pct")),
        dev_window=str(creds.get("window") or "").strip(),
        dev_n=int(creds["n"]) if isinstance(creds.get("n"), int) else None,
        gates=_parse_gates(creds.get("gates")),
    )


def load_recipes(repo: Path) -> tuple[dict[str, Recipe], str]:
    """`({target: Recipe}, note)`. `note` carries the reason on failure.

    An unreadable registry yields no recipes and a reason, never a guess.
    """
    path = Path(repo) / "registry" / "recipes.json"
    if not path.exists():
        return {}, (f"the lab's recipe registry was not found at `{path}`, so "
                    f"I cannot say which model is promoted for any target or "
                    f"whether its numbers may be published")
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"the lab's recipe registry could not be read: {exc}"
    recipes = blob.get("recipes") if isinstance(blob, dict) else None
    if not isinstance(recipes, list):
        return {}, ("the lab's recipe registry has no `recipes` list, so I "
                    "cannot read any publication verdict")

    out: dict[str, Recipe] = {}
    for raw in recipes:
        if not isinstance(raw, dict):
            continue
        recipe = _parse_recipe(raw)
        if recipe.target:
            out[recipe.target] = recipe
    if not out:
        return {}, "the lab's recipe registry lists no targets"
    return out, ""


def champion_for(repo: Path, target: str) -> Recipe:
    """The registry's champion recipe for `target`, or a `Recipe` with a note.

    This is the champion in the contract's sense — the `point_model` a
    registry recipe promotes — and never a family leaderboard's best model.
    """
    recipes, note = load_recipes(repo)
    if note:
        return Recipe(target=target, note=note)
    for name, recipe in recipes.items():
        if name.casefold() == str(target).casefold():
            return recipe
    return Recipe(target=target,
                  note=f"the lab's registry has no champion recipe for "
                       f"`{target}`, so there is no official model for it")


def gate_policy(repo: Path) -> dict:
    """`gate_policy` verbatim — thresholds a consumer may quote but not invent."""
    path = Path(repo) / "registry" / "recipes.json"
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    policy = blob.get("gate_policy") if isinstance(blob, dict) else None
    return policy if isinstance(policy, dict) else {}
