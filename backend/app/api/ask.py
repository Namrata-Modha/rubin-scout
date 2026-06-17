"""POST /api/ask — RAG knowledge base Q&A endpoint."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.security import limiter

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

    result = ask(body.question)
    return AskResponse(answer=result["answer"], sources=result["sources"])
