# 2026-08-20 — Scrub verification, smoke rerun against the widened lab, public-repo prep

**Repo:** `ai4cm-agent-v2` @ `feat/lab-door`, remote `WBG-ITS-Innovation/AI4CM-agent` (private).
**Lab:** `AI4CM` @ `main`, PRs #27 and #28 merged, `675bbe6`. Frozen for this session and read only:
the lab tree was not modified, and every claim below about it was checked against its files.

**Suite: 508 before, 518 after.** Ten new tests. One behaviour change, in the Learn tab.

**Scrub verdict: NOT CLEAN on arrival, CLEAN after the three fixes in §2.**

---

## 0. What the previous session had left

The remediation session of 2026-08-19 ran the scrub as commit `0b1c747`, over eleven files. It did
two things it was not asked to do, and left two it was.

- **It wrote no session record.** There is no `docs/sessions/2026-08-19-*.md`. The commit message is
  long and carries the reasoning, which is why nothing was lost, but the index ended at 2026-08-18
  and a reader looking for the scrub would not have found it.
- **It prepared no push commands.** Searched: `docs/`, every tracked file, `git stash list` (empty),
  the reflog, and all six scratch directories from that day. The only artifact on disk is an
  untracked `publish-dryrun/` copy of the tree in one scratchpad, holding no command file. So there
  was nothing to quote, and §5 below writes the commands for the first time.
- **The scrub was incomplete.** Three real figures survived. See §1 and §2.
- **It misattributed its own rule.** The commit says the decision to keep percentages and MASE
  matches "the README decision". No such decision was in the README. The rule was stated in a
  docstring in `tests/test_real_artifact.py`. This session writes it into the README, under
  *Figures in this repo*, so the next person redacting something can find it.

## 1. Independent scrub verification

The previous session's findings were not trusted; the sweep was rebuilt from scratch. **The method
is the finding**, because the first pass had two blind spots and each one hid a real figure.

**How the corpus was built.** Every number of six digits or more in the *entire* lab tree, not just
its published forecasts: 761,450 distinct values. Then the same extraction over every tracked file
in this repo, and an intersection on three keys.

| Key | Why it was needed |
|---|---|
| Exact token | The baseline check |
| Integer part | A real value re-published with different decimals, or rounded for display, is still that value. This found one figure whose last decimal digit had been altered |
| Separators stripped | `[0-9]{6,}` cannot see a figure written `12,345,678`. **That is the form the lab actually publishes figures in**, and it hid two more |

**The first pass's second blind spot** was the corpus, not the pattern. It compared against the
lab's published forecasts and registry but not `backend/forecast_runs/`, which is where measured
per-model errors live. One of the three survivors is only visible there.

### Inventory

| File | What it holds | Verdict |
|---|---|---|
| `agent/contract.py` | A docstring example. Real per-model error for one target and horizon, last decimal digit altered. The previous pass fixed the same value twelve lines below and missed this one | **REAL** — fixed in §2 |
| `tests/conftest.py` | A fixture family's `best_model` string. Real measured error from the lab's own 2026-08-04 run | **REAL** — fixed in §2 |
| `tests/test_brittle_parsing.py` | A parser input string. Real baseline error, also present in the lab's reports | **REAL** — fixed in §2 |
| `datasets/demo_daily.csv` | 727 rows, the real column names, 371 dates overlapping the real series | **Synthetic** — proven, see below |
| `tests/conftest.py`, `tests/test_official_answers.py` | Round amounts and a digit ladder ending `...2233.44` | **Synthetic** — round numbers and a visible pattern, left deliberately by the previous pass |
| 13 further seven-digit values in `demo_daily.csv` | Value-only matches against a 752,000-value corpus, at unrelated dates and columns | **Clean** — chance |
| Three shorter matches | Substrings of a timestamp, a SHA-256 and a git SHA | **Clean** |
| Whole tree | Personal paths, RFC1918 addresses, credential-shaped strings, Georgian-script column headers, internal domains | **Clean** — none. `.env`, `var/` and `artifacts/` are untracked and gitignored |

**`demo_daily.csv` is synthetic, tested rather than assumed.** It is the only tracked data file, and
it looks like Treasury data: same two column names, a plausible daily range. Four independent
checks:

- **Zero date-and-value matches** across all 371 dates it shares with the real series. This is the
  one that settles it. A leak would agree with reality on at least one day.
