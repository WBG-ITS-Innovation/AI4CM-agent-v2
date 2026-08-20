# Session — dependencies, derived framing, and the missing targets

**Date:** 2026-08-12
**Repo:** `AI4CM-agent`, branch `feat/lab-door`
**Counterpart:** `AI4CM` (the Lab) at `e8c26a2` — "Write data_file, derive the model
composition, document what C_DL writes".
**Scope:** parts 1–3 implemented. Parts 4 and 5 planned only, at the user's instruction.

---

## 1. The prompt, verbatim

> In the AI4CM-agent repo on feat/lab-door. Multi-part session. Do parts 1–3 now; STOP after part 3 and give me plans for parts 4–5 without implementing them.
>
> CONTEXT: the app now runs (localhost:8501) after I hand-installed streamlit, plotly, statsmodels and scikit-learn. Its test suite had been passing against stubs, so "tests green" and "app runs" were independent facts.
>
> PART 1 — dependencies
> Walk every import reachable from app.py, write the complete real dependency set into requirements.txt with pinned versions, and verify from a clean venv build that the app starts. Add a test that imports every top-level module app.py needs, so this cannot recur.
>
> PART 2 — framing, derived not restated
> Replace the Agent's hardcoded MODEL_FRAMING with a read of the Lab's derived client_framing() from the artifact. The Lab now derives model composition from the registry; the Agent restating it as a string is the same defect the Lab just fixed — two repos each internally consistent and mutually wrong. If the field is absent from an older artifact, say the composition is not recorded for that run rather than falling back to a stale literal. Test that the Agent renders the artifact's framing and contains no hardcoded count.
>
> PART 3 — the missing targets, diagnose before fixing
> The running app offers only "Revenues" when the lab forecasts three (revenues, expenditure, state budget balance). Determine whether that is a real property of the 2026-08-04 artifact or a bug in target enumeration. Tell me which. If it's a bug, fix it and test that all targets present in an artifact are offered.
>
> Then STOP and plan the following. Do not implement.
>
> PART 4 (plan only) — chat layout and dataset upload
> (a) Move the chat input to the bottom with conversation scrolling above it, ChatGPT/Claude convention. The "Run a new forecast" panel collapses to a sidebar control or expander so the chat owns the main column. Note any Streamlit constraints that make this awkward.
> (b) Let the user upload their own Excel/CSV daily balance file. The upload must pass through the same preflight checks, schema validation and digest recording as the canonical file, and must refuse clearly rather than silently forecasting from a malformed file. Cover: where the file lands, how its digest is recorded, what validation runs, what the refusal messages say, and whether an uploaded-data forecast can ever be published (my answer: no).
>
> PART 5 (plan only) — arbitrary column as target
> Let the user forecast any eligible numeric column, not only the three official targets. Cover:
> - Official vs arbitrary must be unmissable in the UI. Official targets have a champion recipe, a persistence benchmark and quality gates; an arbitrary column has none. An arbitrary-column forecast is an ExploratoryResult with no publish path, never presented as comparable to an official result.
> - Eligibility: numeric, sufficient non-null history, usable date index. State the minimum series length and where that number comes from.
> - Stock vs flow. Budget balance is a stock, and treating it as a flow cost the LSTM its skill entirely. For an arbitrary column the lab cannot know which — either ask the user or state the assumption in the output. Never silently pick.
> - Which families can run without a recipe, and the default configuration.
> - The benchmark: h-step persistence is computable on any series, so confirm it uses the shared implementation, not a second one.
> - What the result explicitly does NOT claim: no gate verdict, no champion, no validated skill figure.
>
> CROSS-CUTTING — this is a real conversational agent, so it must answer open-ended questions, not only the five demo prompts. Every honesty rule already established holds in free-form answers too: never narrate an absent field as a value, never present a withheld or FAILED_QUALITY family as clean, treat missing and "n/a" as UNKNOWN, and never invent a threshold or standard. That last one is not hypothetical — this Agent previously asserted "the lab requires at least 5%", a number that existed nowhere. When asked something the artifacts cannot answer, say so.
>
> On model backend: report what the Agent currently uses for narration (Azure OpenAI, per the config), and whether the code is provider-agnostic enough to swap in an on-prem model without touching the honesty layer. I want to know where the coupling is, not for you to change it.
>
> Session logging: docs/sessions/2026-08-12-agent-deps-framing-targets.md, full narrative verbatim plus the exact prompt, plan, real command output, verdict, and outstanding items. Run the suite, show passing output, commit the md with the code, walk me through the diff.

