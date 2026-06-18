"""POST /api/ask — RAG knowledge base Q&A endpoint."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.security import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ask"])

# Keep in sync with HISTORY_LIMIT in frontend/src/components/AskWidget.jsx
HISTORY_LIMIT = 6


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=500)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=HISTORY_LIMIT)


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/ask", response_model=AskResponse)
@limiter.limit("10/minute")
def ask_knowledge_base(request: Request, body: AskRequest):
    """Answer a question using the Rubin Scout RAG knowledge base."""
    from rag.chain import ask

    history = [{"role": t.role, "content": t.content} for t in body.history]

    try:
        result = ask(body.question, history=history)
    except Exception as exc:
        logger.error("RAG chain error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Knowledge base query failed. Please try again.")
    return AskResponse(answer=result["answer"], sources=result["sources"])
