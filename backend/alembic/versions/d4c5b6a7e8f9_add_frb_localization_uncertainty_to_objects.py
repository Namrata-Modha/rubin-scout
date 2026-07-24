"""add_frb_localization_uncertainty_to_objects

Adds per-axis sky-localization uncertainty columns (degrees, 1-sigma) to the
objects table.  Populated for CHIME/FRB rows from the VizieR columns
e_RAJ2000 / e_DEJ2000 (catalog J/ApJS/257/59/table2).  NULL for all other
sources.

Revision ID: d4c5b6a7e8f9
Revises: e8f2a1b3c9d0
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd4c5b6a7e8f9'
down_revision: Union[str, None] = 'e8f2a1b3c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('objects', sa.Column('ra_err_deg', sa.Float(), nullable=True))
    op.add_column('objects', sa.Column('dec_err_deg', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('objects', 'dec_err_deg')
    op.drop_column('objects', 'ra_err_deg')
