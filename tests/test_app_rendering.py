# tests/test_app_rendering.py — execute the dashboard, don't just import it.
#
# Every rule in agent/contract.py can be right while the UI still prints
# "Input data: `None`", because the failure lives in an f-string that no test
# ever evaluates. Streamlit is not installed in this environment, so this
# module supplies recording stubs for it (and for the heavy modelling deps
# app.py pulls in), runs app.py top to bottom against each fixture, and then
# asserts over everything the page actually rendered.
#
# What it catches: KeyError on a field the artifact omits, a TypeError from
# formatting an absent value, and any UNKNOWN that reaches the page as a
# number, a zero, or the word "None".
from __future__ import annotations

import ast
import importlib
import json
import re
import sys
import types
from pathlib import Path

import pytest

#: Any count attached to a kind of model. See tests/test_model_framing.py.
_COUNT_OF_MODELS = re.compile(
    r"\b\d+\s+(machine[- ]learning|deep[- ]learning|statistical|quantile|"
    r"reference)\b", re.IGNORECASE)


def _write_registry(run_dir: Path, targets: list[str]) -> None:
    """Give the fixture lab a champion-recipe registry.

    `run_dir` is `<repo>/backend/forecast_runs/<date>`, so the registry lands
    where `lab_bridge.official_targets` looks for it: `<repo>/registry/`.
    """
    repo = run_dir.parent.parent.parent
    path = repo / "registry" / "recipes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "recipes": [{"id": f"{t.lower().replace(' ', '')}-v1", "target": t,
                     "point_model": "LightGBM_L1",
                     "status": "candidate -- pre-tuning"} for t in targets],
    }, indent=2), encoding="utf-8")


# ─────────────────────────── recording stubs ───────────────────────────

class _Recorder:
    """Collects every string the page renders, in order.

    `view` is the sidebar navigation choice this execution stands for; the
    st.radio stub returns it. See `render` for why there is one.
    """

    def __init__(self, view: str | None = None) -> None:
        self.texts: list[str] = []
        self.view = view

    def add(self, *args) -> None:
        for a in args:
            if isinstance(a, str):
                self.texts.append(a)

    @property
    def page(self) -> str:
        return "\n".join(self.texts)


class _Ctx:
    """A stub that is also a context manager and a column/tab/expander."""

    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    # Anything rendered anywhere is recorded.
    def markdown(self, body="", **kw): self._rec.add(body)
    def caption(self, body="", **kw): self._rec.add(body)
    def title(self, body="", **kw): self._rec.add(body)
    def subheader(self, body="", **kw): self._rec.add(body)
    def header(self, body="", **kw): self._rec.add(body)
    def text(self, body="", **kw): self._rec.add(body)
    def code(self, body="", **kw): self._rec.add(body)
    def warning(self, body="", **kw): self._rec.add(body)
    def error(self, body="", **kw): self._rec.add(body)
    def info(self, body="", **kw): self._rec.add(body)
    def success(self, body="", **kw): self._rec.add(body)
    def write(self, body="", **kw): self._rec.add(body)

    def metric(self, label="", value="", **kw): self._rec.add(str(label), str(value))
    def dataframe(self, df=None, **kw): self._rec.add(str(getattr(df, "columns", "")))
    def plotly_chart(self, fig=None, **kw): self._rec.add(str(getattr(fig, "title", "")))

    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx(self._rec) for _ in range(n)]

    def tabs(self, names, **kw):
        self._rec.add(*names)
        return [_Ctx(self._rec) for _ in names]

    def radio(self, label, options, **kw):
        """The sidebar navigation. Returns the view this execution renders."""
        options = list(options)
        self._rec.add(label, *[str(o) for o in options])
        if self._rec.view in options:
            return self._rec.view
        return options[0] if options else None

    def write_stream(self, chunks, **kw):
        text = "".join(str(c) for c in chunks)
        self._rec.add(text)
        return text

    def expander(self, label="", **kw):
        self._rec.add(label)
        return _Ctx(self._rec)

    def container(self, **kw): return _Ctx(self._rec)
    def status(self, label="", **kw): self._rec.add(label); return _Ctx(self._rec)
    def spinner(self, label="", **kw): self._rec.add(label); return _Ctx(self._rec)
    def chat_message(self, role, **kw): return _Ctx(self._rec)

    def update(self, **kw): self._rec.add(str(kw.get("label", "")))

    # Inputs: the page must render without any interaction.
    def selectbox(self, label, options, **kw):
        self._rec.add(label, str(kw.get("help") or ""))
        return options[0] if options else None

    def multiselect(self, label, options, **kw):
        self._rec.add(label)
        return list(kw.get("default") or [])

    def number_input(self, label, lo=1, hi=60, val=5, **kw):
        self._rec.add(label)
        return val

    def slider(self, label, lo=1, hi=10, val=5, **kw):
        self._rec.add(label)
        return val

    def button(self, label, **kw): self._rec.add(label); return False
    def chat_input(self, placeholder="", **kw): self._rec.add(placeholder); return None

    def file_uploader(self, label, **kw):
        """No file is ever uploaded during a render test.

        Returning None is the honest stub: the page must render, and offer the
        control, without anything having been uploaded — which is also the
        state it is in for every user who has not yet chosen a file.
        """
        self._rec.add(label, str(kw.get("help") or ""))
        return None

    def download_button(self, label, data=b"", file_name="", **kw):
        """Records the offer, including the filename.

        The filename is recorded because it carries the issue date, and an
        issue-less `forecast.csv` is indistinguishable from every other
        issue's — see tests/test_action_report.py.
        """
        self._rec.add(label, str(file_name))
        return False

    def set_page_config(self, **kw): pass
    def rerun(self): pass


