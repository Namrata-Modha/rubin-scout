"""Gemini embedding wrapper compatible with LangChain's Embeddings interface.

Uses gemini-embedding-001 at 768 dimensions with L2 normalisation — required
when requesting non-default dimensionality from the Gemini API.
"""

import os
import time
from typing import Callable, List

import numpy as np
from google import genai
from google.genai import types
from langchain_core.embeddings import Embeddings

from rag.pacing import RateLimiter, call_with_retry

_MODEL = "gemini-embedding-001"
_DIMENSIONS = 768


def _l2_normalise(vec: List[float]) -> List[float]:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vec
    return (arr / norm).tolist()


class GeminiEmbeddings(Embeddings):
    """LangChain-compatible embeddings using the Google Gemini API."""

    def __init__(
        self,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        patient: bool = False,
    ) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set")
        self._client = genai.Client(api_key=api_key)
        # patient=True is for the offline knowledge-base build: it paces every
        # request under the free tier's ~100/minute and waits out 429s.
        # The default is for the live chat assistant, where a visitor is
        # waiting in a worker thread: no pacing, at most 3 attempts with a
        # 1-10 s backoff, and a 429's retry delay is never slept out.
        self._patient = patient
        self._limiter = limiter if limiter is not None else (RateLimiter() if patient else None)
        self._sleep = sleep

    def _embed(self, text: str, task_type: str) -> List[float]:
        def request() -> List[float]:
            response = self._client.models.embed_content(
                model=_MODEL,
                contents=text,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=_DIMENSIONS,
                ),
            )
            return _l2_normalise(response.embeddings[0].values)

        return call_with_retry(request, self._limiter, self._sleep, patient=self._patient)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t, "RETRIEVAL_DOCUMENT") for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text, "RETRIEVAL_QUERY")
