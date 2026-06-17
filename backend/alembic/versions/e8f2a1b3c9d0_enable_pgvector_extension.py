"""enable pgvector extension

Revision ID: e8f2a1b3c9d0
Revises: f1e2d3c4b5a6
Create Date: 2026-06-16 00:00:00.000000

"""
from alembic import op

revision = "e8f2a1b3c9d0"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Intentionally a no-op: dropping vector would cascade-delete all vector columns
    pass
