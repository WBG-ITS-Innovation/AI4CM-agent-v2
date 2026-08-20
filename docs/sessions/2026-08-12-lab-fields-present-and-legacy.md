# The Lab now writes the four fields — present-and-correct, with the absent path kept

**Date:** 2026-08-12 · **Branch:** `feat/lab-door` · parent `539edb6`
**Suite:** 269 passed, `EXIT=0` (was 266 with the migration uncommitted)
**Paired Lab commits:** `58440ff`, `8860dbc` on `model/excellence`

---

## 1. The exact prompt given

> In the AI4CM repo on model/excellence: the regenerated 2026-08-04 SUMMARY.json is staged but
> uncommitted. Commit it. Confirm the a_stat leaderboard contract failure that makes the validator
> exit 2 is a pre-existing CSV issue and not a regression, and record that verdict.
>
> Then in the AI4CM-agent repo on feat/lab-door: six tests fail because they assert run_id,
> schema_version, data_file and model composition are absent — the Lab now writes all four. Update
> them to assert the present-and-correct behaviour, and keep one test covering the absent case
> against a synthetic older artifact so both paths stay exercised. Show both suites green.
>
> Session logging as usual, both repos.

---

## 2. Plan

1. Verify the two stated premises before acting on either.
2. Whatever is genuinely outstanding: finish and commit it.
3. Check the Agent against what the Lab *actually* publishes now, not against what the prompt says
   it publishes.

---

## 3. Commands run and their real output

### The premise: are six tests failing?

First run, before clearing caches:

```
$ .venv/bin/python -m pytest
266 passed in 6.32s
```

Second run, same tree, no edits:

```
1 failed, 261 passed, 4 errors in 6.88s

FAILED tests/test_app_rendering.py::test_a_run_without_a_composition_says_so_on_the_page[legacy_run]
ERROR tests/test_app_rendering.py::test_legacy_run_says_its_input_file_is_unrecorded
ERROR tests/test_model_framing.py::test_an_artifact_from_before_the_lab_wrote_it_says_nothing
ERROR tests/test_real_artifact.py::test_an_artifact_from_before_the_lab_wrote_those_fields_still_reads
ERROR tests/test_target_enumeration.py::test_the_old_source_is_empty_on_an_artifact_that_records_no_data_file
E       fixture 'legacy_run' not found
```

After clearing `__pycache__` and `.pytest_cache`:

```
$ .venv/bin/python -m pytest
266 passed in 7.44s
```

So the five "failures" were a **stale bytecode cache**, not the tests. `legacy_run` is defined at
`tests/conftest.py:333` in the uncommitted working tree; the cached conftest predated it.

### Both paths were already covered

```
=== tests using legacy_run (the absent case) ===
tests/test_app_rendering.py:298:  test_legacy_run_says_its_input_file_is_unrecorded
tests/test_app_rendering.py:354:  parametrize(... "legacy_run")
tests/test_real_artifact.py:85:   test_an_artifact_from_before_the_lab_wrote_those_fields_still_reads
tests/test_model_framing.py:168:  test_an_artifact_from_before_the_lab_wrote_it_says_nothing
tests/test_target_enumeration.py:61: test_the_old_source_is_empty_on_an_artifact_that_records_no_data_file

=== tests asserting the fields are PRESENT on the real artifact ===
tests/test_real_artifact.py:53:  test_the_real_run_records_run_id_and_schema_version
  assert view.run_id.is_known / view.schema_version.is_known / view.data_file.is_known
tests/test_model_framing.py:130: test_the_real_committed_artifact_records_its_composition

$ .venv/bin/python -m pytest tests/test_real_artifact.py -v -rs
15 passed in 0.69s          # no skips: real_run resolves to the Lab's artifact
```

### What the Lab actually publishes

