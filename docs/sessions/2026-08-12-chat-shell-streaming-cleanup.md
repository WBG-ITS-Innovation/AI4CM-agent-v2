# Session — the chat shell, streaming, and the outstanding list

**Date:** 2026-08-12
**Repo:** `AI4CM-agent`, branch `feat/lab-door`
**Counterpart:** `AI4CM` (the Lab), read-only this session — no lab run was launched
against it (see §7).
**Scope:** Part 4(a) as planned, option 1; two chat bugs; streaming; outstanding
items 4, 5 and 7 from the previous session.

---

## 1. The prompt, verbatim

> In the AI4CM-agent repo. Implement Part 4(a) as planned, option 1: drop st.tabs,
> move navigation to a sidebar st.radio, st.chat_input returns to top level and pins
> to the viewport bottom. "Run a new forecast" becomes st.expander(expanded=False)
> above the transcript, not in the sidebar — the run panel's st.status trace already
> renders into the chat column. Restyle the sidebar nav controls for the navy
> background.
>
> Fix the two existing bugs while in there: the assistant message is rendered and then
> appended causing a double-render on the run path via st.rerun(), and
> st.session_state.chat is appended in two places with different shapes
> ({"role","content"} vs {...,"trace"}). Unify the shape.
>
> Add streaming: make chat_llm yield chunks in agent/llm.py so st.write_stream works.
> Confined to llm.py, no honesty-layer impact.
>
> Also from your outstanding list: make agent.tools imports lazy so statsmodels and
> scikit-learn (~150MB) aren't loaded for lab mode that never calls them; remove the
> dead code in agent/llm.py (summarize_with_llm imported and never called,
> parse_intent_with_llm dead); and decide frontend/ and var/ — gitignore them, they're
> node_modules and sqlite output.
>
> Same session logging format. Show me the app running, not just tests.

---

## 2. Part 4(a) — the shell

### The constraint that decides the layout

`st.chat_input` pins itself to the bottom of the viewport **only when it is called
from the main body of the page**. Inside a tab — or a column, an expander, a
container, the sidebar — it renders inline, wherever the transcript happened to end.
That is one constraint, and everything else in this part follows from it:

* `st.tabs` is gone. Four views, one sidebar `st.radio`, and exactly one renders per
  script pass.
* The run panel is an `st.expander(expanded=False)` in the main column, above the
  transcript. Not the sidebar: pressing **Run** streams an `st.status` trace that
  renders into the chat column regardless, and a control whose output appears in a
  different region is a worse trade than one extra click.
* The `st.chat_input` call sits at module level under `if view == "Ask the agent":`.
  An `if` is not a layout context; a `with` is. There is now a test that walks the
  AST and fails if the call ends up inside any `with` block, because that regression
  is invisible in a stubbed render — a stub's `chat_input` is pinned nowhere.

Measured in the running app: the composer's bounding box is `y=786, height=58` in a
900px viewport, before the conversation starts and after a full answer has streamed
in. It does not move.

### The sidebar on navy

`st.radio` renders a small BaseWeb circle plus body text. On `#002244` the circle
disappears and the options read as a bulleted list rather than as the thing you
click. Each option is now a full-width chip: translucent white at rest, brighter on
hover, filled `#009FDA` at 30% with a solid cyan inset bar when selected.

Two details worth keeping:

* The selected state hangs off `label[data-testid="stRadioOption"][data-selected="true"]`,
  not `:has(input:checked)`. The real `<input>` lives inside a `clip-path`'d span and
  BaseWeb owns its states; Streamlit's own attribute is the more durable hook.
* The circle is hidden via `label > div > div > div:first-child`. The first attempt
  used `label > div:first-child`, which quietly matched nothing — the label's first
  child is the hidden input's `<span>`. It was caught by looking at the screenshot,
  not by the tests.

---

## 3. The two chat bugs

### One message shape

The transcript held two. The answer path appended `{"role", "content", "trace"}`; the
run path appended a bare `{"role", "content"}` for its acknowledgement and a second
message for the result. The transcript loop's `m.get("trace")` was therefore not a
feature but a coin flip: whether a turn could show its work depended on which branch
produced it, and a saved history mixed both.

Now every append goes through `push(role, content, trace="")`, which builds the full
shape and persists it. `load_saved_chat` normalises what it reads and drops anything
malformed rather than repairing it. A test walks the AST and fails if anything
appends to `st.session_state.chat` outside `push`.

