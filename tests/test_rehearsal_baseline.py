# tests/test_rehearsal_baseline.py — the six demo answers, byte for byte.
#
# Session 5 hashed the six Session 3 rehearsal answers against the real Lab
# before any of that session's code was written, and again after, and recorded
# the six digests in docs/sessions/2026-08-17-session5-agent-action-layer.md §6.
# The comparison was done by hand and the result lived in prose. So the strongest
# evidence the project had — that adding an action layer changed no answer — was
# also the only evidence nothing could re-check.
#
# This module pins it. It is deliberately coupled to the Lab checkout: the whole
# claim is about answers built from the Lab's real artifacts, and a version of
# this test that used fixtures would be testing something else.
#
# WHEN THIS FAILS, it is not necessarily a defect. It means an answer's text
# moved. Read the diff and decide which happened:
#
#   * the Lab's artifacts changed, and the new answer is correct -> re-record
#     the digest here, in a commit that says what moved and why;
#   * the Agent changed, and the answer is worse -> that is the regression this
#     file exists to catch;
#   * the Agent changed, and the answer is better -> re-record, and say so.
#
# Do not delete a digest to make this pass.
from __future__ import annotations

import hashlib
import importlib

import pytest

from agent import lab_bridge as LB

#: Verbatim from docs/sessions/2026-08-15-session3-agent-rehearsal.md §3, and
#: kept in the same order as the digests below. tests/test_action_routing.py
#: pins the same list for a different property.
REHEARSAL = [
    ("a", "What is the latest forecast for Revenues?",
     "c307a64429e8b2cbbd84b8094a4eec96589fc3bb9ab7b7bc9d85985c28d4719b"),
    ("b", "Which model is best for Revenues?",
     "f7b422a9d7995e08357861a1d23941be66407a588cdec9a7c4c66d0cf117d0ec"),
    ("c", "Why is Expenditure not published?",
     "577769b888d67d688818ca048adeb57c8b867154daf0253485f069b321e43971"),
    ("d", "Why is State budget balance not published?",
     "b70fa083325821da85c3baa154331d7b4f7c461f9e203d680bea6a7ca3e8598a"),
    ("e", "How accurate have the forecasts been?",
     "86e61e339842bf9d3e286cd4a6c99ef78bda08a9de19a8c7c908441f1441f914"),
    ("f", "Forecast Revenues 30 days ahead",
     "377d02d7d9cbd0067868c28cdd207bd021cae1c3a3c092b826f6f0f36525b2df"),
]


@pytest.mark.parametrize("label,question,digest",
                         REHEARSAL, ids=[r[0] for r in REHEARSAL])
def test_the_rehearsal_answer_is_byte_identical(label, question, digest,
                                                real_run):
    """One answer, one digest, through the same routing path as the app.

    `app` is imported here rather than at module scope on purpose:
    tests/test_app_rendering.py installs recording stubs into `sys.modules`, and
    this module must not care whether it ran first. `answer_lab_question`
    returns a string and touches no Streamlit call either way.
    """
    app = importlib.import_module("app")
    run = LB.load_run(real_run)
    assert run is not None, "the lab run did not load"

    answer = app.answer_lab_question(question, run)
    actual = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    assert actual == digest, (
        f"rehearsal answer ({label}) is no longer byte-identical.\n"
        f"  question: {question}\n"
        f"  expected: {digest}\n"
        f"  actual:   {actual}\n"
        f"  answer now reads:\n{answer}")


def test_every_rehearsal_question_is_answered_without_a_language_model(real_run):
    """The digests only mean something if nothing random reached them.

    Every one of the six is answered by the rule-based path, so the text is a
    function of the artifacts alone. If narration were ever wired into this
    path, the digests would start moving on their own and the check above would
    become noise rather than evidence.
    """
    app = importlib.import_module("app")
    run = LB.load_run(real_run)
    for _, question, _ in REHEARSAL:
        first = app.answer_lab_question(question, run)
        assert first == app.answer_lab_question(question, run)
        assert first.strip()
