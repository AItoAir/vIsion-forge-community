"""Add in-app notifications."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260330_0006"
down_revision = "20260328_0005"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _existing_indexes(table_name: str) -> set[str]:
    return {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    if not _table_exists("notification"):
        op.create_table(
            "notification",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("item.id"), nullable=True),
            sa.Column(
                "sam2_track_job_id",
                sa.Integer(),
                sa.ForeignKey("sam2_track_job.id"),
                nullable=True,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("link_path", sa.String(length=1024), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    existing_indexes = _existing_indexes("notification")
    if "ix_notification_user_created" not in existing_indexes:
        op.create_index(
            "ix_notification_user_created",
            "notification",
            ["user_id", "created_at", "id"],
            unique=False,
        )
    if "ix_notification_user_read_created" not in existing_indexes:
        op.create_index(
            "ix_notification_user_read_created",
            "notification",
            ["user_id", "read_at", "created_at", "id"],
            unique=False,
        )
    if "ix_notification_sam2_track_job_user" not in existing_indexes:
        op.create_index(
            "ix_notification_sam2_track_job_user",
            "notification",
            ["sam2_track_job_id", "user_id"],
            unique=False,
        )


def downgrade() -> None:
    if _table_exists("notification"):
        existing_indexes = _existing_indexes("notification")
        if "ix_notification_sam2_track_job_user" in existing_indexes:
            op.drop_index(
                "ix_notification_sam2_track_job_user",
                table_name="notification",
            )
        if "ix_notification_user_read_created" in existing_indexes:
            op.drop_index(
                "ix_notification_user_read_created",
                table_name="notification",
            )
        if "ix_notification_user_created" in existing_indexes:
            op.drop_index(
                "ix_notification_user_created",
                table_name="notification",
            )
        op.drop_table("notification")
