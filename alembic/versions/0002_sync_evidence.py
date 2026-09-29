"""Durable local comparison work and snapshot source evidence.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repositories", sa.Column("role_prefixes", sa.JSON(), nullable=False,
                  server_default=sa.text("'{\"release\": [\"release/\"], \"feature\": [\"feature/\"]}'")))
    op.add_column("branches", sa.Column("source_candidates", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("sync_jobs", sa.Column("kind", sa.String(32), nullable=False, server_default="sync"))
    op.add_column("sync_jobs", sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column("sync_jobs", "parameters")
    op.drop_column("sync_jobs", "kind")
    op.drop_column("branches", "source_candidates")
    op.drop_column("repositories", "role_prefixes")