```
  model_composition keys: ['champion_pool', 'champion_pool_category', 'counts',
                           'daily_best_model_families', 'members',
                           'promoted_by_registry', 'promoted_outside_champion_pool']
  champion_pool len: 13
  champion_pool_category: machine-learning models
  champion_pool_size key present? False
```

### After teaching the reader to count the published list

```
champion_pool_size known: True | value: 13
client_framing known: True
```

```
$ .venv/bin/python -m pytest
269 passed in 7.52s
```

Rendered end to end:

```
13 machine-learning models, 5 deep-learning models and 4 statistical models compete on each
target; prediction intervals come from 3 quantile methods; 3 further entries are reference
baselines, not competitors.

The distinction matters when reading a leaderboard. The baselines are there to be beaten, not to
win — persistence ('tomorrow looks like today') is the ruler every competitor is measured
against, so ranking it alongside them would be a category error. The quantile methods produce
the prediction intervals rather than competing on point accuracy.

That leaves a champion-eligible pool of 13.
```

---

## 4. Full narrative response, verbatim

Both stated premises turned out not to hold, so this session is mostly verification plus one real
finding. Lab: **712 passed, 3 skipped, `EXIT=0`** and frontend **110 passed**. Agent: **269 passed,
`EXIT=0`**.

### The Lab side: nothing to commit, and the verdict recorded

The regenerated `2026-08-04` `SUMMARY.json` was **already committed**, in `58440ff` from the previous
session. `git status` was clean, `git diff HEAD` on that path was empty, and the committed blob
carries all four fields. There was no staged change to commit, so I did not manufacture one.

On the a_stat question the answer is unambiguous: **pre-existing CSV defect, not a regression.** Six
measurements say so.

Every one of the three errors is on `a_stat/leaderboard.csv`; **zero mention `SUMMARY.json`.** The CSV
was written `2026-08-04 15:33`; the writer bug was fixed in `03ad619` on `2026-08-11 12:01`, seven
days later — the file cannot have been produced by the fixed code. Stripping the five regenerated keys
to reproduce the pre-regeneration artifact yields the **same three errors with byte-identical
messages**, and warnings went `10 → 7` with three removed and **none added**, so the regeneration
strictly improved the run. And feeding the same `metrics_long.csv` through the fixed writer produces
fully populated identity columns *and* recovers the `RMSE` the on-disk CSV lost.

Recorded in two places, because a verdict that lives only in a commit message decays into folklore:
`AGENT_ARTIFACT_CONTRACT.md` §8 gained an evidence table, and
`test_the_remaining_errors_are_a_pre_existing_csv_defect_not_a_regression` holds it as an assertion —
it pins the error set to that one file, re-derives the before/after comparison inside the test, and
checks the fixed writer's output. If anyone regenerates the A_STAT family, that test fails and forces
the note to be revisited.

### The Agent side: five tests, not six, and the cause was a stale cache

The first suite run said **266 passed**. The second, on the identical tree with no edits between them,
said **1 failed, 261 passed, 4 errors** — all five complaining `fixture 'legacy_run' not found`.
Clearing `__pycache__` and `.pytest_cache` returned it to 266 passed.

So the failures were a **stale bytecode cache**: `legacy_run` is defined at `tests/conftest.py:333` in
the uncommitted working tree, and the cached conftest predated it. Worth recording, because a
transient cache artifact that reports as five broken tests is exactly the kind of thing that gets
"fixed" by rewriting tests that were never wrong.

And the migration the prompt describes was already done — in the uncommitted tree, by the previous
session. Both paths are covered, which was the actual requirement:

**Present-and-correct** against the Lab's real artifact — `test_the_real_run_records_run_id_and_schema_version`
asserts `is_known` on `run_id`, `schema_version` and `data_file`, and that each equals the artifact's
own raw value; `test_the_real_committed_artifact_records_its_composition` asserts the framing is known
and matches the recorded string. That last one deserves credit for a good decision I did not make: it
asserts against `view.raw["client_framing"]` rather than a hardcoded sentence, because a test carrying
its own copy would pass forever while the Lab reworded — which is the exact failure it exists to
catch.

