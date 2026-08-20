# tests/test_model_framing.py — the composition is read, never restated.
#
# History, because it is the whole justification for this file. The Agent
# carried the composition sentence as a literal in agent/plain.py, and
# tests/test_plain_language.py asserted it verbatim. Meanwhile the Lab derived
# the same sentence from its model registry. When C_DL became enumerable the
# Lab's pool went from 23 entries to 28 and its sentence gained "5 deep-learning
# models"; the Agent's string did not move, and its test passed, because the test
# asserted the Agent agreed with itself.
#
# So this module tests the opposite property. Not "the sentence is X" — that
# assertion is the defect — but:
#
#   * when the artifact records a composition, the Agent renders THAT, verbatim;
#   * when it does not, the Agent says so and quotes no number;
#   * no count of models appears anywhere in agent/ or app.py, in any run.
#
# The last one is a source scan. It is the only check that survives someone
# helpfully "restoring" the sentence for a nicer demo.
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent import lab_bridge as LB
from agent import plain
from agent.contract import Presence, read_model_composition, read_summary

from conftest import LAB_DERIVED_FRAMING

REPO = Path(__file__).resolve().parent.parent


# ─────────────────────── reading the field ───────────────────────

def test_nested_composition_is_read(framed_run):
    view = read_summary(framed_run)
    assert view.client_framing.is_known
    assert view.client_framing.value == LAB_DERIVED_FRAMING
    assert view.champion_pool_size.value == 13


def test_flat_composition_is_read(flat_framed_run):
    view = read_summary(flat_framed_run)
    assert view.client_framing.is_known
    assert view.client_framing.value == LAB_DERIVED_FRAMING


def test_absent_composition_is_unknown_not_a_default(clean_run):
    view = read_summary(clean_run)
    assert view.client_framing.is_unknown
    assert view.client_framing.presence is Presence.ABSENT
    assert view.client_framing.value is None


@pytest.mark.parametrize("blob", [
    {},
    {"client_framing": None},
    {"client_framing": ""},
    {"client_framing": "   "},
    {"client_framing": 13},
    {"model_composition": {}},
    {"model_composition": {"framing": None}},
    {"model_composition": None},
    {"model_composition": "13 models"},
])
def test_no_shape_of_absence_becomes_a_sentence(blob):
    framing, _ = read_model_composition(blob)
    assert framing.is_unknown, f"{blob!r} was read as a composition"


def test_counts_without_a_sentence_do_not_become_a_sentence():
    """The Agent must not write the sentence itself, even from real counts.

    Composing "13 machine-learning models…" out of a counts dict is the same
    defect wearing different clothes: the Agent would own the wording and the
    wording would drift the next time the Lab changed how it phrases things.
    """
    framing, pool = read_model_composition(
        {"model_composition": {"counts": {"machine-learning models": 13,
                                          "deep-learning models": 5},
                               "champion_pool_size": 13}})
    assert framing.is_unknown
    assert pool.value == 13          # a number that WAS recorded is still read


def test_the_absence_is_recorded_as_a_note_not_a_defect(clean_run):
    """Not in the published contract, so its absence is not a departure from it
    — but it is a real gap and must be visible, not swallowed."""
    view = read_summary(clean_run)
    notes = " ".join(view.all_notes)
    assert "model composition is not recorded" in notes
    assert not any("composition" in d for d in view.all_defects)


# ─────────────────────── rendering it ───────────────────────

def test_known_composition_renders_verbatim(framed_run):
    view = read_summary(framed_run)
    assert plain.say_model_framing(view) == LAB_DERIVED_FRAMING
    assert LAB_DERIVED_FRAMING in plain.say_model_framing_long(view)


