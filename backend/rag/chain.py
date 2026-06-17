"""Rubin Scout RAG retrieval chain.

Connects to the existing PGVector collection "rubin_scout_kb" and exposes
ask(question) -> {"answer": str, "sources": list[dict]}.

Required env vars:
    DATABASE_URL   — psycopg3-compatible DSN (postgresql+psycopg://... or
                     postgresql+asyncpg://... — normalised automatically)
    GEMINI_API_KEY — Google Gemini API key

CLI usage (from backend/):
    python -m rag.chain "what is a kilonova?"
"""

import os
import sys

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_postgres import PGVector

from rag.embeddings import GeminiEmbeddings

COLLECTION_NAME = "rubin_scout_kb"

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are the Rubin Scout knowledge assistant. Answer the user's \
question using ONLY the context passages provided below. Do not use any \
outside knowledge.

Rules:
- After each factual claim, cite the source in parentheses using the label \
shown in brackets at the start of each passage, e.g. (science-guide.md) or \
(cosmos.js — KN).
- If the context does not contain enough information to answer the question, \
say exactly: "I don't have information about that."
- Do not speculate or extrapolate beyond the provided context.

Context:
{context}""",
        ),
        ("human", "{question}"),
    ]
)


def _format_docs(docs) -> str:
    parts = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        class_key = doc.metadata.get("class_key")
        label = f"{source} — {class_key}" if class_key else source
        parts.append(f"[{label}]\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


def _build_chain():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set")

    connection = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")

    vectorstore = PGVector(
        embeddings=GeminiEmbeddings(),
        collection_name=COLLECTION_NAME,
        connection=connection,
        use_jsonb=True,
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

    llm = ChatGoogleGenerativeAI(
        model="gemini-3.5-flash",
        google_api_key=os.environ.get("GEMINI_API_KEY"),
    )

    # Retrieve docs once, fan out to both the answer chain and source passthrough
    retrieve = RunnableParallel(
        docs=retriever,
        question=RunnablePassthrough(),
    )

    answer_chain = (
        {
            "context": lambda x: _format_docs(x["docs"]),
            "question": lambda x: x["question"],
        }
        | _PROMPT
        | llm
        | StrOutputParser()
    )

    full_chain = retrieve | RunnableParallel(
        answer=answer_chain,
        docs=lambda x: x["docs"],
    )

    return full_chain


def ask(question: str) -> dict:
    """Return {"answer": str, "sources": list[dict]}."""
    chain = _build_chain()
    result = chain.invoke(question)
    sources = [
        {"source": doc.metadata.get("source", "unknown"), "metadata": doc.metadata}
        for doc in result["docs"]
    ]
    return {"answer": result["answer"], "sources": sources}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m rag.chain \"your question here\"", file=sys.stderr)
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(f"Question: {question}\n")

    response = ask(question)

    print("Answer:")
    print(response["answer"])
    print("\nSources retrieved:")
    for s in response["sources"]:
        print(f"  - {s['source']}", end="")
        if "class_key" in s["metadata"]:
            print(f" (class: {s['metadata']['class_key']})", end="")
        print()
