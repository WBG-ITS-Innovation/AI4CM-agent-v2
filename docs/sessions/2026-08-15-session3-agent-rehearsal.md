# Session 3 — agent demo rehearsal against the 2026-08-16 issue

**Date:** 2026-08-17 (the filename carries the requested `2026-08-15` slug; the work ran on the 17th)
**Repo:** `ai4cm-agent-v2`, branch `feat/lab-door`
**Counterpart:** `AI4CM` (the Lab), branch `model/excellence`, published issue `forecasts/published/2026-08-16`
**Audience the rehearsal was for:** Georgia Treasury team, non-technical, live demo

---

## 1. The prompt, verbatim

> Session 3: Agent demo rehearsal against the fresh 2026-08-16 issue.
>
> CONTEXT:
> - The Lab (this repo) now has a clean published issue: forecasts/published/2026-08-16, Revenues publishable (MASE 0.757959), Expenditure withheld (fails accuracy_vs_naive and signal), State budget balance withheld (fails accuracy_vs_naive). Provenance is clean: git_dirty false, SHA reachable.
> - The Agent is the separate repo AI4CM-agent-v2 (conversational Streamlit interface that reads Lab artifacts). Tomorrow it will be demoed live to the Georgia Treasury team (non-technical audience).
> - Goal: NOT new features. Verify the agent runs against the current artifacts and answers the demo questions correctly, with correct verdicts and no stale data.
>
> TASKS:
> 1. Locate how the agent discovers Lab artifacts (path config / env). Point it at the current Lab outputs including the 2026-08-16 issue. Report what it reads and from where.
> 2. Launch the agent (streamlit run) and verify it starts with no errors against real artifacts. Report startup output.
> 3. Walk this demo script and report each answer verbatim with a pass/fail judgment:
>    a. "What is the latest forecast for Revenues?" - must show 2026-08-16 numbers with P10/P50/P90, labeled official.
>    b. "Which model is best for Revenues?" - must name the champion recipe and cite evidence (MASE/skill), not invent numbers.
>    c. "Why is Expenditure not published?" - must state the withheld verdict with the gate reason (fails accuracy vs naive; sentinel reading also failing), in plain language, not hide the target.
>    d. "Why is State budget balance not published?" - withheld, accuracy_vs_naive, MASE 1.57832 vs naive.
>    e. "How accurate have the forecasts been?" - must draw on recorded evaluation evidence, and must NOT present withheld targets' numbers as official estimates.
>    f. One off-script stress question: "Forecast Revenues 30 days ahead" - correct behavior is refusing official framing (validated horizon is 5 business days) or clearly labeling anything longer as exploratory/not gated. It must not fabricate an official 30-day forecast.
> 4. Anything that fails: diagnose, propose the minimal fix, wait for my approval, fix, re-verify. No feature additions, no UI redesign tonight.
> 5. Confirm no client raw data is displayed anywhere the demo script touches beyond what published artifacts intentionally show.
>
> RULES:
> - Plan first, wait for approval. One task at a time. Real output only.
> - Use each repo's own venv python explicitly.
> - If the agent needs the Azure OpenAI key and it has been rotated, tell me exactly which env var to update and wait; do not print the key.
> - End with the full session record at docs/sessions/2026-08-15-session3-agent-rehearsal.md (in the agent repo), including a demo-readiness verdict: READY / READY WITH CAVEATS / NOT READY, with the caveats listed.

The stated goal was "NOT new features". Reconnaissance established that four of the six
demo questions could not be answered without a new read path, which made that
constraint and the demo script mutually exclusive. That was surfaced before any code
was written; the operator chose the read path and explicitly overrode the no-features
rule. Everything in §5 onward exists under that override.

---

## 2. Task 1 — what the agent reads, and from where

Discovery is one function, `_candidate_roots()` in `agent/lab_bridge.py:40-45`, tried
in priority order:

| # | Source | Resolves to |
|---|---|---|
| 1 | `AI4CM_RUNS_ROOT` | used verbatim as the `forecast_runs` dir |
| 2 | `AI4CM_REPO` | `<repo>/backend/forecast_runs` |
| 3 | sibling fallback | `<parent>/AI4CM/backend/forecast_runs` |