---

## 2. Plan

Written after orientation, before any edit.

1. **Part 1.** Walk `app.py`'s import graph with the AST rather than by eye. Pin the
   result. Verify with a genuinely clean venv — new interpreter, `pip install -r`,
   then execute `app.py` under the *real* Streamlit runtime, not an import smoke test.
   Add a test that derives the dependency set the same way so it cannot drift.
2. **Part 2.** Add `client_framing` / `champion_pool_size` to the contract reader as
   tri-state `Value`s. Delete `plain.MODEL_FRAMING`, `MODEL_FRAMING_LONG` and
   `CHAMPION_ELIGIBLE_POOL` outright — no fallback, because a plausible stale
   sentence is the failure mode. Rewire the four render sites, the LLM context and
   the system prompt. Add a source scan that fails on any hardcoded model count.
3. **Part 3.** Diagnose first: read the 2026-08-04 artifact and the enumeration code
   before deciding it is a bug. Report the verdict, then fix only if warranted.
4. **Cross-cutting.** Treat `AGENT_SYSTEM` and `build_agent_context` as testable
   artifacts, since on the free-form path they are the only thing between the model
   and a fabrication.
5. Suite, session doc, commit, diff walkthrough. Parts 4–5: plans only.

---

## 3. Orientation

`app.py` (964 lines), `agent/` (7 modules + `agent/runtime/`), `tests/` (5 files, 159
tests passing at HEAD `6ef8ebd`). The Lab is checked out at `../AI4CM`, one commit
ahead of what the Agent's last session consumed.

`ai4cm/` in the working tree contains only stale `__pycache__` — leftovers from a
previous layout, tracked by nothing. Not touched.

Baseline before any change:

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 45%]
........................................................................ [ 90%]
...............                                                          [100%]
```

159 passed — in an environment that at the time of the report had no streamlit and
no plotly. That is the fact this session starts from.

---

## 4. Part 1 — dependencies

### What was actually wrong

`requirements.txt` was not empty and was not obviously wrong, which is why it
survived. It listed `streamlit>=1.36`, `plotly>=5.18`, `statsmodels>=0.14`,
`scikit-learn>=1.3`. Three separate defects sat underneath:

1. **`certifi` was undeclared.** `agent/llm.py` line 3 is
   `import os, json, certifi` — module scope, and `app.py` imports `agent.llm` at
   module scope. So `certifi` is a hard runtime dependency of starting the app. It
   was never in `requirements.txt`; it happened to be installed as a transitive
   dependency of `openai`. The app worked by luck.
2. **Three pins were fiction.** `pydantic`, `rich` and `joblib` are imported by
   nothing in this repo. Verified by grep across `agent/`, `app.py` and `tests/`.
   `requests` is imported once, inside a `try/except` in
   `agent/runtime/alerts.py`, which is not reachable from `app.py` and has a
   file-log fallback when the import fails.
3. **Nothing verified the file.** The suite stubs `streamlit`, `plotly`,
   `agent.tools` and `agent.llm` (`tests/test_app_rendering.py` lines 172–201).
   That is the right call for those tests — they assert what the page *says*, and
   a real Streamlit runtime would only make them slow. The cost is that a green
   suite stopped implying a startable app, and no other test paid that cost back.

### The dependency set, walked

Transitive closure from `app.py`, following first-party imports, including
function-scope ones:

| Module | Reached via | Why it is not optional |
|---|---|---|
| `streamlit` | `app.py` | the app |
| `plotly` | `app.py`, `agent/tools.py` | every chart |
| `pandas`, `numpy` | `app.py`, `agent/contract.py`, `agent/lab_bridge.py` | every artifact read |
| `dotenv` | `app.py` | `load_dotenv()` at module scope |
| `statsmodels`, `sklearn` | `agent/tools.py` | `app.py` imports `agent.tools` **unconditionally**, even in lab mode where it is never called |
| `certifi` | `agent/llm.py` | module scope; see above |
| `openai` | `agent/llm.py`, function scope | narration |

`statsmodels` and `scikit-learn` deserve a note: they are only used by demo mode,
but `from agent import tools as T` runs at import time regardless of mode, so they
are load-bearing for lab mode too. Declared, not deferred — making them optional is
a code change, not a packaging one.

### What was written

`requirements.txt` — nine packages, `==` pins, with the reasoning in comments.
`requirements-dev.txt` — `-r requirements.txt` plus `pytest==9.1.1`.

### The test

`tests/test_dependencies.py`. It does not carry a list. It parses `app.py` with
`ast`, follows every import that resolves to a file in this repo, and collects the
top-level third-party names. Then, with **no stubs**:

* every module imports for real — fails exactly as `streamlit run app.py` would;
* every module is pinned in `requirements.txt`;
* the installed version equals the pinned version;
* nothing is pinned that no reachable module imports.

The last one is the direction that catches `pydantic`/`rich`/`joblib`. The AST walk
itself is guarded: `test_the_import_graph_is_walkable_and_not_empty` asserts the four
modules from the report are still in the graph, so a broken walk fails loudly rather
than passing thirty tests vacuously.

That guard earned itself immediately. The first version recorded
`from agent import tools as T` as an import of `agent` only, not `agent.tools`, so
`statsmodels` and `scikit-learn` dropped out of the graph and the "nothing unreachable
is pinned" test demanded I delete them:

```
E  AssertionError: statsmodels vanished from app.py's import graph
E  AssertionError: requirements.txt pins ['scikit-learn', 'statsmodels'], which
   nothing reachable from app.py imports.
