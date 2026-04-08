"""Initial schema

Revision ID: bd3a23187407
Revises: 
Create Date: 2026-04-07 22:06:24.670446

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography


revision: str = 'bd3a23187407'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create PostGIS extension
    op.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    
    # Create objects table
    op.create_table(
        'objects',
        sa.Column('oid', sa.String(), nullable=False),
        sa.Column('ra', sa.Float(), nullable=False),
        sa.Column('dec', sa.Float(), nullable=False),
        sa.Column('first_detection', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_detection', sa.DateTime(timezone=True), nullable=True),
        sa.Column('n_detections', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('classification', sa.String(), nullable=True),
        sa.Column('classification_probability', sa.Float(), nullable=True),
        sa.Column('sub_classification', sa.String(), nullable=True),
        sa.Column('classifier_name', sa.String(), nullable=True),
        sa.Column('classifier_version', sa.String(), nullable=True),
        sa.Column('cross_match_catalog', sa.String(), nullable=True),
        sa.Column('cross_match_name', sa.String(), nullable=True),
        sa.Column('cross_match_type', sa.String(), nullable=True),
        sa.Column('cross_match_distance_arcsec', sa.Float(), nullable=True),
        sa.Column('host_galaxy_name', sa.String(), nullable=True),
        sa.Column('host_galaxy_redshift', sa.Float(), nullable=True),
        sa.Column('broker_source', sa.String(), nullable=True),
        sa.Column('alert_url', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('oid')
    )
    
    # Add PostGIS geography column
    op.execute("""
        ALTER TABLE objects 
        ADD COLUMN position GEOGRAPHY(POINT, 4326)
    """)
    
    # Create indexes on objects
    op.create_index('ix_objects_classification', 'objects', ['classification'])
    op.create_index('ix_objects_last_detection', 'objects', ['last_detection'])
    op.execute('CREATE INDEX idx_objects_position ON objects USING GIST(position)')
    
    # Create detections table
    op.create_table(
        'detections',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('oid', sa.String(), nullable=False),
        sa.Column('mjd', sa.Float(), nullable=False),
        sa.Column('detection_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fid', sa.Integer(), nullable=True),
        sa.Column('magpsf', sa.Float(), nullable=True),
        sa.Column('sigmapsf', sa.Float(), nullable=True),
        sa.Column('ra', sa.Float(), nullable=True),
        sa.Column('dec', sa.Float(), nullable=True),
        sa.Column('isdiffpos', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['oid'], ['objects.oid'])
    )
    
    # Create index on detections
    op.create_index('ix_detections_oid_time', 'detections', ['oid', 'detection_time'])
    
    # Create classification_probabilities table
    op.create_table(
        'classification_probabilities',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('oid', sa.String(), nullable=False),
        sa.Column('class_name', sa.String(), nullable=False),
        sa.Column('probability', sa.Float(), nullable=False),
        sa.Column('classifier_name', sa.String(), nullable=True),
        sa.Column('classifier_version', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['oid'], ['objects.oid'])
    )
    
    # Create index on classification_probabilities
    op.create_index('ix_classification_probabilities_oid', 'classification_probabilities', ['oid'])
    
    # Create subscriptions table
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('user_email', sa.String(), nullable=False),
        sa.Column('filter_config', sa.JSON(), nullable=False),
        sa.Column('notification_method', sa.String(), server_default='email', nullable=True),
        sa.Column('webhook_url', sa.String(), nullable=True),
        sa.Column('slack_channel', sa.String(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create index on subscriptions
    op.create_index('ix_subscriptions_active', 'subscriptions', ['active'])
    
    # Create photometry table
    op.create_table(
        'photometry',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('oid', sa.String(), nullable=False),
        sa.Column('jd', sa.Float(), nullable=False),
        sa.Column('flux', sa.Float(), nullable=True),
        sa.Column('flux_error', sa.Float(), nullable=True),
        sa.Column('filter', sa.String(), nullable=True),
        sa.Column('instrument', sa.String(), nullable=True),
        sa.Column('limiting_flux', sa.Float(), nullable=True),
        sa.Column('flux_units', sa.String(), nullable=True),
        sa.Column('observer', sa.String(), nullable=True),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['oid'], ['objects.oid'])
    )
    
    # Create index on photometry
    op.create_index('ix_photometry_oid', 'photometry', ['oid'])


def downgrade() -> None:
    op.drop_table('photometry')
    op.drop_table('subscriptions')
    op.drop_table('classification_probabilities')
    op.drop_table('detections')
    op.drop_table('objects')
    op.execute('DROP EXTENSION IF EXISTS postgis')