There is no `LAB_ROOT`, `PUBLISHED_DIR`, or settings module. `.env` holds only the four
`AZURE_OPENAI_*` variables. `README.md:29` documents only options 1 and 2.

Run against the real Lab, using the agent's own code:

```
candidate roots:
   ~/Projects/AI4CM/backend/forecast_runs | exists: True
find_latest_run -> ~/Projects/AI4CM/backend/forecast_runs/2026-08-12
```

Per run it reads `SUMMARY.json`, `SUMMARY.txt`, and per-family `predictions_long.csv`,
`leaderboard.csv`, `metrics_long.csv`, `integrity_report.json` (the last for six
coverage keys only). Separately it reads `registry/recipes.json` — and, as of this
session's start, extracted **only the target names** from it
(`official_targets`, `lab_bridge.py:339-365`).

### The finding that reframed the session

**The agent never read `forecasts/published/` at all.** No `forecast.csv`, no
`manifest.json`, no `gates.json`, no `provenance.json`. Grepping the whole codebase for
`publication`, `failing_gates`, `dev_credentials`, `MASE`, `accuracy_vs_naive` and
`reason_plain` returned zero hits. The gap was already recorded in this repo's own
audit note at `docs/sessions/2026-08-11-agent-contract-consumption.md:347-350`.

Two facts compounded it:

* `find_latest_run()` selects the lexicographically highest date dir under
  `backend/forecast_runs/` that contains a `SUMMARY.json`. That is **`2026-08-12`**,
  whose `target` is **State budget balance** — a withheld target, all three families
  `WITHHELD — persistence-like`, `freshness.stale: true`. The only run covering
  **Revenues** is `2026-08-04`.
* `forecasts/scorecard.csv` is header-only. **No published forecast has ever been
  scored against a realized outcome**, so no realized-accuracy claim is available to
  any consumer.

So the demo script was written against the Lab's published forward issue, and the agent
consumed backtest run summaries. An architecture gap, not a defect.

### Hazards found in the Lab while establishing the above

1. **A stale issue carrying withdrawn numbers.** `forecasts/published/2025-08-06/` holds
   full p10/p50/p90 rows for **Expenditure and State budget balance**, over the *same
   target dates* (2025-08-07 → 2025-08-13) as the current issue. It predates P2; the
   registry records `superseded_verdict: "publishable"` for State budget balance. Any
   consumer that globs issue dirs, or falls back when a target is missing from the
   latest one, surfaces gated-out numbers with plausible dates attached. Its
   `provenance.json` also records `git_dirty: true` (20 files).
2. **`forecasts/published/` is entirely gitignored** (`.gitignore:72`). No issue is
   tracked, so the agent reads nothing from a fresh clone. Fine for a local demo.
3. **`reports/DEMO_RUNBOOK.md` §1A is inverted.** It instructs the presenter to point at
   "one green banner (State budget balance), two red WITHHELD banners." Under P2 the
   publishable target is **Revenues**. Not touched — it is a Lab-repo doc and outside
   this session's scope — but anyone following it narrates the wrong verdicts. **This is
   the single highest-value thing left outstanding.**

---

## 3. Task 2 — startup: PASS

`AI4CM_REPO=~/Projects/AI4CM ./.venv/bin/streamlit run app.py`

```
Uvicorn server started on :::8503

  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8503
```

No errors. The only warning is a Watchdog performance suggestion. Both venvs are
Python 3.13.11; agent Streamlit is 1.61.1.

### The Azure key — undeterminable here, and that is not evidence against it

`have_llm: True | healthcheck: (True, 'AzureOpenAI') | deployment: gpt-5.4`, then
`APIConnectionError`. Diagnosis: a TLS-intercepting proxy on this machine injects a
self-signed certificate into the chain. DNS resolves (`20.232.91.180`), and a control
request to `example.com` fails identically with
`CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`. It persists
with the sandbox disabled. `agent/llm.py:_patch_ssl()` points `SSL_CERT_FILE` at
certifi, which cannot help against a MITM proxy — that needs the proxy's own CA.

A probe with verification disabled would have separated "key rotated" from "TLS
intercepted"; it was refused by the environment's policy classifier and **not worked
around**. So:

