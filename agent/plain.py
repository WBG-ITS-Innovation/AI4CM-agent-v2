# agent/plain.py — turning contract-read values into plain English without
# losing what the Lab was careful to say.
#
# The Lab's contract goes to some trouble to distinguish "absent because not
# applicable" from "absent because something failed", and to admit where it
# cannot tell them apart. A plain-language layer that renders both as a
# confident sentence throws that away, and the user is the one who pays.
#
# So the rule here is: every phrase that could be mistaken for a measurement
# must be traceable to a KNOWN value. Where a value is UNKNOWN we say what
# the artifact does and does not record — and we never supply a cause the
# artifact did not give us.
from __future__ import annotations

from typing import Iterable, Optional

from agent.contract import (
    CoverageView, FamilyView, Gate, Presence, RunStatus, RunView, Value,
)

# ─────────────────────────── the model framing ───────────────────────────
#
# There is no honest single headline count, because the entries are not one
# kind of thing: competitors, interval methods and reference baselines are
# counted together only by losing the distinction that matters.
#
# There is also no honest *hardcoded* composition. This module used to carry
# the sentence as a pinned literal — "13 machine-learning models and 4
# statistical models…" — with a test asserting it verbatim. The Lab derives the
# same sentence from its model registry, and when C_DL became enumerable the
# Lab's pool went to 13 + 5 + 4 + 3 + 3 while this string stayed where it was.
# Both suites passed. Two repos, each internally consistent, mutually wrong.
#
# So the composition is now read from the artifact and never restated. When the
# run does not record it, the Agent says so. That is a real loss of information
# — and the correct one: a stale sentence delivered confidently is worse than
# an admitted gap, because only one of the two is detectable by the reader.

#: What to say when the artifact carries no composition. Deliberately explains
#: *why* there is no fallback, so "not recorded" doesn't read as a lookup the
#: Agent simply failed to do.
NO_COMPOSITION = (
    "The model composition is not recorded in this run's artifacts, so I "
    "can't tell you how many models of each kind competed. The Lab derives "
    "that sentence from its model registry rather than storing a literal, and "
    "this run's SUMMARY.json predates the field. I won't quote a count from "
    "memory: the last count this Agent held was already out of date by five "
    "models and nothing detected it.")

#: The part of the long framing that is an explanation rather than a
#: measurement. Safe to say whenever the sentence itself is known — it names no
#: quantity, so it cannot go stale when the pool changes.
_WHY_THE_KINDS_ARE_SEPARATE = (
    "The distinction matters when reading a leaderboard. The baselines are "
    "there to be beaten, not to win — persistence ('tomorrow looks like "
    "today') is the ruler every competitor is measured against, so ranking it "
    "alongside them would be a category error. The quantile methods produce "
    "the prediction intervals rather than competing on point accuracy.")


def say_model_framing(run: RunView) -> str:
    """The composition, exactly as the Lab derived it — or an admitted gap."""
    if run.client_framing.is_known:
        return str(run.client_framing.value)
    return NO_COMPOSITION


def say_model_framing_long(run: RunView) -> str:
    """The composition plus why its parts are counted separately."""
    if run.client_framing.is_unknown:
        return NO_COMPOSITION
    parts = [str(run.client_framing.value), _WHY_THE_KINDS_ARE_SEPARATE]
    if run.champion_pool_size.is_known:
        parts.append(f"That leaves a champion-eligible pool of "
                     f"{int(run.champion_pool_size.value)}.")
    else:
        parts.append("The size of the champion-eligible pool is not recorded "
                     "in this run's artifacts, so I can't give you that number.")
    return "\n\n".join(parts)


# ─────────────────────────── value rendering ───────────────────────────

def say_value(value: Value, *, unit: str = "", noun: str = "the value") -> str:
    """Render a `Value` as a phrase that never passes UNKNOWN off as a number."""
    if value.is_known:
        v = value.value
        text = f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v}"
        return f"{text}{unit}"
    if value.presence is Presence.NOT_PRODUCED:
        return f"not produced (no {noun} was published, and the reason is not recorded)"
    if value.presence is Presence.UNPARSEABLE:
        return f"not readable ({value.note})"
    return f"not recorded ({value.note})" if value.note else "not recorded"


