# agent/official.py — answers about the *official* forecast, per target.
#
# This module exists because the agent used to answer every question from
# whichever backtest run happened to be newest, and a run covers one target.
# So "why is Expenditure not published?" was answered with the family verdicts
# of a State budget balance run: confidently, in the right shape, about the
# wrong series. Nothing invented a number — it answered a question nobody had
# asked. Target-blindness, not fabrication.
#
# Three rules hold the answers together:
#
#   1. RESOLVE THE TARGET FIRST. Every public function takes an explicit
#      target, and `resolve_target` returns None rather than a guess when the
#      question names none. Asking is cheaper than being wrong.
#
#   2. A WITHHELD TARGET NEVER EMITS A LEVEL. Not p50, not p10/p90, not an
#      origin value dressed as an estimate. The gates withheld the numbers;
#      quoting them "for context" republishes them. What a withheld target
#      gets is the verdict, the gates it failed with measured-against-
#      threshold, and the registry's own plain-language reason.
#
#   3. THE CHAMPION IS THE REGISTRY'S, NOT A FAMILY'S. Contract §1 keeps these
#      apart and so does this module: `best_model_answer` reads
#      `registry/recipes.json` and never a family leaderboard.
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from . import published as PUB
from . import registry_read as REG

#: Longest first, so "state budget balance" is not shortened to "balance" and
#: "income tax" is not shortened to "income". `intents.parse_intent_rules`
#: iterates shortest-to-longest and keeps the *last* match, which gets this
#: exactly wrong for the one target whose name contains another alias.
_TARGET_ALIASES: tuple[tuple[str, str], ...] = (
    ("state budget balance", "State budget balance"),
    ("budget balance", "State budget balance"),
    ("expenditures", "Expenditure"),
    ("expenditure", "Expenditure"),
    ("spending", "Expenditure"),
    ("revenues", "Revenues"),
    ("revenue", "Revenues"),
    ("balance", "State budget balance"),
)

#: "30 days ahead", "next 30 days", "in 6 weeks", "3 months out".
_HORIZON_RE = re.compile(
    r"(\d{1,4})\s*(business\s+day|working\s+day|day|week|month|quarter|year)s?",
    re.IGNORECASE)

_UNIT_DAYS = {"business day": 1, "working day": 1, "day": 1,
              "week": 5, "month": 21, "quarter": 63, "year": 252}


def resolve_target(question: str, known: Optional[list[str]] = None
                   ) -> Optional[str]:
    """The target a question names, or None when it names none.

    None is a real answer: the caller must ask rather than assume. Matching is
    longest-alias-first and is checked against `known` when supplied, so a
    registry that renames a target does not leave this returning a stale name.
    """
    text = (question or "").casefold()
    for alias, canonical in _TARGET_ALIASES:
        if alias in text:
            if known:
                for name in known:
                    if name.casefold() == canonical.casefold():
                        return name
                continue
            return canonical
    if known:
        for name in known:
            if name.casefold() in text:
                return name
    return None


def requested_horizon(question: str) -> Optional[tuple[int, str]]:
    """`(business_days, phrase)` a question asks for, or None.

    Weeks and longer are converted at *business*-day rates because the Lab's
    horizon is counted in business days; calling 30 calendar days "30 days"
    and comparing it to a 5-business-day horizon would understate the gap.
    """
    match = _HORIZON_RE.search(question or "")
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2).lower().replace("  ", " ")
    unit = re.sub(r"\s+", " ", unit)
    days = count * _UNIT_DAYS.get(unit, 1)
    return days, match.group(0).strip()


def _fmt(value: float) -> str:
    return f"{value:,.0f}"


def _approval_line(recipe: REG.Recipe) -> str:
    if recipe.is_approved:
        return f"Approved by {recipe.approved_by}."
    return ("**Nobody has approved this model.** Its status in the lab's "
            f"registry is _{recipe.status or 'not recorded'}_ — no approval "
            "workflow exists yet, so treat it as a candidate.")


def _gate_bullets(recipe: REG.Recipe) -> str:
    lines = []
    for gate in recipe.failing():
        measured, threshold = gate.measured, gate.threshold
        numbers = ""
        if isinstance(measured, (int, float)) and isinstance(threshold, (int, float)):
            numbers = f" (measured {measured:g}, the limit is {threshold:g})"
        lines.append(f"- **{gate.name}**{numbers}\n  {gate.reason_plain}".rstrip())
    return "\n".join(lines)


# ───────────────────────────── the answers ─────────────────────────────

