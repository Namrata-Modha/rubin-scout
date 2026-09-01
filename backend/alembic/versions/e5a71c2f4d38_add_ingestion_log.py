"""add_ingestion_log

Backfills `ingestion_log` into the Alembic chain, the last of the three
tables that `backend/sql/init.sql` creates but no migration ever did (the
other two, gw_events and gw_candidates, are handled by c3d9f0a15b27).

The gap was known: f1e2d3c4b5a6 records that "ingestion_log already exists
in the database (created outside of Alembic via init.sql / manual DDL). It
is NOT touched here," and deliberately omits it from that migration's
downgrade so an unrelated rollback could not drop it. That reasoning was
about not DROPPING a table it did not create; it left the creation gap
open, which is what this closes.

IDEMPOTENT BY DESIGN, for the same reason as c3d9f0a15b27: every existing
deployment already has this table from init.sql, so this must be a no-op
there rather than an error. It is created only if absent.

Unlike gw_candidates -- where production is missing an `oid` foreign key
that init.sql and models.py both declare -- production's `ingestion_log`
was inspected on 2026-08-27 and matches init.sql exactly: same columns,
types, nullability and defaults, a single `ingestion_log_pkey` primary key,
no foreign keys, and no other indexes. There is no drift to reconcile here,
so the definition below satisfies both.

Revision ID: e5a71c2f4d38
Revises: b7a3c9d1e5f2
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = 'e5a71c2f4d38'
down_revision: Union[str, None] = 'b7a3c9d1e5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if 'ingestion_log' in _existing_tables():
        return

    # The primary key is left unnamed so Postgres generates
    # `ingestion_log_pkey`, which is what init.sql produced and what
    # production carries.
    op.create_table(
        'ingestion_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('query_params', JSONB(), nullable=True),
        sa.Column('objects_ingested', sa.Integer(), server_default='0', nullable=True),
        sa.Column(
            'started_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), server_default='running', nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    # WARNING: destructive, and it cannot tell who created the table. Nothing
    # records whether upgrade() actually created ingestion_log or found it
    # already there from init.sql, so on an existing deployment this drops a
    # data-bearing table that this migration never made. The existence check
    # only keeps the downgrade from erroring when the table is already gone.
    # Reversibility is kept for fresh, Alembic-built databases; on a deployed
    # one, back up first.
    if 'ingestion_log' in _existing_tables():
        op.drop_table('ingestion_log')