class _SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc

    def __setattr__(self, k, v):
        self[k] = v


def _streamlit_stub(rec: _Recorder) -> types.ModuleType:
    mod = types.ModuleType("streamlit")
    root = _Ctx(rec)
    for name in dir(_Ctx):
        if not name.startswith("_"):
            setattr(mod, name, getattr(root, name))
    mod.sidebar = _Ctx(rec)
    mod.session_state = _SessionState()
    return mod


def _plotly_stub() -> tuple[types.ModuleType, types.ModuleType]:
    class Figure:
        def __init__(self, *a, **kw): self.title = ""
        def add_scatter(self, **kw): return self
        def update_layout(self, **kw):
            self.title = kw.get("title", self.title)
            return self

    go = types.ModuleType("plotly.graph_objects")
    go.Figure = Figure
    plotly = types.ModuleType("plotly")
    plotly.graph_objects = go
    return plotly, go


def _tools_stub() -> types.ModuleType:
    """agent.tools drags in statsmodels and scikit-learn; lab mode never uses it."""
    mod = types.ModuleType("agent.tools")
    mod.load_dataset = lambda *a, **kw: (None, [])
    mod.forecast = lambda *a, **kw: {}
    mod.plot_forecast = lambda *a, **kw: None
    return mod


def _llm_stub() -> types.ModuleType:
    """No network, and the rule-based path is the one under test."""
    mod = types.ModuleType("agent.llm")
    mod.have_llm = lambda: False
    mod.chat_llm = lambda *a, **kw: None
    mod.summarize_with_llm = lambda *a, **kw: None
    return mod


#: The sidebar navigation, in app.py's order. `VIEWS[0]` is what an execution
#: renders when the harness does not ask for a particular view.
VIEWS = ("Ask the agent", "Dashboard", "Run history", "Learn")


def _run_app(run_dir: Path, monkeypatch, view: str | None = None):
    """Execute app.py against `run_dir`; return `(module, rendered page)`."""
    rec = _Recorder(view)
    plotly, go = _plotly_stub()
    monkeypatch.setitem(sys.modules, "streamlit", _streamlit_stub(rec))
    monkeypatch.setitem(sys.modules, "plotly", plotly)
    monkeypatch.setitem(sys.modules, "plotly.graph_objects", go)
    monkeypatch.setitem(sys.modules, "agent.tools", _tools_stub())
    monkeypatch.setitem(sys.modules, "agent.llm", _llm_stub())
    # forecast_runs/<date> -> the runs root is its parent.
    monkeypatch.setenv("AI4CM_RUNS_ROOT", str(run_dir.parent))
    monkeypatch.delenv("AI4CM_REPO", raising=False)
    # Keep the developer's own saved conversation out of the rendered page.
    monkeypatch.setenv("AI4CM_CHAT_HISTORY", str(run_dir.parent / "chat.json"))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    sys.modules.pop("app", None)
    return module, rec.page


