"""add_dispersion_measure_to_objects

Revision ID: c7f3b9e21a04
Revises: a9802bcd9ef8
Create Date: 2026-05-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c7f3b9e21a04'
down_revision: Union[str, None] = 'a9802bcd9ef8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('objects', sa.Column('dispersion_measure', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('objects', 'dispersion_measure')
