# AI4CM Agent

A conversational interface to the **AI4CM forecasting lab**: ask about the
Treasury cash forecast in plain language, and — with confirmation at every step
— ask the lab to produce a new one, take in new actuals, and score what it
previously said against what actually happened.

Two ways into this document:

- **If you work at the Treasury** and want to know what this does and what you
  can trust it for → [What it is](#what-it-is) and
  [What it will not do](#what-it-will-not-do), then
  [Current state](#current-state).
- **If you are reviewing the code** → [The principle](#the-principle),
  [How it is put together](#how-it-is-put-together), and
  [Setup](#setup).

---

## What it is

The lab produces daily forecasts of Georgian Treasury cash lines, and puts them
through quality gates that decide whether a forecast may be published at all.
This app sits on top of that. It does four things:

1. **Answers questions** about the published forecast — the numbers, why a line
   is withheld, which model is behind it, how accurate it has been measured to
   be.
2. **Runs a new official forecast**, on request and after you confirm.
3. **Takes in new actuals** — a CSV of daily figures — and checks them before
   anything uses them.
4. **Scores past forecasts** against those actuals: what was predicted, what
   happened, and how far apart they were.

Everything it says about the forecast is read from files the lab wrote. It does
not have opinions about the numbers.

## What it will not do

These are design decisions, not gaps:

- **It will not give you a forecast the lab withheld.** When the gates withhold
  a line, you get the verdict and the reason — never the numbers "for context".
  Quoting them would republish exactly what the gate withdrew.
- **It will not forecast further than 5 business days.** That is the only
  horizon at which the lab's benchmark, model selection and quality gates were
  measured. Ask for 30 days and it will decline and explain why, rather than
  produce a number that carries no evidence.
- **It will not run anything without asking first.** Every action states what it
  will do and waits for a yes. An ambiguous reply is treated as "no".
- **It will not report a number it has not read back off disk.** After a run it
  re-reads the published artifact rather than trusting what the run said.
- **It will not fill in a blank.** A missing value is reported as unknown —
  never as zero, never as a pass, never as a failure.

## Current state

Honest status, as of the last session:

| | |
|---|---|
| Test suite | **518 passing** |
| Answering | Live against the lab's published issues and recipe registry |
| Running a forecast | Live; verified end-to-end against the real lab |
| Taking in actuals | Live; validation exercised, installation path built |
| Scoring | Machinery live and run against the real lab |
| **Scored forecasts so far** | **Zero.** 25 forecast rows are pending |

**No forecast has yet been scored against a real outcome.** Every published
forecast is for a date whose actuals have not arrived, so the scorecard is
correctly empty. The scoring path has been exercised end-to-end, but on
synthetic actuals in an isolated sandbox — never on the real record.

Two things follow that a reader should not have to infer:

- **There is no realized track record.** Accuracy figures the agent quotes come
  from evaluation on historical windows, not from watching these forecasts come
  true. The agent says so every time it quotes one.
- **Nothing here is approved.** Every recipe in the lab's registry has status
  *candidate*; no approval workflow exists yet. The agent states this in any
  answer that names a model.

The forecasting itself has been **honestly evaluated** on held-out historical
data. It has not been proven in production, and this document will not say it
has until forecasts have been scored against real outcomes.

---

## The principle

**The agent invokes the lab's audited entry points. It never reimplements
forecasting, gating, publishing, retention or scoring.**

This is the non-negotiable rule of the codebase, and it is why the agent can be
trusted to speak for the lab. Concretely:

| Action | What is actually called |
|---|---|
| Run an official forecast | `forecast_modes.official_run` → `publish_official` |
| Choose an issue date | `forecast_modes.next_issue_date` |
| Publish and retain | `published_forecasts.publish` (writes the vault too) |
| Score | `published_forecasts.score_published` |

All of it runs in **the lab's own Python interpreter** (`AI4CM/backend/.venv`),
as a subprocess, because the modelling stack lives there and belongs there. The
two files that do this — `agent/lab_entry.py` and `agent/lab_score.py` — contain
sequencing and progress reporting, and no arithmetic.

The consequence worth stating: an agent-run forecast is **byte-equivalent** to
one produced from a terminal. This is verified, not assumed — a forecast run
through the agent was compared with the terminal-run issue and the artifacts
matched exactly.

The agent also never re-derives *trust*. Which lines may be published is the
lab's decision, read from its registry and its gates. The agent cannot disagree
with the pipeline about what is trustworthy, because it has no code that could
form a second opinion.

## How answering works

Four tiers, tried in order. The first that applies wins.

1. **Action** — is this an instruction rather than a question? (see below)
2. **Target-scoped official answer** — a question naming a budget line is
   answered from the published issue and the recipe registry.
3. **Grounded narration** — the artifact-sourced answer is passed to a language
   model to reword. It may rephrase; it may not add, drop or alter a figure.
4. **Rule-based answer** — with no language model available, the agent answers
   from the artifacts directly and says so in its trace.

Tier 2 exists because of a specific failure: the agent once answered *"why is
Expenditure not published?"* using whichever backtest run was newest — which
covered a different budget line. Nothing was fabricated; it was a correct answer
to a question nobody asked. Questions that name a line are now answered from
that line's own record.

**Every answer carries a trace** you can expand, showing which path produced it
and which files were read.

## How actions work

Three actions, each confirmed separately, because they have different
consequences and one yes should not cover all of them.

```
"here is the new data"  →  validate, describe, ask
                        →  [yes]  take it (backing up what it replaces)
                        →  ask
                        →  [yes]  score published forecasts against it
                        →  ask
                        →  [yes]  run a new official forecast
```

Scoring is offered **before** forecasting on purpose: it answers "was the last
one any good", and that belongs in front of a new number rather than behind it.

Guardrails, all of which have fired in practice:

- **Unchanged data.** If the new file is byte-identical to what the last issue
  was built from, the agent says so and asks whether to run anyway. A duplicate
  issue would overstate how often a forecast was actually made.
- **Truncated history.** A file containing only the *new* rows has the right
  columns, later dates and a different checksum — and installing it would
  discard years of history the models train on. Refused.
- **Concurrent runs.** A second run is refused while one is in flight.
- **Real errors.** A failed run shows the lab's own stderr, verbatim. It never
  reports a success it cannot substantiate.
- **Writes are off by default.** `AI4CM_ALLOW_LAB_WRITES` gates every write into
  the lab's real tree. Unset, the agent still validates and scores, but into
  staging paths — useful when someone else is working in the lab.

Published forecasts, quality gates and provenance can be downloaded as files;
the issue date is in every filename, so two downloads can never be confused.

---

## Setup

About five minutes.

```bash
# 1. This app
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Point at your AI4CM checkout
export AI4CM_REPO=~/Projects/AI4CM        # or AI4CM_RUNS_ROOT=.../forecast_runs

# 3. Start
streamlit run app.py
```

If AI4CM is checked out beside this repo (`../AI4CM`), it is found
automatically.

**To run forecasts or scoring, the lab's own virtualenv must exist** at
`AI4CM/backend/.venv`. That is where the models live. Without it the agent still
answers questions and reads published issues — it will tell you it cannot run
anything, rather than substituting its own interpreter.

### Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The dev requirements install the full runtime set on purpose: several tests
import streamlit, plotly, statsmodels and scikit-learn for real, so a green
suite also demonstrates the app can start.

### Optional: language-model narration

```
# .env  (gitignored — never commit it)
AZURE_OPENAI_API_KEY=...        # or OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT=...
```

This polishes wording only. Without a key the agent answers from its own
rule-based path and says so in the trace. **Figures are never produced by the
language model** — it rewords an answer that was already built from artifacts.

Behind a TLS-inspecting proxy, `agent/llm.py` points `SSL_CERT_FILE` at
certifi's bundle, which will not contain your corporate root; every call then
fails and the agent falls back to rules. Export `SSL_CERT_FILE=/path/to/bundle.pem`
(certifi's bundle plus your root) before starting. An existing value that points
at a real file is left alone.

---

## How it is put together

`app.py` is the only user interface. Everything it says comes from `agent/`.

| Module | Responsibility |
|---|---|
| `contract.py` | Reads the lab's `SUMMARY.json` through its published artifact contract. The only place allowed to touch raw fields — it is what turns "absent" into UNKNOWN instead of zero |
| `published.py` | The published forecast issue. Only ever reads the **latest** one |
| `registry_read.py` | The lab's champion recipes and their publication verdicts |
| `official.py` | Target-scoped answers: the forecast, why a line is withheld, which model, how accurate |
| `lab_bridge.py` | Finding the lab, reading a run, listing official targets |
| `plain.py` | Turning contract-read values into plain language |
| `run_intent.py` | Is this message an instruction or a question? Consent parsing |
| `run_exec.py` | Launching the lab, streaming progress, the lock, honest failure |
| `run_report.py` | What actually happened, read back off disk. Retention verification |
| `data_intake.py` | Validating a candidate actuals file before anything uses it |
| `lab_entry.py` | **Runs under the lab's interpreter.** Forecast + publish |
| `lab_score.py` | **Runs under the lab's interpreter.** Scoring |
| `llm.py` | The optional narration backend |
| `tools.py` | Local modelling for demo mode only. Never used against lab data |

### Two modes

- **Lab mode** — a lab run was found. Four views in the sidebar: *Ask the
  agent*, *Dashboard*, *Run history*, *Learn*.
- **Demo mode** — no lab run found. Falls back to the bundled
  `datasets/demo_daily.csv`, which is synthetic and holds no Treasury data (see
  [Figures in this repo](#figures-in-this-repo)). The app labels it as
  uncalibrated sample data on every page that shows it.
  This is the only path that uses `agent/tools.py`, and it is imported lazily so
  a lab-mode start does not pay for statsmodels and scikit-learn.

### Not part of the app

- **`agent/runtime/`** — an unwired side package (alerting, watching for new
  runs). Nothing imports it and no test covers it. Each file carries a banner
  saying so. Do not copy patterns out of it: it predates the contract layer and
  does not carry its absence semantics.
- **`frontend/`, `var/`** — a Node prototype and local machine state. Untracked,
  gitignored, not build inputs.

### Figures in this repo

This repository is public. The lab measures its models on a named client's data,
so the amounts are not published here. What you see instead:

- **Amounts in tests and session records are scaled, not real.** Each one is a
  real value multiplied by a single shared constant. Digit count, ordering and
  every ratio survive that, so the fixtures stay self-consistent and the tests
  still assert what they were written to assert.
- **Ratios are real.** MASE, skill percentages and interval coverage are kept as
  measured. A ratio discloses no amount, and it is what the accuracy argument
  rests on.
- **`datasets/demo_daily.csv` is synthetic.** It carries the real column names
  and a plausible date range, which is what makes it useful as a demo. It shares
  371 dates with the real series and not one of its values on those dates is the
  real one.

The rule is the lab's own: redact an attributed client amount, keep the
reasoning. Test fixtures are the easiest place to get this wrong, because a real
number pasted in once looks like a detail rather than a disclosure.

### Session records

`docs/sessions/` holds a record for every working session: what was attempted,
what was found, what was verified, and what was deliberately left undone. See
[`docs/sessions/README.md`](docs/sessions/README.md) for the index. They are the
detailed history behind everything summarised here.