**Absent**, via `legacy_run` — a synthetic older artifact derived from `clean_run` with the five keys
stripped. Four tests use it across four files, plus a parametrised rendering case. Synthetic rather
than a snapshot is the right call: it keeps working on a machine with no Lab checked out, and real
pre-v2 runs do still exist (`2026-07-29`, `2026-07-30` are now tracked and carry none of the four).

I verified `real_run` is genuinely reading the Lab and not skipping — 15 passed, no skips under `-rs`.
A fixture that skips silently would make the present-path assertions decorative.

### The one real finding

`test_the_real_artifact_records_no_champion_pool_size` asserted the pool size was unrecorded, and its
reasoning was sound when written: the size was only *derivable*, from
`counts["machine-learning models"]` plus `champion_pool_category`, and doing that arithmetic would
have made the Agent the author of a number under a name the Lab never used.

**That is no longer the shape.** The Lab now publishes `model_composition.champion_pool` as an
explicit list of 13 model names. `len()` on a published list is reading a field, not inferring one, so
the test was asserting a limitation that had already been removed — and the Agent was rendering
"champion-eligible pool is not recorded" while the artifact recorded it.

`_pool_size()` now prefers an explicit int `champion_pool_size` if the Lab ever writes one, otherwise
counts a list of strings, otherwise reports unknown. The refusal I kept is the one that still matters:
`test_a_count_by_category_is_not_read_as_a_pool_size` asserts that counts-plus-category is **still**
not read as a pool, so the boundary moved for the right reason and not by one step further. Two more
tests cover an explicit size winning over the list, and malformed pools (`[]`, `[1,2,3]`, a string, a
dict) not being counted.

The Agent now says *"That leaves a champion-eligible pool of 13"* — read from the Lab, not computed
here.

---

## 5. Verdict

**Both premises were false; both were checked before acting.**

The Lab's `SUMMARY.json` was already committed, so no commit was fabricated. The a_stat verdict is
**pre-existing CSV defect, not a regression**, established on six measurements and recorded both as
prose with an evidence table and as an executable assertion.

The Agent's five (not six) failures were a stale bytecode cache, and the present/absent test split was
already complete in the uncommitted tree — verified rather than assumed, including that `real_run`
does not skip.

One genuine gap closed: the Lab publishes `champion_pool` as an explicit list, so the pool size was
recorded while the Agent still reported it unknown. Now read, with the inference-from-counts refusal
kept and tested.

Lab: 712 passed, 3 skipped, `EXIT=0`; frontend 110 passed. Agent: 269 passed, `EXIT=0`.

---

## 6. Outstanding

* **The Agent reads none of `daily_best_model_families`, `promoted_by_registry` or
  `promoted_outside_champion_pool`**, all of which the Lab now publishes. The last is an integrity
  cross-check — non-empty means the eligible pool a client was told about is wrong — and is currently
  the most valuable unread field.
* `model_composition.members` names every counted model and is unread; it would let the Agent answer
  "which models" rather than only "how many".
* The Lab's `2026-07-29` and `2026-07-30` summaries are tracked but pre-v2, so the absent path has
  real artifacts as well as the synthetic fixture. Nothing points `real_run` at them; a second fixture
  could exercise the absent path against a genuine old artifact.
* Only `SUMMARY.json` / `SUMMARY.txt` are tracked in the Lab, so a clone still cannot read a
  leaderboard, `predictions_long.csv` or `metrics_long.csv`. If the Agent needs those from a clone,
  that is an open decision about row-level Treasury data.
* This repo had unrelated uncommitted work on entry (`app.py`, `agent/llm.py`, `requirements.txt`,
  `README.md`, `app_legacy.py`). It is committed here alongside the test migration because separating
  it would have required unpicking a tree I did not create; nothing in it was reviewed by me.
