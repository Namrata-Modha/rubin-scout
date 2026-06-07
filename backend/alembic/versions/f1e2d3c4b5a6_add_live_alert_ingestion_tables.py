"""add live alert ingestion tables

Adds two new tables introduced in Sprint 4 for live alert ingestion:

  alert_sources  — registry of upstream broker / catalog feeds
  alerts_live    — individual alert events ingested from those feeds

NOTE: ingestion_log already exists in the database (created outside of
Alembic via init.sql / manual DDL).  It is NOT touched here.

Additive only.  No existing tables are modified.

Revision ID: f1e2d3c4b5a6
Revises: c7f3b9e21a04
Create Date: 2026-06-07 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# --------------------------------------------------------------------------- #
# Revision identifiers — used by Alembic                                      #
# --------------------------------------------------------------------------- #
revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "c7f3b9e21a04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# upgrade                                                                      #
# --------------------------------------------------------------------------- #

def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. alert_sources                                                    #
    #    Registry of upstream broker / catalog systems.                  #
    #    One row per feed: "fink_ztf", "chime_frb", "tns", …            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "alert_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Stable machine key — unique, used throughout ingestion code
        sa.Column("name", sa.String(), nullable=False),
        # Human-readable label shown in the UI
        sa.Column("display_name", sa.String(), nullable=False),
        # "broker" | "catalog" | "voevent" | "stream"
        sa.Column("source_type", sa.String(), nullable=True),
        # Base URL of the upstream REST / VOEvent API
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        # Arbitrary per-source config (auth tokens, poll intervals, …)
        sa.Column("config", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_alert_sources_name"),
    )
    op.create_index("ix_alert_sources_name", "alert_sources", ["name"])
    op.create_index("ix_alert_sources_is_active", "alert_sources", ["is_active"])

    # ------------------------------------------------------------------ #
    # 2. alerts_live                                                      #
    #    One row per alert event received from an upstream source.        #
    #    external_id is the broker-native identifier; the composite      #
    #    unique constraint prevents duplicate ingestion.                 #
    # ------------------------------------------------------------------ #
    op.create_table(
        "alerts_live",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        # Broker-native identifier (ZTF objectId, CHIME tns_name, …)
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("ra", sa.Float(), nullable=True),
        sa.Column("dec", sa.Float(), nullable=True),
        # Fink classification label or FRB / transient type string
        sa.Column("alert_type", sa.String(), nullable=True),
        sa.Column("classification", sa.String(), nullable=True),
        # Top classifier probability score [0, 1]; NULL when unavailable
        sa.Column("classification_score", sa.Float(), nullable=True),
        # Full raw broker payload — nothing discarded at ingestion time
        sa.Column("raw_payload", JSONB(), nullable=True),
        # Julian date of the originating observation
        sa.Column("jd", sa.Float(), nullable=True),
        # UTC timestamp of the upstream detection event
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
        # UTC timestamp this row was written into the DB
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Populated after cross-match to the unified objects table
        sa.Column("oid", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["alert_sources.id"],
            ondelete="CASCADE",
            name="fk_alerts_live_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["oid"],
            ["objects.oid"],
            ondelete="SET NULL",
            name="fk_alerts_live_oid",
        ),
        sa.UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_alerts_live_source_external",
        ),
    )
    op.create_index("ix_alerts_live_source_id", "alerts_live", ["source_id"])
    op.create_index("ix_alerts_live_external_id", "alerts_live", ["external_id"])
    op.create_index("ix_alerts_live_ingested_at", "alerts_live", ["ingested_at"])
    op.create_index("ix_alerts_live_classification", "alerts_live", ["classification"])
    op.create_index("ix_alerts_live_oid", "alerts_live", ["oid"])


# --------------------------------------------------------------------------- #
# downgrade                                                                    #
# Drops in reverse dependency order.  ingestion_log is intentionally          #
# excluded — it pre-dated this migration and must not be dropped here.        #
# --------------------------------------------------------------------------- #

def downgrade() -> None:
    # alerts_live depends on alert_sources — drop first
    op.drop_index("ix_alerts_live_oid", table_name="alerts_live")
    op.drop_index("ix_alerts_live_classification", table_name="alerts_live")
    op.drop_index("ix_alerts_live_ingested_at", table_name="alerts_live")
    op.drop_index("ix_alerts_live_external_id", table_name="alerts_live")
    op.drop_index("ix_alerts_live_source_id", table_name="alerts_live")
    op.drop_table("alerts_live")

    # alert_sources has no remaining dependents — drop second
    op.drop_index("ix_alert_sources_is_active", table_name="alert_sources")
    op.drop_index("ix_alert_sources_name", table_name="alert_sources")
    op.drop_table("alert_sources")
