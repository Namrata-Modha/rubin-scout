"""Build the Rubin Scout RAG knowledge base.

Reads source documents, extracts transient class descriptions from the
frontend cosmos.js catalogue, chunks everything, and upserts into PGVector
under the collection "rubin_scout_kb".

Usage (from backend/):
    python -m rag.build_knowledge_base

Required env vars:
    DATABASE_URL   — sync psycopg3 DSN, e.g. postgresql+psycopg://...
    GEMINI_API_KEY — Google Gemini API key
"""

import os
import sys
from pathlib import Path

from langchain.schema import Document
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.class_info import ClassInfoError, class_info_texts, parse_class_info
from rag.embeddings import GeminiEmbeddings

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE_DOCS = [
    REPO_ROOT / "docs" / "science-guide.md",
    REPO_ROOT / "docs" / "architecture.md",
]

COSMOS_JS = REPO_ROOT / "frontend" / "src" / "lib" / "cosmos.js"

COLLECTION_NAME = "rubin_scout_kb"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def _extract_class_info_docs() -> list[Document]:
    """One Document per CLASS_INFO entry in cosmos.js.

    Raises ClassInfoError (aborting the build before the existing collection
    is touched) if the file cannot be read, a class has no description, or the
    parsed class count disagrees with the entries in the file.
    """
    class_info = parse_class_info(COSMOS_JS.read_text(encoding="utf-8"))
    return [
        Document(page_content=text, metadata={"source": "cosmos.js", "class_key": key})
        for key, text in class_info_texts(class_info)
    ]


def _load_markdown_docs() -> list[Document]:
    docs = []
    for path in SOURCE_DOCS:
        if not path.exists():
            print(f"WARNING: {path} not found — skipping", file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8")
        docs.append(Document(page_content=text, metadata={"source": path.name}))
    return docs


def rebuild(chunks, embeddings, connection: str, store_cls=PGVector) -> None:
    """Embed every chunk exactly once, then replace the collection.

    The embeddings are computed first and handed to ``from_embeddings`` so the
    library does not embed a second time. If any embedding fails, this raises
    before the old collection is touched.

    Note the remaining window: PGVector deletes the old collection, creates
    the new one and inserts the rows in three separate commits, so a database
    error during the insert would leave the collection empty until the build is
    re-run (it is safe to re-run; it always rebuilds from scratch).
    """
    texts = [c.page_content for c in chunks]
    print(f"Embedding {len(texts)} chunks once each (paced to the API rate limit)...")
    vectors = embeddings.embed_documents(texts)
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"embedding returned {len(vectors)} vectors for {len(texts)} chunks; "
            "refusing to touch the existing collection"
        )
    print(f"All {len(vectors)} embeddings generated, replacing the collection...")

    print(f"Upserting into PGVector collection '{COLLECTION_NAME}'...")
    store_cls.from_embeddings(
        text_embeddings=list(zip(texts, vectors)),
        embedding=embeddings,
        metadatas=[c.metadata for c in chunks],
        collection_name=COLLECTION_NAME,
        connection=connection,
        use_jsonb=True,
        pre_delete_collection=True,  # rebuild from scratch on each run
    )


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    # Normalise to psycopg3 scheme — langchain-postgres requires it.
    connection = (
        db_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgres://", "postgresql+psycopg://")
    )
    if connection.startswith("postgresql://"):
        connection = "postgresql+psycopg://" + connection[len("postgresql://"):]

    print("Loading source documents...")
    try:
        raw_docs = _load_markdown_docs() + _extract_class_info_docs()
    except ClassInfoError as exc:
        print(f"ERROR: cannot build the knowledge base: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(raw_docs)} source document(s) loaded")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(raw_docs)
    print(f"  {len(chunks)} chunks after splitting")

    print("Initialising embeddings (Gemini gemini-embedding-001 @ 768d)...")
    embeddings = GeminiEmbeddings(patient=True)

    rebuild(chunks, embeddings, connection)

    # Verify: count rows in the collection
    from sqlalchemy import create_engine, text

    engine = create_engine(connection)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT COUNT(*), COUNT(embedding)
                FROM langchain_pg_embedding e
                JOIN langchain_pg_collection c ON c.uuid = e.collection_id
                WHERE c.name = :collection_name
                """
            ),
            {"collection_name": COLLECTION_NAME},
        )
        total, non_null = result.fetchone()
    engine.dispose()

    print(f"\nDone. {total} rows inserted, {non_null} with non-null embeddings.")


if __name__ == "__main__":
    main()