```

Fixed by recording `f"{module}.{alias}"` for `ImportFrom` names as well.

### Verification — clean venv

New interpreter, nothing inherited:

```
$ python3 -m venv cleanvenv
$ ./cleanvenv/bin/python -m pip install -r requirements-dev.txt
Successfully installed MarkupSafe-3.0.3 altair-6.2.2 annotated-types-0.8.0
anyio-4.14.2 attrs-26.1.0 blinker-1.9.0 certifi-2026.7.22 charset_normalizer-3.5.0
click-8.4.2 distro-1.9.0 h11-0.16.0 httpcore-1.0.9 httptools-0.8.0 httpx-0.28.1
idna-3.18 iniconfig-2.3.0 itsdangerous-2.2.0 jinja2-3.1.6 jiter-0.16.0 joblib-1.5.3
jsonschema-4.26.0 jsonschema-specifications-2025.9.1 narwhals-2.24.0 numpy-2.5.1
openai-2.53.0 packaging-26.3 pandas-3.0.5 patsy-1.0.2 pillow-12.3.0 plotly-6.9.0
pluggy-1.6.0 protobuf-7.35.1 pyarrow-24.0.0 pydantic-2.13.4 pydantic-core-2.46.4
pydeck-0.9.3 pygments-2.20.0 pytest-9.1.1 python-dateutil-2.9.0.post0
python-dotenv-1.2.2 python-multipart-0.0.32 referencing-0.37.0 requests-2.34.2
rpds-py-2026.6.3 scikit-learn-1.9.0 scipy-1.18.0 six-1.17.0 sniffio-1.3.1
starlette-1.3.1 statsmodels-0.14.6 streamlit-1.61.1 tenacity-9.1.4
threadpoolctl-3.6.0 toml-0.10.2 tqdm-4.70.0 typing-extensions-4.16.0
typing-inspection-0.4.4 urllib3-2.7.0 uvicorn-0.52.1 websockets-16.1.1
```

`joblib`, `requests` and `pydantic` still appear — as transitive dependencies of
scikit-learn, streamlit and openai respectively. That is the correct place for them.
The point of removing them from `requirements.txt` is that this repo no longer
*claims* them.

Server actually serving:

```
$ ./cleanvenv/bin/python -m streamlit run app.py --server.headless true --server.port 8599
  Local URL: http://localhost:8599
