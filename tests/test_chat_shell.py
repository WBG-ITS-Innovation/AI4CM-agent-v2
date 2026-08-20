# tests/test_chat_shell.py — the chat shell: layout constraints, one message
# shape, and a streamed answer.
#
# Three things this file exists to hold still:
#
#   1. The layout constraint that decided the navigation. `st.chat_input` pins
#      itself to the bottom of the viewport ONLY when it is called from the
#      main body of the page — inside a tab, a column or an expander it
#      renders inline. That is not something a rendering test can observe
#      through a stub (a stub's `chat_input` is pinned nowhere), so it is
#      checked on the source: no `st.tabs`, and the `st.chat_input` call is
#      not nested inside any `with` block.
#
#   2. One message shape. The transcript used to hold two — {"role","content"}
#      from the run path and {"role","content","trace"} from the answer path —
#      so whether a turn could show its work depended on which branch produced
#      it. Every append now goes through `push`.
#
#   3. Streaming is a delivery change, not an honesty change. The streamed
#      call carries the same system prompt, and a backend that refuses to
#      stream still produces an answer rather than an empty bubble.
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

from test_app_rendering import _run_app

REPO = Path(__file__).resolve().parent.parent
APP_SOURCE = (REPO / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)


# ─────────────────────────── 1. layout constraints ───────────────────────────

