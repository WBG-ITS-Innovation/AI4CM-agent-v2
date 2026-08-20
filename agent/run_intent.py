# agent/run_intent.py — does this message ask the agent to DO something?
#
# This is a fourth tier, above the three answering tiers, and it is the first
# one in this repo whose output is a WRITE. Every earlier tier could be wrong
# and produce a bad sentence; this one can be wrong and produce a published
# forecast. So the bar is not "did the model probably mean run" — it is "would
# a careful reader agree this is an instruction rather than a question".
#
# THE TRAP, AND WHY THE OBVIOUS RULE FAILS
# ----------------------------------------
# The obvious rule is "the message contains a run verb and the word forecast".
# Under that rule every one of these launches a run:
#
#     how do I run a forecast?            <- a request for instructions
#     what happens when you run one?      <- a request for an explanation
#     why did the forecast run fail?      <- a request for a diagnosis
#     can the lab run a 30-day forecast?  <- a capability question
#
# The first is the trap the brief names. All four are Tier-2 questions, and the
# thing that separates them from an instruction is not vocabulary — they share
# it — but MOOD. `how do I X` is interrogative; `do X` is imperative.
#
# So the rule here is grammatical, not lexical:
#
#   1. An informational opener (how/what/why/when/where/who/which) makes the
#      message a question, full stop. No run verb can override it.
#   2. A modal directed at the agent (`can you`, `could you`, `please`) is a
#      polite imperative and DOES run — "can you run today's forecast?" is an
#      instruction in every workplace on earth. Note the direction matters:
#      `can you run` is a request, `can the lab run` is a capability question,
#      and rule 1 has already caught the latter if it opens with `can the`.
#   3. Otherwise a run verb plus a forecast noun is an instruction.
#
# Rule 1 is deliberately absolute and deliberately over-broad. Its cost is that
# a user who types "what I want is for you to run the forecast" gets an answer
# instead of a run, and then rephrases. Rule 3 being over-broad costs a
# published issue nobody asked for. Those are not the same mistake, so they do
# not get symmetric treatment — and the confirmation step downstream is a
# second net under rule 3, never an excuse to loosen rule 1.
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: Words that open a request for information. `which` is here because "which
#: targets would you run?" is a question about a hypothetical run.
_INTERROGATIVE = re.compile(
    r"^\s*(how|what|why|when|where|who|which|whose|whom)\b", re.IGNORECASE)

#: A question mark alone is NOT enough to make something a question: "run
#: today's forecast?" is a hesitant instruction, and treating it as a query
#: would strand a user who types it. Only the opener decides.

#: Directed at the agent, so a polite imperative rather than a capability ask.
_POLITE_IMPERATIVE = re.compile(
    r"\b(can|could|would|will)\s+you\b|\bplease\b|\bi(?:'d| would)\s+like\s+you\s+to\b",
    re.IGNORECASE)

#: The verbs that mean "produce a new one", not "tell me about the old one".
_RUN_VERB = re.compile(
    r"\b(run|re-?run|refresh|regenerate|generate|produce|issue|publish|"
    r"kick\s*off|launch|execute|update)\b", re.IGNORECASE)

#: The thing being run. Without one of these, a run verb is about something
#: else entirely ("run me through the gates", "refresh the page").
_FORECAST_NOUN = re.compile(
    r"\b(forecast|forecasts|forecasting|issue|projection|projections|"
    r"numbers|prediction|predictions)\b", re.IGNORECASE)

#: Phrases where the verb is doing something other than commanding a run.
#: "run through", "run into", "walk me through the run" — the verb is attached
#: to a preposition that changes its meaning.
_VERB_NOT_A_COMMAND = re.compile(
    r"\brun\s+(me\s+)?(through|into|by|past|over)\b", re.IGNORECASE)