def render(run_dir: Path, monkeypatch) -> str:
    """Everything app.py renders for `run_dir`, across all four views.

    The app was one `st.tabs` page, so a single execution rendered every
    section and one call could assert over all of it. Navigation is now a
    sidebar `st.radio` — the price of a `st.chat_input` that pins to the
    bottom of the viewport, which Streamlit only does outside containers —
    and exactly one view renders per execution. So the harness executes the
    app once per view and concatenates: same coverage, four passes.
    """
    return "\n".join(_run_app(run_dir, monkeypatch, view=v)[1] for v in VIEWS)


# ─────────────────────────── the page renders at all ───────────────────────────

def test_dashboard_renders_for_a_clean_run(clean_run, monkeypatch):
    page = render(clean_run, monkeypatch)
    assert "Champion forecast" in page
    assert "E_QUANTILE" in page


def test_dashboard_renders_for_an_incomplete_run(incomplete_run, monkeypatch):
    """No KeyError on the fields this artifact simply does not carry."""
    page = render(incomplete_run, monkeypatch)
    assert "Champion forecast" in page


def test_dashboard_renders_for_an_inconsistent_run(inconsistent_run, monkeypatch):
    page = render(inconsistent_run, monkeypatch)
    assert "Champion forecast" in page


def test_dashboard_renders_for_the_real_run(real_run, monkeypatch):
    page = render(real_run, monkeypatch)
    assert "Champion forecast" in page
    assert "E_QUANTILE" in page


# ─────────────────────── nothing absent reaches the page ───────────────────────

@pytest.mark.parametrize("fixture", ["clean_run", "incomplete_run",
                                     "inconsistent_run", "real_run"])
def test_no_absent_field_is_rendered_as_a_value(fixture, request, monkeypatch):
    page = render(request.getfixturevalue(fixture), monkeypatch)
    assert "`None`" not in page
    assert "Input data: None" not in page
    assert ": nan" not in page.lower()
    assert "n/a (not produced)%" not in page


@pytest.mark.parametrize("fixture", ["incomplete_run", "inconsistent_run"])
def test_unknown_counters_render_as_a_dash_not_zero(fixture, request, monkeypatch):
    """`.get('leakage_flags', 0)` used to invent a clean 0/0 here."""
    page = render(request.getfixturevalue(fixture), monkeypatch)
    assert "—" in page


def test_incomplete_run_says_its_input_file_is_unrecorded(incomplete_run, monkeypatch):
    page = render(incomplete_run, monkeypatch)
    assert "not recorded" in page


def test_legacy_run_says_its_input_file_is_unrecorded(legacy_run, monkeypatch):
    """The absent path, on a synthetic pre-`e8c26a2` artifact.

    This was `real_run` until the Lab started writing `data_file`. The page
    must still say "not recorded" for a run that does not carry one, and must
    still refuse to name a file it was not given.
    """
    page = render(legacy_run, monkeypatch)
    assert "not recorded" in page
    assert "does not record" in page


def test_the_real_runs_input_file_reaches_the_page(real_run, monkeypatch):
    """What the artifact records has to survive to the pixels.

    The name is read from the artifact rather than written here: hardcoding
    it would pass on a page that printed a stale filename.
    """
    recorded = json.loads((real_run / "SUMMARY.json").read_text())["data_file"]
    page = render(real_run, monkeypatch)
    assert recorded in page
    assert "Input data: not recorded" not in page


# ─────────────────────── the verdicts render as three states ───────────────────────

def test_unverified_family_gets_its_own_badge(incomplete_run, monkeypatch):
    page = render(incomplete_run, monkeypatch)
    assert "NOT VERIFIED" in page
    assert "returned no verdict" in page


def test_withheld_family_shows_its_reason(clean_run, monkeypatch):
    page = render(clean_run, monkeypatch)
    assert "WITHHELD" in page
    assert "no signal beyond shuffled targets" in page


def test_failed_quality_never_renders_as_gate_passed(inconsistent_run, monkeypatch):
    """A_STAT has gate_passed true and run_status FAILED_QUALITY."""
    page = render(inconsistent_run, monkeypatch)
    assert "NOT A CLEAN RESULT" in page
    assert "FAILED_QUALITY" in page


# ─────────────────────── the page never invents ───────────────────────

@pytest.mark.parametrize("fixture", ["clean_run", "incomplete_run",
                                     "inconsistent_run", "real_run"])
def test_no_invented_threshold_on_any_page(fixture, request, monkeypatch):
    page = render(request.getfixturevalue(fixture), monkeypatch)
    for invented in ("requires at least", "the lab requires", "barely beats"):
        assert invented not in page


