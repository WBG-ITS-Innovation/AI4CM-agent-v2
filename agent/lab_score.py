# agent/lab_score.py — the second file that runs under the LAB's interpreter.
#
# Companion to agent/lab_entry.py, and deliberately a separate file rather than
# another mode on it: that script is the verified publish path from Session 5
# and there is no reason for a scoring change to be able to break it.
#
# It calls ONE Lab function — `published_forecasts.score_published` — and does
# no arithmetic of its own. The agent never computes a score, an error, a skill
# figure or an interval hit. If a number appears anywhere in this repo's scoring
# story, the Lab produced it.
#
# WHY A SCRIPT AT ALL, when the Lab has `run_publish_and_score.py`
# ----------------------------------------------------------------
# That runner hardcodes its data path and its scorecard path, and it also
# PUBLISHES from the shared forward directory before scoring — the exact write
# Session 5's per-target staging exists to avoid. Neither of its paths can be
# redirected, so it cannot be used for a verification run that must not touch
# the real tree. `score_published` itself takes both as parameters; this exposes
# them and nothing else.
#
# WHAT THE SCORECARD DOES AND DOES NOT HOLD
# -----------------------------------------
# `score_published` REWRITES the scorecard from scratch on every call — it is
# not an append — and it writes only rows whose truth has arrived. So:
#
#   * scored rows are in the artifact, and the agent reads them from there;
#   * PENDING rows exist nowhere on disk. The return dict is their only record,
#     so `pending_dates` travels on this stream. That is not a shortcut around
#     "read the artifact" — there is no artifact to read, and saying nothing
#     about pending rows would report a partial scoring as a complete one.
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path


def emit(event: str, **fields) -> None:
    sys.stdout.write(json.dumps({"event": event, **fields}, default=str) + "\n")
    sys.stdout.flush()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Score the Lab's published forecasts against arrived truth.")
    ap.add_argument("--lab", required=True, help="AI4CM repo root")
    ap.add_argument("--data", default="",
                    help="actuals to score against; defaults to the Lab's master file")
    ap.add_argument("--published-root", default="",
                    help="omit to score the Lab's real published issues")
    ap.add_argument("--scorecard", default="",
                    help="omit to write the Lab's real forecasts/scorecard.csv")
    args = ap.parse_args(argv)

    lab = Path(args.lab).resolve()
    backend = lab / "backend"
    if not (backend / "published_forecasts.py").exists():
        emit("fatal", reason=f"no Lab backend at {backend}")
        return 2
    sys.path.insert(0, str(backend))

    try:
        import published_forecasts as PF
    except Exception as exc:                              # noqa: BLE001
        emit("fatal", reason=f"cannot import the Lab's published_forecasts: {exc}",
             traceback=traceback.format_exc())
        return 2

    data = Path(args.data) if args.data else (
        backend / "data" / "processed" / "master_daily_clean_treasury.csv")
    if not data.exists():
        emit("fatal", reason=f"the actuals file is missing: {data}")
        return 2

    published_root = Path(args.published_root) if args.published_root else None
    scorecard = Path(args.scorecard) if args.scorecard else None

    emit("start", data=str(data), data_sha256=sha256_of(data),
         published_root=str(published_root) if published_root else "",
         scorecard=str(scorecard) if scorecard else "",
         issues=len(PF.list_published(published_root)))

    try:
        out = PF.score_published(data, published_root=published_root,
                                 scorecard_path=scorecard)
    except PF.UnsupportedIntervalShape as exc:
        # A refusal, not a crash: the Lab declines to score an issue whose band
        # is not p10/p50/p90 rather than computing the wrong arithmetic for it.
        # Reported in its own words and with a zero exit, because refusing is
        # the correct outcome and not a failure of this run.
        emit("refused", stage="score", reason=str(exc))
        emit("finish", scored=0, pending=0, refused=True)
        return 0
    except Exception as exc:                              # noqa: BLE001
        emit("failed", stage="score", error=f"{type(exc).__name__}: {exc}",
             traceback=traceback.format_exc())
        return 1

    emit("scored",
         scored=int(out.get("scored", 0)),
         pending=int(out.get("pending", 0)),
         issues=int(out.get("issues", 0)),
         scorecard=str(out.get("scorecard", "")),
         summary=out.get("summary") or {},
         pending_dates=[list(p) for p in (out.get("pending_dates") or [])],
         baseline_disagreements=out.get("baseline_disagreements") or [])

    emit("finish", scored=int(out.get("scored", 0)),
         pending=int(out.get("pending", 0)), refused=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
