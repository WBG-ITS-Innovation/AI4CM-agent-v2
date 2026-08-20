# Session 5 — the agent action layer: run, publish, verify, download

**Repo** `ai4cm-agent-v2` **Branch** `feat/lab-door`
**Start** `4522a59` **Lab at start** `b022666` → **Lab at end** `fdd7a02` (moved by
the user mid-session, not by this work)

The agent could describe the lab's forecasts. It can now ask the lab to make one — and
only ever by invoking the lab's own audited entry points, with a confirmation step in
front and an artifact-level verification behind.

**The Lab was not modified.** One Lab change is worth making and is written up in §8 for
a separate session.

---

## 1. The finding that decided the design

The brief assumed the agent would call the Lab's forward-forecast runner. Reconnaissance
found something better and something worse.

**Better:** the Lab already publishes exactly the door this session needs.
`backend/forecast_modes.py::_cli` exists so *"a frontend can run a forecast WITHOUT
importing the modelling stack… One interpreter owns the models; the frontend reads
JSON."* The Lab's own Forecast page dispatches to it. No new Lab door was required.

**Worse:** that CLI cannot publish, for a reason that is a property of the data rather
than of either repo.

```
_cli --publish  ->  publish_official(res)          # no issue_date passed
                ->  publish(src, issue_date=None)
                ->  issue_date = max(origin_date)  # == 2025-08-06
                ->  FileExistsError: forecasts/published/2025-08-06 exists
```

The champion recipes forecast forward from the **end of the data**, and the data has not
moved since the first issue was published from it. So the derived issue date is always a
date that already exists. `forecast_modes` carries the remedy in the same module —
`next_issue_date()` — and Session 2 used it, by hand, to publish `2026-08-16`. The CLI
never wires it up. The Lab's own page has the same hole: its checkbox reads *"Publish to
forecasts/published/ under a new issue date"* and no date is passed.

Two ways forward were put to the user: **(A)** compose the Lab's three public functions
from an agent-side script, zero Lab modification; **(B)** add `--issue-date` to the Lab
CLI. The user approved **(A)** for this session and **(B)** as a separate Lab session.
§8 is the proposal text.

---

## 2. What was built

Four new modules in `agent/`, one new tier in `app.py`, four new test files.

### `agent/lab_entry.py` — the only file that runs under the Lab's interpreter

Composes the Lab's own public functions in the Lab's own documented order:

```
next_issue_date()  ->  official_run()  ->  publish_official(issue_date=…)
```

No forecasting, no gating, no publishing, no retention. It does not decide which targets
are publishable — `publish_official` refuses a `withheld` target and this script reports
the refusal **in the Lab's own words**. Emits one JSON object per line on stdout.

**It never puts a forecast level on the pipe.** This was not the first draft. The first
live run exposed the ordering: `official_run` *succeeds* for a withheld target — the
refusal happens one line later, inside `publish_official` — so at that moment
`result.forecasts` holds p10/p50/p90 for a series the gates are about to withhold. The
draft emitted them as a `target_forecast` progress event, which would have put withheld
numbers into the agent trace the user can expand. That is `agent/official.py`'s rule 2
broken by a side door: the gates withheld the numbers, and quoting them *as progress*
republishes them as surely as quoting them as an answer.

The event now carries shape only (`n_rows`, `n_estimators`), never value. Verified
against the real Lab:

```
$ … lab_entry.py --lab … --target Expenditure --published-root <staging>
grep -c '"p10"|"p50"|"p90"'  ->  0
{"event": "target_ran", "target": "Expenditure", …, "n_rows": 5, "n_estimators": 20}
{"event": "target_refused", "target": "Expenditure", "stage": "publish", "reason": "Refusing to publish 'Expenditure': its current verdict is 'withheld'…"}
```

### `agent/run_intent.py` — routing, and consent

The tier's misfire is a **write**, so the rule is grammatical rather than lexical. An
informational opener (`how/what/why/when/where/who/which`) makes a message a question and
no run verb overrides it — that is the brief's trap, *"how do I run a forecast?"*, and
also `what happens when you run one?`, `why did the forecast run fail?`. A modal directed
at the agent (`can you`, `please`) is a polite imperative and does run.

