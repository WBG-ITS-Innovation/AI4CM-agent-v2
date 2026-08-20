# app.py — AI4CM Agent: the conversational door to the AI4CM lab.
#
# Two modes, decided at startup:
#   LAB MODE  — an AI4CM daily run (with M-1's SUMMARY.json) was found. The
#               app presents the lab's *gated* results: champion forecast,
#               per-family quality gates, flags, and a chat that explains them.
#               The agent never re-runs or re-ranks models here — one source
#               of truth, and it lives in the lab.
#   DEMO MODE — no lab run found. Falls back to the bundled demo dataset and
#               the local toolbox (agent/tools.py), clearly labelled as
#               uncalibrated demo output.
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from agent import lab_bridge as LB
from agent import official
from agent import data_intake as DI
from agent import plain
from agent import published as PUB
from agent import run_exec as RX
from agent import run_intent as RI
from agent import run_report as RR
from agent.contract import Gate, RunStatus
import json
from agent.llm import have_llm, chat_llm

# `agent.tools` is NOT imported here. It pulls in statsmodels and scikit-learn
# (~150 MB, several seconds of import time) for the local demo toolbox, and lab
# mode never calls it. It is imported inside the demo-mode branch at the foot of
# this file, which is the only code that uses it. Both packages stay in
# requirements.txt: they are still a hard dependency of demo mode, and
# tests/test_dependencies.py walks nested imports too, so laziness here does not
# quietly undeclare them.

# ────────────────────────────── theming ──────────────────────────────
WB_NAVY, WB_CYAN, WB_GOLD = "#002244", "#009FDA", "#F2A900"
OK_GREEN, BAD_RED = "#1e9e63", "#d64545"
UNKNOWN_GREY = "#6b7f95"   # the gate's third state: never verified

st.set_page_config(page_title="AI4CM Agent", page_icon="💠", layout="wide")
st.markdown(f"""
<style>
  .stApp {{ background: linear-gradient(180deg, #f6f9fc 0%, #eef3f8 100%); }}
  h1, h2, h3 {{ color: {WB_NAVY}; }}
  div[data-testid="stMetric"] {{
      background: white; border: 1px solid #dce6f0; border-radius: 14px;
      padding: 14px 18px; box-shadow: 0 2px 8px rgba(0,34,68,.06); }}
  div[data-testid="stMetric"] label {{ color: #5b7590; }}
  .fam-card {{ background: white; border: 1px solid #dce6f0; border-radius: 14px;
      padding: 16px 18px; margin-bottom: 12px;
      box-shadow: 0 2px 8px rgba(0,34,68,.06); }}
  .badge {{ display:inline-block; padding: 3px 12px; border-radius: 999px;
      font-size: .78rem; font-weight: 700; letter-spacing:.4px; color:white; }}
  .badge-ok {{ background:{OK_GREEN}; }} .badge-bad {{ background:{BAD_RED}; }}
  /* A third badge, because the gate is tri-state: 'never verified' must not
     look like either a pass or a withholding. */
  .badge-unknown {{ background:{UNKNOWN_GREY}; }}
  .badge-demo {{ background:{WB_GOLD}; color:{WB_NAVY}; }}
  .fam-name {{ font-size:1.05rem; font-weight:700; color:{WB_NAVY}; }}
  .fam-line {{ color:#33475f; font-size:.92rem; margin-top:6px; }}
  .why {{ color:#5b7590; font-size:.85rem; margin-top:6px; }}
  section[data-testid="stSidebar"] {{ background:{WB_NAVY}; }}
  section[data-testid="stSidebar"] * {{ color:#e8f0f8 !important; }}
  section[data-testid="stSidebar"] code {{
      background: rgba(255,255,255,.12) !important;
      color: #cfe8ff !important; word-break: break-all; }}
  /* Navigation. st.radio's default is a light-background control with a
     small circle; on navy the circle disappears and the options read as
     body text rather than as the thing you click. Each option becomes a
     full-width chip, and the selected one is filled cyan — so the current
     view is legible at a glance from across a desk. */
  section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 6px; }}
  section[data-testid="stSidebar"] label[data-testid="stRadioOption"] {{
      display: flex; align-items: center; width: 100%;
      padding: 9px 14px; border-radius: 10px;
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.10);
      transition: background .12s ease, border-color .12s ease; }}
  section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {{
      background: rgba(255,255,255,.12); border-color: rgba(255,255,255,.24); }}
  /* Streamlit marks the selected option on the label itself, which is more
     durable than :has(input:checked) — the real <input> is inside a
     clip-path'd span and BaseWeb owns its states. */
  section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] {{
      background: rgba(0,159,218,.30); border-color: {WB_CYAN};
      box-shadow: inset 3px 0 0 {WB_CYAN}; }}
  section[data-testid="stSidebar"] label[data-testid="stRadioOption"] p {{
      font-weight: 600; font-size: .95rem; }}
  /* The radio dot: redundant beside a filled chip, and its unselected state
     reads as a bullet list on navy. The label is the control. */
  section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div > div:first-child {{
      display: none; }}
  section[data-testid="stSidebar"] .stExpander,
  section[data-testid="stSidebar"] details {{
      border-color: rgba(255,255,255,.14) !important; }}
  #MainMenu, footer {{ visibility: hidden; }}
</style>
""", unsafe_allow_html=True)


