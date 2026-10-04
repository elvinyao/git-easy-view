"""Project release plans, Git tags and immutable production history.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # Copy only this revision's frozen schema; no application model imports.
    op.create_table("git_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("object_oid", sa.String(128), nullable=False),
        sa.Column("commit_oid", sa.String(128), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("commit_time", sa.DateTime(timezone=True)),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("repository_id", "name"))
    op.create_table("release_sources",
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), primary_key=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("platform_connections.id"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(1024), nullable=False),
        sa.Column("records", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False))
    op.create_table("release_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("scheduled_date", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("release_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("release_plans.id"), nullable=False),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("branch_name", sa.String(1024), nullable=False),
        sa.Column("tag_name", sa.String(1024)),
        sa.Column("tag_oid", sa.String(128)),
        sa.Column("commit_oid", sa.String(128)),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("commit_time", sa.DateTime(timezone=True)),
        sa.Column("target_branch", sa.String(1024), nullable=False),
        sa.Column("mr_id", sa.String(255)),
        sa.Column("mr_connection_id", sa.Integer(), sa.ForeignKey("platform_connections.id")),
        sa.Column("mr_repository_id", sa.String(255)),
        sa.Column("deployed_at", sa.DateTime(timezone=True)),
        sa.Column("merged_at", sa.DateTime(timezone=True)),
        sa.Column("merge_evidence", sa.JSON(), nullable=False),
        sa.UniqueConstraint("plan_id", "repository_id"))
    op.create_table("deployments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("release_item_id", sa.Integer(), sa.ForeignKey("release_items.id"), unique=True),
        sa.Column("branch_name", sa.String(1024), nullable=False),
        sa.Column("tag_name", sa.String(1024)),
        sa.Column("commit_oid", sa.String(128), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("rollback_of_id", sa.Integer(), sa.ForeignKey("deployments.id")))
    for table, columns in {
        "git_tags": ["repository_id"], "release_plans": ["project_id"],
        "release_items": ["plan_id", "repository_id"], "deployments": ["project_id", "repository_id"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ("deployments", "release_items", "release_plans", "release_sources", "git_tags"):
        op.drop_table(table)