Rule 1 is deliberately absolute and over-broad. Its cost is a rephrase; rule 3 being
over-broad costs a published issue. Those are not the same mistake and do not get
symmetric treatment.

`read_consent` returns **three** values, and `unclear` never runs anything. Whole-message
anchored matching, because a substring search reads `"no, wait — what does the gate
check?"` as consent on the strength of `wait`, and `"is that ok?"` as consent on `ok`.

`Forecast Revenues 30 days ahead` — rehearsal question (f) — stays a question, because
`forecast` is not a run verb. Adding it would have silently converted a horizon refusal
into a publish attempt.

### `agent/run_exec.py` — the boundary, and the three guards

- **The lock.** A file, not a session flag: two browser tabs are two sessions. Stale
  holders (dead pid, or older than an hour) are treated as free, so a crashed run cannot
  lock the agent out permanently. `O_CREAT | O_EXCL` so two arrivals cannot both win.
- **The unchanged-data check.** `sha256(data)` against the last issue's
  `manifest.data_sha_at_issue`. Not hypothetical — see §4.
- **Honest failure.** `stderr` goes to its own file and is read back verbatim; merging it
  into stdout would put a traceback in the middle of the NDJSON stream. `failure_message`
  shows the Lab's own output, last 40 lines, and always ends *"Nothing was published."*

`plan_run` builds the plan once and `confirmation_text` is derived **from that plan**, so
the run that executes is the run that was described.

### `agent/run_report.py` — what happened, read off the disk

A subprocess that says it published is not evidence that it published. The run's `finish`
event is used for exactly one thing — knowing which issue directory to go and read.

`verify_retention` compares the published issue with the vault copy **file by file, by
checksum**, including the estimator blobs. Presence alone is not enough: a truncated
mirror is a retention failure that a name-only check reports as success. Four-valued
status (`verified | missing | differs | unknown`), because "no vault copy" and "a vault
copy that differs" are different problems with different fixes.

The reason this is not taken on trust: Session 2's run reported a successful publish and
had retained nothing durable — `publish()` wrote to the gitignored
`forecasts/published/` and nothing mirrored it, so the only record of the forecast would
have vanished at the next clean checkout. The run's output looked identical in both
worlds. Session 2.5 fixed `publish()`; this verifies the fix on every run.

Downloads carry the issue date in the filename (`ai4cm-forecast-2026-08-17.csv`), because
a bare `forecast.csv` is indistinguishable from every other issue's, and a treasury
comparing two of them has no way to tell which came first. Missing files are returned as
*unavailable with a reason* rather than omitted.

### `app.py` — the ACTION tier

Sits above the three answering tiers in routing and below them in trust. The turn is
split across two messages because Streamlit reruns the whole script on every input —
there is no way to wait inside one turn for a yes. That constraint produces exactly the
behaviour the brief asks for.

`action_turn` returns `False` for anything that is not its business, and the answering
path below runs **unchanged**. That is the mechanism that keeps §6 true.

---

## 3. The principle, tested

> An agent-run forecast must be byte-equivalent in structure and provenance to a
> terminal-run one.

```
$ diff <staging>/published/2026-08-17/forecast.csv \
       AI4CM/forecasts/published/2026-08-16/forecast.csv
BYTE-IDENTICAL
```

Same numbers, same columns, same ordering as the issue a terminal run produced. Same
`data_sha`, same `test_window_touched: false`, gates carried by `recipe_id` from the DEV
credentials run, estimators retained, vault mirrored.

---

## 4. The unchanged-data guardrail is live, not theoretical

```
data sha       : 0b009fd031ad3fa0…
last issue     : 2026-08-16
last issue sha : 0b009fd031ad3fa0…
DATA UNCHANGED : True
```

Byte-identical. So the guardrail fires on the very first real request, and the
confirmation says so before the user agrees:

> ⚠️ **The input data has not changed since the last issue.** Its checksum is identical
> to the one recorded in issue `2026-08-16`, so this run would reproduce the same numbers
> under a new issue date. Published issues are immutable and are the lab's track record,
> so a duplicate would make that record overstate how often a forecast was actually made.
> Say **run anyway** if you want it regardless.