def test_a_two_sentence_framing_renders_whole(clean_run):
    """The Lab's sentence grew a second sentence, and the reader had never met one.

    `client_framing()` now appends how many entries have a recorded result and
    how many are registered candidates with none. Rendering is verbatim, so
    nothing here needed to change — but that was true by accident rather than by
    test. A change that took the first sentence, or split on the first full stop,
    would drop the half that admits how much of the shelf is unmeasured, and no
    test would have failed. This is the run that would catch it.

    The wording is a FIXTURE VALUE, on the same terms as LAB_DERIVED_FRAMING: an
    artifact keeps whatever the Lab wrote into it.
    """
    two = ("21 machine-learning models, 5 deep-learning models and 7 statistical "
           "models compete on each target; prediction intervals come from 6 "
           "quantile methods; 3 further entries are reference baselines, not "
           "competitors. Of those, 8 have a recorded result on at least one "
           "target and 33 are registered candidates with no recorded result yet.")
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["client_framing"] = two
    path.write_text(json.dumps(blob, indent=2), encoding="utf-8")

    view = read_summary(clean_run)
    assert view.client_framing.value == two
    assert plain.say_model_framing(view) == two, "the second sentence was dropped"
    assert two in plain.say_model_framing_long(view)
    assert "not recorded" not in plain.say_model_framing(view)


def test_long_form_still_explains_why_the_kinds_are_separate(framed_run):
    text = plain.say_model_framing_long(read_summary(framed_run))
    assert "there to be beaten" in text
    assert "produce the prediction intervals" in text
    assert "champion-eligible pool of 13" in text


def test_unknown_composition_says_so_and_quotes_nothing(clean_run):
    view = read_summary(clean_run)
    for text in (plain.say_model_framing(view),
                 plain.say_model_framing_long(view)):
        assert "not recorded" in text
        assert not _COUNT_OF_MODELS.search(text), text


def test_unknown_pool_size_is_not_filled_in(flat_framed_run):
    """Sentence recorded, pool size not. The sentence renders; the number does
    not get borrowed from anywhere."""
    view = read_summary(flat_framed_run)
    text = plain.say_model_framing_long(view)
    assert LAB_DERIVED_FRAMING in text
    assert "champion-eligible pool is not recorded" in text


def test_the_real_committed_artifact_records_its_composition(real_run):
    """The Lab now writes `client_framing`, and the Agent picked it up with no
    change on this side — which was the point of reading rather than restating.

    Asserted against the artifact's own string, not against a copy of it. A
    test that hardcoded the sentence would be the original defect in test
    clothing: it would pass forever while the Lab reworded, and the failure it
    is meant to catch is precisely the Agent showing a sentence the Lab no
    longer writes.
    """
    view = read_summary(real_run)
    recorded = view.raw["client_framing"]
    assert view.client_framing.is_known
    assert plain.say_model_framing(view) == recorded
    assert recorded in plain.say_model_framing_long(view)
    assert "not recorded" not in plain.say_model_framing(view)


def test_the_real_artifact_records_its_champion_pool_as_a_list(real_run):
    """The Lab publishes the pool as an explicit list, so its size is READ, not derived.

    This test used to assert the opposite, and the reasoning was right at the time: the size was
    only *derivable*, from `counts[champion_pool_category]`, and doing that arithmetic would have
    made the Agent the author of a number under a name the Lab never used.

    That is no longer the shape. `model_composition.champion_pool` is a list of 13 model names, so
    `len()` is reading a published field rather than inferring one. The inference is still refused —
    see `test_a_count_by_category_is_not_read_as_a_pool_size`.
    """
    view = read_summary(real_run)
    pool = view.raw["model_composition"]["champion_pool"]
    assert isinstance(pool, list) and all(isinstance(m, str) for m in pool)
    assert view.champion_pool_size.is_known
    assert view.champion_pool_size.value == len(pool) == 13
    assert "not recorded" not in plain.say_model_framing_long(view)


def test_an_explicit_pool_size_wins_over_the_list_length(clean_run):
    """If the Lab ever writes the number itself, that is what the Agent reports."""
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["model_composition"] = {"framing": LAB_DERIVED_FRAMING,
                                 "champion_pool": ["A", "B", "C"],
                                 "champion_pool_size": 9}
    path.write_text(json.dumps(blob), encoding="utf-8")
    view = read_summary(clean_run)
    assert view.champion_pool_size.value == 9