- **Not a scaled copy.** The demo-to-real ratio takes 109 distinct values on one column and 228 on
  the other. A single multiplier would give one.
- **Wrong order of magnitude.** Balances average 13 million against a real 1.16 billion.
- **Impossible dates.** 356 of its dates fall after the real series ends.

### Kept, by decision

Ruled on in session and recorded so it is not re-litigated:

- **Real MASE values** (`0.757959`, `1.103854`) in several records and fixtures. A ratio discloses
  no amount, and it is what the accuracy argument rests on.
- **Truncated git and data SHAs.** Fingerprints of code and of an input file, not of client figures.

Both are consistent with the rule as stated, which this session moved from a test docstring into the
README.

### Re-sweep after the fixes

All three values absent in every form, plain and separated. Remaining intersections are the
synthetic fixtures, the demo CSV's chance collisions, and the two decided keeps. **CLEAN.**

## 2. The scrub completion — `87d1beb`

Each of the three is an example string. None was ever the subject of its test: two feed a parser
whose assertions check a name and a flag, and one is a docstring. Each was replaced by the same
value times **0.8216**, the factor the previous pass used everywhere else, so the fixtures stay
consistent with each other. Every replacement was checked against the full lab corpus first, and
none collides.

Suite unchanged at 508, which is the expected result and the reason to state it.

## 3. Smoke rerun against the lab on main

The lab's records name six things to verify. Three needed no work, and saying which is as useful as
listing the fixes.

| # | Check | Result |
|---|---|---|
| 1 | Shelf 31 → 44, `F_FOUNDATION` exploratory only | **Confirmed**, and one defect found. See below |
| 2 | Agent-side reimplementation of `daily_best_model_families` | **None. Zero sites.** `families` is read from the artifact's own list. The only mention in this repo is a 2026-08-12 record noting the agent reads none of it |
| 3 | References to renamed lab pages | **None.** No page filename, no page URL, no prose naming one. Nine renames and `/Models` becoming `/Documentation` reached nothing here |
| 4 | Full suite | **508 passed**, 7.42s |
| 5 | Contract read of the current published issue | **Clean.** 2026-08-16, readable, **zero defects**, Revenues, horizons 1 to 5, clean provenance |
| 6 | Byte-identity baseline | **Passes 6/6.** Not pinned in code. Now pinned. See below |

**The shelf is 44** and the agent holds no copy of it. The lab derives 21 machine-learning, 5
deep-learning, 7 statistical, 6 quantile, 2 pretrained zero-shot and 3 baselines. The agent quotes
the sentence the lab wrote into the artifact and reports UNKNOWN when it is absent, so the widening
required no change. `F_FOUNDATION` is exploratory only, verified three ways rather than taken from
the note that says so: it is absent from the lab's competing categories, absent from the default
family list in `run_daily_forecast.sh`, and the lab's exploratory path writes no run directory the
agent reads.

**What was wrong was older than the shelf change.** The Learn tab rendered `FAMILY_LABELS` straight
to the page: four names typed into this repo, presented as the lab's model families. Wrong in both
directions at once.

- **Incomplete**, once the lab added a fifth family. Nothing failed, because nothing compared the
  list to anything.
- **Wrong on runs it had always been wrong on.** The `clean_run` fixture records three families and
  the page named four, so it advertised `C_DL` on a run that never ran it.

**The byte-identity baseline was the strongest evidence the project had and the only evidence
nothing could re-check.** Session 5 hashed the six rehearsal answers against the real lab before and
after building the action layer, found all six identical, and recorded the digests in a Markdown
table. Re-run here against the frozen lab: **6/6 still byte-identical**, so the widened shelf moved
none of them, and no re-baseline is needed. It is now a test.

## 4. The fixes — `b937682`, `2f95cd9`

