# Session 6 — closing the loop: ingest actuals, score, narrate

**Repo** `ai4cm-agent-v2` · **Branch** `feat/lab-door` · **Start** `ab8954e`
**Lab observed at** `9a97b56` (moving throughout — see §2)

The agent could describe forecasts and make them. It can now be handed new actuals,
validate them, and ask the Lab what its previous forecasts actually got right.

**The Lab was not modified, and nothing was written into its real tree.** The user placed
a hold mid-session while a Lab session regenerated artifacts; §2 records how that was
respected and proven.

---

## 1. Pre-flight, as asked

```
###### AGENT REPO ######
## feat/lab-door...origin/feat/lab-door
ab8954e Let the agent run the lab's official forecast, behind a confirmation

###### LAB REPO ######
## model/excellence...origin/model/excellence [ahead 3]
8267c97 Make the window-tiling test follow the clock, and its decay self-reporting
3f72332 Fix the scorecard schema before the first scored row exists
f40f90a Give the publish CLI an --issue-date so it can actually publish
fdd7a02 docs: carry the two deferred decisions forward with their evidence
```

The Lab's 6-prep session was committed. `f40f90a` is this repo's Session 5 §8 proposal
applied verbatim, plus the docstring it falsified.

---

## 2. The Lab moved five times during this session, and the hold held

The Lab was **not** a fixed thing to read. Observed transitions:

| When | What |
|---|---|
| session start | `8267c97`, clean |
| ~20 min in | `9a97b56` — new commits, **scorecard schema 27 → 31 columns** |
| during work | `backend/forecast_runs/2026-08-18/` appeared **without** `SUMMARY.json` |
| during work | `a_stat_models_pipeline.py`, `c_dl_pipeline.py`, `run_a_stat.py` modified |

Two consequences, both acted on rather than noted.

**A frozen snapshot, not the live tree.** A rehearsal baseline taken against a Lab being
rewritten measures the *other* session's work. So the Lab's read-relevant artifacts were
copied once into a sandbox (`registry/`, `forecasts/published/`, `private_vault/published/`,
one complete run, the data file) and everything in this session was baselined and verified
against that copy.

**A latent bug in the verification harness, found by the moving Lab.** The Session 5
rehearsal script took `sorted(run_dirs)[-1]` — the newest directory. With a half-written
`2026-08-18/` present that is a run with no `SUMMARY.json`, so `load_run` returned `None`
and the harness died on `'NoneType' object has no attribute 'champion'`. **The app was
never affected**: `lab_bridge.find_latest_run` skips directories lacking `SUMMARY.json`,
which is exactly this case. The harness now uses the same rule.

### The hold, and its proof

The user's instruction mid-session: nothing may write to the real Lab tree until the Lab
is declared clear. That is encoded, not remembered — `AI4CM_ALLOW_LAB_WRITES`, defaulting
to **off**, gating installation, the tracked scorecard, and live publication.

It is an operator switch because the agent *cannot detect the condition*. The Lab's data
file and its published issues are gitignored, so a clean `git status` proves nothing about
whether another session is mid-regeneration.

Verified at the end of the session against the snapshot taken at the start:

| | snapshot | at end |
|---|---|---|
| `forecasts/scorecard.csv` | `8e3f5035…` | `8e3f5035…` |
| `master_daily_clean_treasury.csv` | `0b009fd0…` | `0b009fd0…` |
| published issues | `2025-08-06 2026-08-13 2026-08-16` | unchanged |
| vault issues | `2025-08-06 2026-08-13 2026-08-16` | unchanged |
| `*.bak.csv` created | — | **0** |

The only Lab working-tree changes are the concurrent session's three pipeline files.

---

## 3. A correction to what I told the user while planning

I reported the scorecard as **27 columns with no ops comparator**, and said "skill vs ops"
could only be answered with "not recorded". That was true when read and stale within the
hour: the concurrent Lab session shipped schema version 2 the same day, adding `ops_pred`,
`ops_abs_error`, `skill_vs_ops` and `ops_source` — **31 columns**. The brief's "and vs ops
where the columns exist" is fully satisfiable, and is satisfied.

The design consequence outlived the correction: **nothing in this repo hardcodes the
column list.** Every optional figure is emitted where its column exists *and* carries a
value, and absence is reported as absence. A report written to the 27-column schema would
have silently dropped a comparator the Lab had just added.

---

## 4. What was built

### `agent/data_intake.py` — four rejections, one disclosure

Entirely agent-side: no Lab code, no subprocess, no writes.

1. **Schema** — a missing column dies deep inside pandas with a bare `KeyError`, long past
   where a human could tell what was wrong with their file.