def test_a_count_by_category_is_not_read_as_a_pool_size(clean_run):
    """The inference that is still refused: counts + category is not a published pool.

    Deriving 13 from `counts["machine-learning models"]` would make the Agent the author of a
    number under a name the Lab never used. Only an explicit size or an explicit list counts.
    """
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["model_composition"] = {"framing": LAB_DERIVED_FRAMING,
                                 "counts": {"machine-learning models": 13},
                                 "champion_pool_category": "machine-learning models"}
    path.write_text(json.dumps(blob), encoding="utf-8")
    view = read_summary(clean_run)
    assert view.champion_pool_size.is_unknown, (
        "a count by category is not a published pool size")


def test_a_malformed_pool_is_not_counted(clean_run):
    """A list of non-strings, or an empty one, is not a pool."""
    for pool in ([], [1, 2, 3], "thirteen", {"a": 1}):
        path = clean_run / "SUMMARY.json"
        blob = json.loads(path.read_text(encoding="utf-8"))
        blob["model_composition"] = {"framing": LAB_DERIVED_FRAMING,
                                     "champion_pool": pool}
        path.write_text(json.dumps(blob), encoding="utf-8")
        assert read_summary(clean_run).champion_pool_size.is_unknown, pool


def test_an_artifact_from_before_the_lab_wrote_it_says_nothing(legacy_run):
    """The absent path, on a synthetic older artifact rather than a checkout.

    `real_run` used to be this test. It cannot be both, and the absence
    semantics are the ones worth keeping under permanent guard: an unrecorded
    composition must produce no count at all, from any source.
    """
    view = read_summary(legacy_run)
    assert view.client_framing.is_unknown
    for text in (plain.say_model_framing(view),
                 plain.say_model_framing_long(view)):
        assert "not recorded" in text
        assert not _COUNT_OF_MODELS.search(text), text


# ─────────────────────── no literal anywhere ───────────────────────

#: "13 machine-learning models", "5 deep-learning models", "4 statistical
#: models", "3 quantile methods", "3 reference baselines" — any count attached
#: to a kind of model.
_COUNT_OF_MODELS = re.compile(
    r"\b\d+\s+(machine[- ]learning|deep[- ]learning|statistical|quantile|"
    r"reference|competing|point)\b", re.IGNORECASE)

#: "a champion-eligible pool of 13", "the champion-eligible pool is 13".
_POOL_SIZE = re.compile(r"champion[- ]eligible pool\s+(of|is)\s+\d+",
                        re.IGNORECASE)

SOURCE_FILES = sorted(
    [REPO / "app.py"]
    + [p for p in (REPO / "agent").rglob("*.py")])


def _without_comments(path: Path) -> str:
    """Source with `#` comments removed, docstrings and literals kept.

    The distinction is deliberate. A count in a `#` comment cannot reach a
    user by any path, and forbidding it would mean this module could not
    explain the history that made it necessary. A count in a string literal —
    including a docstring — is one f-string away from a rendered page, so it
    stays in scope.
    """
    import io
    import tokenize
    pieces: list[str] = []
    with io.open(path, encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type != tokenize.COMMENT:
                pieces.append(tok.string)
    return "\n".join(pieces)


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_no_source_file_hardcodes_a_model_count(path):
    """The check that outlives every good intention.

    agent/ and app.py may describe the *kinds* of entry freely — that never goes
    stale. They may not attach a number to one, because the number belongs to the
    Lab's registry and the Agent has no way to know when it changes.
    """
    text = _without_comments(path)
    hits = _COUNT_OF_MODELS.findall(text) + _POOL_SIZE.findall(text)
    assert not hits, (
        f"{path.relative_to(REPO)} hardcodes a model count {hits}. The "
        f"composition comes from the artifact via RunView.client_framing; see "
        f"agent/plain.py. If a run does not record it, say so.")


def test_the_glossary_handed_to_the_llm_carries_no_counts():
    """`EXPLANATIONS` goes into the LLM context as `glossary`. A number written
    there is a number the model will quote, and it will not be marked UNKNOWN."""
    for key, text in LB.EXPLANATIONS.items():
        assert not _COUNT_OF_MODELS.search(text), f"EXPLANATIONS[{key!r}]"


def test_the_system_prompt_forbids_quoting_a_count_from_memory():
    text = _without_comments(REPO / "app.py")
    assert "CONTEXT.model_composition" in text
    assert not _COUNT_OF_MODELS.search(text)