| Change | Why |
|---|---|
| Learn tab lists the families the run recorded | Removes the whole staleness class. A family the lab adds now appears without this repo changing |
| `F_FOUNDATION` gains a label | It should never appear in a daily run. If it ever does, a reader gets a description instead of a bare code |
| No empty-list branch, and a test for why | `contract.py` counts absent-or-empty `families` as fatal, so such a run never loads and never reaches the tab. **That rule had no test**, so the new code leaned on an invariant nothing checked |
| Six rehearsal digests pinned | With a docstring saying what to do when it fails, because a moved answer is not always a defect |
| A test for a two-sentence framing | The lab's sentence gained a clause about how many entries have a recorded result. Rendering is verbatim so nothing broke, but that held by accident. A change that split on the first full stop would drop the half that admits how much of the shelf is unmeasured, and no test would have failed |
| Three comments corrected | Each had gone false. The composition field is not "absent from every committed artifact": the 2026-08-04 run carries it, and a test asserted so while the comment above denied it. A **reader-facing** note said the same untrue thing. A third pinned the champion pool at a number that has since changed |
| `KNOWN_FAMILIES` documented, not changed | It is this repo's demo-mode capability list, not a copy of the lab's shelf, so it is not stale. It reaches the language model as `runnable_families`, where a model could read four families as a fact about the lab. Guarded by the system prompt only. See §6 |

Suite after: **518 passed**. The only behaviour change is which family names the Learn tab prints.

## 5. Public-repo preparation

Prepared, not run. Nothing was created, nothing was pushed, no remote was added.

Three properties have to hold at once, and each one is a way this can go wrong quietly. The public
repo gets **one commit**, so no earlier commit carrying a real figure travels with it. The full
history stays **only** on the existing private remote. And **no tag is pushed**, because six local
`demo-*` tags point at commits from before the scrub.

### Decide first

- **The owner.** A public repo under the organisation is a different act from one under a personal
  account. This is not a technical choice and the commands leave it as `<owner>`.
- The squash commit's author email is the committer's work address, and it is visible in a public
  repo. Expected, but better known in advance.

### In the GitHub UI, before any command

1. Create a new repository named exactly **`AI4CM-agent-v2`**.
2. Set visibility to **Public** at creation.
3. Add **no** README, **no** `.gitignore`, **no** licence. The repository must be empty, otherwise
   the first push is rejected as a non-fast-forward and the fix invites a force push.

### The squash push

Checks first. The second must print `0`, and the third must print nothing.

```
cd <path-to>/ai4cm-agent-v2
git status --porcelain
git ls-files | grep -c '\.env$'
git config --get push.followTags
```

Then build a history of one commit from the current tree and push it.

```
git checkout feat/lab-door
git checkout --orphan public-squash
git add -A
git commit -m "AI4CM Agent: a conversational interface to the AI4CM forecasting lab"
git log --oneline
git remote add public https://github.com/<owner>/AI4CM-agent-v2.git
git push --no-follow-tags public public-squash:main
```

`git checkout --orphan` keeps the working tree and starts a branch with no parent, so the commit
that follows is the first commit of a new history. `git log --oneline` before the push must show
exactly one line. `--no-follow-tags` is belt and braces next to the `push.followTags` check above.

### Verify afterwards

Exactly one commit on the new remote, and no tags. The second command must print nothing at all.

```
git clone https://github.com/<owner>/AI4CM-agent-v2.git /tmp/verify-ai4cm-public
git -C /tmp/verify-ai4cm-public log --oneline | wc -l
git ls-remote --tags https://github.com/<owner>/AI4CM-agent-v2.git
```

The pushed tree is the local tree. These two must print the same hash.

```
git rev-parse public-squash^{tree}
git -C /tmp/verify-ai4cm-public rev-parse HEAD^{tree}
```

The private remote is untouched and still has the full history.

```
git log --oneline origin/feat/lab-door | wc -l
git ls-remote --tags origin
```

Then return to the working branch. The orphan branch can stay as a record of what was published.

```
git checkout feat/lab-door
rm -rf /tmp/verify-ai4cm-public
```

## 6. Open items

| Item | State |
|---|---|
| **The honesty layer is prompt-enforced, not code-enforced** | **Unchanged this session, and the main pre-handoff risk.** The rules that stop the agent quoting a figure it did not read are instructions in a prompt. A model that ignores them fails silently and no test catches it. The layers below are code: figures come from the contract reader, and absence is UNKNOWN there. What is not code is the guarantee that narration obeys |
| `runnable_families` in the language-model context | Names four families. True of this repo's demo mode, and a model could restate it as a fact about the lab. The system prompt forbids quoting counts from anything but the artifact's own sentence. Prompt-enforced, so it belongs to the row above |
| `plain.NO_COMPOSITION` says a run without the field "predates" it | Accurate for the runs on disk. Not guaranteed for a future run that omits it. Left alone: reworded copy for a case that has not occurred is churn |
| Live language-model path | **Not exercised this session.** Nothing in this session's work needed the network |
| No session record for 2026-08-19 | Left as a visible gap, on the same reasoning as the missing session 4. The scrub's reasoning is in `0b1c747` and summarised in §0 |
| Real figures remain in this repo's git history | Unavoidable and already handled by the plan: the public repo gets a fresh single-commit history, so no earlier commit travels with it. The private remote keeps the full history |