2. **Does not extend** — no new truth to score, no new origin to forecast from.
3. **Same checksum** — Session 5's guardrail, reached through intake.
4. **Truncated history** — *the dangerous one*. A user who exports "the new rows" rather
   than "the updated series" produces a well-formed CSV with the right columns, later
   dates and a different checksum. It passes 1, 2 and 3. Installing it would discard ~4000
   rows of Treasury history the models train on — and the file it replaced is gitignored,
   so nothing would record the loss. The check is **containment**, not length: every date
   the current file has, the candidate must also have.

A **revision** to values in the overlap is reported with a count and examples, never
refused. It is legitimate, and it changes what an already-published forecast will be
scored against — the Lab's scorer reports the same thing from the other side as
`baseline_disagreements`.

`install()` never overwrites in place: it writes a timestamped backup first and refuses if
that backup already exists, because the backup is the only copy of what is being replaced.

### `agent/lab_score.py` — the second door under the Lab's interpreter

Calls exactly one Lab function, `published_forecasts.score_published`, and does no
arithmetic. A separate file from `lab_entry.py` so a scoring change cannot break Session
5's verified publish path.

It exists because the Lab's `run_publish_and_score.py` hardcodes both its data path and
its scorecard, *and publishes from the shared forward directory before scoring* — the
exact write Session 5's per-target staging avoids. Neither path can be redirected, so it
cannot be used for a verification run.

**Pending rows exist nowhere on disk.** `score_published` rewrites the scorecard and writes
only rows whose truth arrived, so a three-row scorecard cannot be distinguished — from the
file alone — between "three forecasts were made" and "twenty-five were, and twenty-two are
waiting". The pending count lives only in the return value, so it travels on the event
stream. Reporting scored rows without it would present a partial scoring as a complete one.

### `agent/run_report.py` — `score_report()`

Per issue and target: P50, actual, absolute error, inside-interval. Per-target aggregates
come from the Lab's own `summarize_scorecard`; per-row figures are read from the scorecard
CSV with `csv.DictReader` rather than pandas, so a column the agent does not know about
travels through untouched. Nothing is recomputed here.

### `agent/run_intent.py` — the data intent

`classify_data` reuses Session 5's absolute rule-1, and adds a **yes/no opener** screen it
deliberately does not share with `classify`. The reason is asymmetric: `classify` must keep
*"can you run today's forecast?"* as an instruction, where `can` opens a polite imperative.
The data intent has no such need, and `"is the data fresh?"` shares every noun with
*"here is the new data"* — differing only in mood. Found by a failing test, not by review.

### `app.py` — three separately confirmed steps

TAKE → SCORE → RUN, each with its own yes. **Scoring is offered before forecasting on
purpose**: scoring answers "was the last forecast any good", and a treasury should see
that answer before being offered a new number. Reversing them puts a fresh forecast in
front of the evidence about the previous one.

A held run still does everything it safely can — it scores against the candidate file into
a staging scorecard, because the Lab's scorer takes the actuals as an argument and nothing
of the user's is replaced.

---

## 5. Verified for real

### The real path, on real artifacts

Through the agent's own `lab_score.py`, real Lab code, everything redirected:

```
{"event": "start", …, "issues": 3}
{"event": "scored", "scored": 0, "pending": 25, "issues": 3, "summary": {}, …}
{"event": "finish", "scored": 0, "pending": 25, "refused": false}
```

**0 scored, 25 pending** — the honest result, because every published target date is beyond
the data end of 2025-08-06. There are no new actuals anywhere: `master_daily_clean_treasury.csv`,
`master_daily_raw.csv`, `master_daily_clean_conservative.csv` and the `data_preprocessed`
export all hold 3867 rows ending 2025-08-06.

### The scored path, on SYNTHETIC actuals — sandbox only

**Every number in this subsection is arithmetic on fabricated truth and describes nothing
about real Treasury performance.** The actuals were generated as
`last_observed × (1 + 0.03·i)` for the five published target dates: deterministic, plainly
not real, and offset from the persistence ruler so the skill arithmetic is exercised rather
than degenerating to zero error. Written to
`…/sandbox/SYNTHETIC_actuals_do_not_publish.csv`, scored into a sandbox scorecard, against
a **copy** of the published issues. It never touched `forecasts/scorecard.csv` or the
canonical data file.

The file was first put through the real intake validator, which accepted it and reported
5 new dates, 44 columns matching, 0 revisions.

```
scored: 25   pending: 0   issues: 3   baseline disagreements: 0
```

Scorecard written with **31 columns, identical to the Lab's committed header**. One
verification the synthetic data made possible and the real data cannot yet: the stock
target's ops comparator is absent, and the report quotes the Lab's own reason for it rather
than substituting a figure —

