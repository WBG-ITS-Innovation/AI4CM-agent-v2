# tests/test_action_routing.py — the ACTION tier fires on instructions only.
#
# This tier is the first in the repo whose misfire is a WRITE. An answering
# tier that routes wrongly produces a wrong sentence; this one produces a
# published forecast issue nobody asked for. So the tests are asymmetric on
# purpose: the false-positive cases (a question that launches a run) get the
# heavier coverage, because that is the expensive direction.
#
# The six rehearsal questions from Session 3 are pinned here as a group. They
# are the demo script, and the single most likely way this session breaks the
# previous two is by intercepting one of them.
from __future__ import annotations

import pytest

from agent.run_intent import (CONSENT_NO, CONSENT_UNCLEAR, CONSENT_YES,
                              classify, read_consent)

TARGETS = ["Revenues", "Expenditure", "State budget balance"]


# ─────────────────────── instructions: these must run ───────────────────────

@pytest.mark.parametrize("message", [
    "run today's forecast",
    "run the forecast",
    "generate the latest forecast",
    "refresh forecasts",
    "refresh the forecast please",
    "rerun the forecast",
    "re-run the forecast",
    "regenerate the official forecast",
    "produce a new forecast",
    "publish today's forecast",
    "update the forecast",
    "kick off a forecast run",
    "please run a new forecast",
    "can you run today's forecast?",
    "could you refresh the forecasts for me",
    "I'd like you to run the forecast",
    "run today's forecast?",          # hesitant instruction, still an instruction
])
def test_an_instruction_reaches_the_action_tier(message):
    assert classify(message, TARGETS) is not None, (
        f"{message!r} is an instruction to produce a forecast and must route "
        f"to the ACTION tier")


# ─────────────────────── questions: these must NOT run ───────────────────────

@pytest.mark.parametrize("message", [
    # The trap the brief names.
    "how do I run a forecast?",
    "how do you run a forecast",
    "what happens when you run a forecast?",
    "what does it mean to publish a forecast?",
    "why did the forecast run fail?",
    "when was the last forecast run?",
    "who can run a forecast?",
    "which targets would you run?",
    "how often do you refresh the forecasts?",
    # A run verb attached to a preposition means something else entirely.
    "run me through the gates",
    "walk me through how forecasts are produced",
    # About a past run, not a request for a new one.
    "did the last run publish anything",
    "the previous run looked wrong",
])
def test_a_question_never_reaches_the_action_tier(message):
    assert classify(message, TARGETS) is None, (
        f"{message!r} is a question and must be answered, not executed")


# ─────────────────────── the Session 3 rehearsal script ───────────────────────

#: Verbatim from docs/sessions/2026-08-15-session3-agent-rehearsal.md §3.
REHEARSAL = [
    "What is the latest forecast for Revenues?",
    "Which model is best for Revenues?",
    "Why is Expenditure not published?",
    "Why is State budget balance not published?",
    "How accurate have the forecasts been?",
    "Forecast Revenues 30 days ahead",
]


@pytest.mark.parametrize("message", REHEARSAL)
def test_no_rehearsal_question_is_intercepted_by_the_action_tier(message):
    """The demo script must reach the same answers it reached in Session 3.

    `Forecast Revenues 30 days ahead` is the interesting one: it is an
    imperative, and it names a forecast. It stays a question because `forecast`
    is not in the run-verb list — the verbs mean "produce a new official
    issue", and this message asks for a number, which the horizon guard in
    agent/official.py then refuses. Adding `forecast` as a verb here would
    silently convert rehearsal answer (f) into a publish attempt.
    """
    assert classify(message, TARGETS) is None


# ─────────────────────────── target scoping ───────────────────────────

def test_an_unscoped_run_means_every_champion_target():
    """Empty targets is a real answer, not a parse failure."""
    req = classify("run today's forecast", TARGETS)
    assert req is not None and req.targets == ()


def test_a_named_target_scopes_the_run():
    req = classify("run the forecast for Revenues", TARGETS)
    assert req is not None and req.targets == ("Revenues",)


def test_the_longest_target_alias_wins():
    """`state budget balance` must not be shortened to `balance`.

    The same failure agent/official.py's alias table exists to prevent: a run
    scoped to the wrong series is as wrong as an answer about the wrong series.
    """
    req = classify("run the forecast for state budget balance", TARGETS)
    assert req is not None and req.targets == ("State budget balance",)


def test_an_alias_the_registry_does_not_spell_still_resolves():
    req = classify("refresh the spending forecast", TARGETS)
    assert req is not None and req.targets == ("Expenditure",)


def test_several_named_targets_are_all_kept():
    req = classify("rerun the forecast for Revenues and Expenditure", TARGETS)
    assert req is not None
    assert set(req.targets) == {"Revenues", "Expenditure"}


# ─────────────────────────── degenerate input ───────────────────────────

@pytest.mark.parametrize("message", ["", "   ", None])
def test_empty_input_is_not_an_action(message):
    assert classify(message, TARGETS) is None


def test_a_run_verb_without_a_forecast_noun_is_not_an_action():
    assert classify("refresh the page", TARGETS) is None
    assert classify("run the tests", TARGETS) is None


# ─────────────────────────── consent ───────────────────────────

@pytest.mark.parametrize("reply", [
    "yes", "Yes", "y", "yep", "ok", "sure", "go ahead", "do it", "proceed",
    "confirm", "run it", "run anyway", "please do", "go for it", "yes please",
])
def test_an_explicit_affirmative_reads_as_consent(reply):
    assert read_consent(reply) == CONSENT_YES


@pytest.mark.parametrize("reply", [
    "no", "nope", "cancel", "stop", "abort", "don't", "never mind",
    "not now", "hold off", "no thanks",
])
def test_an_explicit_refusal_reads_as_refusal(reply):
    assert read_consent(reply) == CONSENT_NO


@pytest.mark.parametrize("reply", [
    "no, wait — what does the gate check?",   # substring 'no' AND 'wait'
    "is that ok?",                            # substring 'ok'
    "yes, but which targets?",                # substring 'yes'
    "what would that publish?",
    "sure about the horizon?",
    "",
    "   ",
])
def test_anything_short_of_an_explicit_answer_is_unclear(reply):
    """Whole-message matching, because a substring search resolves all of these.

    Every case here contains a word that a naive parser would treat as an
    answer, and in each one the user is asking a question rather than giving
    an instruction. `unclear` never runs anything.
    """
    assert read_consent(reply) == CONSENT_UNCLEAR