Two things surfaced while unifying: `save_chat` and `load_saved_chat` existed and
were **never called** — the "persistent history" of commit `a4055ee` was dead code —
and the history path was a fixed relative path, so a test run rendered whatever
conversation happened to be in the working directory. Both fixed: `push` saves, the
chat view loads, and the path is `AI4CM_CHAT_HISTORY`-overridable.

### One render per turn

The run path rendered the acknowledgement live, ran the lab inside `st.status`,
appended **two** assistant messages, and called `st.rerun()`. The rerun threw away
what had just been drawn and redrew the conversation from the transcript — the
acknowledgement as one bubble, the result as another, with the streamed trace
replaced by a static block.

Both paths now do the same thing: render the turn live inside one
`st.chat_message`, then `push` exactly one assistant message holding what was
rendered. No `st.rerun()`. The transcript above has already been drawn this pass, so
a rerun could only redraw it — and with streaming in place, a rerun turns a streamed
answer into a jump-cut.

The cost is stated in the code: after a chat-driven lab run, the module-level `run`
is stale until the next interaction. The chat view does not read it again in that
pass, and any subsequent widget interaction reloads it. Verified in the app: after a
run, the transcript holds exactly two messages — one user, one assistant, ten trace
lines, the acknowledgement folded into the same message.

---

## 4. Streaming

`chat_llm` gains `stream: bool = False`. `False` returns the whole reply as before;
`True` returns an iterator of text chunks for `st.write_stream`. The import surface
is unchanged — no new name — so nothing that stubs `agent.llm` needs to know about
it.

Three failure modes are absorbed inside the generator rather than raised into the UI:

* a deployment that refuses `stream=True` — one buffered call is made instead and
  yielded whole;
* Azure's first event, which carries the content-filter annotation and an **empty**
  `choices` list — indexing `[0]` on it raises;
* a connection that drops mid-answer — whatever arrived is kept.

`None` and an empty iterator are deliberately different: `None` means no backend is
configured, an empty iterator means the call was made and produced nothing. The
caller falls back to `answer_lab_question` on both, and records which happened in the
trace.

### The one place this was not confined to llm.py

A reply that already exists as a string cannot be streamed. `decide_action` used to
ask the LLM, in a single JSON call, both *how to handle this message* and *what to
say* — so the answer was fully formed before the app ever saw it.

That call is now routing-only (`Do not write the answer here`), and a second call
streams the answer. Same `AGENT_SYSTEM`, same `build_agent_context`, plus the last
six turns of history — so the honesty rules apply to exactly the text the user reads,
which is the property `tests/test_app_rendering.py` already asserts per enclosing
function. Question-shaped messages now cost two calls instead of one; the routing
call's output shrank from a full answer to a small JSON object, and the answer
streams, so wall-clock to first token went down.

The post-run narrative streams the same way. What it rewrites is still
`run_result_narrative`'s output — built by `agent/plain.py` from contract-read values
— and the prompt still says to keep every number and caveat exactly. **No honesty
rule changed, and no new one was added.** Streaming moved where the text is
generated, not what governs it.

---

## 5. The outstanding items

### 4 — `agent.tools` is now lazy

`from agent import tools as T` left app.py's module level and moved into the
demo-mode branch, its only user. Measured on this machine:

```
lab-mode module set:  398 ms,  619 modules
+ agent.tools:       1474 ms, +1257 modules   (statsmodels 54 MB, sklearn 46 MB)
```

Every lab-mode start used to pay that for code it never calls.

Both packages stay pinned. They are a hard dependency of demo mode, and
`tests/test_dependencies.py` walks nested imports, so a lazy import is still a
declared one — the AST-derived requirements set did not change. A new test asserts
both halves: `agent.tools` is not in app.py's *module-level* imports, and it is still
in its *reachable* imports. Drop the second and the two packages quietly become
undeclared dependencies of demo mode.

Verified by running demo mode for real (§7), not by inspection.

### 5 — the dead code in `agent/llm.py`

`parse_intent_with_llm` and `summarize_with_llm` are gone from `agent/llm.py`. They
were not dead, though: `app_legacy.py` calls both. The previous session's audit
looked at `app.py` and missed it, and deleting them outright would have left a
tracked file that cannot import.

