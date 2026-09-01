"""add_retired_at_to_gw_events

Adds soft-retirement columns to `gw_events` so a superevent_id that GWOSC has
stopped serving can be flagged without deleting the row.

Rows are never hard-deleted here: a retired row may carry locally computed
`gw_candidates` or skymap data that must survive, and the row itself is the
only record that the retired ID was ever published.

    retired_at     UTC timestamp when reconciliation first observed that GWOSC
                   no longer serves this superevent_id. NULL = still live.
    superseded_by  The superevent_id that replaced it, when — and ONLY when —
                   GWOSC's own version history documents the rename. NULL on a
                   retired row means "retired, successor unknown, a human still
                   needs to decide" — it is deliberately NOT a time-proximity
                   guess. See docs/gw-events-data-quality.md.

Revision ID: b7a3c9d1e5f2
Revises: c3d9f0a15b27
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b7a3c9d1e5f2'
down_revision: Union[str, None] = 'c3d9f0a15b27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column(
        'gw_events',
        sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'gw_events',
        sa.Column('superseded_by', sa.String(), nullable=True),
    )
    # Self-referential: the successor is itself a gw_events row. ON DELETE SET
    # NULL so deleting a successor never cascades into deleting history.
    op.create_foreign_key(
        'fk_gw_events_superseded_by',
        'gw_events', 'gw_events',
        ['superseded_by'], ['superevent_id'],
        ondelete='SET NULL',
    )
    # Partial index: retired rows are a small minority and the only thing ever
    # filtered on, so indexing the NULLs would be dead weight.
    op.create_index(
        'ix_gw_events_retired_at',
        'gw_events', ['retired_at'],
        postgresql_where=sa.text('retired_at IS NOT NULL'),
    )


def downgrade():
    op.drop_index('ix_gw_events_retired_at', table_name='gw_events')
    op.drop_constraint('fk_gw_events_superseded_by', 'gw_events', type_='foreignkey')
    op.drop_column('gw_events', 'superseded_by')
    op.drop_column('gw_events', 'retired_at')
