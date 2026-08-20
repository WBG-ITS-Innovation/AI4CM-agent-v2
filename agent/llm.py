# agent/llm.py — the narration backend. One chat call, buffered or streamed.
#
# Scope note: nothing in this file decides what is true. It moves text to and
# from a model; every honesty rule lives in app.py's system prompt and in
# agent/contract.py, which is why streaming can be added here without touching
# either — a streamed answer is the same answer, delivered in pieces.
from __future__ import annotations

import os
from typing import Iterator, Optional, Tuple

import certifi


def _patch_ssl():
    need = False
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        v = os.environ.get(k)
        if v and not os.path.exists(v):
            need = True
    if need or not os.environ.get("SSL_CERT_FILE"):
        ca = certifi.where()
        os.environ["SSL_CERT_FILE"] = ca
        os.environ["REQUESTS_CA_BUNDLE"] = ca


def have_llm() -> bool:
    return bool(os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))


def _client_and_model() -> Tuple[Optional[object], Optional[str]]:
    _patch_ssl()
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        )
        return client, os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        return OpenAI(), os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return None, None


def llm_healthcheck() -> Tuple[bool, str]:
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        return True, "AzureOpenAI"
    if os.getenv("OPENAI_API_KEY"):
        return True, "OpenAI"
    return False, "no key found"


def _complete(client, model, messages: list, temperature: float,
              json_mode: bool) -> Optional[str]:
    """One buffered completion. Returns the text, or None if the call failed."""
    kwargs = dict(model=model, messages=messages, temperature=temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        r = client.chat.completions.create(**kwargs)
    except Exception:
        if not json_mode:
            return None
        try:  # some deployments reject response_format — retry plain
            r = client.chat.completions.create(model=model, messages=messages,
                                               temperature=temperature)
        except Exception:
            return None
    text = (r.choices[0].message.content or "") if r.choices else ""
    return text.strip() or None


def _stream(client, model, messages: list, temperature: float) -> Iterator[str]:
    """Yield content deltas as the model produces them.

    Two failure modes are absorbed here rather than raised into the UI:
    a deployment that refuses `stream=True` (one buffered call is made
    instead and yielded whole), and a connection that drops mid-answer
    (whatever arrived is kept — the caller sees a short answer, not a
    traceback). A stream that yields nothing at all is the caller's signal
    to fall back to the rule-based answer.
    """
    try:
        events = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, stream=True)
    except Exception:
        text = _complete(client, model, messages, temperature, json_mode=False)
        if text:
            yield text
        return
    try:
        for event in events:
            # Azure emits a first event with an empty `choices` list (its
            # content filter's prompt annotation); indexing [0] on it raises.
            for choice in (getattr(event, "choices", None) or []):
                piece = getattr(getattr(choice, "delta", None), "content", None)
                if piece:
                    yield piece
    except Exception:
        return


def chat_llm(messages: list, json_mode: bool = False, temperature: float = 0.2,
             stream: bool = False):
    """The single chat entry point used by the agent.

    `stream=False` returns the whole reply as a string, or None.
    `stream=True` returns an *iterator of text chunks* suitable for
    `st.write_stream`, or None.

    None and an empty iterator are deliberately different: None means there
    is no backend configured at all, an empty iterator means the call was
    made and produced nothing. The caller falls back to its rule-based
    answer in both cases, so no answer is ever left blank.
    """
    if not have_llm():
        return None
    client, model = _client_and_model()
    if client is None:
        return None
    if stream:
        return _stream(client, model, messages, temperature)
    return _complete(client, model, messages, temperature, json_mode)
