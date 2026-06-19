"""POST /api/ask — RAG knowledge base Q&A endpoint."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.security import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ask"])

# Keep in sync with HISTORY_LIMIT in frontend/src/components/AskWidget.jsx
HISTORY_LIMIT = 6

# Keep in sync with MAX_USER_CONTENT / MAX_ASSISTANT_CONTENT in AskWidget.jsx
MAX_USER_CONTENT = 500
MAX_ASSISTANT_CONTENT = 2000


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def check_content_length(self) -> "ChatTurn":
        limit = MAX_USER_CONTENT if self.role == "user" else MAX_ASSISTANT_CONTENT
        if len(self.content) > limit:
            raise ValueError(
                f"content must be at most {limit} characters for role '{self.role}'"
            )
        return self


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_USER_CONTENT)
    history: list[ChatTurn] = Field(default_factory=list, max_length=HISTORY_LIMIT)

    @model_validator(mode="after")
    def check_alternation(self) -> "AskRequest":
        """Drop non-alternating turns (orphaned user turns) rather than rejecting,
        so a misbehaving client degrades gracefully instead of getting a 422."""
        cleaned: list[ChatTurn] = []
        for turn in self.history:
            if cleaned and cleaned[-1].role == turn.role:
                # Same role as the previous turn — drop the earlier one so the
                # more recent turn survives (keeps the latest user question if
                # two user turns appear consecutively).
                cleaned.pop()
            cleaned.append(turn)
        # Must end in an assistant turn (last turn before the new question)
        if cleaned and cleaned[-1].role == "user":
            cleaned.pop()
        self.history = cleaned
        return self


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