* **No env var needs changing on this evidence.** The key is 84 chars, the endpoint is an Azure
  OpenAI resource whose name is not recorded here, deployment `gpt-5.4`, API version `2024-06-01`.
* **The operator must confirm the LLM in a real browser session.** The sidebar
  healthcheck will read `AzureOpenAI` regardless; only a live answer is proof.

**Resolved on 2026-08-20.** It was TLS interception, not a rotated key, and it needed no env var
changed permanently: certifi's bundle lacks the intercepting root, and a bundle including the
machine's own keychain roots succeeds. See the addendum to
[the 2026-08-20 record](2026-08-20-agent-verify-smoke-and-public-prep.md).

This mattered more than it first appeared — see §6.

---

## 4. Task 3 — the baseline: 1 pass, 5 fail

Driven through `answer_lab_question()` (`app.py:543`), the rule-based path, against the
real run. Verbatim answers were captured; judgments:

| Q | Verdict | What it actually said |
|---|---|---|
| a | **FAIL** | "I can't quote a trustworthy forecast on this run." No 2026-08-16 numbers, no intervals. Safe, useless |
| b | **FAIL** | "**No model earned trust on this run**, so I won't crown a winner." The opposite of the truth — Revenues is publishable at MASE 0.757959. No recipe named, no MASE cited |
| c | **FAIL** | Asked about **Expenditure**, answered with A_STAT/B_ML/E_QUANTILE verdicts for **State budget balance**, with the wrong gate reasons (`persistence-like`, not `accuracy_vs_naive` + `signal`). Named no target, so it *read* as an answer about Expenditure |
| d | **FAIL** | Byte-identical text to (c). Target coincidentally matched; reasons still wrong; MASE 1.57832 absent |
| e | **FAIL** | Same "no model earned trust" text. No evaluation evidence at all |
| f | **PASS, accidentally** | Refused a 30-day forecast — but because no model passed on this run, not because 30 exceeds the validated 5. Would refuse a legitimate 5-day request identically. No horizon guard existed |

**The failure mode was not fabrication.** Nothing invented a number anywhere;
`contract.py`'s refusal discipline held throughout. It was **target-blindness**:
`answer_lab_question` never looked at which target the question named, and answered
everything from a single-target backtest run. (c) and (d) returning identical text for
two different targets would have sunk the demo on its own.

Three separable defects: no read path to the published issue; `recipes.json` read for
names only; routing that ignores the named target.

---

## 5. The fix

Three new modules and one rewritten routing function. All reads, no writes to Lab data.

### `agent/published.py` — the published issue as a contract

Reads `forecasts/published/*/{manifest,gates,provenance}.json` + `forecast.csv`.
Two rules carry the safety:

* **Only the latest issue is ever read.** A target missing from it is *not published
  today*, full stop. No backward search. This is what keeps the 2025-08-06 issue's
  withdrawn Expenditure and State-budget-balance numbers out of every answer.
* **Absence is never a value.** Callers branch on structure, not on whether a number
  happens to be `None`. A manifest-listed target with no CSV rows is recorded as an
  inconsistency, not an empty forecast.

Contract §7 checks enforced at read time: crossed quantiles drop the row with a defect,
`origin_date >= target_date` drops the row, a `y_true` column on a forward issue is
flagged, `git_dirty` is disclosed rather than assumed.

### `agent/registry_read.py` — the publication verdicts

Per target: `recipe_id`, `point_model`, `family`, `publication.verdict`,
`failing_gates`, `reason_plain`, `named_fix`, `dev_credentials.mase`,
`sentinel_ratio`, `skill_vs_ruler_pct`, and the tri-state gates. An unrecognised
verdict string becomes `""` (unknown) rather than being coerced to either valid value.
`approved_by` is surfaced so nothing can render as approved while it is null.

### `agent/official.py` — the answers

Composes the two. Three rules, stated in the module docstring because they are the
whole point:

1. **Resolve the target first.** `resolve_target` returns `None` rather than a guess.
   Aliases are matched **longest-first**, so "state budget balance" is not shortened to
   "balance" — `intents.parse_intent_rules` iterates shortest-first and keeps the *last*
   match, which gets that one target exactly wrong.