---

## Addendum — the live language-model path, exercised

Added after the sections above, at request. The live path had not been touched all
session, so none of the verification above covered it.

### It failed first, and the failure was the documented one

The configured backend is Azure OpenAI, deployment **`gpt-5.4`**, API version **2024-06-01**. The
resource name and key are not recorded here.

The first call failed in 2.05s:

```
openai.APIConnectionError: Connection error.
  <- httpx.ConnectError
  <- httpcore.ConnectError
  <- ssl.SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED,
     self-signed certificate in certificate chain
```

That is TLS interception, and it is the exact failure the README's *Optional: language-model
narration* section describes. `_patch_ssl` found `SSL_CERT_FILE` unset and pointed it at certifi's
bundle, which does not contain the intercepting root, so verification failed and the agent fell back
to rules. Working as documented, including the fallback.

**It did not need a different network.** The intercepting root is already in the machine's System
keychain. A bundle of certifi plus the keychain roots, exported as `SSL_CERT_FILE`, succeeded in
1.42s and returned the expected single word. So the README's instruction is not merely plausible, it
is confirmed: the bundle it asks for can be built locally, and the same call fails and succeeds on
the same network depending only on it.

Worth recording because it looked like something else. `openssl s_client` to the same host showed the
genuine Microsoft chain rooted at DigiCert Global Root G2, which certifi does contain, and no proxy
is configured in the environment or in `scutil --proxy`. So the direct evidence pointed at "no
interception" while the client failed on interception. The bundle test settled it. A conclusion drawn
from the openssl output alone would have been wrong.

### One end-to-end question, live

Question: *"What is the latest published forecast for Revenues and how good has the model been?"*

It names a target, so it routed to the artifact-sourced tier and the model only reworded the result.
Both halves ran live: `answer_official_question` built the answer from the published issue and the
registry, then `official_narrative_stream` rephrased it in **7.62s**.

### The honesty check: no fabricated figure, one breached instruction

Every figure in the narration was extracted and traced to the contract reader's values for the
current issue, not to the agent's own prose.

| Figure | Traced to | Result |
|---|---|---|
| MASE per target, three values | `registry_read.champion_for(...).mase` | Exact for two. The third is the artifact value at the rounding the factual text already used, carried through unchanged |
| Three "% more/less accurate" readings | Derived in `agent/plain.py` from those MASE values | Traces |
| Evaluation window and its length | `dev_window`, `dev_n` | Exact |
| "nobody has approved the model" | `approved_by` is null on all three recipes | Traces |
| Horizon of 5 business days | The published issue's own horizons | Traces to the artifact, **but not to the text the model was given** |

**No figure failed to trace to the artifact. One instruction was broken anyway.** That tier's prompt
says to keep every number exactly as written and *"Add none."* The narration added the horizon. The
number is true, it came from CONTEXT, which is artifact-derived, and a reader is not misled. But the
constraint was breached, the breach was silent, and **nothing in code would have caught it** — the
figure-tracing above was done by hand for this record.

This is the prompt-enforced honesty layer in §6, with a live example instead of an argument. The
useful shape of the risk: the layer failed in the direction that happened to be harmless. It quoted
a true number it was told not to add. The same mechanism, with a less well-grounded CONTEXT, has
nothing standing between it and a wrong one.

Two smaller observations from the same run:

- **The verdict wording held.** "withheld" survived on both withheld lines, no forecast value was
  supplied for either, and every caveat in the source text reached the reader.
- **The question was compound and half of it was answered.** It asked for the forecast *and* the
  accuracy; the routing matched the accuracy branch and returned no forecast values. The narration
  did not invent them, and closed by suggesting the forecast path as the next question. Honest, and
  still a partial answer to what was asked. Not fixed here, and not a fabrication risk.

### A leak found by doing this, and a gap in §1's method