$ curl -s http://localhost:8599/_stcore/health
ok
$ curl -s -o /dev/null -w "%{http_code}" http://localhost:8599/
200
```

A listening server is weaker evidence than it looks — Streamlit executes the script
per session, so a script that raises still gives you a 200. So the script was also
run end to end under the real Streamlit runtime, which surfaces exceptions:

```
$ ./cleanvenv/bin/python boot_check.py     # streamlit.testing.v1.AppTest
exceptions: 0
titles: ['AI4CM — Daily Forecast, with receipts']
tab labels: ['Ask the agent', 'Dashboard', 'Run history', 'Learn']
first selectbox options: [['Revenues']]
```

Zero exceptions from a clean build. That last line is Part 3's symptom, reproduced
before I went looking for it.

---

## 5. Part 2 — framing, derived not restated

### What the Lab actually publishes

Read before writing. `backend/model_reference.py` gains `client_category()`,
`composition()` and `client_framing()` at Lab commit `e8c26a2`. `composition()`
counts from `model_pool()`; `client_framing()` writes the sentence from the counts;
`client_category()` **raises** on an unrecognised pipeline, so a fifth family cannot
reach the pool while silently missing from the numbers. The Lab's own session record
states the output and the finding:

> 13 machine-learning models, 5 deep-learning models and 4 statistical models
> compete on each target; prediction intervals come from 3 quantile methods; 3
> further entries are reference baselines, not competitors.

versus the Agent's pinned literal, which omitted the deep-learning clause entirely.
The Lab's outstanding item 1 names the situation exactly: *"Both suites pass, because
each repo is internally consistent; they disagree with each other."*

### The correction I have to make to the brief

**`client_framing` is not in any artifact.** The prompt says to read it "from the
artifact"; it is not there to read. Confirmed three ways:

* `grep client_framing` across the Lab hits `model_reference.py`, its test, one
  session doc and one report — never `daily_summary.py`, which writes `SUMMARY.json`;
* `docs/AGENT_ARTIFACT_CONTRACT.md` §1 lists no composition field;
* every committed `SUMMARY.json` (`2026-07-29`, `2026-07-30`, `2026-08-04`) carries
  exactly `run_date, target, cadence, horizon, families, overall, mode, freshness`.

So the implemented behaviour is the second half of the instruction — "if the field is
absent from an older artifact, say the composition is not recorded for that run" —
applied to *every* run, because no run records it. **The Agent now says "not
recorded" everywhere.** That is a visible loss of information relative to yesterday,
and it is the correct trade: the sentence it used to print had been wrong for weeks
and nothing could tell. Publishing the field is Lab-side work; when it lands the
Agent picks it up with no further change, and a test asserts exactly that
(`test_the_real_committed_artifact_records_no_composition` fails the day it appears).

### Built

**`agent/contract.py`** — `RunView.client_framing` and `RunView.champion_pool_size`,
both tri-state `Value`s, read by `read_model_composition()`. It accepts two shapes so
the Lab can choose either without an Agent change:

```json
{"client_framing": "<sentence>"}
{"model_composition": {"framing": "<sentence>", "champion_pool_size": 13}}
```

Everything else is UNKNOWN. `model_composition` present but not an object is
`UNPARSEABLE`, not a sentence — contract rule 3, structure is detected, never
assumed. And **counts without a sentence stay UNKNOWN**: the Agent will not compose
"13 machine-learning models…" out of a counts dict, because then the Agent owns the
wording again and the wording drifts again.

Absence is recorded as a **note**, not a defect. The field is not in the published
contract, so its absence is not a departure from it — but it is a real gap and it
surfaces in the "Known ambiguities" panel rather than being swallowed.

**`agent/plain.py`** — `MODEL_FRAMING`, `MODEL_FRAMING_LONG`,
`CHAMPION_ELIGIBLE_POOL` and `model_framing()` deleted. Replaced by
`say_model_framing(run)` / `say_model_framing_long(run)`. When unknown they return
`NO_COMPOSITION`, which explains *why* there is no fallback so that "not recorded"
does not read as a lookup the Agent failed to do. The long form keeps
`_WHY_THE_KINDS_ARE_SEPARATE` — baselines are the ruler, quantile methods produce
intervals — because that paragraph names no quantity and therefore cannot go stale.

**`agent/lab_bridge.py`** — `EXPLANATIONS["models"]` rewritten with no numbers in it.
This mattered more than it looks: that dict is handed to the LLM as `glossary`, so
every count written there was a count the model could quote, unlabelled and
unqualified.

**`app.py`** — four render sites rewired, plus the LLM context (`model_composition`
now carries either the sentence or `"UNKNOWN — … state no model counts of any kind"`)
and the system prompt, which now states the *rule* rather than interpolating the
*fact*.

### Tests

`tests/test_model_framing.py`, 30 tests. The old tests asserted the sentence
verbatim, which is the defect wearing a lab coat — a test that asserts the Agent
agrees with itself. The new ones assert the property instead:

* a recorded composition renders verbatim (nested and flat shapes);
* nine shapes of absence — `{}`, null, blank, wrong type, empty object — none
  becomes a sentence;
* counts-without-a-sentence stays UNKNOWN while a recorded pool size is still read;
* an unknown pool size is not borrowed from anywhere;
* **no source file in `agent/` or `app.py` contains a model count**, by regex over
  the tokenised source. `#` comments are excluded — they cannot reach a user by any
  path, and forbidding them would mean this history could not be written down.
  Docstrings and string literals stay in scope, because those are one f-string from
  a rendered page.