def forecast_answer(repo: Path, target: str, question: str = "") -> str:
    """The official forecast for `target`, or why there is not one.

    The horizon check runs *before* the numbers, because a request beyond the
    validated horizon must not be answered with the validated one silently
    substituted.
    """
    issue = PUB.latest_issue(repo)
    recipe = REG.champion_for(repo, target)

    if not issue.is_readable:
        return (f"I can't read the lab's latest published forecast, so I have "
                f"no official numbers for {target}: {issue.note}. I won't "
                f"substitute a backtest figure for a forecast.")

    asked = requested_horizon(question)
    limit = issue.max_horizon
    if asked and limit and asked[0] > limit:
        return (
            f"**I can't give you an official {asked[1]} forecast.** The lab "
            f"validates and publishes **{limit} business days** ahead and "
            f"nothing further — issue `{issue.issue_date}` covers "
            f"{', '.join(issue.target_dates)}.\n\n"
            f"Anything beyond that horizon has never been evaluated, so a "
            f"number for it would carry no evidence about whether it is any "
            f"good. I'd rather give you the {limit}-day forecast, which has "
            f"been measured, than a longer one that has not.")

    # A target the registry withheld: the verdict, never the level.
    if recipe.is_withheld:
        return why_not_published_answer(repo, target)

    if not issue.publishes(target):
        base = (f"**{target} is not in the lab's latest published forecast** "
                f"(issue `{issue.issue_date}`, which covers "
                f"{', '.join(issue.targets) or 'no targets'}).")
        if recipe.note:
            return f"{base} {recipe.note.capitalize()}."
        if not recipe.verdict_known:
            return (f"{base} The registry records no publication verdict for "
                    f"it either, so its status is unknown rather than bad — I "
                    f"won't guess a number or a reason.")
        return base

    rows = issue.rows_for(target)
    if not rows:
        return (f"The lab's manifest lists {target} in issue "
                f"`{issue.issue_date}`, but the forecast file has no rows for "
                f"it. That is an inconsistency in the artifact, so I have no "
                f"numbers to quote and won't reconstruct any.")

    name = issue.canonical_target(target)
    lines = [f"| Date | Low (P10) | **Central (P50)** | High (P90) |",
             f"|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row.target_date} | {_fmt(row.p10)} | "
                     f"**{_fmt(row.p50)}** | {_fmt(row.p90)} |")
    table = "\n".join(lines)

    origin = rows[0].origin_value
    origin_line = ""
    if origin is not None:
        origin_line = (f"\n\nFor comparison, the last actual value before the "
                       f"forecast was made ({rows[0].origin_date}) was "
                       f"**{_fmt(origin)}**.")

    evidence = ""
    if recipe.mase is not None:
        evidence = (f"\n\nThis is published because it passed every quality "
                    f"gate — most importantly it is more accurate than simply "
                    f"repeating the same weekday last week (MASE "
                    f"{recipe.mase:g}, where anything at or above 1.0 would be "
                    f"withheld).")

    return (
        f"**Official forecast for {name}** — issue `{issue.issue_date}`, "
        f"{len(rows)} business days ahead, from the champion recipe "
        f"`{recipe.recipe_id or 'not recorded'}`"
        f"{f' ({recipe.point_model})' if recipe.point_model else ''}.\n\n"
        f"{table}\n\n"
        f"P10–P90 is the range the model expects the outcome to fall in four "
        f"times out of five; P50 is the central estimate."
        f"{origin_line}{evidence}\n\n"
        f"{_approval_line(recipe)} {PUB.provenance_sentence(issue)} "
        f"Latest input data: {issue.latest_data_date or 'not recorded'}.")


def why_not_published_answer(repo: Path, target: str) -> str:
    """Why `target` is withheld — the verdict and the gates, no levels."""
    recipe = REG.champion_for(repo, target)
    issue = PUB.latest_issue(repo)

    if recipe.note:
        return f"I can't answer that for {target}: {recipe.note}."

    if recipe.is_publishable:
        published_now = issue.is_readable and issue.publishes(target)
        where = (f" It is in the latest published issue, `{issue.issue_date}`."
                 if published_now else
                 f" Note it is not in the latest issue (`{issue.issue_date}`), "
                 f"even though its verdict allows publication — so the issue, "
                 f"not the verdict, is why you don't see numbers.")
        return (f"**{target} *is* published.** The lab's verdict for it is "
                f"_publishable_: {recipe.reason_plain}{where}")

    if not recipe.verdict_known:
        return (f"The lab's registry records no publication verdict for "
                f"{target}, so I can't tell you it was withheld and I can't "
                f"tell you it passed. Unknown is not the same as failed.")

    bullets = _gate_bullets(recipe)
    fix = (f"\n\n**What would change this:** {recipe.named_fix}"
           if recipe.named_fix else "")
    count = len(recipe.failing())
    checks = "check" if count == 1 else "checks"

    return (
        f"**{target} is withheld — the lab does not publish it as a "
        f"forecast.**\n\n"
        f"It failed {count} quality {checks}:\n\n{bullets}\n\n"
        f"Because of that, no forecast numbers are published for {target} at "
        f"all — not shown with a warning, not published. The model behind it "
        f"is `{recipe.recipe_id}` ({recipe.point_model}), measured on "
        f"{recipe.dev_window or 'the lab’s evaluation window'}"
        f"{f' over {recipe.dev_n} days' if recipe.dev_n else ''}."
        f"{fix}")


