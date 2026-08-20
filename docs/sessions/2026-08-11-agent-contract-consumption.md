# Session — consuming the Lab's artifact contract

**Date:** 2026-08-11
**Repo:** `AI4CM-agent`, branch `feat/lab-door`
**Counterpart:** `AI4CM` (the Lab), branch `model/excellence`, which now publishes
`docs/AGENT_ARTIFACT_CONTRACT.md` and validates against it before publishing.

---

## 1. The prompt, verbatim

> In the AI4CM-agent repo on feat/lab-door. The Lab repo now publishes a formal artifact contract at docs/AGENT_ARTIFACT_CONTRACT.md and validates against it before publishing. Read that document first — it is the interface you consume.
>
> Part 1 — audit every place the Agent reads a Lab artifact. For each field, confirm it handles the contract's absence semantics correctly. Specifically: the Agent must never narrate an absent field as a value, must never present a withheld or FAILED_QUALITY family as a clean result, and must treat missing and "n/a" as UNKNOWN — never as zero, a pass, or a failure. Report what it currently does wrong before changing anything.
>
> Part 2 — fix the brittle parsing the contract exposed: numbers published as strings (horizon "5", skill_pct "27.51%"), best_model as one prose field combining a name and a formatted number, metrics_long.csv carrying two incompatible shapes under one filename, and three leaderboards with three different schemas where only e_quantile lacks target and horizon. Parse defensively and fail visibly rather than guessing.
>
> Part 3 — the plain-language layer must inherit the Lab's honesty. When skill_pct is "n/a (not produced)" the Agent should say the family produced no skill figure and that the reason is not recorded — not invent one. When a family is withheld, it names the gate reason. When coverage is unreported, it says so rather than implying a level.
>
> Then: adopt the corrected model framing. Never a single headline count. "13 machine-learning models and 4 statistical models compete on each target; prediction intervals come from 3 quantile methods; 3 further entries are reference baselines, not competitors." Champion-eligible pool is 13.
>
> Test against a deliberately incomplete and a deliberately inconsistent artifact set, not just a clean one.
>
> Session logging: docs/sessions/2026-08-11-agent-contract-consumption.md, full narrative verbatim plus prompt, plan, real output, verdict, outstanding. Run the suite, show passing output, commit the md with the code, walk me through the diff.

---

## 2. Narrative

### Orientation

The working tree was on `v2`, not `feat/lab-door`. Switched branches first; the two
have materially different layouts (`v2` has `contracts/`, `stub_lab/`, a populated
`tests/`; `feat/lab-door` has `agent/` and an empty `tests/`). Everything below is
on `feat/lab-door`.

Found the contract at `~/Projects/AI4CM/docs/AGENT_ARTIFACT_CONTRACT.md`
and read it in full before touching the Agent. Its §0 is the governing rule:

> a consumer should treat a missing or `n/a` value as UNKNOWN — never as zero,
> never as a pass, and never as a failure.

### Establishing the read surface

Grepped for every artifact field name across the repo. The Lab-artifact surface is
exactly two files:

- `agent/lab_bridge.py` — loads `SUMMARY.json`, `predictions_long.csv`, `leaderboard.csv`
- `app.py` — renders all of it, and builds the LLM's context

`agent/tools.py` is a standalone forecasting toolkit that touches no Lab artifact.
`agent/runtime/*` reads user-supplied CSVs and the Agent's own `champions.json`,
not the Lab's output. `agent/runtime/registry.py` matched the grep only because it
has its own unrelated `best_model` helpers.

### Grounding the audit in real files

Rather than audit against the contract's prose, I read the artifacts the Agent
actually consumes — `backend/forecast_runs/2026-08-04` — and confirmed each hazard
empirically. This is what turned up the one finding that was not in the contract at
all: **`data_file` is absent from every committed `SUMMARY.json`**, and the Agent
renders it in four places, producing the literal string ``Input data: `None` ``.

---

## 3. Part 1 — the audit, before any change

Every finding below was verified against `2026-08-04`, not inferred.

### A. Absence narrated as a value

