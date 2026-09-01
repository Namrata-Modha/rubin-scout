"""add_gw_events_and_gw_candidates

Backfills the two gravitational-wave tables into the Alembic chain.

`gw_events` and `gw_candidates` were only ever created by
`backend/sql/init.sql` (mounted into the Postgres container by
docker-compose), never by a migration, so `alembic upgrade head` against an
empty database produced a schema with no GW tables at all -- and
b7a3c9d1e5f2, which adds `retired_at` to `gw_events`, would fail outright.
This migration closes that gap and is positioned immediately beneath it.

IDEMPOTENT BY DESIGN. Every existing deployment (production included, at
d4c5b6a7e8f9 when this was written) already has both tables from init.sql,
so this must be a no-op there rather than an error. Each table is created
only if absent.

The definitions below mirror PRODUCTION's real schema as inspected on
2026-08-27, which is what an existing database will actually have -- not
necessarily what init.sql or models.py declare. One difference is
deliberate and load-bearing:

    init.sql and models.py both declare gw_candidates.oid as
    REFERENCES objects(oid), but NO such foreign key exists in production;
    the only FK on that table is superevent_id -> gw_events. Reproducing
    init.sql here instead would make a freshly migrated database disagree
    with every deployed one. Adding that FK is a real schema CHANGE and
    belongs in its own migration that applies to both, not smuggled into
    the migration whose whole job is to reproduce the status quo.

Revision ID: c3d9f0a15b27
Revises: d4c5b6a7e8f9
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, REAL

from alembic import op

revision: str = 'c3d9f0a15b27'
down_revision: Union[str, None] = 'd4c5b6a7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    # Constraints are left unnamed so Postgres applies its own defaults --
    # gw_events_pkey, gw_candidates_pkey, gw_candidates_superevent_id_fkey,
    # gw_candidates_superevent_id_oid_key -- which is exactly what init.sql
    # produced and what production carries today.
    if 'gw_events' not in existing:
        op.create_table(
            'gw_events',
            sa.Column('superevent_id', sa.Text(), nullable=False),
            sa.Column('event_time', sa.DateTime(timezone=True), nullable=True),
            sa.Column('far', DOUBLE_PRECISION(), nullable=True),
            sa.Column('skymap_url', sa.Text(), nullable=True),
            sa.Column('classification', JSONB(), nullable=True),
            sa.Column('properties', JSONB(), nullable=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint('superevent_id'),
        )

    if 'gw_candidates' not in existing:
        op.create_table(
            'gw_candidates',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('superevent_id', sa.Text(), nullable=False),
            # No ForeignKeyConstraint onto objects.oid -- see module docstring.
            sa.Column('oid', sa.Text(), nullable=False),
            sa.Column('probability_in_skymap', REAL(), nullable=True),
            sa.Column('distance_to_peak_arcsec', REAL(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['superevent_id'], ['gw_events.superevent_id']),
            sa.UniqueConstraint('superevent_id', 'oid'),
        )


def downgrade() -> None:
    # Drop in FK order (gw_candidates references gw_events).
    #
    # WARNING: destructive, and it cannot tell who created these tables.
    # Nothing records whether upgrade() actually created them or found them
    # already there from init.sql, so on an existing deployment this drops
    # data-bearing tables that this migration never made. The existence
    # checks only keep the downgrade from erroring when they are already
    # gone. Reversibility is kept for fresh, Alembic-built databases; on a
    # deployed one, back up first.
    existing = _existing_tables()
    if 'gw_candidates' in existing:
        op.drop_table('gw_candidates')
    if 'gw_events' in existing:
        op.drop_table('gw_events')