def best_model_answer(repo: Path, target: str) -> str:
    """The champion recipe for `target`, with the evidence behind it.

    The champion in the contract's sense: the `point_model` the registry
    promotes. Never a family leaderboard's `best_model`.
    """
    recipe = REG.champion_for(repo, target)
    if recipe.note:
        return f"I can't name a best model for {target}: {recipe.note}."

    policy = REG.gate_policy(repo)
    bits = []
    if recipe.mase is not None:
        limit = policy.get("mase_max")
        against = (f", where 1.0 is break-even against that rule"
                   if isinstance(limit, (int, float)) else "")
        bits.append(f"- **Accuracy vs the naive rule:** MASE "
                    f"**{recipe.mase:g}**{against}. "
                    + ("Below 1.0, so it beats repeating the same weekday "
                       "last week." if recipe.mase < 1.0 else
                       "At or above 1.0, so the naive rule is more accurate."))
    if recipe.sentinel_ratio is not None:
        limit = policy.get("sentinel_min")
        need = f" (the lab requires {limit:g})" if isinstance(limit, (int, float)) else ""
        bits.append(f"- **Signal check:** shuffling the historical answers "
                    f"made its error **{recipe.sentinel_ratio:g}×** worse"
                    f"{need}.")
    if recipe.skill_vs_ruler_pct is not None:
        bits.append(f"- **Skill vs the benchmark:** "
                    f"{recipe.skill_vs_ruler_pct:.2f}% — reported, not gated. "
                    f"On its own this is weak evidence, which is why MASE is "
                    f"the binding accuracy test.")
    evidence = "\n".join(bits) or "- The registry records no measured evidence."

    verdict = ""
    if recipe.is_withheld:
        verdict = (f"\n\n⚠️ Even so, **{target} is withheld** and its numbers "
                   f"are not published — this is the best model the lab has "
                   f"for it, not a model it trusts. Ask me why it is withheld.")
    elif recipe.is_publishable:
        verdict = (f"\n\nIts numbers **are** published, in the lab's latest "
                   f"issue.")

    return (
        f"For **{target}** the lab promotes one champion recipe: "
        f"`{recipe.recipe_id}` — **{recipe.point_model}**, from the "
        f"{recipe.family or 'unrecorded'} family.\n\n"
        f"Measured on {recipe.dev_window or 'the evaluation window'}"
        f"{f' over {recipe.dev_n} days' if recipe.dev_n else ''}:\n\n"
        f"{evidence}{verdict}\n\n"
        f"{_approval_line(recipe)}\n\n"
        f"_One distinction worth keeping: this is the champion the lab's "
        f"registry promotes for {target}, which is what an official forecast "
        f"uses. It is not the same as the best model within a single model "
        f"family on a given run — that is a narrower comparison._")


def scored_rows(repo: Path) -> int:
    """How many published forecasts have been scored against real outcomes."""
    path = Path(repo) / "forecasts" / "scorecard.csv"
    if not path.exists():
        return 0
    try:
        rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    except (OSError, ValueError):
        return 0
    return len([r for r in rows if (r.get("y_true") or "").strip()])


def accuracy_answer(repo: Path) -> str:
    """What the recorded evidence says about accuracy, per target, with verdicts.

    Deliberately per-target and verdict-labelled. A single headline accuracy
    number across three targets would average a published forecast together
    with two the gates withheld, which is how a withheld figure becomes an
    official-sounding one.
    """
    recipes, note = REG.load_recipes(repo)
    if note:
        return f"I can't answer that: {note}."

    scored = scored_rows(repo)
    lines = []
    for target, recipe in recipes.items():
        if recipe.mase is None:
            lines.append(f"- **{target}** — no accuracy figure is recorded.")
            continue
        if recipe.is_publishable:
            verdict = ("✅ published — this is the one figure here that "
                       "backs an official forecast")
        elif recipe.is_withheld:
            verdict = ("❌ withheld — this figure is the *reason* it is "
                       "withheld, not an estimate you can use")
        else:
            verdict = "— no verdict recorded"
        comparison = ("more accurate" if recipe.mase < 1.0 else "less accurate")
        pct = abs(1.0 - recipe.mase) * 100
        lines.append(
            f"- **{target}** — MASE **{recipe.mase:g}**, i.e. about "
            f"{pct:.0f}% {comparison} than simply repeating the same weekday "
            f"last week. {verdict}.")

    window = next((r.dev_window for r in recipes.values() if r.dev_window),
                  "the lab's evaluation window")
    n = next((r.dev_n for r in recipes.values() if r.dev_n), None)

    realized = (
        f"**No published forecast has been scored against a real outcome "
        f"yet.** The lab's scorecard has no completed rows, so every figure "
        f"below comes from evaluation on {window}"
        f"{f' ({n} days)' if n else ''} — not from watching these forecasts "
        f"come true."
        if scored == 0 else
        f"{scored} published forecast row(s) have been scored against real "
        f"outcomes. The figures below are from {window}.")

    return (
        f"{realized}\n\n" + "\n".join(lines) + "\n\n"
        f"The 2025 holdout has deliberately never been evaluated — it is a "
        f"one-shot check and spending it early would destroy its value. So "
        f"there is no out-of-sample accuracy claim I can make, and I won't "
        f"imply one.")