---

## 5. End-to-end, real models, staging publish

Driven through the agent's own modules — `plan_run` → `stream_run` → `report` →
`verify_retention` — against the real Lab, real champion recipes, real data, published
into a staging root and staging vault so no live issue was minted.

```
[   0.7s] start              2026-08-17
[   0.7s] target_start       Revenues
[  43.3s] target_ran         Revenues
[  43.7s] target_published   Revenues
[  43.7s] target_start       Expenditure
[  79.0s] target_ran         Expenditure
[  79.0s] target_refused     Expenditure
[  79.0s] target_start       State budget balance
[  79.1s] (lab stdout) [config] Auto-enabled delta modeling for stock target 'State budget balance'
[  97.9s] target_ran         State budget balance
[  97.9s] target_refused     State budget balance
[  97.9s] finish             2026-08-17
exit code    : 0
stderr bytes : 0

RETENTION  status: verified   files compared: 25   missing: ()   differing: ()

DOWNLOADS
  Forecast (CSV)         ai4cm-forecast-2026-08-17.csv        971 bytes
  Quality gates (JSON)   ai4cm-gates-2026-08-17.json         2404 bytes
  Provenance (JSON)      ai4cm-provenance-2026-08-17.json    1855 bytes
```

One published, two withheld by the Lab's own publishing code, 25 files verified into the
vault by checksum. The report quotes the Lab's refusal text verbatim and shows **no
levels** for either withheld target.

**Lab untouched:** `git status --porcelain` → 0 lines, before and after. Published issues
still `2025-08-06 / 2026-08-13 / 2026-08-16`; vault the same.

---

## 6. The Session 3/4 rehearsal answers are byte-identical

Not "the suite is green" — the six demo answers were hashed against the **real Lab**
before any code was written, and re-hashed after, through `app.answer_lab_question`'s
full routing path:

```
a c307a64429e8b2cbbd84b8094a4eec96589fc3bb9ab7b7bc9d85985c28d4719b
b f7b422a9d7995e08357861a1d23941be66407a588cdec9a7c4c66d0cf117d0ec
c 577769b888d67d688818ca048adeb57c8b867154daf0253485f069b321e43971
d b70fa083325821da85c3baa154331d7b4f7c461f9e203d680bea6a7ca3e8598a
e 86e61e339842bf9d3e286cd4a6c99ef78bda08a9de19a8c7c908441f1441f914
f 377d02d7d9cbd0067868c28cdd207bd021cae1c3a3c092b826f6f0f36525b2df
```

`diff BEFORE AFTER` → empty. All six unchanged.

They are also pinned as assertions in two places, so this cannot regress silently:
`test_action_routing.py` asserts none of the six reaches the ACTION tier, and
`test_action_confirmation.py` drives all six plus the trap through the real `action_turn`
and asserts each falls through.

---

## 7. Tests

**454 passed** (was 313).

| File | Tests | What it defends |
|---|---|---|
| `test_action_routing.py` | 77 | instructions run, questions never do; consent is three-valued |
| `test_action_confirmation.py` | 32 | **no subprocess without an explicit yes** |
| `test_action_execution.py` | 13 | streaming, real stderr, the lock |
| `test_action_report.py` | 15 | the report follows the disk, not the run |

Three further tests appeared in `test_model_framing.py` without being written: it globs
`agent/*.py`, so the new modules were automatically enrolled in the "no hardcoded model
counts" rule.

Two deliberate emphases:

- **The negative is tested harder than the positive.** `test_action_confirmation.py`
  replaces the subprocess launcher with a recorder that *fails the test if called*, then
  drives every non-affirmative path through it. The positive path is the one a developer
  exercises by hand a hundred times while building; the negative is the one nobody
  notices is broken until an agent publishes a forecast during a demo.
- **The subprocess is not stubbed** in `test_action_execution.py`. Those tests launch a
  real process, read a real pipe and a real stderr fd, because every bug this module can
  have lives exactly there — buffering, interleaving, and the exit code being read before
  the stream is drained.

---

