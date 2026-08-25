"""initial schema: rate_limit_configs, request_logs

Revision ID: 0001
Revises:
Create Date: 2026-01-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(255), nullable=False, unique=True),
        sa.Column("algorithm", sa.String(64), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("burst", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_rate_limit_configs_client_id", "rate_limit_configs", ["client_id"]
    )

    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("algorithm", sa.String(64), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_request_logs_client_id", "request_logs", ["client_id"])
    op.create_index("ix_request_logs_allowed", "request_logs", ["allowed"])
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("request_logs")
    op.drop_table("rate_limit_configs")
