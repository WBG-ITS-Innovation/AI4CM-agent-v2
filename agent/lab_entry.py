# agent/lab_entry.py — the one file in this repo that runs under the LAB's
# interpreter, not the agent's.
#
# WHY IT EXISTS
# -------------
# The Lab already publishes a CLI for exactly this purpose
# (`backend/forecast_modes.py::_cli`, used by the Lab's own Forecast page):
# one interpreter owns the models, the caller reads JSON. This script would be
# unnecessary if that CLI could publish — but it cannot, today, for a reason
# that is a property of the data rather than of this repo:
#
#     _cli --publish  ->  publish_official(res)          # no issue_date
#                     ->  publish(src, issue_date=None)
#                     ->  issue_date = max(origin_date)  # == 2025-08-06
#                     ->  FileExistsError: forecasts/published/2025-08-06 exists
#
# The champion recipes forecast forward from the END OF THE DATA, and the data
# has not moved since the first issue was published from it. So the derived
# issue date is always a date that already exists. `forecast_modes` has the
# remedy in the same module — `next_issue_date()` — and the Lab's own Session 2
# used it, by hand, to publish `2026-08-16`. The CLI just never wires it up.
#
# So this script composes the Lab's OWN three public functions in the Lab's own
# documented order:
#
#     next_issue_date()  ->  official_run()  ->  publish_official(issue_date=)
#
# WHAT IT DELIBERATELY DOES NOT DO
# --------------------------------
# There is no forecasting here, no gating, no publishing, and no retention. It
# does not decide which targets are publishable — `publish_official` refuses a
# `withheld` target and this script reports the refusal in the Lab's own words.
# It does not write to `forecasts/published/`; `publish()` does. It does not
# copy anything to the vault; `publish()` does that too, and the agent verifies
# it afterwards by looking at the disk rather than by believing this script.
#
# Everything it emits is one JSON object per line on stdout. Anything the
# modelling libraries print lands between those lines and is kept as trace, so
# a stray LightGBM warning can never be mistaken for a result.
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path


