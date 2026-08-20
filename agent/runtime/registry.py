# ─────────────────────────────────────────────────────────────────────────────
# NOT REACHABLE FROM app.py. NO TEST COVERS THIS FILE.
#
# `agent/runtime/` is an unwired side package: nothing in `app.py` or `agent/`
# imports it, and the test suite neither exercises nor stubs it. It is kept
# because it sketches a capability the project may still want — watching for
# new lab runs and alerting on them — not because anything depends on it.
#
# Two consequences a reader should hold onto:
#   * Nothing here has been checked against the Lab's artifact contract, so it
#     does not carry the absence semantics the rest of `agent/` is built on.
#     Do not copy patterns out of this package into live code.
#   * `requests`, imported by alerts.py, is deliberately NOT in requirements.txt
#     for this reason. Install it yourself if you wire this up.
# ─────────────────────────────────────────────────────────────────────────────
# agent/runtime/registry.py — tiny persistence for best model & target aliases
from __future__ import annotations
from pathlib import Path
import json
from typing import Optional, Dict

PREFS = Path("artifacts/agent_prefs.json")
ALIASES = Path("artifacts/target_aliases.json")
CHAMP = Path("artifacts/champions.json")  # also used by app.py

def _load(path: Path) -> Dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save(path: Path, obj: Dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

# ---- Best model (Champion) helpers ------------------------------------------
def get_best_model(dataset_name: str, target: str) -> Optional[str]:
    """Look up preferred model for (dataset, target). First check champions.json, then prefs."""
    key = f"{dataset_name}::{target}"
    ch = _load(CHAMP)
    if key in ch and isinstance(ch[key], dict):
        return ch[key].get("method")
    prefs = _load(PREFS)
    return (prefs.get("champions") or {}).get(key)

def set_best_model(dataset_name: str, target: str, method: str, metrics: Optional[Dict] = None) -> None:
    """Persist preferred model under champions.json (also mirrored in prefs for compatibility)."""
    key = f"{dataset_name}::{target}"
    champs = _load(CHAMP)
    champs[key] = {"method": method, "metrics": (metrics or {})}
    _save(CHAMP, champs)

    prefs = _load(PREFS)
    champs2 = prefs.get("champions") or {}
    champs2[key] = method
    prefs["champions"] = champs2
    _save(PREFS, prefs)

# ---- Natural-language target aliases ----------------------------------------
def alias_target(phrase: str, column_name: str) -> None:
    """Remember that 'state budget balance' -> 'State budget balance' for this user."""
    if not phrase:
        return
    m = _load(ALIASES)
    m[phrase.lower()] = column_name
    _save(ALIASES, m)

def resolve_alias(phrase: str | None) -> Optional[str]:
    if not phrase:
        return None
    return _load(ALIASES).get(phrase.lower())