2. **A withheld target never emits a level.** Not p50, not p10/p90, not an origin value
   dressed as an estimate. It gets the verdict, the gates it failed with measured-vs-
   threshold, and the registry's own `reason_plain`.
3. **The champion is the registry's, not a family's.** Contract §1's distinction is
   both respected and stated in the answer text.

Horizon requests are parsed at **business-day** rates (a week is 5, not 7) because the
Lab's horizon is counted in business days; converting at calendar rates would understate
the gap between "30 days" and a 5-business-day horizon.

### Routing — `answer_official_question()` in `app.py`

Runs **before** the run-based answers. Returns `None` for questions that are
legitimately about "this run" so they fall through unchanged. Accuracy questions
outrank the forecast branch, distinguished by the presence of an explicit horizon
rather than by the noun — "how accurate have the *forecasts* been" contains the word
but asks about a track record.

---

## 6. The bug my own fix nearly shipped with

The rehearsal passed after the routing change. It passed **because Azure was
unreachable**, and that is the only reason.

`app.py` computed a rule-based answer only when the model produced nothing:

```python
stream = answer_stream(q, run, targets, history)
if stream is not None:
    ans = st.write_stream(stream)
if not ans.strip():
    ans = answer_lab_question(q, run)      # only on an empty stream
```

`answer_stream` is grounded in `build_agent_context(run, ...)`, and a run covers one
target's backtest. **With a working backend — the demo case — the model would have
answered these questions from the State budget balance run**, while the new
artifact-sourced answers sat unused behind a branch only a broken backend could reach.
A rehearsal without a key passes; the demo would not have.

Fixed two ways:

* **The artifact answer is now the source, and the model may only rewrite it.**
  `official_narrative_stream()` follows the existing `run_narrative_stream` idiom:
  the deterministic text is handed over as `factual`, with absolute instructions to keep
  every figure, keep the word "withheld" unsoftened, and supply no value for a withheld
  line. If the model is unavailable or returns nothing, the deterministic text renders
  directly.
* **`build_agent_context` now carries `official_publication_status`** — every target's
  verdict, the latest issue date, the validated horizon, the approval rule and the
  accuracy rule. So off-script questions the model does handle cannot contradict the
  publication state. For a withheld target the block carries an explicit
  `forecast_numbers: "WITHHELD — do not quote, estimate, or reconstruct"` **and no
  levels at all**: a number in the context window is a number that can be echoed.

Context budget raised 14,000 → 16,000. Measured against the real Lab: **10,542 chars,
valid JSON, 34% headroom** (the official block is 2,306 of it). See §9 for the residual
risk.

---

## 7. Task 3 re-run — 6 of 6 pass

Same script, same code path, real artifacts. Verbatim:

**(a)**

> **Official forecast for Revenues** — issue `2026-08-16`, 5 business days ahead, from the champion recipe `revenues-lgbm-l1-ws3-v1` (LightGBM_L1).
>
> | Date | Low (P10) | **Central (P50)** | High (P90) |
> |---|---|---|---|
> | 2025-08-07 | *[redacted]* | ***[redacted]*** | *[redacted]* |
> | 2025-08-08 | *[redacted]* | ***[redacted]*** | *[redacted]* |
> | 2025-08-11 | *[redacted]* | ***[redacted]*** | *[redacted]* |
> | 2025-08-12 | *[redacted]* | ***[redacted]*** | *[redacted]* |
> | 2025-08-13 | *[redacted]* | ***[redacted]*** | *[redacted]* |

_(Amounts redacted for public release. The five business days, the P10/P50/P90
structure and the ordering are what this section is evidence of; the absolute
lari figures are attributed client performance data and are withheld under the
Lab's exposure rules. Ratios and percentages elsewhere in this record are
kept — they carry the argument without disclosing amounts.)_
>
> P10–P90 is the range the model expects the outcome to fall in four times out of five; P50 is the central estimate.
>
> For comparison, the last actual value before the forecast was made (2025-08-06) was **[redacted]**.
>
> This is published because it passed every quality gate — most importantly it is more accurate than simply repeating the same weekday last week (MASE 0.757959, where anything at or above 1.0 would be withheld).
>
> **Nobody has approved this model.** Its status in the lab's registry is _candidate -- pre-tuning_ — no approval workflow exists yet, so treat it as a candidate. Produced by code version `4a0ff5087c` from a clean working tree, so it can be reproduced exactly. Latest input data: 2025-08-06.