They moved to `app_legacy.py`, their only caller, which imports `_client_and_model`
from `agent/llm.py`. The shared module now contains only what the current agent uses
— which was the point — and the legacy app still runs. `app_legacy.py` remains a
deletion candidate; that call is the user's, and it is one `git rm` away now that
nothing in `agent/` exists for its sake.

`agent/llm.py` also gained a `_complete` helper shared by the buffered and streamed
paths. One behaviour change: the retry-without-`response_format` now fires only when
`json_mode` was set. Previously any failure triggered a retry that, without
`json_mode`, was byte-identical to the call that had just failed.

### 7 — `frontend/` and `var/`

Both gitignored, with the reason in the file:

* `frontend/` — a Node prototype (`node_modules/` + `dist/`). Nothing in this repo
  imports it, `app.py` is the only UI, and it is not a build input.
* `var/` — local runtime output: an SQLite database with its `-wal`/`-shm`, a logs
  directory, and an empty `forecast_runs`. Machine state, regenerated on use.

---

## 6. Tests

254 pass, 6 fail — the same 6 fail on `git stash`, i.e. before any change in this
session (baseline 237 passed / 6 failed; after, 254 passed / 6 failed). See §6.1.
New file `tests/test_chat_shell.py` (16 tests) plus one in `test_dependencies.py`:

* **Layout, on the source.** No `st.tabs`; exactly one navigation `st.radio`;
  `st.chat_input` called exactly once and inside zero `with` blocks; the run panel is
  an `st.expander` with `expanded=False`.
* **One shape.** Nothing appends to `st.session_state.chat` outside `push`; every
  entry carries all three keys; a saved transcript in the old two-key shape loads and
  is normalised; a missing file is an empty transcript, not a crash.
* **Streaming.** Chunks arrive in order; an empty-`choices` event does not crash the
  stream; a deployment that refuses streaming still answers; a connection that drops
  keeps what arrived; no backend returns `None` rather than an empty stream; buffered
  calls are byte-identical to before and send no `stream` key.
* **The split call.** The streamed answer carries `AGENT_SYSTEM` and the CONTEXT; the
  router is asserted to contain its "do not write the answer here" instruction and no
  `stream=True`.

### 6.1 The six red tests — the Lab moved under them

```
tests/test_app_rendering.py::test_real_run_says_its_input_file_is_unrecorded
tests/test_app_rendering.py::test_a_run_without_a_composition_says_so_on_the_page[real_run]
tests/test_model_framing.py::test_the_real_committed_artifact_records_no_composition
tests/test_real_artifact.py::test_the_real_run_is_missing_run_id_and_schema_version
tests/test_real_artifact.py::test_the_real_run_records_no_input_file
tests/test_target_enumeration.py::test_the_old_source_is_empty_on_a_committed_artifact
```

All six read the *real* artifact at `../AI4CM/backend/forecast_runs/2026-08-04`, and
all six assert that a field is **absent** from it. That file was rewritten at 15:16
today, during this session, and now carries `run_id`, `schema_version`, `data_file`
and the derived model composition — precisely what the previous session's Outstanding
item 1 asked the Lab for. In the lab repo it is staged but uncommitted (`A `), so
this is work in flight.

Nothing in this session touched them: `git stash -u` reproduces the same six.

They are not wrong so much as newly stale — each encodes "the committed artifact
lacks X" as a fact about a file the Agent does not own. The fixtures already prove
the absence *rules*; these tests exist to prove the rules survive contact with the
real artifact. The honest repair is to assert the app reads whatever the artifact
records, rather than to assert the artifact is missing something. That rewrites four
honesty tests as a side effect of a UI task, and it should follow a decision about
whether the Lab change is final — so it was left, deliberately, and is listed in
Outstanding.

`tests/test_app_rendering.py` changed in one structural way. It asserted over the
whole page from a single execution, which worked because tabs rendered everything at
once. With a sidebar radio, one view renders per pass, so `render()` now executes the
app once per view and concatenates. Every existing assertion is unchanged. The
harness also pins `AI4CM_CHAT_HISTORY` into the fixture directory, so the developer's
own saved conversation cannot leak into a rendered page.

---

## 7. Real output — the app, running

Driven headless (Chromium via Playwright) against the real lab at
`../AI4CM/backend/forecast_runs/2026-08-04`, with the live Azure deployment
(`gpt-5.4`). Screenshots were read, not merely captured.