def _calls_to(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def test_the_app_uses_no_tabs():
    """Tabs were the reason the chat input drifted down the page."""
    assert not _calls_to(APP_TREE, "tabs"), (
        "st.tabs is back; st.chat_input cannot pin to the viewport inside one")


def test_navigation_is_a_sidebar_radio():
    radios = _calls_to(APP_TREE, "radio")
    assert len(radios) == 1, "expected exactly one navigation control"


def _enclosing_with_blocks(tree: ast.AST, target: ast.AST) -> list[ast.With]:
    """Every `with` statement whose body contains `target`."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for stmt in node.body:
                if any(child is target for child in ast.walk(stmt)):
                    out.append(node)
    return out


def test_chat_input_is_called_at_the_top_level_of_the_page():
    """Nested in a container, Streamlit renders it inline instead of pinning it.

    `if` and `elif` are fine — they do not open a layout context. A `with`
    block is not: `with st.expander(...)`, `with st.chat_message(...)` and
    `with col1` all put the composer inside a box, halfway up the page.
    """
    calls = _calls_to(APP_TREE, "chat_input")
    assert len(calls) == 1, "expected exactly one chat_input"
    enclosing = _enclosing_with_blocks(APP_TREE, calls[0])
    assert not enclosing, (
        f"st.chat_input is inside {len(enclosing)} `with` block(s); it will "
        f"render inline rather than pinned to the bottom of the viewport")


def test_the_run_panel_is_a_collapsed_expander():
    """It shares the chat column, so it must not push the transcript down."""
    expanders = [c for c in _calls_to(APP_TREE, "expander")
                 if c.args and isinstance(c.args[0], ast.Constant)
                 and "Run a new forecast" in str(c.args[0].value)]
    assert len(expanders) == 1, "the run panel is not an st.expander"
    expanded = [kw.value for kw in expanders[0].keywords if kw.arg == "expanded"]
    assert expanded and expanded[0].value is False, (
        "the run panel must start collapsed")


# ─────────────────────────── 2. one message shape ───────────────────────────

def _function(name: str) -> ast.FunctionDef:
    for node in ast.walk(APP_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"app.py has no function {name}()")


def test_the_transcript_is_only_ever_appended_to_through_push():
    """Two shapes in one list is how `trace` became a coin flip."""
    appends = [c for c in _calls_to(APP_TREE, "append")
               if "session_state.chat" in ast.unparse(c.func)]
    assert appends, "no transcript appends found — this test went blind"
    inside_push = {id(c) for c in _calls_to(_function("push"), "append")}
    outside = [ast.unparse(c) for c in appends if id(c) not in inside_push]
    assert not outside, (
        f"these append to st.session_state.chat directly, bypassing push(): "
        f"{outside}")


def test_every_entry_carries_all_three_keys(clean_run, monkeypatch):
    app, _ = _run_app(clean_run, monkeypatch)
    for entry in (app.chat_entry("user", "hello"),
                  app.chat_entry("assistant", "hi", "traced")):
        assert set(entry) == {"role", "content", "trace"}


def test_a_saved_transcript_of_the_old_shape_is_normalised(clean_run, monkeypatch,
                                                           tmp_path):
    """Histories written before the shapes were unified must still load."""
    import json
    path = tmp_path / "chat.json"
    path.write_text(json.dumps([
        {"role": "user", "content": "why was B_ML withheld?"},
        {"role": "assistant", "content": "Because ..."},          # no trace
        {"role": "assistant", "content": "With one", "trace": "t"},
        ["assistant", "the legacy tuple shape"],                   # not a dict
        {"role": "assistant", "content": ""},                      # empty
    ]), encoding="utf-8")
    monkeypatch.setenv("AI4CM_CHAT_HISTORY", str(path))
    app, _ = _run_app(clean_run, monkeypatch)
    monkeypatch.setattr(app, "HIST_PATH", path)
    loaded = app.load_saved_chat()
    assert len(loaded) == 3
    assert all(set(m) == {"role", "content", "trace"} for m in loaded)
    assert loaded[0]["trace"] == ""


def test_a_missing_history_file_is_an_empty_transcript(clean_run, monkeypatch,
                                                       tmp_path):
    app, _ = _run_app(clean_run, monkeypatch)
    monkeypatch.setattr(app, "HIST_PATH", tmp_path / "nope.json")
    assert app.load_saved_chat() == []


# ─────────────────────── 3. streaming, and its fallbacks ───────────────────────

class _FakeChunk:
    def __init__(self, text=None, choices=None):
        if choices is not None:
            self.choices = choices
        else:
            delta = types.SimpleNamespace(content=text)
            self.choices = [types.SimpleNamespace(delta=delta)]


class _FakeCompletions:
    """Records what it was asked for, and replays a scripted response."""

    def __init__(self, chunks=None, refuse_stream=False, buffered="buffered"):
        self.chunks, self.refuse_stream, self.buffered = chunks, refuse_stream, buffered
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            if self.refuse_stream:
                raise RuntimeError("this deployment does not support streaming")
            return iter(self.chunks or [])
        message = types.SimpleNamespace(content=self.buffered)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _fake_client(completions) -> object:
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=completions))


@pytest.fixture
def llm(monkeypatch):
    """agent.llm with a key present and a scripted client."""
    import agent.llm as L
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    def install(completions):
        monkeypatch.setattr(L, "_client_and_model",
                            lambda: (_fake_client(completions), "test-model"))
        return L

    return install


def test_stream_yields_the_chunks_in_order(llm):
    completions = _FakeCompletions([_FakeChunk("Because "), _FakeChunk("the gate "),
                                    _FakeChunk("withheld it.")])
    L = llm(completions)
    assert "".join(L.chat_llm([{"role": "user", "content": "hi"}], stream=True)) \
        == "Because the gate withheld it."
    assert completions.calls[0]["stream"] is True


def test_an_empty_choices_event_does_not_crash_the_stream(llm):
    """Azure's first event carries the content-filter annotation and no choices."""
    completions = _FakeCompletions([_FakeChunk(choices=[]), _FakeChunk("answer"),
                                    _FakeChunk(None)])
    L = llm(completions)
    assert "".join(L.chat_llm([{"role": "user", "content": "hi"}], stream=True)) \
        == "answer"


def test_a_deployment_that_refuses_streaming_still_answers(llm):
    completions = _FakeCompletions(refuse_stream=True, buffered="the whole answer")
    L = llm(completions)
    assert "".join(L.chat_llm([{"role": "user", "content": "hi"}], stream=True)) \
        == "the whole answer"


def test_a_connection_that_drops_keeps_what_arrived(llm):
    def broken():
        yield _FakeChunk("half an ")
        raise ConnectionError("dropped")

    completions = _FakeCompletions(broken())
    L = llm(completions)
    assert "".join(L.chat_llm([{"role": "user", "content": "hi"}], stream=True)) \
        == "half an "


def test_no_backend_returns_none_not_an_empty_stream(monkeypatch):
    """None means 'no LLM configured'; an empty iterator means 'the call
    produced nothing'. The caller falls back to rules on both, but only the
    first is a configuration fact worth distinguishing."""
    import agent.llm as L
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert L.chat_llm([{"role": "user", "content": "hi"}], stream=True) is None
    assert L.chat_llm([{"role": "user", "content": "hi"}]) is None


def test_buffered_calls_are_unchanged_by_the_streaming_option(llm):
    completions = _FakeCompletions(buffered="  a buffered reply  ")
    L = llm(completions)
    assert L.chat_llm([{"role": "user", "content": "hi"}]) == "a buffered reply"
    assert "stream" not in completions.calls[0]


def test_the_streamed_answer_carries_the_system_prompt(clean_run, monkeypatch):
    """The honesty rules live in the prompt, so a second call that skips them
    would be a second agent with none of them."""
    app, _ = _run_app(clean_run, monkeypatch)
    sent: dict = {}

    def fake_chat_llm(messages, **kw):
        sent["messages"], sent["kw"] = messages, kw
        return iter(["ok"])

    monkeypatch.setattr(app, "chat_llm", fake_chat_llm)
    monkeypatch.setattr(app, "have_llm", lambda: True)
    stream = app.answer_stream("what's the forecast?", app.run, ["Revenues"])
    assert "".join(stream) == "ok"
    assert sent["kw"]["stream"] is True
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][0]["content"] == app.AGENT_SYSTEM
    assert "CONTEXT:" in sent["messages"][-1]["content"]


def test_the_router_does_not_write_the_answer_itself():
    """It routes; the answer is a separate streamed call. If the router starts
    composing replies again, the answer stops streaming and the two prompts
    drift apart."""
    router = ast.unparse(_function("decide_action"))
    assert "Do not write the answer here" in router
    assert "stream=True" not in router