def fmt_money(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def line_chart(history: pd.Series | None, fam_preds: pd.DataFrame | None,
               title: str, horizon: str = "", trusted: bool = True) -> go.Figure:
    """Actuals vs the model's h-step-ahead predictions, recent window.

    The lab's predictions_long.csv stores, for each origin date, the single
    prediction made h steps ahead. Overlaying that series on the actuals
    shows exactly how good the model has been lately — and the newest
    prediction (beyond the last actual) is highlighted as 'latest forecast'.
    """
    fig = go.Figure()
    last_actual = None
    if history is not None and len(history):
        h = history.iloc[-75:]
        last_actual = history.index[-1]
        fig.add_scatter(x=h.index, y=h.values, mode="lines", name="Actual",
                        line=dict(color=WB_NAVY, width=2),
                        hovertemplate="%{x|%d %b %Y}<br>Actual: %{y:,.0f}<extra></extra>")
    if (fam_preds is not None
            and {"target_date", "y_pred"}.issubset(fam_preds.columns)):
        p = (fam_preds.sort_values("target_date")
                      .groupby("target_date", as_index=False)["y_pred"].mean())
        if last_actual is not None:
            p = p[p["target_date"] >= (last_actual - pd.Timedelta(days=75))]
        hlabel = f"{horizon}-day-ahead" if horizon else "h-step-ahead"
        pcolor = WB_CYAN if trusted else "#c0392b"
        pname = (f"Model, {hlabel} prediction" if trusted
                 else f"WITHHELD model, {hlabel} (diagnosis only)")
        fig.add_scatter(x=p["target_date"], y=p["y_pred"], mode="lines+markers",
                        name=pname,
                        line=dict(color=pcolor, width=2, dash="dot"),
                        marker=dict(size=6),
                        hovertemplate="%{x|%d %b %Y}<br>Predicted: %{y:,.0f}<extra></extra>")
        if len(p) and trusted:
            tip = p.iloc[-1]
            fig.add_scatter(x=[tip["target_date"]], y=[tip["y_pred"]],
                            mode="markers+text", name="Latest forecast",
                            marker=dict(color=WB_GOLD, size=14, symbol="star"),
                            text=[f"  {float(tip['y_pred']):,.0f}"],
                            textposition="middle right")
    fig.update_layout(title=title, template="plotly_white", height=380,
                      margin=dict(l=10, r=10, t=48, b=10),
                      legend=dict(orientation="h", y=1.12))
    return fig


# ────────────────────────────── chat brain ──────────────────────────────
import re as _re


def parse_run_request(q: str, targets: list[str]) -> dict | None:
    """Detect 'do the work for me' asks: run a new forecast with settings.

    Returns {'target','horizon','families'} or None if this isn't a run ask.
    """
    t = q.lower()
    if not any(k in t for k in ["run", "launch", "rerun", "generate", "new forecast",
                                "make a forecast", "forecast for the next"]):
        return None
    if not any(k in t for k in ["forecast", "run"]):
        return None
    target = next((c for c in targets if c.lower() in t), targets[0] if targets else None)
    if target is None:
        return None
    m = _re.search(r"(\d{1,3})\s*(?:day|days|d)\b", t)
    horizon = int(m.group(1)) if m else 5
    fams = [f for f in LB.KNOWN_FAMILIES if f.lower() in t or f.replace("_", " ").lower() in t]
    return {"target": target, "horizon": max(1, min(horizon, 60)),
            "families": fams or ["A_STAT"]}


AGENT_SYSTEM = f"""You are the AI4CM Agent, a warm, precise assistant for Georgia
Treasury cash forecasting. You sit on top of an audited forecasting lab.

Hard rules you never break:
- You NEVER invent numbers. Every figure you state must come from the CONTEXT.
- Trust decisions come from the lab's quality gate. A withheld family is never
  presented as a winner, no matter its raw error. A family whose run_status is
  FAILED_QUALITY is never presented as a clean result either.
- The gate is TRI-STATE. "passed", "withheld", and "not verified" are three
  different things. Never verified is NOT a pass and NOT a failure — say the
  gate returned no verdict.
- ABSENCE IS NOT A VALUE. The CONTEXT marks unknown fields explicitly. A
  missing or "n/a" field is UNKNOWN — never zero, never a pass, never a
  failure. If a family produced no skill figure, say it produced none and
  that the artifact does not record why. NEVER invent a reason, a threshold,
  or a cause the artifact did not state.
- If interval coverage is not reported, say it is not reported. Do not imply
  a level, and never call it zero or a failed check.
- On how many models there are: state ONLY what CONTEXT.model_composition says,
  word for word. It is the sentence the lab derived from its own model registry.
  If it is UNKNOWN, say the composition is not recorded for this run and give NO
  counts at all — not from the glossary, not from an earlier answer, not from
  your own knowledge. Never give a single headline count in any case: point
  competitors, quantile methods and reference baselines are not the same kind of
  entry and adding them together is wrong.
- Never state a threshold, cut-off or standard that is not in CONTEXT. If you
  are about to write "the lab requires at least N", stop: no such number exists
  unless CONTEXT gives it. Say what was measured, or say it is not recorded.
- You may ask the lab to run forecasts (that is your only way to compute
  anything). The lab does all data prep, modeling, baselines and checks.
- Explain jargon in plain language (skill = % better than the naive
  'tomorrow looks like today' rule; leakage = model saw the future in
  training; shift = forecast is a lagged copy of the series; persistence =
  the naive baseline; horizon = how many days ahead).
- Data is local: every run's artifacts are stored in the run folder shown in
  CONTEXT. If the run does not record its input file, say so — do not name one.
- Be concise, warm, and suggest a sensible next step or comparison.
- Professional tone: never use emojis or emoticons."""


def _family_context(f) -> dict:
    """A family as the Agent actually read it — unknowns labelled as unknown.

    The LLM used to receive the raw SUMMARY.json, which meant it saw
    `"skill_pct": "n/a (not produced)"` and `"gate_passed": null` with no
    instruction about what they mean, and confabulated accordingly. It now
    receives the contract-read view, where absence is spelled out.
    """
    return {
        "name": f.name,
        "gate": f.gate.value,
        "gate_reasons": f.gate_reasons,
        "run_status": f.run_status.value,
        "may_be_presented_as_clean_result": f.is_presentable,
        "champion_eligible": f.is_champion_eligible,
        "skill_pct": (f.skill_pct.value if f.skill_pct.is_known
                      else f"UNKNOWN — {plain.say_skill(f)}"),
        "best_model_name": (f.best_model.name.value
                            if f.best_model.name.is_known else "UNKNOWN"),
        "best_model_metric": (
            {"metric": f.best_model.metric_name.value,
             "value": f.best_model.metric_value.value}
            if f.best_model.metric_value.is_known else "UNKNOWN"),
        "leakage_flag": (f.leakage_flag.value if f.leakage_flag.is_known
                         else "UNKNOWN — not recorded"),
        "shift_flag": (f.shift_flag.value if f.shift_flag.is_known
                       else "UNKNOWN — not recorded"),
        "artifact_defects": f.defects,
    }


def build_agent_context(run: LB.LabRun, targets: list[str]) -> str:
    lb_bits = {}
    for name, lb in list(run.leaderboards.items())[:4]:
        if not lb.is_readable:
            lb_bits[name] = {"unreadable": lb.fatal}
            continue
        lb_bits[name] = {
            "schema": lb.schema,
            "target": (lb.target.value if lb.target.is_known else "UNKNOWN"),
            "target_source": lb.target_source,
            "columns_entirely_empty": lb.all_null_columns,
            "reference_baselines_not_competitors": lb.baseline_models,
            "top_rows": lb.frame.head(8).to_dict(orient="records"),
        }
    cov = {name: plain.say_coverage(c) for name, c in run.coverage.items()}
    ctx = {
        "run_folder": str(run.run_dir),
        "run_date": run.run_date,
        "target": (run.view.target.value if run.view.target.is_known
                   else "UNKNOWN — not recorded"),
        "horizon_days": (run.view.horizon.value if run.view.horizon.is_known
                         else "UNKNOWN — not recorded"),
        "input_data_file": (run.view.data_file.value
                            if run.view.data_file.is_known
                            else "UNKNOWN — this run does not record it; do not name a file"),
        "data_is_stale": (run.view.stale.value if run.view.stale.is_known
                          else "UNKNOWN — not recorded"),
        "families": [_family_context(f) for f in run.families],
        "champion": (run.champion().name if run.champion() else None),
        "interval_coverage": cov,
        "leaderboards": lb_bits,
        "available_targets": targets,
        "runnable_families": LB.KNOWN_FAMILIES,
        "family_labels": LB.FAMILY_LABELS,
        # Read from the artifact, never restated here. See agent/plain.py.
        "model_composition": (run.view.client_framing.value
                              if run.view.client_framing.is_known else
                              "UNKNOWN — this run does not record the model "
                              "composition; state no model counts of any kind"),
        "champion_eligible_pool": (
            int(run.view.champion_pool_size.value)
            if run.view.champion_pool_size.is_known else
            "UNKNOWN — not recorded in this run's artifacts"),
        "artifact_contract_departures": run.all_defects,
        "glossary": LB.EXPLANATIONS,
        # The published issue and the registry's verdicts. Everything above
        # describes ONE BACKTEST RUN of ONE target; this describes what the
        # lab actually publishes, for every target. Without it the model
        # answers "is Revenues published?" from a State budget balance
        # backtest, which is how a withheld line acquires an official number.
        "official_publication_status": official_context(run),
    }
    return json.dumps(ctx, default=str)[:16000]


def official_context(run: LB.LabRun) -> dict:
    """The published issue + per-target verdicts, as facts for the model.

    Deliberately carries no forecast levels for a withheld target — not even
    for the model's private consumption. A number in the context window is a
    number that can be echoed.
    """
    repo = LB.repo_root(run.run_dir)
    issue = official.PUB.latest_issue(repo)
    recipes, note = official.REG.load_recipes(repo)

    targets = {}
    for name, recipe in recipes.items():
        entry = {
            "publication_verdict": recipe.verdict or "UNKNOWN — not recorded",
            "champion_recipe_id": recipe.recipe_id or "UNKNOWN",
            "champion_point_model": recipe.point_model or "UNKNOWN",
            "status": recipe.status or "UNKNOWN",
            "approved_by": recipe.approved_by,
            "approved": recipe.is_approved,
            "mase_on_dev": recipe.mase,
            "sentinel_ratio": recipe.sentinel_ratio,
            "failing_gates": list(recipe.failing_gates),
            "in_latest_published_issue": (issue.is_readable
                                          and issue.publishes(name)),
        }
        if recipe.is_withheld:
            entry["forecast_numbers"] = (
                "WITHHELD — the lab publishes no forecast values for this "
                "line. Do not quote, estimate, or reconstruct any. State the "
                "verdict and the failing gates instead.")
        targets[name] = entry

    return {
        "latest_issue_date": issue.issue_date or "NONE READABLE",
        "latest_issue_readable": issue.is_readable,
        "latest_issue_note": issue.note,
        "targets_in_latest_issue": list(issue.targets),
        "validated_horizon_business_days": issue.max_horizon,
        "horizon_rule": (
            "The lab validates and publishes only the horizons listed above. "
            "Never present a longer horizon as official; say it has not been "
            "evaluated."),
        "provenance": official.PUB.provenance_sentence(issue),
        "registry_note": note,
        "per_target": targets,
        "realized_scored_rows": official.scored_rows(repo),
        "accuracy_rule": (
            "No published forecast has been scored against a real outcome "
            "unless realized_scored_rows is above zero. Every MASE above is "
            "from the lab's DEV window, not from realized accuracy, and the "
            "2025 holdout has never been evaluated."),
        "approval_rule": (
            "approved_by is null on every recipe. Nothing may be described as "
            "approved, signed off, validated or production-ready."),
    }


COMPARE_WORDS = ("compare", "which model is best", "best model", "review",
                 "all families", "all models", "benchmark")

_COMPARE_RE = _re.compile(
    r"compar|all\s+(the\s+)?(different\s+)?(model\s+)?famil|all\s+models"
    r"|every\s+(model|family)|benchmark|which\s+\w*\s*(model|one)?\s*is\s+(the\s+)?best")

_IDENTITY_RE = _re.compile(
    r"\b(what|who)\s+are\s+you\b|are\s+you\s+(an?\s+)?(agent|ai|bot|assistant)"
    r"|what\s+can\s+you\s+do|how\s+do\s+you\s+work|^\s*help\s*\??\s*$")

RUN_VERBS = ("run", "launch", "rerun", "generate", "forecast", "predict",
             "make a forecast")


def resolve_target(text: str, targets: list[str]) -> str | None:
    """Find which target the user means, tolerating typos.

    Exact substring first; then fuzzy-match every same-length word window in
    the message against each target name ('srtate budget balance' →
    'State budget balance'). Returns None when nothing is confident — the
    caller must ASK the user rather than guess."""
    import difflib
    tl = text.lower()
    for t in targets:
        if _re.search(rf"\b{_re.escape(t.lower())}\b", tl):
            return t
    words = _re.findall(r"[a-z0-9]+", tl)
    best, score = None, 0.0
    for t in targets:
        tw = t.lower()
        n = max(1, len(tw.split()))
        for i in range(0, max(1, len(words) - n + 1)):
            cand = " ".join(words[i:i + n])
            r = difflib.SequenceMatcher(None, tw, cand).ratio()
            if r > score:
                best, score = t, r
    return best if score >= 0.78 else None


def capabilities_text(run: LB.LabRun) -> str:
    return (
        "I'm the **AI4CM Agent** — a conversational front door to the AI4CM "
        "forecasting lab. I don't do any modeling myself; when you ask for a "
        "forecast I drive the lab, which handles data preparation, model "
        "training, honest baselines, leakage/shift checks, and a quality gate "
        "that withholds untrustworthy results.\n\n"
        "Things you can ask me:\n"
        "- **Run**: “forecast Revenues for the next 10 days”\n"
        "- **Compare**: “compare all model families for State budget balance”\n"
        "- **Explain**: “why was a family withheld?”, “what does skill mean?”, "
        "“what's a leakage check?”\n"
        "- **Inspect**: “what's the forecast?”, “any flags?”, “is the data "
        "fresh?”, “where are results stored?”\n\n"
        f"{plain.say_model_framing(run.view)}\n\n"
        f"Right now I'm looking at the run from **{run.run_date}** "
        f"(target: {plain.say_value(run.view.target, noun='target')}, horizon "
        f"{plain.say_value(run.view.horizon, noun='horizon')} days). Every "
        "answer I give is grounded in that run's artifacts — I never invent "
        "numbers, and where a field isn't recorded I say so rather than "
        "filling the gap.")


FAMILY_SYNONYMS = {
    "A_STAT": ["a_stat", "a stat", "statistical", "classical", "ets", "arima"],
    "B_ML": ["b_ml", "b ml", "machine learning", "ml models", "tree", "boosting",
             "lasso", "ridge", "random forest", "lightgbm", "gradient"],
    "C_DL": ["c_dl", "c dl", "deep learning", "neural", "neural net", "pytorch",
             "deep-learning", "dl model"],
    "E_QUANTILE": ["e_quantile", "e quantile", "quantile", "uncertainty",
                   "interval", "pinball"],
}


def families_in_text(text: str) -> list[str]:
    tl = text.lower()
    found = []
    for fam, words in FAMILY_SYNONYMS.items():
        if any(w in tl for w in words):
            found.append(fam)
    return found


def decide_action(q: str, run: LB.LabRun, targets: list[str],
                  history: list | None = None) -> dict:
    """Decide whether to answer, run the lab, or ask a clarifying question.

    Robustness principle: deterministic signals from the user's own words
    (typo-tolerant target match, compare detection) OVERRIDE whatever the
    LLM proposes, and a run is never launched on a guessed target — if the
    target can't be resolved from the message, the agent asks."""
    tl = q.lower()
    wants_compare = bool(_COMPARE_RE.search(tl))
    wants_run = wants_compare or any(v in tl for v in RUN_VERBS)
    # 'Why/what/how...' openers are questions to answer, not commands —
    # even when they contain words like 'run' or a family name.
    if _re.match(r"\s*(why|what|how|when|where|who)\b", tl):
        wants_run, wants_compare = False, False
    fuzzy_target = resolve_target(q, targets)
    named_fams = families_in_text(q)
    wants_all = bool(_re.search(r"\ball\b.*(famil|model)", tl))
    m = _re.search(r"(\d{1,3})\s*(?:day|days|d)\b", tl)
    horizon = max(1, min(int(m.group(1)), 60)) if m else 5

    if _IDENTITY_RE.search(tl):
        return {"action": "answer", "reply": capabilities_text(run),
                "why": "identity/capabilities question"}

    llm_d = None
    if have_llm():
        msgs = [{"role": "system", "content": AGENT_SYSTEM + """

Decide how to handle the user's message. Reply ONLY with a JSON object:
{"action": "run_lab" or "answer",
 "reply": "short acknowledgement — run_lab ONLY",
 "target": "<exact name from available_targets>",   // run_lab only
 "horizon": <int days>,                              // run_lab only
 "families": ["A_STAT", ...],                        // run_lab only
 "why": "one-line reason"}
run_lab when the user asks to run/compare/generate forecasts. For compare
requests use ["A_STAT","B_ML","E_QUANTILE"]. Default horizon 5. For
questions about existing results, definitions, or guidance: 'answer'.
This call ROUTES ONLY. Do not write the answer here — for 'answer' omit
'reply' entirely; the grounded reply is produced by a second, streamed
call that gets the same CONTEXT and the same rules."""}]
        for h in (history or [])[-6:]:
            msgs.append({"role": h["role"], "content": h["content"][:1200]})
        msgs.append({"role": "user", "content":
                     f"CONTEXT:\n{build_agent_context(run, targets)}\n\n"
                     f"USER MESSAGE: {q}"})
        raw = chat_llm(msgs, json_mode=True, temperature=0.0)
        if raw:
            try:
                llm_d = json.loads(raw)
            except Exception:
                llm_d = None

    llm_says_run = bool(llm_d and llm_d.get("action") == "run_lab")

    if wants_run or llm_says_run:
        # Families: user's explicit words win; compare forces the trio;
        # C_DL joins only when the user names it (it is much slower).
        if named_fams:
            fams = named_fams
            if wants_compare or wants_all:
                fams = sorted(set(fams) | {"A_STAT", "B_ML", "E_QUANTILE"},
                              key=LB.KNOWN_FAMILIES.index)
        elif wants_compare:
            fams = ["A_STAT", "B_ML", "E_QUANTILE"]
        else:
            fams = [f for f in (llm_d or {}).get("families", [])
                    if f in LB.KNOWN_FAMILIES] or ["A_STAT"]
        # Target: only from the user's own text (typo-tolerant). LLM's pick
        # is accepted only if it also appears in the message. Never guess.
        target = fuzzy_target
        if target is None and llm_d and llm_d.get("target") in targets:
            if resolve_target(str(llm_d.get("target")), [llm_d["target"]]):
                pass  # LLM target still needs support from the message
            target = None
        if target is None:
            opts = "\n".join(f"- {t}" for t in targets[:12])
            return {"action": "answer", "why": "run requested, target unclear",
                    "reply": ("Happy to run that — I just don't want to guess "
                              "the wrong series. **Which target should I "
                              "forecast?** The lab's data has these:\n"
                              f"{opts}\n\nSay e.g. “compare all families for "
                              f"State budget balance, 5 days”.")}
        hz = horizon if m else int((llm_d or {}).get("horizon") or 5)
        return {"action": "run_lab", "target": target,
                "horizon": max(1, min(hz, 60)), "families": fams,
                "reply": (llm_d or {}).get("reply"),
                "why": f"run request (compare={wants_compare}, "
                       f"target from message: {target})"}

    # Everything else is an answer. `reply: None` means "not decided here" —
    # the caller streams a grounded answer (answer_stream) and falls back to
    # answer_lab_question if there is no LLM. Only answers this function can
    # produce deterministically, like the identity reply above, carry text.
    return {"action": "answer", "reply": None,
            "why": (llm_d or {}).get("why", "question about this run")}


def answer_stream(q: str, run: LB.LabRun, targets: list[str],
                  history: list | None = None):
    """A grounded answer, streamed chunk by chunk, or None if there is no LLM.

    Split out of `decide_action` so the answer is generated *after* routing
    rather than inside it: a reply that already exists as a string cannot be
    streamed, and the router's JSON call has to be buffered to be parsed.
    The system prompt and CONTEXT are identical to the router's, so the
    honesty rules apply to exactly the text the user reads.
    """
    msgs = [{"role": "system", "content": AGENT_SYSTEM}]
    for h in (history or [])[-6:]:
        msgs.append({"role": h["role"], "content": h["content"][:1200]})
    msgs.append({"role": "user", "content":
                 f"CONTEXT:\n{build_agent_context(run, targets)}\n\n"
                 f"Answer the user's question, grounded only in CONTEXT, warm "
                 f"and concise, ending with one useful suggested next "
                 f"question.\n\nQUESTION: {q}"})
    return chat_llm(msgs, stream=True)


def run_narrative_stream(q: str, run: LB.LabRun, targets: list[str],
                         factual: str):
    """The post-run narrative, streamed. None if there is no LLM.

    `factual` is `run_result_narrative`'s output — built by agent/plain.py
    from contract-read values. The model rewrites that text; it does not get
    to add figures to it, and the prompt says so.
    """
    return chat_llm([
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content":
            f"CONTEXT:\n{build_agent_context(run, targets)}\n\n"
            f"The user originally asked: {q}\n"
            f"A faithful factual summary of the run:\n{factual}\n\n"
            f"Rewrite this as a warm, clear answer to their question. Keep "
            f"every number and caveat exactly. If they asked which model is "
            f"best, state the champion among gate-passing families and why. "
            f"End with one useful suggested next question."}], stream=True)