* **Layout.** Composer pinned at `y=786` in a 900px viewport, unchanged before and
  after a streamed answer. All four sidebar views render: chat, dashboard (champion
  E_QUANTILE, 48.45% skill, gate passed), run history (three dated runs), Learn.
* **Streaming, mid-flight.** Captured a frame at 309 characters — the answer cut off
  mid-sentence, still arriving. The completed answer quotes the gate reason verbatim,
  names the withheld families as withheld, flags the stale data, and ends with a
  suggested next question. Its trace reads `grounded LLM answer (streamed)` with no
  rule-based line under it.
* **The fallback, unrehearsed.** The first run of the app answered from rules, and
  the trace said so: `grounded LLM answer (streamed)` followed by `rule-based
  answer`. Cause: a TLS-inspecting proxy on this machine. `curl` succeeds because it
  trusts the corporate root through the keychain; the SDK fails with
  `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`, because
  `_patch_ssl` forces `SSL_CERT_FILE` to certifi's bundle, which does not contain
  that root. Re-launched with a combined bundle and streaming worked immediately.
  That the app degraded to a correct answer and said so in the trace is the behaviour
  working; that `_patch_ssl` can cause it is now in Outstanding and in the README.
* **The run path.** Exercised end to end — acknowledgement, live `st.status` showing
  `Running A_STAT`, streamed narrative of the gated summary, one bubble, one trace —
  against a **stand-in lab**: a copy of the 2026-08-04 run plus a shell script that
  emits the runner's stdout shape. No lab run was launched into `../AI4CM`, which is
  a separate repo with uncommitted changes and a tracked `backend/forecast_runs/`.
  What this verifies is the app's run path; the lab integration itself is unchanged
  code. The persisted transcript afterwards: two messages, one shape.
* **Demo mode.** Verified against a copy of the repo with no sibling lab (the sibling
  fallback in `_candidate_roots` is path-based and would otherwise always find it).
  Demo mode rendered, the demo forecast ran and charted, and `agent.tools` imported
  on demand. Zero page errors in every session.

---

## 8. Verdict

**Part 4(a) — done, option 1 as specified.** Tabs out, sidebar radio in, composer
pinned and staying pinned, run panel collapsed above the transcript, nav restyled for
navy. The pinning constraint is now guarded by a test rather than by memory.

**Both bugs — fixed, and one more found.** One shape, one render per turn, no rerun.
The third: `save_chat`/`load_saved_chat` were never called, so the persistent history
shipped in `a4055ee` did not exist. It does now.

**Streaming — done, and honest about where it was not confined to llm.py.** The
router had to stop writing answers for anything to stream. Same system prompt, same
context, same rules, on the text the user actually reads.

**Outstanding 4, 5, 7 — done.** With one correction to the brief: the "dead" code was
live in `app_legacy.py`, so it moved to its caller rather than being deleted.

**Unresolved, unchanged from last session:** the honesty guarantees on the free-form
path are still prompt-level. Nothing here made that better or worse — streaming
changed delivery, not verification.

---

## 9. Outstanding

1. **No output verifier on the free-form path.** Carried forward unchanged. Every
   honesty rule is stated and tested *as stated*; none is enforced against the text
   the model returns.
2. **`_patch_ssl` breaks TLS-inspecting proxies.** It sets `SSL_CERT_FILE` to
   certifi's bundle whenever the variable is unset, which is exactly the environment
   where a corporate root is needed. The failure is silent — `APIConnectionError` is
   swallowed, the agent answers from rules, and only the trace hints at it. A fix
   would try the system trust store first, or surface the connection error in the
   sidebar instead of only in the trace. Documented in the README meanwhile.
3. **`app_legacy.py` is a deletion candidate.** Nothing imports it, no test covers
   it, and it no longer holds anything back in `agent/`.
4. **A chat-driven lab run has not been exercised against the real lab** in this
   session's code. The stand-in reproduces the runner's stdout contract, not the
   lab's behaviour.
5. **Six red tests, from the Lab writing the fields it was asked to write.** See
   §6.1. They assert absence on a live artifact that now records those fields. The
   Lab-side change is staged, not committed; once it is, these should assert what the
   Agent *does* with a recorded `data_file`, `run_id` and composition, and the
   absence cases should stay where they already are — in the fixtures.
6. **Part 4(b) and Part 5 remain planned only** — dataset upload, and arbitrary
   columns as targets.