def emit(event: str, **fields) -> None:
    """One NDJSON line, flushed, so the agent sees progress as it happens.

    Unbuffered on purpose: the whole point of streaming is that a run taking
    minutes shows its work, and a block-buffered pipe would deliver every
    event at once when the process exits.
    """
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
        description="Run the Lab's official forward forecast and publish it.")
    ap.add_argument("--lab", required=True, help="AI4CM repo root")
    ap.add_argument("--data", default="", help="defaults to the Lab's master file")
    ap.add_argument("--target", action="append", default=[],
                    help="repeatable; defaults to every registry champion")
    ap.add_argument("--published-root", default="",
                    help="staging root; omit to publish for real")
    ap.add_argument("--vault-root", default="",
                    help="required when --published-root is set, if retention "
                         "is to be exercised at all")
    ap.add_argument("--issue-date", default="",
                    help="omit to take the Lab's own next_issue_date()")
    args = ap.parse_args(argv)

    lab = Path(args.lab).resolve()
    backend = lab / "backend"
    if not (backend / "forecast_modes.py").exists():
        emit("fatal", reason=f"no Lab backend at {backend}")
        return 2
    # The Lab's modules import each other by bare name, so the backend must be
    # on the path and the process must run from the repo root — the same two
    # conditions `backend/forecast_modes.py::_cli` runs under.
    sys.path.insert(0, str(backend))

    try:
        import forecast_modes as FM
    except Exception as exc:                              # noqa: BLE001
        emit("fatal", reason=f"cannot import the Lab's forecast_modes: {exc}",
             traceback=traceback.format_exc())
        return 2

    data = Path(args.data) if args.data else (
        backend / "data" / "processed" / "master_daily_clean_treasury.csv")
    if not data.exists():
        emit("fatal", reason=f"the Lab's data file is missing: {data}")
        return 2

    published_root = Path(args.published_root) if args.published_root else None
    # publish() defaults `vault_root` to the real vault ONLY when publishing to
    # the real root; a redirected root retains nothing unless told where. That
    # default is the Lab's and it is right — it stops a staging run writing
    # into the real vault — but it means a staging run proves nothing about
    # retention unless a vault is named. So the agent names one.
    vault_root = Path(args.vault_root) if args.vault_root else None

    targets = list(args.target)
    if not targets:
        try:
            from registry import load_registry
            targets = [r["target"] for r in load_registry()["recipes"]]
        except Exception as exc:                          # noqa: BLE001
            emit("fatal", reason=f"cannot read the Lab's recipe registry: {exc}",
                 traceback=traceback.format_exc())
            return 2

    issue_date = args.issue_date or FM.next_issue_date(published_root)

    emit("start", issue_date=issue_date, targets=targets, data=str(data),
         data_sha256=sha256_of(data), horizon=FM.VALIDATED_HORIZON,
         published_root=str(published_root) if published_root else "",
         mode=FM.MODE_OFFICIAL)

    published, refused, failed = [], [], []
    for target in targets:
        status = FM.recipe_status(target)
        emit("target_start", target=target,
             recipe_id=status.get("recipe_id"), model=status.get("model"))
        try:
            result = FM.official_run(target, data, horizon=FM.VALIDATED_HORIZON)
        except (FM.NoRecipe, FM.NotOfficial) as exc:
            # A refusal is the system working. It is not an error and it does
            # not fail the run.
            refused.append(target)
            emit("target_refused", target=target, stage="run", reason=str(exc))
            continue
        except Exception as exc:                          # noqa: BLE001
            failed.append(target)
            emit("target_failed", target=target, stage="run",
                 error=f"{type(exc).__name__}: {exc}",
                 traceback=traceback.format_exc())
            continue

        # DELIBERATELY NO LEVELS. `official_run` succeeds for a withheld target
        # — the refusal happens one line below, in `publish_official` — so at
        # this point `result.forecasts` holds p10/p50/p90 for a series the
        # gates may be about to withhold. Emitting them would put withheld
        # numbers on the agent's own pipe and into the trace the user can
        # expand, which is `agent/official.py`'s rule 2 broken by a side door:
        # the gates withheld the numbers, and quoting them "as progress"
        # republishes them just as surely as quoting them as an answer.
        #
        # So the stream carries only shape, never value. Every number the agent
        # later reports is read back out of the PUBLISHED artifact, which by
        # construction exists only for targets that cleared publication.
        emit("target_ran", target=target, recipe_id=result.recipe_id,
             model=result.model, horizon=result.horizon,
             n_rows=int(len(result.forecasts)),
             n_estimators=len(result.estimators or []))

        kwargs = {"issue_date": issue_date}
        if published_root is not None:
            kwargs["published_root"] = published_root
        try:
            dest = FM.publish_official(result, **kwargs)
        except FM.NotOfficial as exc:
            refused.append(target)
            emit("target_refused", target=target, stage="publish",
                 reason=str(exc))
            continue
        except Exception as exc:                          # noqa: BLE001
            failed.append(target)
            emit("target_failed", target=target, stage="publish",
                 error=f"{type(exc).__name__}: {exc}",
                 traceback=traceback.format_exc())
            continue

        # Retention for a staging run. On a real publish, publish() has already
        # mirrored to the vault and publish_official has re-synced the blobs;
        # doing it again here would be a second, differently-shaped write path
        # for the same guarantee, which is how the two drift apart.
        if published_root is not None and vault_root is not None:
            try:
                from published_forecasts import retain_to_vault
                retain_to_vault(dest, vault_root)
            except Exception as exc:                      # noqa: BLE001
                emit("retain_failed", target=target, dest=str(dest),
                     error=f"{type(exc).__name__}: {exc}")

        published.append(target)
        emit("target_published", target=target, dest=str(dest))

    emit("finish", issue_date=issue_date, published=published,
         refused=refused, failed=failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
