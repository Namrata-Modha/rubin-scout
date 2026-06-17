"""POST /api/ask — RAG knowledge base Q&A endpoint."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.security import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/ask", response_model=AskResponse)
@limiter.limit("10/minute")
def ask_knowledge_base(request: Request, body: AskRequest):
    """Answer a question using the Rubin Scout RAG knowledge base."""
    from rag.chain import ask

    try:
        result = ask(body.question)
    except Exception as exc:
        logger.error("RAG chain error: %s", exc, exc_info=True)
        # Raise HTTPException so ExceptionMiddleware (inside CORSMiddleware) handles
        # it — a bare uncaught exception reaches ServerErrorMiddleware which sits
        # above CORSMiddleware and returns a response with no CORS headers.
        raise HTTPException(status_code=500, detail="Knowledge base query failed. Please try again.")
    return AskResponse(answer=result["answer"], sources=result["sources"])