| # | Site | What it did | Contract |
|---|---|---|---|
| A1 | `app.py` dashboard caption, "About this run", `run_result_narrative`, Learn tab | Rendered `summary.get('data_file')` → the string `None`, in four places, on every real run | §0 — absence is not a value |
| A2 | `app.py` run-history table | `sm.get("horizon", "?")` → `?` for a field that is *unknown*, conflating "not recorded" with "unparseable" | §1 |
| A3 | `app.py` family cards | `f['best_model_display']`, `f['skill_pct']`, `f['run_status']` by direct subscript — `KeyError` on any artifact omitting them | §1 "If absent" columns |
| A4 | `app.py` champion banner | `champ['best_model']` and `champ['skill_pct']` interpolated raw, so `"n/a (not produced)"` renders as if it were the skill | §1 |

### B. Withheld / FAILED_QUALITY presented as clean

| # | Site | What it did |
|---|---|---|
| B1 | `lab_bridge.champion()` | Eligibility tested `gate_passed` only. A family with `gate_passed: true` and `run_status: "FAILED_QUALITY"` — an inconsistent but publishable artifact — would be crowned champion. |
| B2 | `lab_bridge.champion()` | `skill()` returned `-inf` on an unparseable skill, then `max()` over an all-`-inf` pool returns the *first* family. A family with no skill figure could become champion by position. |
| B3 | `lab_bridge.champion()` | No baseline exclusion. A gate-passing family whose `best_model` is `⚡ Persistence (baseline)` was eligible to win — a reference baseline crowned as a competitor. |
| B4 | `champion()` `except ValueError` | `TypeError` was uncaught, so a non-string, non-numeric `skill_pct` raised out of the accessor. |

### C. Missing / "n/a" treated as zero, pass, or failure

| # | Site | What it did | Contract |
|---|---|---|---|
| C1 | `lab_bridge.passed_families` | `f.get("gate_passed")` truthiness. `null` is falsy → excluded from passed. **Correct outcome, wrong mechanism.** | §1 tri-state |
| C2 | `lab_bridge.failed_families` | `not f.get("gate_passed")` → `null` lands in *failed*, and the UI badges it `WITHHELD` with reason "unknown". **Never-verified rendered as a failure.** | §0 — never a failure |
| C3 | `app.py` metrics row | `o.get('leakage_flags', 0)` and `o.get('shift_flags', 0)` — absence became a clean, reassuring `0`. | §0 — never zero |
| C4 | `app.py` metrics row | `o.get('families_gate_passed', 0)/o.get('families_requested', 0)` → `0/0` on an artifact with no `overall`. | §0 |
| C5 | `app.py` freshness metric | `"STALE" if fr.get("stale") else "Fresh"` — absent freshness rendered as **Fresh**. | §1 "Treat staleness as unknown" |
| C6 | `run_result_narrative` | `f.get("shift_flag")` / `f.get("leakage_flag")` truthiness — absent flags read as `false`. | §1 "Assume unknown, not false" |
| C7 | `app.py` | `overall` used directly as fact. The contract says it is derived and *can contradict* `families`; the Lab's own validator recomputes it. The Agent did not. | §1 |

### D. Invented content

| # | Site | What it did |
|---|---|---|
| D1 | `run_result_narrative` | Asserted **"the lab requires at least 5%"**. That threshold appears nowhere in the contract, the artifacts, or the Lab. It was fabricated by the Agent and shown to users as the reason a family was withheld. |
| D2 | `run_result_narrative` | Interpolated `f['skill_pct']` into *"its skill vs persistence was only **{x}** (the lab requires at least 5%)"*. With `x = "n/a (not produced)"` the sentence asserts a measured failure where the artifact records no measurement — precisely the Part 3 target. |
| D3 | `run_result_narrative` | Inferred the cause from `run_status` (`"FAILED_QUALITY" in gate_reasons`), which §1 forbids: *"`gate_reasons` is the only place the four verdicts are distinguished. Do not infer the cause from `run_status`."* |
| D4 | `app.py` target picker | `run.summary.get("target", "Revenues")` — hardcoded fallback to a guessed series name. |

### E. Structure assumed rather than detected