def say_skill(family: FamilyView) -> str:
    """The Part 3 sentence: no skill figure, and no invented reason.

    The contract is explicit that `"n/a (not produced)"` is one marker for
    several causes and that a consumer *cannot* separate them. So we say
    exactly that, and stop.
    """
    skill = family.skill_pct
    if skill.is_known:
        return (f"{skill.value:.2f}% better than the naive "
                f"“tomorrow looks like today” rule")
    if skill.presence is Presence.NOT_PRODUCED:
        return ("no skill figure — this family produced none, and the artifact "
                "does not record why (the Lab publishes one marker for several "
                "possible causes, so neither it nor I can tell them apart)")
    if skill.presence is Presence.UNPARSEABLE:
        return (f"no usable skill figure — it was published as "
                f"{skill.raw!r}, which is not a number")
    return ("no skill figure — none was recorded for this family")


def say_gate(family: FamilyView) -> str:
    """Passed / withheld-with-reasons / never verified. Never a bare 'failed'."""
    if family.gate is Gate.PASSED:
        if family.run_status is RunStatus.FAILED_QUALITY:
            return ("inconsistent — the gate is marked passed but the run "
                    "status is FAILED_QUALITY, so it is not shown as a clean "
                    "result until the Lab resolves the contradiction")
        return "passed the quality gate"
    if family.gate is Gate.WITHHELD:
        reasons = family.withheld_reasons
        if reasons:
            return "withheld: " + "; ".join(reasons)
        return ("withheld, but the artifact records no reason — that is an "
                "inconsistency in the run, not a verdict I can explain")
    return ("not verified — the quality gate returned no verdict for this "
            "family, so it is neither a pass nor a failure")


def say_status(family: FamilyView) -> str:
    if family.run_status is RunStatus.SUCCESS:
        return "run status SUCCESS"
    if family.run_status is RunStatus.FAILED_QUALITY:
        return "run status FAILED_QUALITY — the run's own integrity checks failed"
    return "run status not recorded"


def say_best_model(family: FamilyView) -> str:
    """Name and metric kept apart, so neither is asserted from the other."""
    best = family.best_model
    if best.name.is_unknown:
        return "no best model was published"
    name = best.name.value
    if best.metric_value.is_known and best.metric_name.is_known:
        return (f"{name} ({best.metric_name.value} "
                f"{best.metric_value.value:,.0f})")
    return f"{name} (no error figure was published alongside the name)"


def say_flag(value: Value, label: str) -> str:
    if value.is_known:
        return f"{label}: {'yes' if value.value else 'no'}"
    return f"{label}: not recorded"


def say_coverage(coverage: CoverageView) -> str:
    """Contract §5: never 0, never 'failed', and never an implied level."""
    if not coverage.is_reported:
        if coverage.unavailable_reason.is_known:
            return (f"Interval coverage is not reported: "
                    f"{coverage.unavailable_reason.value}")
        return ("Interval coverage is not reported for this family. That is "
                "not a coverage of zero and not a failed check — this run "
                "simply published no coverage measurement.")
    measured = f"{coverage.measured.value * 100:.1f}%"
    if coverage.level_is_recorded:
        nominal = f"{coverage.nominal.value * 100:.0f}%"
        return (f"Measured interval coverage is {measured}, against a fitted "
                f"level of {nominal}.")
    return (f"Measured interval coverage is {measured}. The level the "
            f"interval was fitted for is not recorded in the artifact, so I "
            f"cannot say what it was aiming at — the number alone does not "
            f"tell you whether that is good.")


# ─────────────────────────── family & run narratives ───────────────────────────

def describe_family(family: FamilyView, label: str = "") -> str:
    """One family, in plain English, with nothing asserted that wasn't read."""
    head = f"**{family.name}**" + (f" ({label})" if label else "")
    lines = [f"{head} — {say_gate(family)}."]

    if family.is_presentable:
        lines.append(f"Best model: **{say_best_model(family)}** — {say_skill(family)}.")
    else:
        lines.append(f"Best result, for diagnosis only: {say_best_model(family)} "
                     f"— {say_skill(family)}.")

    flags = [say_flag(family.leakage_flag, "leakage"),
             say_flag(family.shift_flag, "shift")]
    lines.append(f"Checks — {', '.join(flags)}; {say_status(family)}.")

    if family.defects:
        lines.append("Artifact issues: " + "; ".join(family.defects) + ".")
    return " ".join(lines)


