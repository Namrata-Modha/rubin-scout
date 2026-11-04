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
    ) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set")
        self._client = genai.Client(api_key=api_key)
        # One limiter per instance, shared by every call, so the whole build
        # (and any retries) stays under the free tier's ~100 requests/minute.
        self._limiter = limiter or RateLimiter()
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

        return call_with_retry(request, self._limiter, self._sleep)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t, "RETRIEVAL_DOCUMENT") for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text, "RETRIEVAL_QUERY")