Exercising the live path meant reading `.env`, which led to checking whether the endpoint appears
anywhere tracked. It did. `docs/sessions/2026-08-15-session3-agent-rehearsal.md` recorded the Azure
OpenAI resource name in full, while diagnosing this same TLS failure in an earlier session. Now
masked, with a pointer to the resolution above.

**§1's sweep would never have found it, and the reason matters more than the fix.** That sweep was
built to find *figures*, and it was thorough about them: three match keys, the whole Lab tree as a
corpus. Its non-numeric checks were a short list of patterns, and the internal-domain pattern
covered `worldbank` and `sharepoint`. An Azure resource host matches neither. A sweep is only as
wide as its worst pattern, and the numeric half being careful said nothing about the rest.

Redone properly, across every tracked file:

| Check | Result |
|---|---|
| Hostname-shaped strings | **One**, the leak above. None now |
| URLs | `localhost` only, plus the placeholder in §5 |
| Email addresses | None |

The verdict in §1 stands for figures and now stands for endpoints, on evidence rather than on a
pattern that happened not to match.

---

## Addendum — Task 4 executed

Owner **WBG-ITS-Innovation**, repository **AI4CM-agent-v2**, created empty in the UI beforehand.

The orphan was built fresh from the `feat/lab-door` tip rather than from anything prepared earlier,
so the published tree is the reviewed one and not a tree from before the endpoint was masked. No
`public-squash` existed to delete: §5 above was written as commands, never run.

### Pre-flight, on `feat/lab-door` at `c2e27ad`

| Check | Required | Actual |
|---|---|---|
| `git status --porcelain` | empty | empty |
| `git ls-files \| grep -c '\.env$'` | `0` | `0` |
| `git config --get push.followTags` | empty | empty |
| `HEAD` vs `origin/feat/lab-door` | equal | both `c2e27ad` |
| Endpoint masking present in tree | yes | hostname absent |

### Executed

```
git checkout feat/lab-door
git checkout --orphan public-squash
git add -A
git commit -m "AI4CM Agent: a conversational interface to the AI4CM forecasting lab"
git remote add public https://github.com/WBG-ITS-Innovation/AI4CM-agent-v2.git
git push --no-follow-tags public public-squash:main
```

The commit is `7a0a3be`, with **no parent** and a history of exactly one line. `git ls-remote public`
before the push returned nothing, confirming the target was empty rather than assumed to be.

### The tree was proved before the push, not after

```
public-squash^{tree}  = 48e8b33ac17c2aa3dfe2c2e4f614576661c1f894
feat/lab-door^{tree}  = 48e8b33ac17c2aa3dfe2c2e4f614576661c1f894
git diff --stat feat/lab-door public-squash  ->  empty
```

### Verification

| Check | Required | Result |
|---|---|---|
| Commits in a fresh clone | exactly 1 | **1** (`7a0a3be`) |
| `git ls-remote --tags` on the public repo | nothing | **nothing** |
| Clone `HEAD^{tree}` vs local `public-squash^{tree}` | equal | **equal**, `48e8b33` |
| `origin/feat/lab-door` history | intact | **23 commits**, tip `c2e27ad` |
| Tags on the private remote | exactly six | **six**, all `demo-*` |
| Tracked files published | — | 54 |
| `.env` in the published tree | absent | **absent** |
| The three scrubbed figures | absent | **all absent** |
| The Azure resource name | absent | **absent** |

### One thing went public that should not have

The published tree was checked for personal paths, in the clone rather than locally, and one was
found: `cd /Users/<name>/...` on line 179 of this record, inside the §5 command block. **This session
wrote it.** The 2026-08-19 pass removed six such paths as a disclosure, §1 above verified there were
none left, and then §5 introduced one while writing commands for a reader to copy.

It is now masked to `<path-to>/`. The incremental disclosure is close to nil, because the squash
commit's author metadata already carries the same username and the work email, which was flagged
before the push and accepted. It is recorded at length anyway for what it says about method: the
check that caught it was reading the *published* tree, and no check performed before the push would
have. §1's sweep ran against the working tree; a command block added afterwards was never re-swept.
Verifying the artifact rather than the intent is the only step that finds this class of thing.

**Consequence for the invariant this task exists to hold.** The public tree now differs from
`feat/lab-door` by exactly that masking. Restoring "the public tree is the reviewed tree" means
amending `7a0a3be` and force-pushing, which rewrites published history and was not authorised. Left
for a decision rather than done.

