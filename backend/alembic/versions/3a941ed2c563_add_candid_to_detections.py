"""add candid to detections

Revision ID: 3a941ed2c563
Revises: bd3a23187407
Create Date: 2026-04-14 18:51:29.350222

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '3a941ed2c563'
down_revision: Union[str, None] = 'bd3a23187407'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('detections', sa.Column('candid', sa.BigInteger(), nullable=True))

def downgrade():
    op.drop_column('detections', 'candid')
