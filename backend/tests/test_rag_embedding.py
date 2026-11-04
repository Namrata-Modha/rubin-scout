"""Embedding pacing, 429 handling and the embed-once rebuild, with no network."""

from types import SimpleNamespace

import pytest

from rag import pacing
from rag.pacing import RateLimiter, call_with_retry, retry_delay_seconds


class FakeClock:
    """A clock that only moves when the code sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RateLimitError(Exception):
    """Shaped like google.genai.errors.ClientError for a 429."""

    def __init__(self, delay="21s"):
        self.code = 429
        self.details = {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": f"Quota exceeded. Please retry in {delay}.",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                             "retryDelay": delay}],
            }
        }
        super().__init__(f"429 RESOURCE_EXHAUSTED. {self.details}")


# --- pacing ------------------------------------------------------------------

def test_the_limiter_never_exceeds_the_rate_in_any_sixty_second_window():
    clock = FakeClock()
    limiter = RateLimiter(100, clock=clock.time, sleep=clock.sleep)
    stamps = []
    for _ in range(350):
        limiter.wait()
        stamps.append(clock.now)
    for i, start in enumerate(stamps):
        # Half-open 60 s window; the epsilon absorbs float error in 0.6 s steps.
        in_window = sum(1 for t in stamps[i:] if t < start + 60.0 - 1e-6)
        assert in_window <= 100
    # And it is not needlessly slow: 350 calls at 100/min take about 3.5 minutes.
    assert 205 <= stamps[-1] <= 215


def test_the_default_rate_leaves_headroom_under_the_free_tier_limit():
    assert pacing.DEFAULT_MAX_PER_MINUTE < 100


# --- 429 handling ------------------------------------------------------------

def test_retry_delay_is_read_from_the_error_details_or_message():
    assert retry_delay_seconds(RateLimitError("21s")) == 21.0
    assert retry_delay_seconds(RateLimitError("21.5s")) == 21.5
    assert retry_delay_seconds(Exception("Quota exceeded. Please retry in 7.2s.")) == 7.2
    assert retry_delay_seconds(Exception("boom")) is None


def test_a_429_waits_at_least_the_requested_delay_and_then_succeeds():
    clock = FakeClock()
    limiter = RateLimiter(90, clock=clock.time, sleep=clock.sleep)
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) <= 3:
            raise RateLimitError("21s")
        return "vector"

    assert call_with_retry(fn, limiter, clock.sleep) == "vector"
    assert len(attempts) == 4
    waits = [s for s in clock.sleeps if s >= 21]
    assert len(waits) == 3 and all(w >= 21 for w in waits)


def test_a_429_is_retried_far_longer_than_three_times():
    """The old code gave up after three tries within seconds."""
    clock = FakeClock()
    limiter = RateLimiter(90, clock=clock.time, sleep=clock.sleep)
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 6:
            raise RateLimitError("21s")
        return "ok"

    assert call_with_retry(fn, limiter, clock.sleep) == "ok"


def test_a_persistent_429_eventually_raises():
    clock = FakeClock()
    limiter = RateLimiter(90, clock=clock.time, sleep=clock.sleep)
    calls = []

    def fn():
        calls.append(1)
        raise RateLimitError("5s")

    with pytest.raises(RateLimitError):
        call_with_retry(fn, limiter, clock.sleep)
    assert len(calls) == pacing.MAX_RATE_LIMIT_ATTEMPTS


def test_other_errors_get_a_short_retry_then_raise():
    clock = FakeClock()
    limiter = RateLimiter(90, clock=clock.time, sleep=clock.sleep)
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        call_with_retry(fn, limiter, clock.sleep)
    assert len(calls) == pacing.MAX_OTHER_ATTEMPTS


# --- GeminiEmbeddings: one request per chunk ------------------------------------

def test_gemini_embeddings_makes_one_request_per_text_and_retries_a_429():
    pytest.importorskip("google.genai")
    pytest.importorskip("langchain_core")
    from rag.embeddings import GeminiEmbeddings

    clock = FakeClock()
    requests = []

    def embed_content(model, contents, config):
        requests.append(contents)
        if len(requests) == 2:  # the second request is rate limited once
            raise RateLimitError("10s")
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0])])

    emb = GeminiEmbeddings.__new__(GeminiEmbeddings)
    emb._client = SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    emb._limiter = RateLimiter(90, clock=clock.time, sleep=clock.sleep)
    emb._sleep = clock.sleep

    out = emb.embed_documents(["a", "b", "c"])
    assert len(out) == 3
    assert out[0] == pytest.approx([0.6, 0.8])           # L2-normalised
    assert requests == ["a", "b", "b", "c"]              # only "b" was repeated
    assert any(s >= 10 for s in clock.sleeps)


# --- the rebuild: embed once, abort before deleting ---------------------------

def _chunks(n):
    return [SimpleNamespace(page_content=f"chunk {i}", metadata={"source": "x", "i": i})
            for i in range(n)]


class FakeEmbeddings:
    def __init__(self, fail=False):
        self.calls: list[list[str]] = []
        self.fail = fail

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RateLimitError("21s")
        return [[float(i)] for i, _ in enumerate(texts)]


class FakeStore:
    calls: list[dict] = []

    @classmethod
    def from_embeddings(cls, **kwargs):
        cls.calls.append(kwargs)

    @classmethod
    def from_documents(cls, **kwargs):  # must never be used: it embeds again
        raise AssertionError("from_documents embeds a second time")


@pytest.fixture
def builder():
    pytest.importorskip("langchain_postgres")
    pytest.importorskip("langchain_text_splitters")
    pytest.importorskip("google.genai")
    pytest.importorskip("langchain.schema")
    from rag import build_knowledge_base

    FakeStore.calls = []
    return build_knowledge_base


def test_every_chunk_is_embedded_exactly_once(builder):
    chunks, emb = _chunks(75), FakeEmbeddings()
    builder.rebuild(chunks, emb, "postgresql+psycopg://x/y", store_cls=FakeStore)

    assert len(emb.calls) == 1                       # one embed_documents pass
    assert emb.calls[0] == [c.page_content for c in chunks]
    assert len(FakeStore.calls) == 1


def test_the_precomputed_vectors_are_passed_to_the_store(builder):
    chunks, emb = _chunks(3), FakeEmbeddings()
    builder.rebuild(chunks, emb, "postgresql+psycopg://x/y", store_cls=FakeStore)

    kwargs = FakeStore.calls[0]
    assert kwargs["text_embeddings"] == [("chunk 0", [0.0]), ("chunk 1", [1.0]), ("chunk 2", [2.0])]
    assert kwargs["metadatas"] == [c.metadata for c in chunks]
    assert kwargs["pre_delete_collection"] is True
    assert kwargs["collection_name"] == builder.COLLECTION_NAME
    assert kwargs["connection"] == "postgresql+psycopg://x/y"


def test_an_embedding_failure_raises_before_the_store_is_touched(builder):
    emb = FakeEmbeddings(fail=True)
    with pytest.raises(RateLimitError):
        builder.rebuild(_chunks(5), emb, "postgresql+psycopg://x/y", store_cls=FakeStore)
    assert FakeStore.calls == []                     # nothing deleted or written


def test_a_short_vector_list_refuses_to_touch_the_collection(builder):
    class ShortEmbeddings(FakeEmbeddings):
        def embed_documents(self, texts):
            return super().embed_documents(texts)[:-1]

    with pytest.raises(RuntimeError, match="refusing to touch"):
        builder.rebuild(_chunks(4), ShortEmbeddings(), "postgresql+psycopg://x/y",
                        store_cls=FakeStore)
    assert FakeStore.calls == []