def official_narrative_stream(q: str, run: LB.LabRun, targets: list[str],
                              factual: str):
    """Rephrase an artifact-sourced official answer. None if there is no LLM.

    Same contract as `run_narrative_stream`: `factual` is built by
    `agent/official.py` from the published issue and the registry, and the
    model may reword it but may not add, drop or alter a figure or a caveat.
    The verdict wording is fixed too — "withheld" is the lab's word and a
    softer synonym would misreport a gate decision to a treasury.
    """
    return chat_llm([
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content":
            f"CONTEXT:\n{build_agent_context(run, targets)}\n\n"
            f"The user asked: {q}\n\n"
            f"This is the lab's own answer, read from its published artifacts "
            f"and its recipe registry:\n\n{factual}\n\n"
            f"Rewrite it as a warm, clear reply for a senior treasury official "
            f"who is not technical. Rules, all absolute:\n"
            f"- Keep EVERY number exactly as written, including dates, MASE "
            f"values and P10/P50/P90 figures. Add none.\n"
            f"- If the text says a line is withheld, keep the word 'withheld' "
            f"and do not soften it, and do not supply any forecast value for "
            f"that line.\n"
            f"- Keep every caveat, especially that nobody has approved the "
            f"model and that the horizon is limited.\n"
            f"- Keep the table if there is one.\n"
            f"- End with one useful suggested next question."}], stream=True)


def run_result_narrative(run: LB.LabRun) -> str:
    """Plain-language story of a run, delegated to agent/plain.py.

    This used to build its own prose from raw dict fields, and in doing so
    invented a threshold the lab never published ("the lab requires at least
    5%") and narrated `skill_pct` straight into a sentence even when its
    value was the string "n/a (not produced)". Both are now impossible: the
    only route from an artifact to a sentence goes through a contract-read
    `Value` that knows whether it is known.
    """
    text = plain.describe_run(run.view, LB.FAMILY_LABELS)
    if run.withheld_families or run.unverified_families:
        text += ("\n\nTip: very short horizons are the hardest place to beat "
                 "the naive rule — try a longer horizon (e.g. 5 days), where "
                 "models have more room to add value.")
    return text


# ────────────────────────── the ACTION tier ──────────────────────────
#
# Three tiers answered; this one acts. It sits ABOVE them in the routing and
# below them in trust: an answering tier that misroutes writes a wrong
# sentence, and this one writes an immutable published forecast.
#
# The turn is split across two messages on purpose. Streamlit reruns the whole
# script on every input, so there is no way to "wait" inside one turn for a
# yes — the confirmation has to be a message the user answers, with the pending
# plan carried in session state between the two passes. That constraint happens
# to produce exactly the behaviour the brief asks for: the agent states what it
# will do, and stops, and nothing launches until the next message says so.

#: The plan awaiting a yes, carried between two Streamlit passes. Cleared on
#: consent, on refusal, and on anything ambiguous — see `read_consent`.
PENDING_RUN = "pending_run"