| # | Site | What it did |
|---|---|---|
| E1 | `load_run` | `pd.read_csv(leaderboard)` with no schema awareness. Three (in practice **four** — C_DL exists and the contract omits it) incompatible schemas read identically. |
| E2 | `build_agent_context` | Fed raw leaderboard rows to the LLM. `e_quantile` has no `target` column, so the model received quantile rows with no way to know which target they belong to — and the raw `SUMMARY.json`, including `gate_passed: null` and `"n/a (not produced)"`, with no instruction on what they mean. |
| E3 | `load_run` | `metrics_long.csv` was never read at all, so the two-shapes problem was unhandled rather than mishandled. |
| E4 | `load_run` | `except Exception: pass` on every CSV read — a malformed artifact silently became a missing one. |
| E5 | everywhere | No coverage handling. §5 requires "not reported" and forbids inferring the level from the key name. |
| E6 | `load_run` | No `origin_date >= target_date` check (§3 calls it an error), no join-loss check for the baseline row that has no prediction rows (§2, known-not-fixed). |

### F. Bugs the audit surfaced incidentally

| # | Finding |
|---|---|
| F1 | `_load_history` and `available_targets` both key off `data_file`, which no committed run records. Both therefore always return empty — so the target dropdown was empty on every real run and fell back to the hardcoded `"Revenues"` (D4). |
| F2 | `find_latest_run` accepts the newest directory containing a `SUMMARY.json` regardless of whether it parses. |

---

## 4. Plan

1. `agent/contract.py` — a single reading layer, the only code permitted to touch a
   raw artifact dict. Everything leaves it as a `Value` that knows whether it is
   known and, if not, why.
2. `agent/plain.py` — the plain-language layer, plus the corrected model framing as
   a constant.
3. Rewire `lab_bridge.py` and `app.py` onto both. No `.get(k, 0)` and no direct
   subscript of an artifact field survives.
4. Three fixture artifact sets — clean, incomplete, inconsistent — plus the real run.
5. A rendering test that *executes* `app.py`, because the audit's most embarrassing
   finding (A1) lived in an f-string no test would ever evaluate.

---

## 5. What was built

### `agent/contract.py`

- **`Value` / `Presence`** — `KNOWN`, `ABSENT`, `NULL`, `NOT_PRODUCED`, `UNPARSEABLE`.
  `Value.__bool__` **raises `TypeError`**, so `if value:` — the exact construct that
  turns absence into `False` — is a hard error rather than a silent wrong answer.
- **`parse_number` / `parse_int` / `parse_percent`** — `"5"`, `"27.51%"`,
  `"1,234,567"`, `"n/a (not produced)"`, `NaN`, `True`. Never casts.
- **`parse_best_model`** — splits `"RandomForest (MAE 31,685,490)"` into name,
  metric name and metric value; handles the `WITHHELD — …; <Model> (MAE …) for
  diagnosis only` form; strips `⚡` from join keys; returns UNKNOWNs rather than a
  half-parsed name.
- **`Gate`** (`PASSED` / `WITHHELD` / `UNVERIFIED`) and **`RunStatus`** as real
  tri-states. `FamilyView.is_presentable` requires *both* `PASSED` and `SUCCESS`.
  `is_champion_eligible` additionally requires a known skill and a non-baseline.
- **`recompute_overall`** — recomputes every derived counter from `families` and
  records disagreements. Flag counts stay UNKNOWN unless *every* family recorded
  the flag, so a partial count never renders as a total.
- **`read_leaderboard`** — identifies the schema by column overlap (Jaccard ≥ 0.6),
  reports partial identification, all-null columns, decoration in the join key, and
  where the target came from (`file` / `summary` / `unknown`).
- **`read_metrics_long`** — `{"metric","value"} ⊆ columns → LONG, else WIDE`.
  `metric()` works in both shapes and returns UNKNOWN, never `0`, for an absent or
  all-null metric.
- **`read_predictions`** — drops and reports `origin_date >= target_date` rows.
- **`read_coverage`** — reports the measurement but refuses to attach a level when
  `coverage_nominal` is absent; rejects values outside `[0, 1]`.

