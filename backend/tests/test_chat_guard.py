"""Self-harm pre-check for the chat assistant: matching, wiring, and silence."""

import logging
import sys
import types

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.security import limiter
from rag import guard
from rag.guard import (
    SELF_HARM_MESSAGE,
    conversation_is_self_harm,
    guarded_response,
    is_self_harm,
)

EXPECTED_MESSAGE = (
    "I'm really sorry you're hurting. I'm an astronomy assistant, so I can't help with "
    "this here, but you deserve real support right now. If you're in Canada or the US, you "
    "can call or text 988 at any time to talk with someone. If you're in immediate danger, "
    "call 911 or your local emergency number. Anywhere else, findahelpline.com lists "
    "services by country. If you can, reach out to someone you trust and tell them how "
    "you're feeling."
)


def test_the_message_is_exactly_the_agreed_text():
    assert SELF_HARM_MESSAGE == EXPECTED_MESSAGE


# --- matching ------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "I want to kill myself",
        "i'm going to end my life tonight",
        "I dont want to live anymore",
        "I've been thinking about suicide",
        "i am suicidal",
        "I feel so suicidal",
        "I wish I was dead",
        "everyone would be better off without me",
        "I want to die",
        "I have the urge to cut myself",
        "I keep hurting myself",
        "I'm thinking of ending it all",
        "I want to take my own life",
        "i'm gonna hurt myself",
        "I’m planning to kill myself",
        "there's no reason to live",
        "ive decided to end myself",
        "I’m thinking about killing myself",
        "  I   WANT   TO   KILL   MYSELF  ",
    ],
)
def test_first_person_self_harm_intent_matches(text):
    assert is_self_harm(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "What is a supernova?",
        "supernova",
        "How does a supernova kill a star?",
        "What is stellar death?",
        "How do massive stars die?",
        "What kills a white dwarf?",
        "Does a black hole kill everything near it?",
        "Tidal disruption: the star is torn apart and killed",
        "I wonder when the Sun is going to die",
        "I want to know how stars die",
        "I want to understand stellar death",
        "Will the Earth die when the Sun becomes a red giant?",
        "What happens when a star runs out of fuel and dies?",
        "Why do some stars end their lives as supernovae?",
        "how does the universe end",
        "How can I safely observe the sun without hurting myself?",
        "I need to be careful not to hurt myself with the telescope",
        "I'm worried about hurting my eyes",
        "What is self-harm?",
        "How does a nova differ from a kilonova?",
    ],
)
def test_normal_astronomy_questions_do_not_match(text):
    assert is_self_harm(text) is False


def test_user_turns_in_the_history_are_checked_but_assistant_turns_are_not():
    history = [
        {"role": "user", "content": "I want to kill myself"},
        {"role": "assistant", "content": SELF_HARM_MESSAGE},
    ]
    assert conversation_is_self_harm("what is a nova?", history) is True

    only_assistant = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "I want to kill myself"},   # our own text
    ]
    assert conversation_is_self_harm("what is a nova?", only_assistant) is False


def test_guarded_response_has_the_normal_shape_and_none_for_ordinary_questions():
    assert guarded_response("I want to die") == {"answer": SELF_HARM_MESSAGE, "sources": []}
    assert guarded_response("what is a kilonova?", []) is None


# --- through the API route ---------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def forbidden_chain(monkeypatch):
    """A stand-in for rag.chain that fails the test if the route ever calls it."""
    calls = []

    def ask(question, history=None):
        calls.append(question)
        raise AssertionError("the model path must not run")

    stub = types.ModuleType("rag.chain")
    stub.ask = ask
    monkeypatch.setitem(sys.modules, "rag.chain", stub)
    return calls


async def _post(payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/ask", json=payload)


@pytest.mark.asyncio
async def test_a_match_returns_the_fixed_message_without_touching_the_model(forbidden_chain):
    resp = await _post({"question": "I want to kill myself"})

    assert resp.status_code == 200
    assert resp.json() == {"answer": EXPECTED_MESSAGE, "sources": []}
    assert forbidden_chain == []


@pytest.mark.asyncio
async def test_a_match_in_an_earlier_user_turn_also_short_circuits(forbidden_chain):
    resp = await _post({
        "question": "ok, what is a nova?",
        "history": [
            {"role": "user", "content": "I'm thinking of ending it all"},
            {"role": "assistant", "content": "I don't have information about that."},
        ],
    })

    assert resp.json()["answer"] == EXPECTED_MESSAGE
    assert forbidden_chain == []


@pytest.mark.asyncio
async def test_a_match_logs_nothing_about_the_message(forbidden_chain, caplog):
    caplog.set_level(logging.DEBUG)
    await _post({"question": "I want to kill myself"})

    for record in caplog.records:
        text = record.getMessage().lower()
        assert "kill myself" not in text
        assert "kill" not in text
        assert not record.name.startswith(("app.api.ask", "rag"))


@pytest.mark.asyncio
async def test_an_ordinary_question_still_reaches_the_assistant(monkeypatch):
    seen = []

    def ask(question, history=None):
        seen.append(question)
        return {"answer": "A nova is a white dwarf flare.", "sources": []}

    stub = types.ModuleType("rag.chain")
    stub.ask = ask
    monkeypatch.setitem(sys.modules, "rag.chain", stub)

    resp = await _post({"question": "What is a nova?"})

    assert resp.status_code == 200
    assert resp.json()["answer"] == "A nova is a white dwarf flare."
    assert seen == ["What is a nova?"]


def test_the_guard_module_has_no_logging_and_no_third_party_imports():
    import ast

    tree = ast.parse(open(guard.__file__, encoding="utf-8").read())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= {"re", "typing"}
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "print" not in called


# --- rag.chain (needs the RAG stack; runs in CI and in the rag venv) ----------------

def test_ask_returns_the_message_before_building_any_component(monkeypatch):
    pytest.importorskip("langchain_core")
    pytest.importorskip("langchain_google_genai")
    pytest.importorskip("langchain_postgres")
    from rag import chain

    def boom():
        raise AssertionError("no embedding or model component may be built")

    monkeypatch.setattr(chain, "_get_components", boom)
    assert chain.ask("I want to end my life") == {"answer": SELF_HARM_MESSAGE, "sources": []}


def test_the_answer_prompt_carries_the_same_rule_with_the_exact_message():
    pytest.importorskip("langchain_core")
    pytest.importorskip("langchain_google_genai")
    pytest.importorskip("langchain_postgres")
    from rag import chain

    rendered = chain._ANSWER_PROMPT.invoke(
        {"context": "[x]\nsome passage", "history": [], "question": "hi"}
    ).to_messages()[0].content
    assert "thoughts of suicide or self-harm" in rendered
    assert SELF_HARM_MESSAGE in rendered