**PASS** — 2026-08-16, all three quantiles, labeled official, approval caveat present.

**(b)**

> For **Revenues** the lab promotes one champion recipe: `revenues-lgbm-l1-ws3-v1` — **LightGBM_L1**, from the B_ML family.
>
> Measured on DEV (2024) over 262 days:
>
> - **Accuracy vs the naive rule:** MASE **0.757959**, where 1.0 is break-even against that rule. Below 1.0, so it beats repeating the same weekday last week.
> - **Signal check:** shuffling the historical answers made its error **1.2255×** worse (the lab requires 1.15).
> - **Skill vs the benchmark:** 55.92% — reported, not gated. On its own this is weak evidence, which is why MASE is the binding accuracy test.
>
> Its numbers **are** published, in the lab's latest issue.
>
> **Nobody has approved this model.** Its status in the lab's registry is _candidate -- pre-tuning_ — no approval workflow exists yet, so treat it as a candidate.
>
> _One distinction worth keeping: this is the champion the lab's registry promotes for Revenues, which is what an official forecast uses. It is not the same as the best model within a single model family on a given run — that is a narrower comparison._

**PASS** — champion recipe named, MASE and sentinel cited from the registry, contract §1
distinction stated rather than merely respected.

**(c)**

> **Expenditure is withheld — the lab does not publish it as a forecast.**
>
> It failed 2 quality checks:
>
> - **accuracy vs repeating the same weekday last week** (measured 1.10385, the limit is 1)
>   Simply repeating what happened on the same weekday last week is 10.4% more accurate than this model. There is no reading under which this model's numbers are the best available estimate, so they are not published as a forecast.
> - **signal (shuffled-target control)** (measured 1.0882, the limit is 1.15)
>   Shuffling the historical answers barely worsened this model's error (1.09 times, where we require 1.15 times). That reading sits INSIDE the range that deliberately uninformative inputs produce on this data, so it is not distinguishable from no information at all. The model is tracking the typical level rather than anticipating events.
>
> Because of that, no forecast numbers are published for Expenditure at all — not shown with a warning, not published. The model behind it is `expenditure-lgbm-l1-ws3-v1` (LightGBM_L1), measured on DEV (2024) over 262 days.
>
> **What would change this:** Treasury's forward commitment and payment-run calendar. Shuffling the historical answers barely changes this model's error, and that reading sits inside the range deliberately uninformative inputs produce, so better inputs are needed before any claim is possible.

**PASS** — target named, both failing gates, plain language from the registry, no level
leaked.

**(d)**

> **State budget balance is withheld — the lab does not publish it as a forecast.**
>
> It failed 1 quality check:
>
> - **accuracy vs repeating the same weekday last week** (measured 1.57832, the limit is 1)
>   Simply repeating what happened on the same weekday last week is 57.8% more accurate than this model. There is no reading under which this model's numbers are the best available estimate, so they are not published as a forecast.
>
> Because of that, no forecast numbers are published for State budget balance at all — not shown with a warning, not published. The model behind it is `statebudgetbalance-histgbdt-l1-ws5-v1` (HistGBDT_L1), measured on DEV (2024) over 262 days.
>
> **What would change this:** A model that is more accurate than simply repeating what happened on the same weekday last week. The inputs for this line do carry real information; the accuracy does not yet clear that benchmark.

**PASS** — MASE 1.57832, one gate not two (its signal check passes at 7.0058 and is
correctly not listed as a failure), and **textually different from (c)**.

**(e)**

> **No published forecast has been scored against a real outcome yet.** The lab's scorecard has no completed rows, so every figure below comes from evaluation on DEV (2024) (262 days) — not from watching these forecasts come true.
>
> - **Revenues** — MASE **0.757959**, i.e. about 24% more accurate than simply repeating the same weekday last week. ✅ published — this is the one figure here that backs an official forecast.
> - **Expenditure** — MASE **1.10385**, i.e. about 10% less accurate than simply repeating the same weekday last week. ❌ withheld — this figure is the *reason* it is withheld, not an estimate you can use.
> - **State budget balance** — MASE **1.57832**, i.e. about 58% less accurate than simply repeating the same weekday last week. ❌ withheld — this figure is the *reason* it is withheld, not an estimate you can use.
>
> The 2025 holdout has deliberately never been evaluated — it is a one-shot check and spending it early would destroy its value. So there is no out-of-sample accuracy claim I can make, and I won't imply one.