#: Asking about a past run, not asking for a new one.
_ABOUT_A_PAST_RUN = re.compile(
    r"\b(last|previous|latest|recent|yesterday'?s?|today'?s?)\s+run\b|"
    r"\brun\s+(failed|succeeded|took|was|did)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RunRequest:
    """A message read as an instruction to produce a new official forecast."""

    #: Targets the user named, or () meaning "every champion target". Empty is
    #: the normal case and is NOT a failure to parse — "run today's forecast"
    #: legitimately means all of them.
    targets: tuple[str, ...] = ()

    #: Why this was read as an instruction. Shown in the agent trace, so a user
    #: who disagrees with the routing can see what triggered it.
    why: str = ""


def classify(text: str, known_targets: Optional[list[str]] = None
             ) -> Optional[RunRequest]:
    """A `RunRequest` when the message instructs a run, else None.

    None means "this is not an action" and the caller falls through to the
    answering tiers unchanged. That fall-through is what keeps every Session
    3/4 rehearsal answer byte-identical: a question that reached the official
    answer before this module existed still reaches it now, because `classify`
    returns None for it and nothing else in the path changed.
    """
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return None

    # Rule 1, absolute: an informational opener makes it a question.
    if _INTERROGATIVE.search(stripped):
        return None

    if _VERB_NOT_A_COMMAND.search(stripped) or _ABOUT_A_PAST_RUN.search(stripped):
        return None

    has_verb = bool(_RUN_VERB.search(stripped))
    has_noun = bool(_FORECAST_NOUN.search(stripped))
    if not (has_verb and has_noun):
        return None

    polite = bool(_POLITE_IMPERATIVE.search(stripped))
    why = ("polite imperative with a run verb" if polite else
           "imperative with a run verb and a forecast noun")

    return RunRequest(targets=_named_targets(stripped, known_targets), why=why)


# ─────────────────────────── consent ───────────────────────────
#
# The confirmation step is only worth having if "unclear" is a real outcome.
# A yes/no parser that guesses when it cannot tell converts every ambiguous
# reply into whichever answer it defaults to, and one of those defaults
# publishes a forecast. So there are three results, and only an explicit
# affirmative runs anything.

_YES = re.compile(
    r"^\s*(y|ye|yes|yep|yeah|yup|ok|okay|sure|confirm(ed)?|affirmative|"
    r"go|go\s+ahead|do\s+it|proceed|please\s+do|run\s+it|run\s+anyway|"
    r"run\s+it\s+anyway|yes\s+please|go\s+for\s+it)\s*[.!]?\s*$",
    re.IGNORECASE)

_NO = re.compile(
    r"^\s*(n|no|nope|nah|cancel|stop|abort|don'?t|do\s+not|never\s+mind|"
    r"nevermind|not\s+now|hold\s+off|wait|no\s+thanks?|forget\s+it)\s*[.!]?\s*$",
    re.IGNORECASE)

CONSENT_YES = "yes"
CONSENT_NO = "no"
CONSENT_UNCLEAR = "unclear"


def read_consent(text: str) -> str:
    """`yes`, `no`, or `unclear` — and `unclear` never runs anything.

    Anchored whole-message matching, deliberately. A substring search would
    read "no, wait — what does the gate check?" as a yes on the strength of
    the "wait", and would read "is that ok?" as consent. If the reply is not
    unambiguously one word of agreement, the caller treats it as a new
    message and the pending run is dropped rather than held open: a
    confirmation that survives an unrelated question is a confirmation the
    user has stopped thinking about.
    """
    raw = (text or "").strip()
    if not raw:
        return CONSENT_UNCLEAR
    if _YES.match(raw):
        return CONSENT_YES
    if _NO.match(raw):
        return CONSENT_NO
    return CONSENT_UNCLEAR


def _named_targets(text: str, known: Optional[list[str]]) -> tuple[str, ...]:
    """Targets the message names, longest alias first, or () for all of them.

    Shares `official.resolve_target`'s longest-first discipline rather than
    reimplementing it: "state budget balance" must not be shortened to
    "balance", and a run scoped to the wrong series is the same class of error
    as an answer about the wrong series.
    """
    from . import official

    if not known:
        return ()
    found: list[str] = []
    lowered = text.casefold()
    for name in sorted(known, key=len, reverse=True):
        if name.casefold() in lowered and name not in found:
            found.append(name)
    if found:
        return tuple(found)
    # Fall back to the alias table, which knows "spending" means Expenditure.
    one = official.resolve_target(text, known)
    return (one,) if one else ()


# ─────────────────────── "here is the new data" ───────────────────────
#
# A second instruction shape, and a gentler one: nothing about announcing new
# data is destructive on its own, so the bar is lower than for `classify`. What
# it must NOT do is fire on questions about data — "is the data fresh?", "what
# data does the lab use?" — which are Tier-2 questions the agent already
# answers, and which share every noun with the instruction.
#
# Rule 1 from `classify` carries over unchanged and does that work.

_DATA_NOUN = re.compile(
    r"\b(data|actuals?|figures?|outturns?|observations?|csv|dataset|"
    r"spreadsheet|file)\b", re.IGNORECASE)

#: Arrival, delivery or replacement — not enquiry.
_ARRIVAL = re.compile(
    r"\b(here'?s|here\s+is|here\s+are|i\s+have|i'?ve\s+got|new|newer|fresh|"
    r"updated|latest|arrived|came\s+in|received|got|upload(ed|ing)?|attach(ed)?|"
    r"load|import|ingest|take|use)\b", re.IGNORECASE)

#: A filesystem path to a CSV, anywhere in the message.
_PATH = re.compile(r"(?:^|\s)(/[^\s]+\.csv|[~.][^\s]*\.csv|[^\s]+\.csv)",
                   re.IGNORECASE)

#: Yes/no question openers. These are NOT in `_INTERROGATIVE` because `classify`
#: must keep "can you run today's forecast?" as an instruction — there, `can`
#: opens a polite imperative. The data intent has no such need and a much
#: cheaper failure mode, so it screens them out: "is the data fresh?" shares
#: every noun with "here is the new data" and differs only in mood.
_YESNO_OPENER = re.compile(
    r"^\s*(is|are|was|were|does|do|did|has|have|had|am|shall|should|"
    r"could|would|will|can|may|might)\b", re.IGNORECASE)


@dataclass(frozen=True)
class DataRequest:
    """A message read as "new actuals are available"."""

    path: str = ""      # a path the message named, or "" for "use the upload"
    why: str = ""


def classify_data(text: str) -> Optional[DataRequest]:
    """A `DataRequest` when the message announces new data, else None.

    Returns None for every question about data, so "is the data fresh?" keeps
    reaching the answer it reached before this tier existed.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if _INTERROGATIVE.search(raw):
        return None
    # A yes/no opener is a question unless it is aimed at the agent as a
    # request: "is the data fresh?" asks, "can you take this data?" instructs.
    if _YESNO_OPENER.search(raw) and not _POLITE_IMPERATIVE.search(raw):
        return None

    match = _PATH.search(raw)
    path = match.group(1).strip() if match else ""

    # A bare path on its own line is an instruction: nobody types a CSV path
    # into a chat box to make conversation.
    if path and len(raw.split()) <= 3:
        return DataRequest(path=path, why="a bare path to a CSV file")

    if not (_DATA_NOUN.search(raw) and _ARRIVAL.search(raw)):
        return None
    return DataRequest(path=path,
                       why="an announcement that new data is available"
                           + (" with a path" if path else ""))