@pytest.mark.parametrize("fixture", ["clean_run", "incomplete_run",
                                     "inconsistent_run", "legacy_run"])
def test_a_run_without_a_composition_says_so_on_the_page(fixture, request,
                                                         monkeypatch):
    """This used to assert a hardcoded sentence appeared on every page. It did
    appear — and it had been wrong for weeks. Then: no artifact recorded the
    composition, so every page had to say so rather than print a count.

    `real_run` was in this list until the Lab began writing `client_framing`;
    its place is taken by `legacy_run`, an artifact in the older shape, so the
    "says so and quotes nothing" path keeps a page-level test.
    """
    page = render(request.getfixturevalue(fixture), monkeypatch)
    assert "model composition is not recorded" in page
    assert not _COUNT_OF_MODELS.search(page), _COUNT_OF_MODELS.findall(page)


def test_a_run_with_a_composition_renders_it_verbatim(framed_run, monkeypatch):
    from conftest import LAB_DERIVED_FRAMING
    page = render(framed_run, monkeypatch)
    assert LAB_DERIVED_FRAMING in page
    assert "model composition is not recorded" not in page


def test_the_real_runs_composition_renders_verbatim(real_run, monkeypatch):
    """The counts on this page are the Lab's, read from the artifact.

    Which is why `_COUNT_OF_MODELS` is not asserted against here: a count is
    forbidden when the Agent would be its author, not when the artifact
    published it. The check that keeps that distinction honest is the source
    scan in tests/test_model_framing.py — no count may appear in agent/ or
    app.py at all.
    """
    recorded = json.loads(
        (real_run / "SUMMARY.json").read_text())["client_framing"]
    page = render(real_run, monkeypatch)
    assert recorded in page
    assert "model composition is not recorded" not in page


# ─────────────────────── the target menu ───────────────────────

def test_the_learn_tab_lists_the_families_the_run_recorded(clean_run, monkeypatch):
    """The family list is read, not typed in.

    It used to be a dict in agent/lab_bridge.py rendered straight to the page,
    which was wrong in both directions at once. It named C_DL on a run that
    never ran C_DL, and it went silently incomplete when the Lab added a fifth
    family. `clean_run` records three families, so the fix and the defect give
    different pages: this asserts the run's three are named and the fourth is
    not.
    """
    page = render(clean_run, monkeypatch)
    for family in ("A_STAT", "B_ML", "E_QUANTILE"):
        assert family in page, family
    assert "C_DL" not in page, (
        "C_DL is not in this run's artifacts, so no page may name it")
    assert "quantile models with uncertainty bands" in page, (
        "the label for a family the run DID record should still be shown")


def test_a_run_recording_no_families_never_reaches_the_page(clean_run, monkeypatch):
    """Why the family list needs no empty case, tested rather than asserted.

    Reading the list from the artifact raises the question of what an empty list
    renders as. The answer is that it cannot happen: contract.py counts
    absent-or-empty `families` as FATAL, so the run does not load and the app
    falls back to demo mode without ever reaching the Learn tab. That rule had no
    test of its own, so the code below depended on it while nothing checked it.
    """
    path = clean_run / "SUMMARY.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["families"] = []
    path.write_text(json.dumps(blob, indent=2), encoding="utf-8")

    # Imported here, not at module scope: this file stubs agent.tools and
    # agent.llm into sys.modules, and the top of it deliberately pulls in no
    # agent module at all.
    from agent import lab_bridge as LB
    from agent.contract import read_summary

    view = read_summary(clean_run)
    assert "`families` is absent or empty" in view.fatal
    assert LB.load_run(clean_run) is None

    page = render(clean_run, monkeypatch)
    assert "Model families" not in page
    assert "None" not in page


def test_every_official_target_is_offered(clean_run, monkeypatch):
    """The bug: the picker asked "what can the lab forecast?" and answered from
    `SUMMARY.json.data_file`, which the Lab's contract documents as absent on
    every committed artifact. One known-empty source, so the menu collapsed to
    the run's own target and the app offered Revenues alone."""
    _write_registry(clean_run, ["Revenues", "Expenditure", "State budget balance"])
    page = render(clean_run, monkeypatch)
    for target in ("Revenues", "Expenditure", "State budget balance"):
        assert target in page, f"{target} is in the registry but not on the page"
    assert "Official targets" in page