**PASS** — recorded evidence, per target, each withheld figure explicitly marked as the
reason for withholding rather than a usable estimate. No realized-accuracy claim, and
the empty scorecard is stated rather than glossed. A single averaged headline was
deliberately avoided: it would have blended one published forecast with two withheld
ones.

**(f)**

> **I can't give you an official 30 days forecast.** The lab validates and publishes **5 business days** ahead and nothing further — issue `2026-08-16` covers 2025-08-07, 2025-08-08, 2025-08-11, 2025-08-12, 2025-08-13.
>
> Anything beyond that horizon has never been evaluated, so a number for it would carry no evidence about whether it is any good. I'd rather give you the 5-day forecast, which has been measured, than a longer one that has not.

**PASS** — refuses on the correct ground (validated horizon), names it, fabricates
nothing. A 5-day request still answers normally, so the guard is not a blanket refusal.

---

## 8. Task 5 — client raw data

**The demo script displays none.** All three raw-history uses sit inside
`if view == "Dashboard":` (`app.py:908-1076`):

```
L951:  hist = run.history                 -> inside if view == "Dashboard":
L984:  line_chart(run.history, ...)       -> inside if view == "Dashboard":
L1002: line_chart(run.history, ...)       -> inside if view == "Dashboard":
```

The script's six questions run in `if view == "Ask the agent":` (`app.py:1077+`), which
touches none of them. The new official answers read only published artifacts and the
registry.

**But the raw path is live, not dormant**, and this is worth knowing:

* `run.history` is a **3,867-point Series spanning 2015-01-05 → 2025-08-06**, read from
  `backend/data/processed/master_daily_clean_treasury.csv`. The Dashboard plots its last
  75 actual daily values.
* `available_targets()` enumerates **43 columns**, including tax-line detail
  (`Income tax`, `Profit tax`, `Value added tax`, `Excise duty`).
* Both are gated on `view.data_file.is_known` — and **both run summaries carry
  `data_file: 'master_daily_clean_treasury.csv'`**, so the gate is open. An earlier
  reading that this path never fires was wrong.

The only actual value the chat path shows is `origin_value` (amount redacted), which comes
from `forecast.csv` and is a published field — contract §7 includes it deliberately as
the persistence benchmark. So nothing exceeds what the artifacts intentionally show.

Judgment: showing a treasury its own history is defensible, and this is scope to decide
rather than a leak. Flagged because it is read from the Lab's *input* directory, outside
the published-artifact contract.

---

## 9. Tests and suite

`tests/test_official_answers.py` — **32 tests.** One per demo question, plus the traps:

* `test_only_the_latest_issue_is_ever_read`
* `test_a_withheld_target_never_reaches_the_older_issues_numbers` — the fixture
  reproduces the real 2025-08-06 trap (all three targets, same target dates, dirty tree)
  and asserts its Expenditure p50 appears in no answer
* `test_no_withheld_target_ever_emits_a_level` — regex-scans for any 7+ digit figure
* `test_c_and_d_do_not_produce_the_same_text` — the regression that would have sunk the
  demo
* absence semantics: unrecognised verdict → unknown, missing registry → reason not
  guess, missing published root → reason not a backtest, crossed quantiles dropped with
  a defect, `y_true` on a forward issue flagged, dirty tree disclosed
* `test_the_real_lab_answers_the_demo_script_correctly` — skips if the Lab is not
  checked out beside this repo, so the fixtures prove the rules and this proves they
  survive the real artifacts

`tests/test_official_routing.py` — **9 tests**, pinning §6's bug. They load `app.py`
with a **working** stub backend, because the bug is invisible with a broken one:

* `test_an_official_question_is_answered_from_artifacts_not_the_run`
* `test_the_official_answer_is_the_source_and_the_model_only_rewrites` — asserts the
  deterministic text reaches the prompt verbatim, with the keep-every-number and
  don't-soften-withheld instructions