* `EXPLANATIONS` carries no counts, since it goes to the LLM.

The source scan caught my own comment on the first run, which is the behaviour I
wanted from it.

---

## 6. Part 3 — the missing targets

### Verdict: **a bug in target enumeration.** Both halves, precisely:

**The artifact really does record one target, and that is correct.**
`backend/forecast_runs/2026-08-04/SUMMARY.json` has `"target": "Revenues"` — a
string, not a list. All three committed runs are the same. A run forecasts one
series; there is nothing hidden in the artifact and nothing wrong with it.

**But that is not the question the picker asks.** The control is labelled *"Run a new
forecast"*. It asks what the lab **can** forecast — a property of the lab, not of its
last run — and `lab_bridge.target_choices()` answered it from
`SUMMARY.json.data_file`:

```python
p = _data_file_path(run.view, run.run_dir)   # needs view.data_file
if p is None: return []                       # → always, on every committed run
...
return ([str(run.view.target.value)] ...), False
```

`data_file` is the field the Lab's own contract documents as *"written by current
code; **absent on every committed artifact**"*. So the enumeration had exactly one
source, that source was known-empty by documentation, and the failure degraded
silently to `[run.target]` — which looks like an answer. The menu could not have
held more than one entry on any artifact the Lab has ever committed.

Measured, not assumed:

```
$ python3 -c "... json.load(SUMMARY.json) ..."
/target = Revenues
/families[0]/name = A_STAT   ...   (no data_file key anywhere)

$ ls ../AI4CM/backend/data/processed/
master_daily_clean_treasury.csv     # exists — the run just never named it
```

### The fix

The Lab's authority on official targets is `registry/recipes.json` — one champion
recipe per target, version-controlled, always present, and the same file
`backend/forecast_modes.recipe_status()` consults before it will issue an official
forecast. Three recipes: `Revenues`, `Expenditure`, `State budget balance`.

`lab_bridge.official_targets(repo)` reads it and returns `(targets, note)`; a missing,
unparseable or malformed registry returns no targets **and a reason**, never a guess.
`target_choices()` now returns a `TargetMenu` — `targets`, `official`, `source`,
`note`, `enumerated`, `is_official()` — so the UI can say which entries are official
rather than presenting one undifferentiated list. That distinction is Part 5's
foundation and it is now in place.

The run's own target is always kept, even if the registry omits it — a run that
happened is evidence the lab can forecast that series — but it is labelled *"has no
champion recipe in the registry — offered because the run used it, not because it is
official."*

`available_targets()` (the data-file path) is kept, unused by the menu. It is Part 5's
route to the other 38 columns, and one test asserts it currently enumerates nothing,
so the day the Lab starts writing `data_file` that becomes a visible change rather
than a silent one.

### Verification, against the real lab

Same clean-venv boot check, after the fix:

