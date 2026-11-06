"""The chat model's daily quota: fail fast, remember it, answer kindly."""

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_google_genai")
pytest.importorskip("langchain_postgres")

from google.api_core.exceptions import ResourceExhausted  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402
from app.security import limiter  # noqa: E402
from rag import chain  # noqa: E402
from rag.guard import SELF_HARM_MESSAGE  # noqa: E402

QUOTA_REPLY = {
    "answer": "The assistant has reached its daily limit for questions. Please try again tomorrow.",
    "sources": [],
}


BUSY_REPLY = {
    "answer": "The assistant is busy right now. Please try again in a minute.",
    "sources": [],
}


def daily_quota_error(delay="7h20m12s"):
    return ResourceExhausted(
        f"429 You exceeded your current quota. limit: 20, model: gemini. Please retry in {delay}."
    )


class FakeLLM:
    """Raises (or answers) per call and counts every call attempt."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, prompt_value):
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(content=outcome)


class FakeStore:
    def similarity_search(self, query, k=4):
        return [SimpleNamespace(page_content="A nova is a flare.", metadata={"source": "x.md"})]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    chain._reset_quota_memory()
    limiter.reset()
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)       # tenacity sleeps through this
    yield sleeps
    chain._reset_quota_memory()
    limiter.reset()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(chain, "_clock", lambda: now[0])
    return now


def use(monkeypatch, llm, store=None):
    monkeypatch.setattr(
        chain, "_get_components", lambda: {"llm": llm, "vectorstore": store or FakeStore()}
    )


# --- how the model is built ---------------------------------------------------------

def test_the_chat_model_is_built_with_one_attempt_and_a_30_second_timeout(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(chain, "_components", {})
    monkeypatch.setattr(chain, "PGVector", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(chain, "GeminiEmbeddings", lambda *a, **k: object())

    llm = chain._get_components()["llm"]

    assert llm.max_retries == 1
    assert llm.timeout == 30


# --- fail fast ----------------------------------------------------------------------

def test_a_quota_error_makes_one_attempt_no_long_sleep_and_a_friendly_reply(monkeypatch, _clean):
    llm = FakeLLM(daily_quota_error())
    use(monkeypatch, llm)

    assert chain.ask("What is a nova?") == QUOTA_REPLY

    assert llm.calls == 1                       # not retried by _invoke_llm
    assert all(s <= 10 for s in _clean)         # and never a long wait
    assert _clean == []


def test_a_quota_error_in_the_condense_step_is_also_one_attempt(monkeypatch):
    llm = FakeLLM(daily_quota_error())
    use(monkeypatch, llm)
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    assert chain.ask("what is a nova?", history=history) == QUOTA_REPLY
    assert llm.calls == 1


def test_a_429_from_the_embedding_service_gets_the_same_reply(monkeypatch):
    class Embedding429(Exception):
        code = 429

    class ExhaustedStore:
        def similarity_search(self, query, k=4):
            raise Embedding429("429 RESOURCE_EXHAUSTED. Please retry in 5s")

    use(monkeypatch, FakeLLM("never used"), ExhaustedStore())
    assert chain.ask("What is a nova?") == BUSY_REPLY      # "retry in 5s": short, so busy


def test_other_errors_are_still_retried_briefly_and_then_raised_as_themselves(monkeypatch, _clean):
    llm = FakeLLM(ValueError("boom"))
    use(monkeypatch, llm)

    with pytest.raises(ValueError):             # the real error, not a tenacity RetryError
        chain.ask("What is a nova?")
    assert llm.calls == 3
    assert all(s <= 10 for s in _clean)


def test_a_transient_error_then_success_still_answers(monkeypatch):
    llm = FakeLLM(ValueError("blip"), "A nova is a flare.")
    use(monkeypatch, llm)
    assert chain.ask("What is a nova?")["answer"] == "A nova is a flare."
    assert llm.calls == 2


# --- remembering the exhaustion -------------------------------------------------------

def test_a_long_retry_delay_short_circuits_the_next_question(monkeypatch, clock):
    llm = FakeLLM(daily_quota_error("7h20m"))
    use(monkeypatch, llm)

    chain.ask("first question")
    assert llm.calls == 1

    # Now the model must not be touched at all, not even to build the components.
    def boom():
        raise AssertionError("the model path must not run while unavailable")

    monkeypatch.setattr(chain, "_get_components", boom)
    assert chain.ask("second question") == QUOTA_REPLY
    assert llm.calls == 1


def test_the_memory_expires_and_normal_questions_work_again(monkeypatch, clock):
    llm = FakeLLM(daily_quota_error("1h"), "A nova is a flare.")
    use(monkeypatch, llm)

    chain.ask("first")                                   # quota error, remembered for 1 h
    clock[0] += 1800
    assert chain.ask("still blocked") == QUOTA_REPLY
    assert llm.calls == 1

    clock[0] += 1801                                     # past the hour
    assert chain.ask("What is a nova?")["answer"] == "A nova is a flare."
    assert llm.calls == 2


def test_the_hold_is_capped_at_twelve_hours(monkeypatch, clock):
    use(monkeypatch, FakeLLM(daily_quota_error("100h")))
    chain.ask("first")
    assert chain._unavailable_until - clock[0] == 12 * 3600


def test_a_short_delay_is_not_remembered(monkeypatch, clock):
    llm = FakeLLM(daily_quota_error("21s"), "A nova is a flare.")
    use(monkeypatch, llm)

    assert chain.ask("first") == BUSY_REPLY              # this question still gets a message
    assert chain.ask("What is a nova?")["answer"] == "A nova is a flare."   # the next tries again
    assert llm.calls == 2


def test_an_error_with_no_stated_delay_is_not_remembered(monkeypatch, clock):
    llm = FakeLLM(ResourceExhausted("429 quota exceeded"), "A nova is a flare.")
    use(monkeypatch, llm)

    assert chain.ask("first") == BUSY_REPLY
    assert chain.ask("What is a nova?")["answer"] == "A nova is a flare."


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("Please retry in 21s.", 21),
        ("Please retry in 21.5s", 21.5),
        ("Please retry in 7h20m12s.", 7 * 3600 + 20 * 60 + 12),
        ("Please retry in 7h20m", 7 * 3600 + 20 * 60),
        ("Please retry in 1m5.2s", 65.2),
        ("Please retry in 26416.9s", 26416.9),
        ("Please retry in 500ms", 0.5),
        ("Please retry in 819.7ms.", 0.8197),
        ("Please retry in 1m500ms", 60.5),
        ("Please retry in 2h5m", 2 * 3600 + 300),
    ],
)
def test_the_retry_delay_is_read_from_the_error_message(text, seconds):
    assert chain._retry_delay_seconds(Exception(f"429 quota. {text}")) == pytest.approx(seconds)


def test_the_retry_delay_is_read_from_structured_details():
    delay = SimpleNamespace(seconds=26400, nanos=500_000_000)
    err = ResourceExhausted("quota", details=[SimpleNamespace(retry_delay=delay)])
    assert chain._retry_delay_seconds(err) == pytest.approx(26400.5)


def test_structured_delay_with_zero_seconds_and_only_nanos():
    delay = SimpleNamespace(seconds=0, nanos=819_700_000)
    err = ResourceExhausted("quota", details=[SimpleNamespace(retry_delay=delay)])
    assert chain._retry_delay_seconds(err) == pytest.approx(0.8197)


@pytest.mark.parametrize("delay", ["500ms", "819.7ms", "1m500ms"])
def test_a_short_delay_in_any_unit_is_never_remembered(monkeypatch, clock, delay):
    llm = FakeLLM(daily_quota_error(delay), "A nova is a flare.")
    use(monkeypatch, llm)

    assert chain.ask("first") == BUSY_REPLY
    assert chain._unavailable_until == 0.0
    assert chain.ask("What is a nova?")["answer"] == "A nova is a flare."
    assert llm.calls == 2


def test_the_two_messages_differ_by_delay(monkeypatch, clock):
    use(monkeypatch, FakeLLM(daily_quota_error("30s")))
    short = chain.ask("q")
    chain._reset_quota_memory()
    use(monkeypatch, FakeLLM(daily_quota_error("7h")))
    long = chain.ask("q")

    assert short == BUSY_REPLY and long == QUOTA_REPLY
    assert short["answer"] != long["answer"]


# --- the safety check still comes first ------------------------------------------------

def test_the_self_harm_message_still_wins_while_the_model_is_unavailable(monkeypatch, clock):
    use(monkeypatch, FakeLLM(daily_quota_error("7h")))
    chain.ask("first")                                   # now unavailable

    reply = chain.ask("I want to kill myself")

    assert reply == {"answer": SELF_HARM_MESSAGE, "sources": []}


# --- through the HTTP route -------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_route_returns_200_with_the_busy_reply_for_a_short_delay(monkeypatch):
    use(monkeypatch, FakeLLM(daily_quota_error("500ms")))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/ask", json={"question": "What is a nova?"})

    assert resp.status_code == 200
    assert resp.json() == BUSY_REPLY


@pytest.mark.asyncio
async def test_the_route_returns_200_with_the_friendly_reply(monkeypatch):
    use(monkeypatch, FakeLLM(daily_quota_error()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/ask", json={"question": "What is a nova?"})

    assert resp.status_code == 200
    assert resp.json() == QUOTA_REPLY