* `test_a_non_official_question_still_reaches_the_general_llm_path`
* `test_the_context_carries_no_forecast_level_for_a_withheld_target`
* `test_the_context_is_valid_json_and_within_budget`

```
$ ./.venv/bin/python -m pytest
313 passed in 8.62s

$ ./.venv/bin/python -m pytest --ignore=tests/test_official_answers.py \
                              --ignore=tests/test_official_routing.py
272 passed in 8.71s
```

272 pre-existing pass unchanged; 41 new. **No regressions.**

Startup after the change, on the new code:

```
Uvicorn server started on :::8504
  You can now view your Streamlit app in your browser.
  Local URL: http://localhost:8504
```

`HTTP 200` on load, no runtime errors in the log.

### Diff

```
 app.py                          | 199 +++++++++++++++++-----  (modified)
 agent/published.py              | new
 agent/registry_read.py          | new
 agent/official.py               | new
 tests/test_official_answers.py  | new
 tests/test_official_routing.py  | new
```

Nothing is committed — the working tree is left for review as requested.

---

## 10. Demo-readiness verdict

# READY WITH CAVEATS

All six demo questions now answer correctly from the published `2026-08-16` issue and
the registry, with real figures, correct verdicts, and no fabrication. The suite is
green at 313 and the app starts clean. What keeps this from unqualified READY is a set
of things outside the code, none of which I could resolve from here.

### Caveats, in the order they could bite

1. **The LLM backend is unverified.** A TLS-intercepting proxy on this machine made a
   live check impossible (§3). If it is down tomorrow the agent degrades to the
   deterministic answers above — which are correct and complete, just less
   conversational — so this is a quality risk, not a correctness one. **Open the app and
   ask one question before the demo starts.**

2. **The 2026-08-16 Revenues numbers invite an obvious question.** At h1, `p50` is
   the h=1 P50 against the `origin_value` — the forecast more than
   doubles overnight — and the h5 band runs 44.1M to 141.9M. These are the real
   published figures, faithfully displayed. "Why does revenue double tomorrow, and why
   is the range that wide?" is the first thing a Treasury audience will ask. Have an
   answer, or steer the chat surface toward verdicts rather than levels.

3. **`reports/DEMO_RUNBOOK.md` §1A tells the presenter the wrong verdicts** — "one green
   banner (State budget balance)" is the pre-P2 state, now inverted. Lab-repo doc,
   deliberately not touched. **Fix or discard it before anyone reads from it.**

4. **Nothing is approved, and the agent now says so in every answer.** `approved_by` is
   null on all three recipes and status is `candidate -- pre-tuning`. That language is
   in the output by design. Expect a question about it and do not soften the answer.

5. **The run-based surfaces still show the 2026-08-12 State budget balance backtest.**
   Only the target-scoped chat questions were rerouted. The Dashboard, the sidebar and
   the run browser continue to describe that run — correctly, with `WITHHELD` labels and
   a `stale: True` banner, but it is a different subject from the published forecast. If
   the presenter moves between the chat and the Dashboard, be ready to explain that one
   shows what is published and the other shows how models were evaluated.

6. **The Dashboard plots 75 actual daily Treasury values** (§8). Not on the script's
   path; one click away.

7. **`forecasts/published/` is gitignored**, so none of this works from a fresh clone.
   The demo must run on this machine, or the artifacts must be copied across manually.

8. **Residual, untriggered:** `build_agent_context` truncates at a byte budget with
   `json.dumps(...)[:16000]`, so a large enough run would hand the model malformed JSON
   and silently degrade its grounding. Measured headroom on the real Lab is 34%
   (10,542 of 16,000), and this is pre-existing behaviour — but my block added 2,306
   chars to that payload. A length check that drops the leaderboard samples rather than
   truncating mid-structure is the right fix, and it is not a thing to write the night
   before a demo.

### Not done, deliberately

No UI redesign. No new views. The `forward` run directory
(`backend/forecast_runs/forward/latest/`) is still unread — it holds
`forward_forecast.csv`, `forward_gates.json` and `forward_provenance.json`, and the
Lab's own runbook drives the Forecast page from it. Reading the published issue was the
narrower change and the one the demo script actually needed.
