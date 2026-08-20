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
# agent/runtime/watcher.py
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Dict, Any
import pandas as pd

STATE = Path("artifacts/.seen_datasets.json")

def scan_new_csvs(folder: Path) -> Dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        seen = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except Exception:
        seen = {}
    files = {p.name: p.stat().st_mtime for p in folder.glob("*.csv")}
    new = [name for name, m in files.items() if name not in seen or m > float(seen[name])]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(files, indent=2), encoding="utf-8")
    return {"new_files": new, "count": len(new), "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S")}

def quick_profile(csv_path: Path, date_col_guess: str = "date") -> Dict[str, Any]:
    out = {"file": csv_path.name}
    try:
        df = pd.read_csv(csv_path)
        dcol = date_col_guess if date_col_guess in df.columns else next((c for c in df.columns if "date" in c.lower()), df.columns[0])
        df[dcol] = pd.to_datetime(df[dcol], errors="coerce")
        df = df.dropna(subset=[dcol]).sort_values(dcol)
        numeric = [c for c in df.columns if c != dcol and pd.api.types.is_numeric_dtype(df[c])]
        out.update({
            "rows": int(len(df)),
            "span": "n/a" if df.empty else f"{df[dcol].min().date()} → {df[dcol].max().date()}",
            "targets": numeric[:25],
            "date_col": dcol,
        })
    except Exception as e:
        out["error"] = str(e)
    return out
