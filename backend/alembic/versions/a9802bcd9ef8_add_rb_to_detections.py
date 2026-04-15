"""add rb to detections

Revision ID: a9802bcd9ef8
Revises: 2634842c16f7
Create Date: 2026-04-14 20:17:13.509594

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a9802bcd9ef8'
down_revision: Union[str, None] = '2634842c16f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('detections', sa.Column('rb', sa.Float(), nullable=True))

def downgrade():
    op.drop_column('detections', 'rb')