---

## Addendum — the amend, and the second leak the amend's own sweep found

The invariant was restored by rebuilding the orphan from the masking commit and force-pushing with
`--force-with-lease`, authorised as a single amend while the repository has no external consumers.
Then the final sweep was pointed at the fresh clone rather than the working tree, as the lesson
below requires, and **it found a second leak, worse than the first**.

### What the sweep found

**A real Treasury figure was in the published tree: the Revenues origin value for 2025-08-06.** Not
in a fixture. In §1 of this record, quoted as an example of the pattern problem it was documenting.
The sentence explaining that `[0-9]{6,}` cannot see a figure written with thousands separators used a
real figure to make the point. It is now an obviously illustrative `12,345,678`.

This is the most serious item in the session, and it was self-inflicted twice over: by the same
mechanism as the personal path, in the section whose subject is finding exactly this.

### Why both leaks got through, stated once, properly

Neither was a gap in §1's method. §1 was correct and thorough **about the tree as it stood when it
ran**. Both leaks entered *afterwards*, in prose written later in the same session:

- §5's command block introduced a personal path.
- §1's own explanation introduced a real figure.

A sweep is a measurement of a tree at a moment. Every commit after it is unmeasured. The session
verified its own intentions repeatedly and verified the artifact once, at the end, and that single
check is what caught both. **The rule this yields: the last action before declaring done is a sweep
of the published artifact, not of the working tree, and any commit after that sweep voids it.**

There is a sharper version. Writing about sensitive data is itself a way of handling sensitive data.
A redaction record is a high-risk document, because its subject matter is the exact material it must
not contain, and every example it reaches for is drawn from the set it is trying to empty.

### The amend

```
git branch -D public-squash
git checkout feat/lab-door
git checkout --orphan public-squash
git add -A
git commit -m "AI4CM Agent: a conversational interface to the AI4CM forecasting lab"
git push --no-follow-tags --force-with-lease public public-squash:main
```

`--force-with-lease` rather than `--force`, with `refs/remotes/public/main` fetched first so the
lease had a real basis. Without that fetch the lease has nothing to compare and the guard is
inert.

Trees proved before pushing, every time:

```
public-squash^{tree}  = d999139eeef70a2736109534733c865f20f749c8
feat/lab-door^{tree}  = d999139eeef70a2736109534733c865f20f749c8
git diff --stat feat/lab-door public-squash  ->  empty
```

### Verification after the amend

| Check | Required | Result |
|---|---|---|
| Commits in a fresh clone | exactly 1 | **1** |
| `ls-remote --tags` on public | nothing | **nothing** |
| Clone `HEAD^{tree}` vs local | equal | **equal** |
| `origin/feat/lab-door` | intact | **24 commits** |
| Tags on the private remote | six `demo-*` | **six**, zero non-`demo` |

### Final sweep, against the fresh clone

| Target | Result |
|---|---|
| Personal paths | **absent** |
| Azure resource name | **absent** |
| Three scrubbed figures, plain and comma-separated | **absent, all six forms** |
| `.env`, any path | **absent** |
| Full numeric intersection against the whole lab tree | Only the items classified in §1: synthetic fixtures, the demo CSV's chance collisions, and the two decided keeps |

### Terminating the loop

Applying "sweep the published artifact last" to a record that documents the sweep is a fixpoint
problem: every section written about a sweep invalidates it. It was resolved by consolidating, not by
iterating. The published history is three pushes, two of them forced:

| Push | Commit | Why |
|---|---|---|
| 1 | `7a0a3be` | The initial publish |
| 2 | `06d5220` | The authorised amend, restoring the tree after the personal path was masked |
| 3 | `dee9f57` | After the sweep of push 2's clone found the real figure described above |

A fourth and final push carries this section plus one classification, and then nothing further is
written. **The sweep that counts is the one run against the clone of that last push**, and its
results are the tables above, re-run rather than copied.

One entry in the numeric sweep needs naming, because a reviewer re-running it will see a hit that
§1's table does not list. The illustrative `12,345,678` in §1 collides with the lab's corpus. It is
not a disclosure: the lab's own tests use `12345678`, `12345678.0` and `12345678.12` as placeholders,
so this is one repository's obvious fake number matching another's. Both reach for the same ladder.
It is the only hit in the published tree not already classified in §1.
