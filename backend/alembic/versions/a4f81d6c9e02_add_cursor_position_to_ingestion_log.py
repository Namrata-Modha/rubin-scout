"""add_cursor_position_to_ingestion_log

Separates the LSST ingestion cursor from `completed_at`.

`lsst_service` stored its resume point in `IngestionLog.completed_at`
(`log.completed_at = window_stop_dt`) and read it back the same way in
`_get_window_start`. Every other source writes that column with its literal
meaning -- when the run finished -- so one column carried two different
meanings depending on the row's `source`. The consequence was not
theoretical: 577 of 578 `fink_lsst` rows have `completed_at < started_at`
(minimum delta -23:30:00), and a cross-source duration query returned
nonsense for LSST while looking perfectly correct.

`cursor_position` now holds the resume point. `completed_at` goes back to
meaning one thing everywhere.

BACKFILL IS REQUIRED, NOT COSMETIC. `_get_window_start` reads
`cursor_position` after this change. Leaving it NULL on existing rows would
make the next LSST run find no prior cursor and silently fall back to
DEFAULT_LOOKBACK_HOURS, discarding the real resume point. The backfill is
exact rather than reconstructed: `query_params->>'window_stop'` was already
written alongside every cursor and holds the identical value, so this
copies a recorded fact rather than inferring one.

Historical `completed_at` values on those rows are deliberately LEFT ALONE.
They hold a cursor rather than a completion time, but the true completion
time was never recorded anywhere, so overwriting them would invent data.
They stay wrong-but-original; only rows written after this migration carry
a trustworthy `completed_at`. Nulling them out instead is a defensible
alternative -- NULL would at least mean "unknown" rather than a misleading
value, and would keep them out of duration aggregates -- but that is a
judgement call about historical data, so it is not made here.

Revision ID: a4f81d6c9e02
Revises: e5a71c2f4d38
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a4f81d6c9e02'
down_revision: Union[str, None] = 'e5a71c2f4d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ingestion_log',
        sa.Column('cursor_position', sa.DateTime(timezone=True), nullable=True),
    )

    # Recover each completed LSST run's cursor from the window_stop it
    # recorded in query_params at the time. Scoped to the only source that
    # ever wrote a cursor, and to "completed" because that is the only status
    # whose cursor _get_window_start ever consulted -- partial and failed runs
    # deliberately do not advance it.
    op.execute(
        """
        UPDATE ingestion_log
           SET cursor_position = (query_params->>'window_stop')::timestamptz
         WHERE source = 'fink_lsst'
           AND status = 'completed'
           AND query_params ? 'window_stop'
           AND cursor_position IS NULL
        """
    )


def downgrade() -> None:
    # The cursor lives on in query_params.window_stop, and the pre-change
    # _get_window_start reads completed_at, which was never modified -- so
    # dropping this column restores the old behaviour without data loss.
    op.drop_column('ingestion_log', 'cursor_position')