def describe_champion(run: RunView, labels: Optional[dict] = None) -> str:
    """The champion, or an honest account of why there isn't one."""
    labels = labels or {}
    champ = run.champion()
    if champ is not None:
        return (
            f"Today's champion is **{champ.name}** "
            f"({labels.get(champ.name, '')}) — best model "
            f"**{say_best_model(champ)}**, {say_skill(champ)}, "
            f"quality gate **PASSED**.")

    # No champion. Say precisely which of the three reasons applies to whom.
    parts = ["**No model earned trust on this run**, so I won't crown a winner."]
    withheld = run.withheld_families
    unverified = run.unverified_families
    presentable_no_skill = [f for f in run.presentable_families
                            if not f.skill_pct.is_known]
    if withheld:
        parts.append("Withheld by the gate: "
                     + "; ".join(f"{f.name} ({'; '.join(f.withheld_reasons) or 'no reason recorded'})"
                                 for f in withheld) + ".")
    if unverified:
        parts.append("Never verified (the gate returned no verdict — this is "
                     "not a failure): " + ", ".join(f.name for f in unverified) + ".")
    if presentable_no_skill:
        parts.append("Passed the gate but published no skill figure, so it "
                     "cannot be ranked: "
                     + ", ".join(f.name for f in presentable_no_skill) + ".")
    return " ".join(parts)


def describe_run(run: RunView, labels: Optional[dict] = None) -> str:
    """The whole run, in plain English — the chat's post-run answer."""
    labels = labels or {}
    target = say_value(run.target, noun="target")
    horizon = (f"{run.horizon.value} day(s)" if run.horizon.is_known
               else "an unrecorded horizon")
    fam_names = ", ".join(f.name for f in run.families) or "no families"

    head = (f"The lab ran **{fam_names}** for **{target}**, horizon "
            f"**{horizon}**. All artifacts are saved in `{run.run_dir}`.")
    if run.data_file.is_known:
        head += f" Input data: `{run.data_file.value}`."
    else:
        head += (" The run does not record which input file it used, so I "
                 "can't name it.")

    body = [describe_family(f, labels.get(f.name, "")) for f in run.families]

    tail = []
    if run.stale.is_known and run.stale.value:
        tail.append("Note: the input data ends well before today, so treat "
                    "this as a rehearsal on historical data, not a live forecast.")
    elif run.stale.is_unknown:
        tail.append("Data freshness is not recorded for this run, so I can't "
                    "tell you whether the inputs are current.")

    if run.overall.disagreements:
        tail.append("The run's own summary counters disagree with its family "
                    "records (" + "; ".join(run.overall.disagreements)
                    + "). I've used the family records, which are the source.")

    return "\n\n".join([head] + body + tail)


def describe_defects(run: RunView) -> str:
    """Everything the artifact got wrong, shown rather than absorbed."""
    defects = run.all_defects
    if not defects:
        return "No contract departures were found in this run's artifacts."
    lines = "\n".join(f"- {d}" for d in defects)
    return (f"I found {len(defects)} place(s) where this run's artifacts depart "
            f"from the Lab's published contract. None of them are guesses on my "
            f"part — they are what the files do or don't say:\n{lines}")


def say_counter(value: Value, label: str) -> str:
    """A count that is UNKNOWN says so — it never renders as 0."""
    if value.is_known:
        return f"{label}: {int(value.value)}"
    return f"{label}: not recorded"


def describe_flags(run: RunView) -> str:
    """Flag counts, honest about families that recorded no flag at all."""
    leak = run.overall.get("leakage_flags")
    shift = run.overall.get("shift_flags")
    missing = [f.name for f in run.families
               if f.leakage_flag.is_unknown or f.shift_flag.is_unknown]
    text = f"Flags on this run — {say_counter(leak, 'leakage')}, {say_counter(shift, 'shift')}."
    if missing:
        text += (f" These counts exclude {', '.join(missing)}, which recorded "
                 f"no flag either way — so the totals are a floor, not a total.")
    return text
