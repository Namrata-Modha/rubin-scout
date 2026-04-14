"""add band to detections

Revision ID: 2634842c16f7
Revises: 3a941ed2c563
Create Date: 2026-04-14 19:07:04.093664

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '2634842c16f7'
down_revision: Union[str, None] = '3a941ed2c563'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('detections', sa.Column('band', sa.String(), nullable=True))

def downgrade():
    op.drop_column('detections', 'band')