def action_confirmation(repo: Path, request: "RI.RunRequest") -> tuple[str, dict]:
    """`(what the agent says, what to remember)` for a run it has not started.

    The plan is built here, once, and the confirmation text is derived from it
    rather than written beside it — so the run that executes is the run that
    was described. Storing the plan's own fields (not the RunPlan object) keeps
    session state to plain JSON-able values, which is what survives a rerun.
    """
    plan = RX.plan_run(repo, request.targets or None)
    pending = {"targets": list(plan.targets), "repo": str(repo),
               "data_unchanged": plan.data_unchanged,
               "runnable": plan.is_runnable}
    return RX.confirmation_text(plan), pending


def _run_and_report(repo: Path, pending: dict, status, trace_lines: list) -> tuple[str, str]:
    """Execute a confirmed run. Returns `(message, issue_date)`.

    Holds the lock for the duration and releases it in a `finally`, so a run
    that raises does not leave the agent refusing every subsequent request with
    "a run is already in progress".
    """
    plan = RX.plan_run(repo, tuple(pending.get("targets") or ()) or None)
    acquired, holder = RX.acquire_lock(plan.targets)
    if not acquired and holder is not None:
        trace_lines.append("[agent] refused: a run is already in flight")
        return RX.lock_message(holder), ""

    try:
        trace_lines.append(f"[agent] launching the lab's own interpreter: "
                           f"{plan.python}")
        trace_lines.append(f"[agent] targets={list(plan.targets)} horizon="
                           f"{plan.horizon} mode=official")
        outcome = None
        for kind, payload in RX.stream_run(plan):
            if kind == "event":
                trace_lines.append(f"[lab] {json.dumps(payload)}")
                status.update(label=_status_label(payload))
                line = _event_line(payload)
                if line:
                    st.markdown(line)
            elif kind == "log":
                trace_lines.append(f"[lab] {payload}")
            else:
                outcome = payload

        if outcome is None:                      # cannot happen; not assumed
            status.update(label="Run ended without a result", state="error")
            return ("The run ended without reporting an outcome, so I have "
                    "nothing to tell you about it and won't guess."), ""

        trace_lines.append(f"[agent] lab exited with code {outcome.returncode}")
        if not outcome.ok:
            status.update(label="Run failed", state="error")
            return RX.failure_message(outcome), ""

        status.update(label="Run finished — reading the artifacts back",
                      state="complete")
        finish = outcome.first("finish") or {}
        issue_date = str(finish.get("issue_date") or "")
        trace_lines.append(f"[agent] reading issue {issue_date} back off disk")
        return RR.report(repo, outcome), issue_date
    finally:
        RX.release_lock()


def _status_label(event: dict) -> str:
    """A one-line label for the spinner, from the lab's own event."""
    kind = event.get("event")
    target = event.get("target", "")
    if kind == "start":
        return (f"Running {len(event.get('targets') or [])} target(s) at "
                f"horizon {event.get('horizon')}…")
    if kind == "target_start":
        return f"{target}: fitting {event.get('model') or 'the champion recipe'}…"
    if kind == "target_ran":
        return f"{target}: fitted, checking whether it may be published…"
    if kind == "target_published":
        return f"{target}: published."
    if kind == "target_refused":
        return f"{target}: withheld by the gates."
    if kind == "finish":
        return "Finished."
    return "Working…"


def _event_line(event: dict) -> str:
    """What the user sees live, in plain language. No levels, ever.

    A withheld target's numbers exist in the lab process at the moment it is
    refused (`official_run` succeeds; `publish_official` is what refuses), so
    the rule that they never surface has to hold here too. It holds by
    construction: `agent/lab_entry.py` never puts a level on the stream, so
    there is none here to print.
    """
    kind = event.get("event")
    target = event.get("target", "")
    if kind == "target_start":
        return (f"**{target}** — running the registry champion "
                f"`{event.get('recipe_id') or 'not recorded'}`.")
    if kind == "target_published":
        return f"**{target}** — published."
    if kind == "target_refused":
        return (f"**{target}** — withheld. The lab refused to publish it; "
                f"the reason is in the summary below.")
    if kind == "target_failed":
        return f"**{target}** — failed at the {event.get('stage')} stage."
    return ""


def _offer_downloads(repo: Path, issue_date: str, where=None) -> None:
    """Download buttons for one published issue, read at render time.

    Streamlit widgets do not survive into the stored transcript — `render_turn`
    replays markdown, not widgets — so these are also offered from the sidebar
    on every later pass. Rendering them in both places is not duplication: the
    inline ones are for the person who just ran the forecast, and the sidebar
    ones are for the person who comes back to the tab tomorrow.
    """
    target = where or st
    items = RR.downloads(repo, issue_date)
    available = [d for d in items if d.is_available]
    if not available:
        return
    target.caption(f"Download issue `{issue_date}`")
    for item in available:
        target.download_button(
            item.label, data=item.data, file_name=item.filename,
            mime=item.mime, key=f"dl-{issue_date}-{item.filename}")
    for item in items:
        if not item.is_available and item.note:
            target.caption(f"Not available: {item.note}")


# ─────────────────── the data leg of the ACTION tier ───────────────────
#
# "Here is the new data" opens a THREE step flow, each step separately
# confirmed, because the steps have different consequences and a single yes
# covering all of them would be consent to something the user never saw:
#
#   1. TAKE   — validate, describe, and (when writes are permitted) install it
#               as the lab's canonical file, keeping a timestamped backup.
#   2. SCORE  — ask the lab what its already-published forecasts got right.
#               Reads the published issues; writes only a scorecard.
#   3. RUN    — a new official forecast on the new data. Hands off to the
#               Session 5 flow unchanged, guardrail and all.
#
# Step 2 is offered before step 3 deliberately. Scoring answers "was the last
# forecast any good", and that is the question a treasury should be allowed to
# see the answer to BEFORE being offered a new number. Reversing them puts a
# fresh forecast in front of the evidence about the previous one.

PENDING_DATA = "pending_data"
PENDING_SCORE = "pending_score"

#: Where an uploaded file is parked before anything is decided about it. In the
#: agent's own repo, never the lab's: an upload is a candidate, and a candidate
#: sitting in the lab's data directory is one careless glob away from being
#: treated as canonical.
UPLOAD_DIR = Path(os.getenv("AI4CM_UPLOAD_DIR", "artifacts/uploads"))


def stage_upload(uploaded) -> Path:
    """Write a Streamlit upload to the staging directory and return its path."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / Path(getattr(uploaded, "name", "uploaded.csv")).name
    dest.write_bytes(uploaded.getvalue())
    return dest


def data_confirmation(repo: Path, path: Path) -> tuple[str, dict | None]:
    """`(what the agent says, what to remember)` for a candidate data file."""
    verdict = DI.validate(path, repo)
    text = DI.intake_message(verdict)
    if not verdict.accepted:
        return text, None
    return text, {"path": str(path), "repo": str(repo),
                  "n_new": verdict.n_new,
                  "last_date": verdict.candidate.last_date,
                  "sha": verdict.candidate.sha256}


def take_data(repo: Path, pending: dict, trace_lines: list) -> tuple[str, dict]:
    """Install the file if writes are permitted; either way, offer scoring.

    Under a hold the file is NOT installed and that is said plainly — but
    scoring is still offered, because scoring against the candidate file writes
    nothing into the lab that a redirected scorecard cannot absorb.
    """
    path = Path(pending["path"])
    if RX.lab_writes_allowed():
        result = DI.install(path, repo)
        if not result.ok:
            trace_lines.append(f"[agent] install refused: {result.note}")
            return (f"**I could not install that file.** {result.note}.\n\n"
                    f"Nothing was scored or run."), {}
        trace_lines.append(f"[agent] installed -> {result.installed_to}")
        head = (f"**Taken.** The lab's data file is now the one you gave me.\n\n"
                f"- **Installed to:** `{result.installed_to}`\n"
                f"- **Previous data backed up to:** `{result.backup_at}`\n\n"
                f"The backup is the only copy of what was replaced, so I kept it "
                f"rather than overwriting in place — that file is gitignored, and "
                f"nothing else would have recorded the loss.")
    else:
        trace_lines.append("[agent] hold: not installing; scoring will use the "
                           "candidate file directly")
        head = (RX.hold_message("installing that file as the lab's data") +
                f"\n\nI can still score against it without installing it — "
                f"the lab's scorer takes the actuals as an argument, so nothing "
                f"of yours gets replaced.")

    return (head + f"\n\n**Shall I score the published forecasts against these "
                   f"actuals?** That asks the lab what its previous forecasts "
                   f"actually got right, using its own scoring code.",
            {"repo": str(repo), "data": str(path)})


def _score_and_report(repo: Path, pending: dict, status,
                      trace_lines: list) -> str:
    """Run the lab's scorer and report from the scorecard it wrote."""
    data = Path(pending["data"])
    python = RX.lab_python(repo)

    if RX.lab_writes_allowed():
        scorecard = None                       # the lab's real, tracked file
        where = "the lab's own `forecasts/scorecard.csv`"
    else:
        RX.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        scorecard = Path("artifacts") / "scorecard.staging.csv"
        where = f"`{scorecard}` in my own repo, not the lab's tracked scorecard"
    trace_lines.append(f"[agent] scoring with {python}; scorecard -> {where}")

    acquired, holder = RX.acquire_lock(("score",))
    if not acquired and holder is not None:
        return RX.lock_message(holder)
    try:
        outcome = None
        for kind, payload in RX.stream_score(repo, python, data,
                                             scorecard=scorecard):
            if kind == "event":
                trace_lines.append(f"[lab] {json.dumps(payload)}")
                status.update(label=_score_label(payload))
            elif kind == "log":
                trace_lines.append(f"[lab] {payload}")
            else:
                outcome = payload
        if outcome is None:
            status.update(label="Scoring ended without a result", state="error")
            return ("The scoring run ended without reporting an outcome, so I "
                    "have nothing to tell you and won't guess.")
        trace_lines.append(f"[agent] scorer exited {outcome.returncode}")
        if not outcome.ok:
            status.update(label="Scoring failed", state="error")
            return RX.failure_message(outcome)

        status.update(label="Scored — reading the scorecard back", state="complete")
        # Where the scorer says it wrote, else where we asked it to. Both can be
        # absent — `scorecard` is None when writing the lab's default path and
        # the event carries "" — and a Path(None) here would turn a successful
        # score into a TypeError. Absence is reported, not constructed.
        written = ((outcome.first("scored") or {}).get("scorecard")
                   or (str(scorecard) if scorecard else ""))
        if not written:
            return ("The scoring run finished but did not record where it wrote "
                    "its scorecard, so I can't read the figures back. I won't "
                    "report numbers I haven't read.")
        body = RR.score_report(Path(written), outcome)
        return f"{body}\n\n_Scorecard written to {where}._"
    finally:
        RX.release_lock()


