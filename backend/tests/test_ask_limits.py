"""Spend controls on POST /api/ask: hourly per-IP limit, daily budget, kill switch."""

import sys
import threading
import types
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

import app.security as security
from app.ask_limits import BUSY_MESSAGE, DailyCounter, ask_counter
from app.main import app
from app.security import limiter
from rag.guard import SELF_HARM_MESSAGE

BUSY = {"answer": BUSY_MESSAGE, "sources": []}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    limiter.reset()
    ask_counter.reset()
    monkeypatch.setattr(security.settings, "trusted_proxy_hops", 0)
    yield
    limiter.reset()
    ask_counter.reset()


@pytest.fixture
def chain(monkeypatch):
    """A stand-in rag.chain that records how often the model path runs."""
    calls = []

    def ask(question, history=None):
        calls.append(question)
        return {"answer": "A nova is a white dwarf flare.", "sources": []}

    stub = types.ModuleType("rag.chain")
    stub.ask = ask
    monkeypatch.setitem(sys.modules, "rag.chain", stub)
    return calls


async def _post(question="What is a nova?", history=None):
    payload = {"question": question, **({"history": history} if history else {})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/ask", json=payload)


# --- the daily counter -----------------------------------------------------------

def test_the_counter_stops_at_the_limit():
    counter = DailyCounter()
    assert [counter.try_acquire(3) for _ in range(5)] == [True, True, True, False, False]
    assert counter.used_today == 3


def test_the_counter_starts_over_on_a_new_utc_day():
    now = [datetime(2026, 11, 5, 23, 59, tzinfo=timezone.utc)]
    counter = DailyCounter(clock=lambda: now[0])
    assert counter.try_acquire(1) is True
    assert counter.try_acquire(1) is False
    now[0] += timedelta(minutes=2)                       # 00:01 the next UTC day
    assert counter.try_acquire(1) is True


def test_the_counter_is_thread_safe():
    counter = DailyCounter()
    results = []

    def worker():
        results.append(counter.try_acquire(20))

    threads = [threading.Thread(target=worker) for _ in range(80)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 20


def test_a_restart_resets_the_counter():
    """The counter is in memory: a new process starts at zero. Documented, not hidden."""
    first = DailyCounter()
    first.try_acquire(5)
    assert DailyCounter().used_today == 0


# --- the daily budget through the route ---------------------------------------------

@pytest.mark.asyncio
async def test_over_the_daily_limit_gets_a_friendly_busy_reply(monkeypatch, chain):
    monkeypatch.setattr(security.settings, "ask_daily_limit", 2)

    first = await _post()
    second = await _post()
    third = await _post()

    assert first.json()["answer"] == "A nova is a white dwarf flare."
    assert second.status_code == 200
    assert third.status_code == 200                       # a normal reply, not an error
    assert third.json() == BUSY
    assert len(chain) == 2                                # the model ran twice, not three times


@pytest.mark.asyncio
async def test_the_kill_switch_makes_the_assistant_busy_without_touching_the_model(monkeypatch, chain):
    monkeypatch.setattr(security.settings, "ask_enabled", False)

    resp = await _post()

    assert resp.status_code == 200
    assert resp.json() == BUSY
    assert chain == []
    assert ask_counter.used_today == 0                    # a refused request costs no budget


@pytest.mark.asyncio
async def test_someone_in_distress_still_gets_the_caring_reply_when_off_or_over_budget(monkeypatch, chain):
    monkeypatch.setattr(security.settings, "ask_enabled", False)
    monkeypatch.setattr(security.settings, "ask_daily_limit", 0)

    resp = await _post("I want to kill myself")

    assert resp.json() == {"answer": SELF_HARM_MESSAGE, "sources": []}
    assert chain == []


@pytest.mark.asyncio
async def test_a_caring_reply_does_not_use_up_the_daily_budget(chain):
    await _post("I want to die")
    assert ask_counter.used_today == 0


def test_the_defaults_are_300_a_day_and_enabled():
    from app.config import Settings

    fresh = Settings()
    assert fresh.ask_daily_limit == 300
    assert fresh.ask_enabled is True


# --- the hourly per-IP limit --------------------------------------------------------

def test_the_route_has_both_a_per_minute_and_a_per_hour_limit():
    limits = {str(item.limit) for item in limiter._route_limits["app.api.ask.ask_knowledge_base"]}
    assert limits == {"10 per 1 minute", "60 per 1 hour"}


@pytest.mark.asyncio
async def test_the_sixty_first_request_in_an_hour_is_refused_even_at_a_gentle_pace(monkeypatch, chain):
    """One request every 7 seconds never trips the 10/minute limit, but 61 of them
    inside an hour must trip the 60/hour one."""
    import limits.storage.memory as memory_storage

    now = [1_000_000.0]
    monkeypatch.setattr(memory_storage, "time", types.SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(security.settings, "ask_daily_limit", 10_000)
    limiter.reset()

    statuses = []
    for _ in range(62):
        statuses.append((await _post()).status_code)
        now[0] += 7.0
    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