```
$ ./cleanvenv/bin/python boot_check.py
exceptions: 0
first selectbox options: [['Revenues', 'Expenditure', 'State budget balance']]
```

`tests/test_target_enumeration.py`, 14 tests: all official targets offered; a
non-registry run target kept and labelled; duplicates collapsed; missing /
unparseable / `recipes`-not-a-list / empty / target-less registries each reported
with their own reason; and one test against the actual checkout.

---

## 7. Cross-cutting — the free-form path

The five demo prompts go through `answer_lab_question()`, which is rule-based and
testable line by line. Everything else goes to the LLM, where the only things between
the model and a fabrication are `AGENT_SYSTEM` and `build_agent_context`. Those are
now treated as artifacts under test:

* `build_agent_context` marks an absent composition `UNKNOWN` and instructs "state no
  model counts of any kind"; a recorded one passes through verbatim;
* no family field reaches the model as a bare `null` — every unknown arrives labelled;
* `AGENT_SYSTEM` is asserted to contain each honesty rule, including a new one:

  > Never state a threshold, cut-off or standard that is not in CONTEXT. If you are
  > about to write "the lab requires at least N", stop: no such number exists unless
  > CONTEXT gives it.

  The invented "5%" is named in the prompt on purpose, so the specific failure is
  fenced rather than left to a general instruction.
* every function that calls `chat_llm` mentions `AGENT_SYSTEM` — checked by AST, per
  enclosing function, because `decide_action` builds its message list several
  statements before the call.

**What this does not do.** These are prompt-level guarantees, not enforced ones.
There is no verifier between the model's text and the page: no post-hoc check that a
number in the answer appears in the context. The rule-based path is verified; the
free-form path is governed. That gap is stated in Outstanding, not papered over.

---

## 8. Model backend — where the coupling is

**Today.** Azure OpenAI. `agent/llm.py` prefers `AZURE_OPENAI_API_KEY` +
`AZURE_OPENAI_ENDPOINT` (`AzureOpenAI`, deployment from `AZURE_OPENAI_DEPLOYMENT`,
default `gpt-4o-mini`), falls back to `OPENAI_API_KEY` (`OpenAI`), else no narration
and the app runs rule-based. `.env` in this checkout sets all four Azure variables.

**Is it provider-agnostic enough to swap in an on-prem model without touching the
honesty layer? Yes — the honesty layer is genuinely untouched. The coupling is in
four places, all shallow, and one of them is not shallow at all.**

| # | Coupling | Where | Swap cost |
|---|---|---|---|
| 1 | Client construction | `agent/llm.py::_client_and_model` | Rewrite one function. This is the intended seam. |
| 2 | "Is a model available?" is expressed as "is an OpenAI key set?" | `have_llm()`, called from `app.py` ×4 | One function; `app.py` never names a provider. |
| 3 | Wire format is OpenAI chat-completions — `[{"role","content"}]`, `temperature`, `response_format={"type":"json_object"}` | `chat_llm` signature | Free for any OpenAI-compatible server (vLLM, Ollama, TGI, llama.cpp). Real work for a native non-OpenAI API. |
| 4 | JSON mode | `chat_llm(json_mode=True)` in `decide_action` | Already defensive: on failure it retries without `response_format`, and an unparseable reply falls back to the deterministic path. |

**The honesty layer has zero LLM coupling, verified not assumed:**

```
$ grep -c "llm\|openai\|azure" agent/contract.py agent/plain.py
agent/contract.py:0
agent/plain.py:0
```

`app.py` imports exactly three names from `agent.llm` — `have_llm`,
`summarize_with_llm`, `chat_llm` — and none of the rendering, parsing or plain-
language code imports it at all. The provider name appears nowhere outside
`agent/llm.py`.

**The coupling that is not shallow, and is the one worth planning for.** The honesty
rules are *prompt-enforced*, so swapping to a smaller on-prem model degrades honesty
with no code change and no test failure. `AGENT_SYSTEM` is ~30 lines of "never" —
GPT-4o-class instruction-following. A 7B on-prem model given the same context will
confabulate a threshold sooner, and nothing in this repo would catch it. If an on-prem
swap is real, the work is not the client: it is an output verifier that rejects any
number in the reply that does not appear in the context, so the guarantee moves from
the prompt into code. Not built, not in scope — flagged.