## 8. PROPOSAL FOR A SEPARATE LAB SESSION — not applied

> **Do not apply this in the agent repo. It belongs to AI4CM.**

### `backend/forecast_modes.py::_cli` cannot publish

**Symptom.** `--publish` raises `FileExistsError` on the current data, so the Lab's own
Forecast page cannot publish either, despite its checkbox reading *"under a new issue
date"*.

**Cause.** `_cli` calls `publish_official(res)` without `issue_date`. `publish()` then
derives it from `max(origin_date)` — the end of the data — which is a date that already
has an issue.

**Proposed change.** Add an `--issue-date` argument, defaulting to the module's own
`next_issue_date()`:

```python
ap.add_argument("--issue-date", default="",
                help="official mode only; defaults to next_issue_date(), which "
                     "avoids colliding with an existing issue")
...
if a.publish:
    out["published_to"] = str(publish_official(
        res, issue_date=a.issue_date or next_issue_date()))
```

**Why the default and not just the flag.** A caller who omits it today gets a crash; a
caller who omits it after this change gets the behaviour Session 2 performed by hand. The
flag stays so a deliberate re-issue can name its own date.

**Test to add.** `--publish` twice in a row against a temp `published_root` produces two
issues (`<today>` then `<today>-r2`) rather than a `FileExistsError` — which is what
`next_issue_date` already promises and nothing currently exercises through the CLI.

**Second observation, same file, needs a decision rather than a patch.** `publish()`
keys the destination on issue date alone, so **an issue holds one target**. With today's
registry exactly one target is publishable, so it never bites. If a second target ever
becomes publishable, the second `publish_official` call with the same `issue_date` will
raise `FileExistsError`. `agent/lab_entry.py` surfaces that honestly rather than routing
around it — inventing a `-r2` date for a second *target* would label it a re-issue, which
it is not. Whether one issue should hold several targets, or several issues should share
a date, is a Lab modelling decision and not the agent's to make.

---

## 9. Smaller findings, recorded

**The Lab prints to stdout.** `[config] Auto-enabled delta modeling for stock target
'State budget balance'` and one more line appear on stdout during the State-budget-balance
fit. A strict NDJSON parser either crashes on them or drops them silently. The reader
requires both valid JSON *and* an `event` key, and keeps everything else as trace. Pinned
by `test_library_noise_on_stdout_is_kept_as_log_not_parsed_as_a_result`.

**`git_dirty` is worth surfacing after a run, not just when reading.** The first staging
run recorded `git_dirty: true` / `git_dirty_files: 1`, and the second recorded
`git_dirty: false`. Nothing the agent did caused either: a Lab commit (`fdd7a02`) was in
progress during the first. A run launched while someone is mid-edit in the Lab produces
an issue that cannot be reproduced from its recorded SHA, so `run_report` now says so
explicitly — *"The forecast is real; its reproducibility is not guaranteed."*

**An empty `backend/forecast_runs/forward/staging/` is left behind.** `publish_official`
removes the per-target staging subdirectory on success but not its parent. Gitignored,
empty, zero effect on `git status`. The Lab's own code, not the agent's; noted, not
touched.

**The Lab moved during the session.** `b022666` → `fdd7a02`, and `model/excellence` went
from *ahead 2* to in sync with origin. The user's own work, confirmed by reflog.

---

## 10. Verification summary

| Check | Result |
|---|---|
| Full suite | **454 passed** |
| Rehearsal answers vs pre-session baseline | **byte-identical, all six** |
| Agent-run forecast vs terminal-run issue | **byte-identical** |
| Retention verified by checksum | **25/25 files** |
| Withheld levels on the event stream | **none** |
| Lab working tree, before and after | **0 changes** |
| Lab published issues, before and after | **unchanged** |
| App starts (`streamlit run`, headless) | **HTTP 200, no exceptions** |

**Not done, deliberately:** no live issue was published. Every real run in this session
went to a staging root with a staging vault. Publishing a genuine `2026-08-17` issue is a
one-word decision for the user, and the unchanged-data guardrail means the agent will
warn before it does — which is the behaviour worth keeping, not worth spending on a
demonstration.