**Departures vs. anticipated ambiguities.** A late correction, forced by a failing
test: my first cut put everything in one `defects` list, so the *clean* fixture
reported 10 "departures". That is a panel that cries wolf on a compliant artifact,
which trains people to ignore it. The contract's own vocabulary already separates
ERROR from WARNING, so the layer now does too — `defects` (departures) and `notes`
(ambiguities the contract anticipates: an all-null column, a baseline with no
prediction rows, decoration in a join key).

### `agent/plain.py`

`say_skill`, `say_gate`, `say_coverage`, `say_best_model`, `say_flag`,
`describe_family`, `describe_champion`, `describe_run`, `describe_flags`,
`describe_defects`. Plus `MODEL_FRAMING` verbatim and
`CHAMPION_ELIGIBLE_POOL = 13`.

### Rewiring

`LabRun` now wraps a `RunView`. `passed_families` / `failed_families` are gone —
replaced by `presentable_families`, `withheld_families`, `unverified_families`,
which are three lists because the gate has three states. The UI gained a third
badge (`NOT VERIFIED`, grey) for exactly that reason. The LLM now receives the
*read* view, with every unknown spelled out, instead of the raw `SUMMARY.json`.

---

## 6. Real output

Against the real Lab run, `backend/forecast_runs/2026-08-04`:

```
=== CHAMPION ===
Today's champion is **E_QUANTILE** (quantile models with uncertainty bands) — best
model **GBQuantile (MAE 27,905,122)**, 48.45% better than the naive "tomorrow looks
like today" rule, quality gate **PASSED**.

=== COVERAGE (E_QUANTILE) ===
Measured interval coverage is 73.9%. The level the interval was fitted for is not
recorded in the artifact, so I cannot say what it was aiming at — the number alone
does not tell you whether that is good.

=== COVERAGE (A_STAT, a point family) ===
Interval coverage is not reported for this family. That is not a coverage of zero
and not a failed check — this run simply published no coverage measurement.

=== FRAMING ===
13 machine-learning models and 4 statistical models compete on each target;
prediction intervals come from 3 quantile methods; 3 further entries are reference
baselines, not competitors.

The distinction matters when reading a leaderboard. The baselines are there to be
beaten, not to win — persistence ('tomorrow looks like today') is the ruler every
competitor is measured against, so ranking it alongside them would be a category
error. The quantile methods produce the prediction intervals rather than competing
on point accuracy. That leaves a champion-eligible pool of 13.
```

Note the champion: B_ML's 36.75% beats A_STAT's 27.51%, but B_ML is withheld, so
E_QUANTILE's 48.45% wins among *eligible* families.

### What the Agent now says about the real artifact

**5 departures:**

```
- `run_id` absent — the run is identified by its folder name only
- `schema_version` absent — assuming version 1, so since-v2 fields may be missing
- A_STAT: leaderboard.csv: ['cadence', 'horizon', 'target'] filled on some rows and
  blank on others — the file cannot answer 'which model won for target X' on its own
- E_QUANTILE: coverage was measured but no `coverage_nominal` was published — the
  level the interval was fitted for is not recorded, so the measurement is reported
  without one
- C_DL: leaderboard.csv looks like the b_ml schema, not c_dl — reading it by its
  columns, not its folder
```

**8 anticipated ambiguities:**

```
- A_STAT: predictions_long.csv: `y_lo` is entirely empty — this family may have no
  native prediction intervals, or they may have failed; the artifact does not
  distinguish the two
- A_STAT: predictions_long.csv: `y_hi` is entirely empty — (same)
- A_STAT: leaderboard.csv: column(s) ['RMSE'] are entirely empty — reported as not
  recorded, not as zero
- A_STAT: baseline row(s) ['Persistence (baseline)'] appear in the leaderboard but
  have no rows in predictions_long.csv — they are derived from origin_value, not
  predicted
- B_ML: leaderboard.csv carries decoration in the join key (⚡ Persistence
  (baseline)) — matched on the stripped name
- B_ML: metrics_long.csv: column(s) ['MAE_skill_vs_Ops'] are entirely empty — an
  empty cell asserts nothing, so these read as not recorded
- B_ML: baseline row(s) ['Persistence (baseline)'] appear in the leaderboard but
  have no rows in predictions_long.csv
- E_QUANTILE: leaderboard.csv does not identify its target — taken from SUMMARY.json
```