def _score_label(event: dict) -> str:
    kind = event.get("event")
    if kind == "start":
        return f"Scoring {event.get('issues')} published issue(s)…"
    if kind == "scored":
        return (f"{event.get('scored')} scored, {event.get('pending')} pending "
                f"— reading the scorecard…")
    if kind == "refused":
        return "The lab refused to score."
    return "Scoring…"


def _data_leg(q: str, repo: Path, targets: list[str]) -> bool:
    """The TAKE and SCORE confirmations. True when this leg owned the turn."""
    # ── waiting on "shall I score?" ──
    if st.session_state.get(PENDING_SCORE):
        pending = st.session_state[PENDING_SCORE]
        consent = RI.read_consent(q)
        if consent == RI.CONSENT_NO:
            st.session_state.pop(PENDING_SCORE, None)
            msg = ("Understood — **nothing was scored.** The lab's scorecard is "
                   "untouched.")
            with st.chat_message("assistant"):
                st.markdown(msg)
            push("assistant", msg, "[agent] scoring declined")
            return True
        if consent == RI.CONSENT_YES:
            st.session_state.pop(PENDING_SCORE, None)
            trace_lines = ["[agent] ACTION tier: user confirmed scoring"]
            with st.chat_message("assistant"):
                with st.status("Scoring published forecasts…",
                               expanded=True) as status:
                    msg = _score_and_report(repo, pending, status, trace_lines)
                st.markdown(msg)
                trace = "\n".join(trace_lines)
                with st.expander("Agent trace"):
                    st.code(trace, language="text")
            push("assistant", msg, trace)
            return True
        st.session_state.pop(PENDING_SCORE, None)
        return False

    # ── waiting on "shall I take this file?" ──
    if st.session_state.get(PENDING_DATA):
        pending = st.session_state[PENDING_DATA]
        consent = RI.read_consent(q)
        if consent == RI.CONSENT_NO:
            st.session_state.pop(PENDING_DATA, None)
            msg = ("Understood — **I have not taken that file.** The lab's data "
                   "is unchanged and nothing was scored or run.")
            with st.chat_message("assistant"):
                st.markdown(msg)
            push("assistant", msg, "[agent] data intake declined")
            return True
        if consent == RI.CONSENT_YES:
            st.session_state.pop(PENDING_DATA, None)
            trace_lines = ["[agent] ACTION tier: user confirmed the data file"]
            msg, next_pending = take_data(repo, pending, trace_lines)
            if next_pending:
                st.session_state[PENDING_SCORE] = next_pending
            with st.chat_message("assistant"):
                st.markdown(msg)
                trace = "\n".join(trace_lines)
                with st.expander("Agent trace"):
                    st.code(trace, language="text")
            push("assistant", msg, trace)
            return True
        st.session_state.pop(PENDING_DATA, None)
        return False

    # ── a new announcement of data ──
    ask = RI.classify_data(q)
    if ask is None:
        return False
    if not ask.path:
        msg = ("I can take new actuals — **use the uploader above the "
               "conversation**, or give me the full path to the CSV and I'll "
               "check it before anything runs.")
        with st.chat_message("assistant"):
            st.markdown(msg)
        push("assistant", msg, f"[agent] ACTION tier: {ask.why}; no path given")
        return True

    text, remember = data_confirmation(repo, Path(ask.path))
    if remember:
        st.session_state[PENDING_DATA] = remember
    with st.chat_message("assistant"):
        st.markdown(text)
    push("assistant", text,
         f"[agent] ACTION tier: {ask.why}; validated `{ask.path}`; "
         f"{'awaiting confirmation' if remember else 'rejected'} — nothing run")
    return True


def action_turn(q: str, repo: Path, targets: list[str]) -> bool:
    """Handle a run instruction, or a reply to one. True when the turn is done.

    Returns False for anything that is not this tier's business, and the
    answering tiers below run unchanged — which is what keeps every Session
    3/4 rehearsal answer byte-identical.
    """
    if _data_leg(q, repo, targets):
        return True

    pending = st.session_state.get(PENDING_RUN)
    request = RI.classify(q, targets)

    # ── a reply to a confirmation we are waiting on ──
    if pending:
        consent = RI.read_consent(q)

        if consent == RI.CONSENT_NO:
            st.session_state.pop(PENDING_RUN, None)
            msg = ("Understood — **I have not run anything.** Nothing was "
                   "published and the lab is untouched. Ask me again whenever "
                   "you want it.")
            with st.chat_message("assistant"):
                st.markdown(msg)
            push("assistant", msg,
                 "[agent] pending run cancelled by the user; nothing launched")
            return True

        if consent == RI.CONSENT_YES:
            st.session_state.pop(PENDING_RUN, None)
            trace_lines = ["[agent] ACTION tier: user confirmed the run"]
            with st.chat_message("assistant"):
                with st.status("Running the lab's official forecast…",
                               expanded=True) as status:
                    msg, issue_date = _run_and_report(
                        repo, pending, status, trace_lines)
                st.markdown(msg)
                if issue_date:
                    _offer_downloads(repo, issue_date)
                trace = "\n".join(trace_lines)
                with st.expander("Agent trace"):
                    st.code(trace, language="text")
            if issue_date:
                st.session_state["last_issue_date"] = issue_date
            push("assistant", msg, trace)
            return True

        # Unclear. Never execute on ambiguity, and never hold consent open
        # across an unrelated turn — a confirmation the user has stopped
        # thinking about is not a confirmation. The pending run is dropped and
        # the message is answered normally (or, if it is itself a fresh run
        # instruction, re-confirmed below).
        st.session_state.pop(PENDING_RUN, None)
        if request is None:
            return False

    # ── a new run instruction ──
    if request is None:
        return False

    text, remember = action_confirmation(repo, request)
    if remember["runnable"]:
        st.session_state[PENDING_RUN] = remember
    with st.chat_message("assistant"):
        st.markdown(text)
    push("assistant", text,
         f"[agent] ACTION tier: {request.why}; targets="
         f"{list(request.targets) or 'all champion targets'}; "
         f"awaiting confirmation — nothing launched")
    return True


def answer_official_question(q: str, run: LB.LabRun) -> str | None:
    """Answers about a *named target's* official status, or None to fall through.

    Routed before the run-based answers below because a run covers one target
    and these questions name their own. Before this existed, "why is
    Expenditure not published?" was answered from whichever run was newest —
    which was a State budget balance run, so the reply listed that run's
    family verdicts and never mentioned which series it was describing. It
    read as an answer about Expenditure and was not one.
    #
    Returning None is how a question with no target reaches the run-based
    path: those answers are legitimately about "this run", not about a series.
    """
    repo = LB.repo_root(run.run_dir)
    t = q.lower()

    menu = LB.target_choices(run)
    target = official.resolve_target(q, menu.targets or None)

    asks_why_not = any(k in t for k in [
        "not published", "why is", "why isn't", "why isnt", "withheld",
        "not publish", "why not", "excluded"])
    asks_best = any(k in t for k in [
        "best model", "which model", "champion", "winner", "best recipe"])
    asks_forecast = any(k in t for k in [
        "forecast", "predict", "outlook", "next", "ahead", "expect"])
    asks_accuracy = any(k in t for k in [
        "how accurate", "accuracy", "how well", "track record", "how good",
        "been right", "performed"])

    # Accuracy is the one question that is legitimately about every target at
    # once, so it does not require one to be named.
    #
    # It outranks the forecast branch rather than deferring to it: "how
    # accurate have the *forecasts* been" contains the word, but it asks about
    # a track record, not for a new number. The signal that separates them is
    # an explicit horizon ("30 days ahead"), not the noun.
    if asks_accuracy and official.requested_horizon(q) is None:
        return official.accuracy_answer(repo)

    if not (asks_why_not or asks_best or asks_forecast):
        return None

    if target is None:
        # Ask, don't guess. Answering about the wrong series is the failure
        # this whole module exists to prevent.
        if not (menu.targets):
            return None
        names = ", ".join(f"**{name}**" for name in menu.targets)
        return (f"Which line do you mean? The lab has official recipes for "
                f"{names}. They have different verdicts, so the answer is "
                f"different for each and I don't want to guess.")

    if asks_why_not:
        return official.why_not_published_answer(repo, target)
    if asks_best:
        return official.best_model_answer(repo, target)
    return official.forecast_answer(repo, target, q)