Minor, noted not fixed: `summarize_with_llm` is imported by `app.py` and never
called; `parse_intent_with_llm` is dead in `agent/llm.py`.

---

## 9. Real output

Full suite, project venv:

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 29%]
........................................................................ [ 59%]
........................................................................ [ 88%]
...........................                                              [100%]
```

Full suite in the clean venv built from `requirements-dev.txt` alone:

```
$ ./cleanvenv/bin/python -m pytest
........................................................................ [ 29%]
........................................................................ [ 59%]
........................................................................ [ 88%]
...........................                                              [100%]
243 passed in 4.40s
```

App executed end to end under the real Streamlit runtime, same clean venv:

```
$ ./cleanvenv/bin/python boot_check.py
exceptions: 0
titles: ['AI4CM — Daily Forecast, with receipts']
tab labels: ['Ask the agent', 'Dashboard', 'Run history', 'Learn']
first selectbox options: [['Revenues', 'Expenditure', 'State budget balance']]
```

159 → 243 tests. 84 added: 30 dependency, 30 framing, 14 target enumeration, 10
rendering and free-form-path. 6 removed — the ones that pinned the stale literal.

---

## 10. Verdict

**Part 1 — done.** Nine packages, exactly pinned, derived from the AST rather than
from memory. `certifi` was the undeclared one that mattered; `pydantic`, `rich` and
`joblib` were fiction. Verified from a clean venv by executing the app under the real
Streamlit runtime, not by importing it. `tests/test_dependencies.py` checks both
directions, so neither an undeclared import nor a phantom pin survives.

**Part 2 — done, with a correction to the brief.** `client_framing()` exists in the
Lab but is written into no artifact, so the Agent now says "the model composition is
not recorded" on every current run. The hardcoded sentence, the long form and the
literal 13 are gone with no fallback, and a source scan keeps them gone. The Agent
will render the Lab's sentence the day the Lab writes it, with no further change.

**Part 3 — a bug, and fixed.** The artifact correctly records one target; the picker
correctly asks a different question and was answering it from a field documented as
absent. It now reads the Lab's champion-recipe registry and offers all three, with
official and non-official distinguished in the model rather than only in prose.

**Parts 4 and 5 — not implemented, as instructed.** Plans delivered separately.

**One thing I would flag as unresolved rather than done:** the honesty guarantees on
the free-form path are prompt-level. Every rule is stated, tested as *stated*, and
none is *enforced*. That is a real limit and it is the first thing an on-prem model
swap would expose.

---

## 11. Outstanding

1. **The Lab does not write `client_framing` into `SUMMARY.json`.** Until it does,
   every run reads "not recorded" and the Agent quotes no composition at all. This is
   Lab-side: `daily_summary.py` would write either
   `{"client_framing": client_framing()}` or the richer
   `{"model_composition": {"framing": ..., "champion_pool_size": ...}}`. The Agent
   already reads both. Their outstanding item 1 also asks for a cross-repo test that
   the two agree; with the Agent holding no literal, that test is now trivially
   satisfiable in one direction — there is nothing on this side to disagree.
2. **No output verifier on the free-form path.** See §7 and §8.
3. **`data_file` is still absent from every committed run**, so
   `available_targets()` enumerates nothing. Harmless now that the menu does not
   depend on it; load-bearing for Part 5's arbitrary-column work, which needs the
   canonical file's column list.
4. **`statsmodels` and `scikit-learn` are demo-mode-only but imported
   unconditionally.** ~150 MB of install for code lab mode never calls. Making
   `agent.tools` a lazy import inside the demo-mode branch would drop them to
   optional — a code change, deliberately not made in a packaging task.
5. **Dead code in `agent/llm.py`**: `parse_intent_with_llm` unused,
   `summarize_with_llm` imported but never called.
6. **`ai4cm/` in the working tree is stale `__pycache__` only.** Untracked, unused,
   left alone.
7. **`frontend/` and `var/` remain untracked**, including `frontend/node_modules/`.
   Not added; not in `.gitignore` either. Worth a decision.