This lines up with the contract's §8 self-assessment ("3 errors, 10 warnings") and
adds two the Lab's own validator does not report, because they are consumer-side
concerns: `run_id`/`schema_version` are graded as departures here (the contract
promotes them to errors under `--strict`), and the C_DL leaderboard schema is
undocumented in §2 entirely.

### Suite

```
$ .venv/bin/python -m pytest
........................................................................ [ 45%]
........................................................................ [ 90%]
...............                                                          [100%]
159 passed in 1.32s
```

---

## 7. Verdict

**Part 1 — done.** Audit above, with 22 numbered findings across six categories,
each verified against the real artifact before any code changed. All are fixed.

**Part 2 — done.** All four fragilities parse defensively and fail visibly. The
fourth turned out to be under-specified in the contract: there are **four**
leaderboard schemas in practice, not three — C_DL writes one and §2 does not
document it. The reader detects by columns rather than by folder, so it reads C_DL
correctly *and* reports the mismatch.

**Part 3 — done.** `n/a (not produced)` renders as *"no skill figure — this family
produced none, and the artifact does not record why (the Lab publishes one marker
for several possible causes, so neither it nor I can tell them apart)"*. Withheld
families name their gate reason, and say so plainly when no reason was recorded
rather than inventing one. Unreported coverage says it is not reported, and states
that this is neither zero nor a failed check.

**Model framing — adopted.** Verbatim, in `plain.MODEL_FRAMING`, on the dashboard,
in the Learn tab, in the system prompt, in the glossary, and in the answer to "how
many models are there". Pinned by a test that also rejects the wrong totals (17, 20,
23) you get by adding the wrong categories together.

**Testing — done, and it earned its keep.** 159 tests across four artifact sets. The
rendering test caught a real bug I had introduced (a malformed conditional
expression in the "About this run" block) that the syntax check passed over.

### One correction to my own work

My first implementation collapsed contract ERRORs and WARNINGs into a single
`defects` list. A failing test on the *clean* fixture showed why that is wrong: a
compliant artifact reported 10 departures. Split into `defects` and `notes`.

---

## 8. Outstanding

1. **`streamlit` and `plotly` are not installed in `.venv`.** The app has never been
   run in this environment. The rendering test covers the code paths with stubs,
   which is genuinely useful, but it is not the same as the real thing. Install and
   run before demoing.
2. **`data_file` is absent from every `SUMMARY.json`** and is not in the contract.
   Consequences: no history chart, no target enumeration, so the target picker shows
   only the run's own target. The Agent now says so instead of guessing, but the
   *fix* belongs in the Lab — either publish `data_file`, or add it to the contract
   as deliberately absent with a companion field.
3. **C_DL is undocumented in contract §2, §4 and §1's family table** beyond the name.
   It writes a fourth leaderboard schema and nests its artifacts under `<fam>/daily/`.
   Worth raising with the Lab.
4. **Coverage side-channel keys are a guess about location, not content.**
   `_coverage_extras` looks in `run.json` and `integrity_report.json` because that
   is where they appear; §5 names the fields but not the file. Confirm with the Lab.
5. **The model framing is a constant, not a measurement.** 13/4/3/3 is asserted
   because it was specified, not derived from the artifacts. If the Lab's roster
   changes, this constant is stale and nothing will detect it. A cross-check against
   the leaderboards would close that gap.
6. **`integrity_report.json` is only read for coverage keys.** Contract §6 exposes
   `alignment_checked` / `alignment_ok` / `alignment_check_error`, `signal_detected`,
   and the `skill_pct`-vs-MAE reconciliation rule. The Agent reads none of it, so it
   cannot currently tell a user *why* the shuffled-target sentinel fired.
7. **`forecasts/published/<issue_date>/` (§7) is entirely unconsumed** — no
   `forecast.csv`, `gates.json`, `provenance.json`, or estimator handling. Note
   §7's rule that nothing may render as approved while `approved_by` is null; when
   that surface is built, that rule needs a test.
8. **The LLM path is untested.** `have_llm()` is stubbed to `False` throughout. The
   system prompt now carries the absence rules and the framing, but nothing verifies
   the model honours them.