def answer_lab_question(q: str, run: LB.LabRun) -> str:
    """Rule-based answers grounded ONLY in the lab's gated artifacts."""
    t = q.lower()
    champ = run.champion()

    if any(k in t for k in ["how many model", "how many are there",
                            "number of models", "model count"]):
        return plain.say_model_framing_long(run.view)

    # Target-scoped questions answer from the published issue and the
    # registry, not from this run. See answer_official_question.
    official_answer = answer_official_question(q, run)
    if official_answer is not None:
        return official_answer

    if any(k in t for k in ["champion", "best model", "which model", "winner"]):
        base = plain.describe_champion(run.view, LB.FAMILY_LABELS)
        if champ is None:
            return base
        return (base + " That skill number means it beats the naive "
                       "'tomorrow looks like today' rule by that margin.")

    if any(k in t for k in ["withheld", "why is", "failed", "gate"]):
        withheld, unverified = run.withheld_families, run.unverified_families
        if not withheld and not unverified:
            return ("Nothing was withheld on this run — every family reached a "
                    "gate verdict and passed it.")
        parts = []
        for f in withheld:
            reasons = "; ".join(f.gate_reasons)
            parts.append(f"**{f.name}** was withheld because: {reasons}."
                         if reasons else
                         f"**{f.name}** is marked withheld but the artifact "
                         f"records no reason — that is an inconsistency in the "
                         f"run, and I won't guess at a cause.")
        for f in unverified:
            parts.append(f"**{f.name}** was neither passed nor withheld — the "
                         f"gate returned no verdict for it, so its status is "
                         f"unknown rather than bad.")
        return " ".join(parts) + f"\n\n_{LB.EXPLANATIONS['gate']}_"

    if any(k in t for k in ["coverage", "interval", "uncertainty", "band"]):
        lines = [f"**{name}** — {plain.say_coverage(cov)}"
                 for name, cov in run.coverage.items()]
        return ("\n\n".join(lines) if lines else
                "This run published no interval coverage for any family. That "
                "is not a coverage of zero and not a failed check.")

    if any(k in t for k in ["flag", "leakage", "shift", "problem", "issue"]):
        return (plain.describe_flags(run.view) + "\n\n"
                + LB.EXPLANATIONS["leakage"] + " " + LB.EXPLANATIONS["shift"])

    if any(k in t for k in ["forecast", "predict", "next", "tomorrow", "friday"]):
        if champ is None:
            return ("I can't quote a trustworthy forecast on this run.\n\n"
                    + plain.describe_champion(run.view, LB.FAMILY_LABELS))
        preds = run.family_predictions(champ.name)
        if preds is None or "y_pred" not in preds.columns:
            return (f"**{champ.name}** passed the gate, but I can't read a "
                    f"`y_pred` column from its predictions file, so I have no "
                    f"forecast values to quote.")
        p = (preds.sort_values("target_date")
                  .groupby("target_date", as_index=False)["y_pred"].mean()).tail(7)
        rows = "\n".join(f"- {d.date()}: **{fmt_money(v)}**"
                         for d, v in zip(p["target_date"], p["y_pred"]))
        caveat = ""
        if run.view.stale.is_known and run.view.stale.value:
            caveat = ("\n\n**Data is stale** — these are the model's most "
                      "recent predictions from the last available data, not a "
                      "forecast from today. Refresh the input data and rerun "
                      "the lab for current numbers.")
        elif run.view.stale.is_unknown:
            caveat = ("\n\nNote: this run does not record whether its input "
                      "data was fresh, so I can't tell you how current these are.")
        target = (run.view.target.value if run.view.target.is_known
                  else "the run's target (not recorded)")
        return (f"From **{champ.name}** (gate PASSED), the {target} "
                f"forecast:\n{rows}{caveat}")

    if any(k in t for k in ["fresh", "stale", "data", "date"]):
        if run.view.freshness_line.is_known:
            note = (" " + LB.EXPLANATIONS["stale"]
                    if run.view.stale.is_known and run.view.stale.value else "")
            return f"{run.view.freshness_line.value}{note}"
        return ("This run records no freshness information, so I can't say "
                "whether its inputs were current. That is a gap in the "
                "artifact, not a clean bill of health.")

    if "skill" in t or "persistence" in t or "baseline" in t:
        return LB.EXPLANATIONS["skill"]

    return ("I can answer about today's run: try **“which model is champion?”**, "
            "**“what's the forecast?”**, **“why was a family withheld?”**, "
            "**“any flags?”**, **“what's the interval coverage?”**, or "
            "**“is the data fresh?”**")


#: Where the transcript is persisted. Overridable so a test (or a second
#: instance on the same machine) does not inherit whatever conversation
#: happens to be sitting in the working directory.
HIST_PATH = Path(os.getenv("AI4CM_CHAT_HISTORY", "artifacts/chat_history.json"))

# One message shape, everywhere: {"role", "content", "trace"}.
#
# There used to be two. The answer path appended {"role", "content", "trace"}
# and the run path appended a bare {"role", "content"} for its acknowledgement
# and a second message for the result — so the transcript loop's
# `m.get("trace")` was silently the difference between a turn that could show
# its work and one that could not, and a saved history mixed both. Everything
# now goes through `push()`, which produces the full shape and persists it.


def chat_entry(role: str, content: str, trace: str = "") -> dict:
    return {"role": role, "content": str(content), "trace": str(trace or "")}


def push(role: str, content: str, trace: str = "") -> dict:
    """Append one turn to the transcript and persist it. Returns the entry."""
    entry = chat_entry(role, content, trace)
    st.session_state.chat.append(entry)
    save_chat()
    return entry


def load_saved_chat() -> list:
    """Previous turns from disk, normalised to the current shape.

    Anything that is not a well-formed message is dropped rather than
    repaired: a half-read transcript that renders is worse than a short one.
    """
    try:
        if not HIST_PATH.exists():
            return []
        data = json.loads(HIST_PATH.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for m in data:
        if (isinstance(m, dict) and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str) and m["content"].strip()):
            out.append(chat_entry(m["role"], m["content"], m.get("trace", "")))
    return out[-200:]


def save_chat() -> None:
    try:
        HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        HIST_PATH.write_text(json.dumps(st.session_state.chat[-200:],
                                        ensure_ascii=False, indent=1))
    except Exception:
        pass


def render_turn(m: dict) -> None:
    """Draw one stored message. Every past turn goes through here.

    The turn being answered right now is drawn inline instead, because it
    needs the live widgets this cannot hold — st.status for the lab's
    progress, st.write_stream for the answer arriving. It ends up with the
    same parts in the same order, and is stored in the same shape, so the
    switch from live to stored is invisible.
    """
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("trace"):
            with st.expander("Agent trace"):
                st.code(m["trace"], language="text")


# ────────────────────────────── load state ──────────────────────────────
run = LB.load_latest()
lab_mode = run is not None

# Navigation lives in the sidebar, not in st.tabs.
#
# The constraint that decided it: `st.chat_input` only pins itself to the
# bottom of the viewport when it is called from the main body of the page.
# Inside a tab — or any container — it renders inline, wherever the transcript
# happens to have ended, which is why the input used to drift down the page as
# the conversation grew. Moving the view switch to the sidebar frees the main
# body, so the transcript scrolls above a fixed composer, as in every chat
# application people already know how to use.
VIEWS = ("Ask the agent", "Dashboard", "Run history", "Learn")

with st.sidebar:
    st.markdown("## AI4CM Agent")
    view = st.radio("View", VIEWS, label_visibility="collapsed") if lab_mode \
        else None
    st.markdown("---")
    if lab_mode:
        st.markdown(f"**Mode:** Lab (gated)\n\n**Run:** `{run.run_date}`\n\n"
                    f"**Folder:** `{run.run_dir}`")
    else:
        st.markdown("**Mode:** Demo\n\nNo lab run found. Point me at one with "
                    "`AI4CM_REPO=/path/to/AI4CM` or `AI4CM_RUNS_ROOT=...`, "
                    "then run `scripts/run_daily_forecast.sh` in the lab.")
    # The latest published issue, downloadable on every pass. The buttons the
    # chat renders after a run are gone by the next interaction — Streamlit
    # widgets do not survive into the replayed transcript — so the durable
    # offer lives here, where it does not depend on what happened this turn.
    if lab_mode:
        _issue = PUB.latest_issue(LB.repo_root(run.run_dir))
        if _issue.is_readable:
            st.markdown("---")
            _offer_downloads(LB.repo_root(run.run_dir), _issue.issue_date,
                             where=st.sidebar)

    st.markdown("---")
    st.markdown(f"**LLM narration:** {'on' if have_llm() else 'off (rule-based)'}")
    with st.expander("What do the checks mean?"):
        for key in ("gate", "skill", "leakage", "shift", "stale"):
            st.markdown(f"**{key.title()}** — {LB.EXPLANATIONS[key]}")

