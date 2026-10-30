"""add_alerts_live_detected_at_index

GET /api/alerts/live orders by detected_at DESC, and detected_at had no index.
Housekeeping rather than a fix: alerts_live holds about 2,000 rows, so nothing
is slow either way today. The index lets ORDER BY detected_at DESC LIMIT n
read the newest rows directly instead of sorting the whole table as it grows.

A plain btree with no DESC. Postgres scans a btree backward, so one ascending
index serves the descending ORDER BY.

Not CONCURRENTLY: at this size the lock lasts milliseconds, and CONCURRENTLY
cannot run inside Alembic's transaction without extra ceremony.

No existence guard, unlike the gw_events/ingestion_log backfills. alerts_live
is created by Alembic (f1e2d3c4b5a6), never by init.sql, so there is no
database that already has this table without also being on this chain.

Revision ID: d8b2f4a96c13
Revises: a4f81d6c9e02
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd8b2f4a96c13'
down_revision: Union[str, None] = 'a4f81d6c9e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_alerts_live_detected_at', 'alerts_live', ['detected_at'])


def downgrade() -> None:
    op.drop_index('ix_alerts_live_detected_at', table_name='alerts_live')