> Skill vs the Treasury's current method: not recorded — not defined: the Treasury method
> aggregates a flow to an annual total, and a balance level has no annual total.

### Rehearsal answers

Hashed against the frozen Lab before any code was written and after everything:
`diff` empty, all six unchanged — and identical to Session 5's hashes, so the Lab's new
commits moved none of them either.

```
a c307a644…  b f7b422a9…  c 577769b8…  d b70fa083…  e 86e61e33…  f 377d02d7…
```

---

## 6. Tests

**509 passed** (was 476 after Session 5's 454 plus the Lab-driven additions).

| File | Tests | What it defends |
|---|---|---|
| `test_data_intake.py` | 20 | the four rejections, revision disclosure, backup discipline |
| `test_data_flow.py` | 33 | three-step consent, **no scoring subprocess without a yes**, the hold |

Two bugs were found by tests rather than by review, and both are recorded above: the
`"is the data fresh?"` misroute (§4), and a `Path(None)` crash when writes are permitted
and the scorer does not echo its scorecard path — which would have turned a successful
production score into a `TypeError` on the one configuration this session could not run.

---

## 7. The held step, released and run

The user lifted the hold once the Lab session finished (`4bacc75`, clean, in sync with
origin). `AI4CM_ALLOW_LAB_WRITES=1`, `scorecard=None` — the Lab's own real, git-tracked
`forecasts/scorecard.csv` — driven through the agent's own `stream_score`, exactly as
`app.py` does when writes are permitted.

```
writes allowed : True
lab interpreter: ~/Projects/AI4CM/backend/.venv/bin/python
scorecard      : None -> the lab's own forecasts/scorecard.csv

  event: start   {'issues': 3}
  event: scored  {'scored': 0, 'pending': 25, 'issues': 3,
                  'scorecard': '~/Projects/AI4CM/forecasts/scorecard.csv'}
  event: finish  {'scored': 0, 'pending': 25}

exit code   : 0
stderr bytes: 0
```

**The honest result, which is the deliverable:** 0 scored, 25 pending, across 3 issues.

### What changed in the Lab tree: nothing

| | before | after |
|---|---|---|
| `forecasts/scorecard.csv` sha | `8e3f5035…` | `8e3f5035…` |
| mtime | `Aug 18 20:27:53` | `Aug 18 21:32:08` |
| rows | 0 | 0 |
| `git status --porcelain` | 0 lines | **0 lines** |
| data file / issues / vault / backups | — | unchanged / unchanged / unchanged / 0 |

The scorer really did open and rewrite the file — the mtime moved — and the bytes are
identical, because a run that scores nothing writes a header, and the header was already
correct after the Lab's 6-prep regenerated it.

**So there is nothing to commit in the Lab.** That was not the expected outcome on either
side, and it is the correct one: an empty scorecard is what "no truth has arrived yet"
looks like on disk, and a commit would have been a commit of no change.

### A reporting bug the real run exposed

The first production run printed *"25 target-date(s) are still waiting"* and then listed
fifteen. Both numbers were read correctly and they count different things: the Lab returns
`pending` as a **row** count and `pending_dates` as a **set**. Revenues is published in
three issues, so one target-date is pending three times — 25 rows over 15 distinct dates.

Printing the first and listing the second is arithmetic that does not add up in front of a
reader, which is the kind of thing that costs confidence in every other number on the page.
Corrected to state both, and to say why they differ. Found by running the real path; no
fixture had a target published in more than one issue.

---

## 8. What is still NOT done, and why

**No installed data, and no first real agent-run issue.** The hold is lifted; the remaining
block is not procedural:

**There are still no actuals past 2025-08-06.** Step 3b needs real new data to produce
anything but the same numbers under a new issue date, and the Session 5 guardrail will say
so before it runs. `AI4CM_ALLOW_LAB_WRITES=1` does not change that and was never meant to.

Synthetic actuals must never be used to get past it. Fabricated truth in
`forecasts/scorecard.csv` would corrupt the Lab's track record permanently, and a published
issue is immutable.

---

## 9. Verification summary

| Check | Result |
|---|---|
| Full suite | **509 passed** |
| Rehearsal answers vs baseline | **byte-identical, all six** |
| Real scoring path (verification, redirected) | **0 scored / 25 pending / 3 issues** |
| **Production score into the Lab's real scorecard** | **exit 0, 0 scored, 25 pending, no stderr** |
| Lab tree after the production score | **0 tracked changes; scorecard byte-identical** |
| Synthetic scoring path (sandbox) | **25 scored, 31-column schema matching the Lab's header** |
| Lab scorecard / data / issues / vault | **unchanged, by checksum** |
| Backups created in the Lab | **0** |
| App starts (`streamlit run`, headless) | **HTTP 200, no exceptions** |