# ────────────────────────────── LAB MODE ──────────────────────────────
if lab_mode:
    st.title("AI4CM — Daily Forecast, with receipts")
    st.caption("Everything below comes from the lab's audited daily run. "
               "Models only appear as winners if they passed the quality gate.")

    if view == "Dashboard":

        v = run.view
        gate_passed = v.overall.get("families_gate_passed")
        requested = v.overall.get("families_requested")
        leak, shift = v.overall.get("leakage_flags"), v.overall.get("shift_flags")

        def _count(value) -> str:
            """A counter that is UNKNOWN renders as '—', never as 0."""
            return f"{int(value.value)}" if value.is_known else "—"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Run date", run.run_date)
        c2.metric("Families passed gate", f"{_count(gate_passed)}/{_count(requested)}")
        c3.metric("Flags (leakage / shift)", f"{_count(leak)} / {_count(shift)}")
        c4.metric("Data", ("STALE" if v.stale.is_known and v.stale.value
                           else "Fresh" if v.stale.is_known else "Not recorded"))
        st.caption(f"**Target:** {plain.say_value(v.target, noun='target')} · "
                   f"**Horizon:** {plain.say_value(v.horizon, noun='horizon')} day(s) · "
                   f"**Input data:** {plain.say_value(v.data_file, noun='input file')} · "
                   f"**All outputs saved in:** `{run.run_dir}`")
        st.caption(plain.say_model_framing(v))

        # Two lists, deliberately. A panel that cries wolf on a compliant run
        # trains people to ignore it, so contract *departures* are separated
        # from the ambiguities the contract itself anticipates and calls
        # warnings (an all-null column, a baseline with no prediction rows).
        if run.all_defects:
            with st.expander(f"This run's artifacts depart from the lab's "
                             f"contract in {len(run.all_defects)} place(s)"):
                st.markdown(plain.describe_defects(v))
                for d in run.read_defects:
                    st.markdown(f"- {d}")
        if run.all_notes:
            with st.expander(f"Known ambiguities in this run's artifacts "
                             f"({len(run.all_notes)})"):
                st.caption("The lab's contract anticipates each of these. They "
                           "are not signs that anything went wrong — they are "
                           "places where the artifact cannot tell me something.")
                for n in run.all_notes:
                    st.markdown(f"- {n}")

        with st.expander("About this run & where the data lives"):
            hist = run.history
            span = ((f"{hist.index.min().date()} → {hist.index.max().date()} "
                     f"({len(hist):,} daily observations)")
                    if hist is not None else "an unrecorded date span")
            input_line = (f"the lab loaded `{v.data_file.value}` (the "
                          f"preprocessed Treasury master file, covering {span})"
                          if v.data_file.is_known else
                          "the lab loaded its preprocessed Treasury master file "
                          "(this run does not record which file, so I can't name "
                          "it or show its date span)")
            st.markdown(
                f"**What happened:** {input_line}, trained "
                f"each requested model family with its audited settings, compared "
                f"every model against the honest persistence baseline, ran leakage "
                f"and shift checks, and applied the quality gate.\n\n"
                f"**What competed:** {plain.say_model_framing_long(v)}\n\n"
                f"**Where things are stored:** every artifact of this run — "
                f"predictions, leaderboards, integrity reports, this summary — "
                f"lives in `{run.run_dir}` on the machine running the lab. "
                f"Nothing leaves your computer. Each new run for the same date "
                f"replaces that folder; different dates get their own folders.")
            st.markdown(f"_{LB.EXPLANATIONS['gate']}_")

        champ = run.champion()
        target_title = (v.target.value if v.target.is_known else "Target not recorded")
        horizon_title = str(v.horizon.value) if v.horizon.is_known else ""
        st.subheader("Champion forecast")
        if champ is not None:
            st.markdown(f"<span class='badge badge-ok'>GATE PASSED</span> "
                        f"&nbsp;<b>{champ.name}</b> — {plain.say_best_model(champ)} · "
                        f"skill vs persistence {plain.say_skill(champ)}",
                        unsafe_allow_html=True)
            st.plotly_chart(
                line_chart(run.history, run.family_predictions(champ.name),
                           f"{target_title} — actuals & forecast",
                           horizon=horizon_title),
                width="stretch")
            st.caption(plain.say_coverage(run.family_coverage(champ.name)))
        else:
            st.warning("No model earned trust on this run, so the agent won't "
                       "present a forecast as reliable. Here's the plain-language "
                       "story — and the ungated result is charted below for "
                       "diagnosis, clearly marked.")
            st.markdown(run_result_narrative(run))
            first = next((f for f in run.families
                          if run.family_predictions(f.name) is not None), None)
            if first is not None:
                label = ("WITHHELD" if first.gate is Gate.WITHHELD
                         else "UNVERIFIED" if first.gate is Gate.UNVERIFIED
                         else "NOT PRESENTABLE")
                st.plotly_chart(
                    line_chart(run.history, run.family_predictions(first.name),
                               f"{target_title} — actuals vs {label} "
                               f"{first.name} (diagnosis only)",
                               horizon=horizon_title, trusted=False),
                    width="stretch")
                horizon_phrase = (f"{v.horizon.value} day(s)" if v.horizon.is_known
                                  else "the run's horizon (not recorded)")
                st.caption(
                    f"How to read this: the navy line is what actually happened; "
                    f"the red dotted line is what {first.name} had predicted "
                    f"{horizon_phrase} earlier for each date. "
                    f"If red mostly looks like navy slid sideways, the model is "
                    f"echoing the recent past instead of predicting. "
                    f"Status: {plain.say_gate(first)}.")

        st.subheader("Model families")
        cols = st.columns(2)
        for i, f in enumerate(run.families):
            # Three states, three badges. `gate_passed: null` is not a failure
            # and must never wear the same badge as a withheld family.
            if f.is_presentable:
                badge = "<span class='badge badge-ok'>GATE PASSED</span>"
            elif f.gate is Gate.WITHHELD:
                badge = "<span class='badge badge-bad'>WITHHELD</span>"
            elif f.gate is Gate.UNVERIFIED:
                badge = "<span class='badge badge-unknown'>NOT VERIFIED</span>"
            else:
                badge = "<span class='badge badge-bad'>NOT A CLEAN RESULT</span>"
            why = (f"<div class='why'>Why: {'; '.join(f.gate_reasons)}</div>"
                   if f.gate_reasons else "")
            if f.gate is Gate.UNVERIFIED:
                why = ("<div class='why'>The quality gate returned no verdict "
                       "for this family — unknown, not bad.</div>")
            display = (f.best_model_display.value
                       if f.best_model_display.is_known
                       else plain.say_best_model(f))
            cols[i % 2].markdown(
                f"<div class='fam-card'><span class='fam-name'>{f.name}</span> {badge}"
                f"<div class='fam-line'>Best: {display}</div>"
                f"<div class='fam-line'>Skill vs persistence: "
                f"{plain.say_skill(f)}</div>"
                f"<div class='fam-line'>{plain.say_status(f)} · "
                f"{plain.say_flag(f.leakage_flag, 'leakage')} · "
                f"{plain.say_flag(f.shift_flag, 'shift')}</div>{why}</div>",
                unsafe_allow_html=True)

        with st.expander("Interval coverage"):
            st.caption("Coverage says how often the true value landed inside "
                       "the predicted band. A family that produces no intervals "
                       "reports none — that is not a coverage of zero.")
            for name, cov in run.coverage.items():
                st.markdown(f"**{name}** — {plain.say_coverage(cov)}")

        with st.expander("Leaderboards (raw numbers, including baselines)"):
            for name, lb in run.leaderboards.items():
                st.markdown(f"**{name}**")
                if not lb.is_readable:
                    st.error("  \n".join(lb.fatal))
                    continue
                st.caption(
                    f"Schema: `{lb.schema}` · target "
                    f"{plain.say_value(lb.target, noun='target')} "
                    f"(from the {lb.target_source}) · reference baselines "
                    f"(not competitors): "
                    f"{', '.join(lb.baseline_models) or 'none detected'}")
                if lb.all_null_columns:
                    st.caption(f"Entirely empty column(s): "
                               f"{', '.join(lb.all_null_columns)} — not recorded, "
                               f"not zero.")
                st.dataframe(lb.frame.drop(columns=["model_key"]),
                             width="stretch", hide_index=True)
        with st.expander("Raw SUMMARY.txt from the lab"):
            st.code(run.summary_text or "(missing)", language="text")


    if view == "Ask the agent":
        # What the LAB can forecast, from its champion-recipe registry — not
        # what the last run happened to forecast. Deriving this menu from the
        # run's `data_file` (absent on every committed artifact) is why the app
        # offered Revenues alone while the lab has recipes for three targets.
        menu = LB.target_choices(run)
        targets = menu.targets

        # The manual run panel stays in the main column, above the transcript,
        # collapsed. Not the sidebar: pressing Run streams an st.status trace
        # that renders here regardless, and a control whose output appears in a
        # different region is a worse trade than one extra click.
        with st.expander("Run a new forecast (the agent drives the lab)",
                         expanded=False):
            c1, c2, c3, c4 = st.columns([2, 1, 2, 1])
            sel_target = c1.selectbox(
                "Target", targets or ["(no target recorded)"],
                help=(("These are the targets the lab has a champion recipe "
                       "for. " + menu.note).strip() if menu.enumerated
                      else menu.note))
            sel_h = c2.number_input("Horizon (days)", 1, 60, 5)
            sel_fams = c3.multiselect("Model families", LB.KNOWN_FAMILIES, default=["A_STAT"],
                                      help="A_STAT is fast (~seconds). B_ML / C_DL / "
                                           "E_QUANTILE can take much longer.")
            if c4.button("Run", type="primary", disabled=not sel_fams):
                with st.spinner(f"Lab is running {', '.join(sel_fams)} for {sel_target}, "
                                f"h={sel_h} — all integrity checks included…"):
                    ok, log = LB.run_lab(LB.repo_root(run.run_dir), sel_target,
                                         int(sel_h), sel_fams)
                st.session_state["last_run_log"] = log
                if ok:
                    st.success("Lab run finished — reloading gated results.")
                    st.rerun()
                else:
                    st.error("Lab run failed — see log below.")
            if menu.enumerated:
                st.caption(
                    f"Official targets (the lab holds a champion recipe for "
                    f"each): {', '.join(menu.official)}. "
                    f"{menu.note}".strip())
            elif menu.note:
                st.caption(menu.note)
            if st.session_state.get("last_run_log"):
                with st.expander("Last run log"):
                    st.code(st.session_state["last_run_log"][-6000:], language="text")

        # New actuals. Deliberately its own control rather than a chat
        # affordance: a file is a file, and asking someone to type a path into
        # a chat box when their export is sitting in Downloads is a worse
        # interface than the one every browser already gives them.
        #
        # Uploading validates and ASKS. It never installs, scores or runs — the
        # three-step confirmation in `_data_leg` owns all of that.
        with st.expander("New actuals have arrived (upload a CSV)",
                         expanded=False):
            st.caption(
                "I check the file before anything happens: that its columns "
                "match the lab's, that it extends past the current data, that "
                "it still contains the existing history, and that it is not the "
                "same file the last issue was built from. Then I tell you what "
                "is in it and wait.")
            _up = st.file_uploader("Daily series CSV", type=["csv"],
                                   key="actuals_upload")
            if _up is not None:
                _sig = f"{getattr(_up, 'name', '?')}:{len(_up.getvalue())}"
                if st.session_state.get("last_upload_sig") != _sig:
                    st.session_state["last_upload_sig"] = _sig
                    _staged = stage_upload(_up)
                    _text, _remember = data_confirmation(
                        LB.repo_root(run.run_dir), _staged)
                    if _remember:
                        st.session_state[PENDING_DATA] = _remember
                    push("assistant", _text,
                         f"[agent] ACTION tier: upload `{_staged}` validated; "
                         f"{'awaiting confirmation' if _remember else 'rejected'}"
                         f" — nothing installed, scored or run")
                    st.rerun()
            if not RX.lab_writes_allowed():
                st.caption(
                    f"Writes into the lab's real tree are currently off, so I "
                    f"will validate and score but not install or publish. Set "
                    f"`{RX.LAB_WRITES_ENV}=1` when the lab is clear.")

        st.caption("Ask about this run, or ask me to run a new one. I can "
                   "compare model families, explain results and definitions, "
                   "and every answer shows its trace.")
        if "chat" not in st.session_state:
            st.session_state.chat = load_saved_chat()
        for m in st.session_state.chat:
            render_turn(m)

        # Top level, so Streamlit pins it to the bottom of the viewport.
        if q := st.chat_input("e.g. compare all models for Revenues · why was "
                              "B_ML withheld? · what does skill mean?"):
            history = list(st.session_state.chat)     # before this turn
            push("user", q)
            with st.chat_message("user"):
                st.markdown(q)

            repo_for_action = LB.repo_root(run.run_dir)
            # The ACTION tier runs first and can end the turn. Everything
            # below is the answering path, unchanged — a question that
            # reached the official answer before this tier existed still
            # reaches it, because `action_turn` returns False for it.
            if not action_turn(q, repo_for_action, targets):
                decision = decide_action(q, run, targets, history=history)
                trace_lines = [f"[agent] understood request as: {decision['why']}"]

                # Both branches below end the same way: render the turn live, then
                # `push` exactly one assistant message holding what was rendered.
                # No `st.rerun()` — the transcript above has already been drawn
                # this pass, so a rerun would redraw the whole conversation and
                # replay the streamed answer as a jump-cut. The turn is on screen
                # once, and it is on screen from the transcript on every pass after.
                if decision["action"] == "run_lab":
                    tgt, hz, fams = decision["target"], decision["horizon"], decision["families"]
                    ack = (decision.get("reply") or
                           f"On it — running **{', '.join(fams)}** for **{tgt}**, "
                           f"horizon **{hz}** days, through the lab with all "
                           f"integrity checks. You can watch the trace below."
                           + (" Deep learning (C_DL) is included — this can take "
                              "several minutes." if "C_DL" in fams else ""))
                    with st.chat_message("assistant"):
                        st.markdown(ack)
                        with st.status("Lab is working…", expanded=True) as status:
                            trace_lines.append(f"[agent] launching lab: families="
                                               f"{fams}, target={tgt}, horizon={hz}")
                            rc = 1
                            for kind, payload in LB.run_lab_stream(
                                    LB.repo_root(run.run_dir), tgt, hz, fams):
                                if kind == "line":
                                    trace_lines.append(payload)
                                    if "=== Running" in payload:
                                        status.update(label=payload.split("===")[1].strip())
                                    if ("Skill" in payload or "gate" in payload.lower()
                                            or "Running" in payload or "DONE" in payload):
                                        st.text(payload)
                                else:
                                    rc = payload
                            trace_lines.append(f"[agent] lab finished with code {rc}; "
                                               f"reading gated SUMMARY.json")
                            status.update(label="Lab finished — reading gated summary",
                                          state="complete" if rc == 0 else "error")

                        new_run = LB.load_latest() if rc == 0 else None
                        if rc != 0:
                            msg = ("The lab run failed before producing results — "
                                   "the trace below has the full log.")
                            st.markdown(msg)
                        elif new_run is None:
                            msg = ("The run finished but I couldn't read the "
                                   "summary back.")
                            st.markdown(msg)
                        else:
                            # The factual narrative is built from contract-read
                            # values first; the model only ever rewrites that text.
                            msg = run_result_narrative(new_run)
                            stream = run_narrative_stream(q, new_run, targets, msg)
                            written = ""
                            if stream is not None:
                                trace_lines.append("[agent] narrating the gated "
                                                   "summary (streamed)")
                                written = str(st.write_stream(stream) or "")
                            if written.strip():
                                msg = written
                            else:   # no backend, or a stream that produced nothing
                                st.markdown(msg)
                        trace = "\n".join(trace_lines)
                        with st.expander("Agent trace"):
                            st.code(trace, language="text")
                    st.session_state["last_run_log"] = trace
                    # `run` is now stale — the next interaction reloads it. The
                    # chat view does not read it again in this pass.
                    push("assistant", f"{ack}\n\n{msg}", trace)
                else:
                    with st.chat_message("assistant"):
                        ans = decision.get("reply")
                        if ans:
                            st.markdown(ans)
                        else:
                            # A question about a named target's official status is
                            # answered from the published issue and the registry
                            # FIRST, and the model may only rewrite that answer.
                            #
                            # This ordering is the whole point. `answer_stream`
                            # is grounded in one backtest run, so letting it take
                            # these questions put the wrong series' verdicts in
                            # front of the user whenever a backend was reachable —
                            # and reachable is the demo case. The rule-based path
                            # used to be the fallback for "no LLM"; for these
                            # questions it is now the source, and the LLM is the
                            # optional rephrasing on top.
                            factual = answer_official_question(q, run)
                            if factual is not None:
                                trace_lines.append(
                                    "[agent] official answer from the published "
                                    "issue + registry (artifact-sourced)")
                                stream = official_narrative_stream(q, run, targets,
                                                                   factual)
                                if stream is not None:
                                    trace_lines.append("[agent] model rephrased it; "
                                                       "figures are fixed")
                                    ans = str(st.write_stream(stream) or "")
                                if not (ans or "").strip():
                                    ans = factual
                                    st.markdown(ans)
                            else:
                                stream = answer_stream(q, run, targets, history)
                                if stream is not None:
                                    trace_lines.append("[agent] grounded LLM answer "
                                                       "(streamed)")
                                    ans = str(st.write_stream(stream) or "")
                                if not (ans or "").strip():
                                    # No backend, or a stream that produced nothing.
                                    ans = answer_lab_question(q, run)
                                    trace_lines.append("[agent] rule-based answer")
                                    st.markdown(ans)
                        trace = "\n".join(trace_lines)
                        with st.expander("Agent trace"):
                            st.code(trace, language="text")
                    push("assistant", ans, trace)


    if view == "Run history":
        st.subheader("Past runs on this machine")
        st.caption("Each dated folder under the lab's forecast_runs is one "
                   "run. Re-running on the same date replaces that folder.")
        runs_list = LB.list_runs(LB.repo_root(run.run_dir))
        if not runs_list:
            st.info("No past runs found.")
        else:
            def _cell(value) -> str:
                return f"{value.value}" if value.is_known else "not recorded"

            hist_rows = []
            for rd, sv in runs_list:
                if not sv.is_readable:
                    hist_rows.append({
                        "Run date": rd.name, "Target": "unreadable",
                        "Horizon": "—",
                        "Families": "; ".join(sv.fatal),
                        "Gate passed": "—", "Folder": str(rd)})
                    continue
                hist_rows.append({
                    "Run date": (str(sv.run_date.value) if sv.run_date.is_known
                                 else rd.name),
                    "Target": _cell(sv.target),
                    "Horizon": _cell(sv.horizon),
                    "Families": ", ".join(f.name for f in sv.families),
                    "Gate passed": (
                        f"{_cell(sv.overall.get('families_gate_passed'))}"
                        f"/{_cell(sv.overall.get('families_requested'))}"),
                    "Folder": str(rd),
                })
            st.dataframe(pd.DataFrame(hist_rows), width="stretch", hide_index=True)
            st.caption("The Dashboard always shows the most recent run. To "
                       "revisit an older one, its SUMMARY.txt and artifacts "
                       "are in the folder listed above.")

    if view == "Learn":
        st.subheader("How to read the results")
        for key, title in (("gate", "The quality gate"),
                           ("skill", "Skill vs persistence"),
                           ("leakage", "Leakage checks"),
                           ("shift", "Shift flags"),
                           ("stale", "Stale data"),
                           ("unknown", "“Not recorded”")):
            st.markdown(f"**{title}.** {LB.EXPLANATIONS[key]}")
        st.subheader("What competes")
        st.markdown(plain.say_model_framing_long(run.view))
        st.subheader("Model families")
        # Read from the run, not from a list typed in here. The old version
        # rendered agent/lab_bridge.FAMILY_LABELS straight to the page, which
        # was wrong twice over: it named C_DL on runs that never ran C_DL, and
        # it went quietly incomplete when the Lab added a fifth family. No
        # empty-list branch: contract.py treats absent-or-empty `families` as
        # fatal, so a run that records none never loads and never reaches here.
        for fam in (f.name for f in run.view.families):
            st.markdown(f"**{fam}** — "
                        f"{LB.FAMILY_LABELS.get(fam, 'not described here')}")
        st.caption("These are the families this run recorded, read from its own "
                   "artifacts. The lab can also run exploratory families that "
                   "write no daily forecast. Those do not appear here, because "
                   "this run did not use them.")
        st.subheader("Where things live")
        input_note = (f"Input data: the lab's processed master file "
                      f"(`{run.view.data_file.value}`)."
                      if run.view.data_file.is_known else
                      "Input data: the lab's processed master file — this run "
                      "does not record its name, so I can't show it.")
        st.markdown(
            f"{input_note} Every run's artifacts are "
            f"saved under `{LB.repo_root(run.run_dir) / 'backend' / 'forecast_runs'}` "
            f"on this machine; nothing leaves your computer.")

# ────────────────────────────── DEMO MODE ──────────────────────────────
else:
    st.title("AI4CM Agent — Demo mode")
    st.markdown("<span class='badge badge-demo'>DEMO — uncalibrated sample "
                "data, not Treasury results</span>", unsafe_allow_html=True)
    from agent import tools as T   # lazy: statsmodels + scikit-learn live here
    demo = Path("datasets/demo_daily.csv")
    df, num_cols = T.load_dataset(demo)
    target = st.selectbox("Target", num_cols, index=0)
    horizon = st.slider("Horizon (days)", 3, 30, 7)
    if st.button("Run demo forecast", type="primary"):
        res = T.forecast(df, "date", target, horizon=horizon, method="auto")
        naive = float(np.mean(np.abs(np.diff(res["history"].iloc[-90:].values))))
        st.plotly_chart(T.plot_forecast(res["history"].iloc[-120:], res["forecast"],
                        f"{target} — {res['method'].upper()} (demo)"),
                        width="stretch")
        st.caption(f"Demo caveat: method chosen automatically, no quality gate, "
                   f"no leakage checks. For context, a naive day-over-day move "
                   f"on this series averages ±{fmt_money(naive)}.")