def test_a_run_target_outside_the_registry_is_offered_but_labelled(clean_run,
                                                                   monkeypatch):
    _write_registry(clean_run, ["Expenditure"])
    page = render(clean_run, monkeypatch)
    assert "Revenues" in page                       # the run's own target
    assert "no champion recipe in the registry" in page


def test_no_registry_falls_back_to_the_run_target_and_says_why(clean_run,
                                                               monkeypatch):
    page = render(clean_run, monkeypatch)           # no registry written
    assert "target registry was not found" in page
    assert "only this run's own target" in page


def test_coverage_is_explained_rather_than_implied(real_run, monkeypatch):
    page = render(real_run, monkeypatch)
    assert "not a coverage of zero" in page or "not reported" in page


def test_defects_are_surfaced_on_the_page(inconsistent_run, monkeypatch):
    page = render(inconsistent_run, monkeypatch)
    assert "depart" in page


def test_clean_run_shows_no_defect_panel(clean_run, monkeypatch):
    page = render(clean_run, monkeypatch)
    assert "artifacts depart from the lab's contract" not in page


# ─────────────────── the free-form path carries the same rules ───────────────────
#
# The five demo prompts go through `answer_lab_question`, which is rule-based
# and testable line by line. Anything else goes to the LLM with a system prompt
# and a JSON context, and the only thing standing between it and a confident
# fabrication is what those two say. So they are tested as artifacts in their
# own right: an honesty rule that exists only on the rule-based path is not a
# property of this agent, it is a property of five sentences.

def test_the_context_marks_an_absent_composition_as_unknown(clean_run, monkeypatch):
    app, _ = _run_app(clean_run, monkeypatch)
    ctx = json.loads(app.build_agent_context(app.run, ["Revenues"]))
    assert ctx["model_composition"].startswith("UNKNOWN")
    assert "state no model counts" in ctx["model_composition"]
    assert str(ctx["champion_eligible_pool"]).startswith("UNKNOWN")


def test_the_context_passes_a_recorded_composition_through(framed_run, monkeypatch):
    from conftest import LAB_DERIVED_FRAMING
    app, _ = _run_app(framed_run, monkeypatch)
    ctx = json.loads(app.build_agent_context(app.run, ["Revenues"]))
    assert ctx["model_composition"] == LAB_DERIVED_FRAMING
    assert ctx["champion_eligible_pool"] == 13


def test_the_context_never_renders_an_unknown_as_a_number(incomplete_run,
                                                          monkeypatch):
    """Everything the LLM sees is labelled. `skill_pct: null` reaching the model
    unlabelled is how "n/a (not produced)" became a percentage in prose."""
    app, _ = _run_app(incomplete_run, monkeypatch)
    ctx = json.loads(app.build_agent_context(app.run, ["Revenues"]))
    for fam in ctx["families"]:
        for key in ("skill_pct", "leakage_flag", "shift_flag"):
            value = fam[key]
            assert value is not None, f"{fam['name']}.{key} reached the LLM as null"
            if isinstance(value, str) and "UNKNOWN" not in value:
                assert value not in ("", "n/a", "n/a (not produced)"), key


@pytest.mark.parametrize("rule", [
    "Never state a threshold, cut-off or standard that is not in CONTEXT",
    "the lab requires at least",          # named, so it cannot be re-invented
    "ABSENCE IS NOT A VALUE",
    "The gate is TRI-STATE",
    "CONTEXT.model_composition",
])
def test_the_system_prompt_states_each_honesty_rule(rule, clean_run, monkeypatch):
    app, _ = _run_app(clean_run, monkeypatch)
    assert rule in app.AGENT_SYSTEM


def _functions_calling(source: str, callee: str) -> list[ast.FunctionDef]:
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == callee):
                out.append(node)
                break
    return out


def test_every_llm_call_is_given_the_system_prompt():
    """A free-form answer with no system prompt is an ungrounded answer.

    Checked per enclosing function rather than per call site, because
    `decide_action` builds its message list several statements before it calls
    `chat_llm` — a text window around the call sees only the variable name.
    """
    source = (Path(__file__).resolve().parent.parent / "app.py").read_text(
        encoding="utf-8")
    functions = _functions_calling(source, "chat_llm")
    assert functions, "no chat_llm call sites found — this test went blind"
    for fn in functions:
        assert "AGENT_SYSTEM" in ast.unparse(fn), (
            f"{fn.name}() calls chat_llm without AGENT_SYSTEM anywhere in it")
