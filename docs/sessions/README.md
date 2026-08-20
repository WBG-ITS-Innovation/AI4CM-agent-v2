# Session records

One record per working session: what was attempted, what was actually found
(including where the brief's premise turned out to be wrong), what was verified
with real output, and what was deliberately left undone.

They are written to be read after the fact by someone who was not there. Where a
session found a defect, the record says how it was found — because "a test can
name a flag without proving it works" is the kind of lesson that only survives
if it is written down next to the bug it produced.

## The records

| Date | Record | What it covers |
|---|---|---|
| 2026-08-11 | [Consuming the Lab's artifact contract](2026-08-11-agent-contract-consumption.md) | `agent/contract.py`: absence read as UNKNOWN, never as zero, a pass, or a failure. The tri-state gate |
| 2026-08-12 | [Dependencies, derived framing, targets](2026-08-12-agent-deps-framing-targets.md) | Pinning the real dependency set; reading the Lab's own model-composition sentence instead of counting; offering every official target |
| 2026-08-12 | [The chat shell, streaming, cleanup](2026-08-12-chat-shell-streaming-cleanup.md) | One message shape everywhere, persisted history, streamed answers. First flagged `app_legacy.py` as a deletion candidate |
| 2026-08-12 | [The Lab writes the four fields](2026-08-12-lab-fields-present-and-legacy.md) | The Lab began emitting fields the agent had only ever seen absent — keeping the absent path tested via a legacy fixture |
| 2026-08-15 | [Session 3 — demo rehearsal](2026-08-15-session3-agent-rehearsal.md) | The demo script walked end to end. Found target-blindness: five of six answers wrong, none fabricated. Produced `published.py`, `registry_read.py`, `official.py` |
| 2026-08-17 | [Session 5 — the action layer](2026-08-17-session5-agent-action-layer.md) | Running an official forecast from chat, behind a confirmation. Verified byte-equivalent to a terminal run; retention verified by checksum |
| 2026-08-18 | [Session 6 — closing the loop](2026-08-18-session6-closing-the-loop.md) | Taking in new actuals, validating them, and scoring published forecasts through the Lab's own scorer |
| 2026-08-19 | *no record written* | A pre-publication audit and the first redaction pass, shipped as commit `0b1c747`. Its reasoning is in the commit message. The 2026-08-20 record explains what the pass missed and why |
| 2026-08-20 | [Scrub verification, smoke rerun, public prep](2026-08-20-agent-verify-smoke-and-public-prep.md) | Re-verified the redaction independently and finished it. Smoke checks against the widened Lab shelf. Pinned the six rehearsal answers. Prepared the public squash-push |

## A note on the numbering

The numbering is not continuous, and the file names reflect what happened rather
than a tidy scheme:

- The first four records are topic-named; session numbering only started with
  the rehearsal.
- **There is no record for 2026-08-19.** The redaction pass of that day shipped as `0b1c747`
  without one. It is listed above as a gap rather than left out, because a reader who finds a
  redaction commit with no record should be able to see that the omission is known.
- **There is no `session4` record.** The work that would carry that number —
  answering target-scoped questions from the published issue rather than from
  whichever backtest run was newest — shipped in commit `4522a59`, which is also
  the commit that added the Session 3 record. The rehearsal record documents the
  defect and the fix; no separate Session 4 record was written.

The gap is left visible rather than closed by renaming files after the fact. A
history that has been tidied is harder to trust than one that admits a gap.

## Related, in the Lab repo

The Lab (`AI4CM`) keeps its own session records under `docs/sessions/`. Two are
directly relevant to this repo:

- **`2026-08-15-session2p5-retention.md`** — made publishing dual-write to the
  private vault. Session 5 here verifies that on every run rather than assuming
  it.
- **`2026-08-18-session6-prep.md`** — applied this repo's Session 5 §8 proposal
  (`--issue-date` on the publish CLI) and fixed the scorecard schema before the
  first scored row existed.
