"""Add persistent SAM2 background track jobs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260328_0005"
down_revision = "20260328_0004"
branch_labels = None
depends_on = None


sam2_job_status_enum = sa.Enum(
    "queued",
    "running",
    "completed",
    "failed",
    name="sam2jobstatus",
)


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _existing_indexes(table_name: str) -> set[str]:
    return {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    bind = op.get_bind()
    sam2_job_status_enum.create(bind, checkfirst=True)

    if not _table_exists("sam2_track_job"):
        op.create_table(
            "sam2_track_job",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("item.id"), nullable=False),
            sa.Column(
                "requested_by",
                sa.Integer(),
                sa.ForeignKey("user.id"),
                nullable=True,
            ),
            sa.Column(
                "status",
                sam2_job_status_enum,
                nullable=False,
                server_default="queued",
            ),
            sa.Column("label_class_id", sa.Integer(), nullable=False),
            sa.Column("track_id", sa.Integer(), nullable=True),
            sa.Column("frame_index", sa.Integer(), nullable=True),
            sa.Column("track_start_frame", sa.Integer(), nullable=True),
            sa.Column("track_end_frame", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_annotation_count", sa.Integer(), nullable=True),
            sa.Column("applied_revision", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    existing_indexes = _existing_indexes("sam2_track_job")
    if "ix_sam2_track_job_status_id" not in existing_indexes:
        op.create_index(
            "ix_sam2_track_job_status_id",
            "sam2_track_job",
            ["status", "id"],
            unique=False,
        )
    if "ix_sam2_track_job_item_status_id" not in existing_indexes:
        op.create_index(
            "ix_sam2_track_job_item_status_id",
            "sam2_track_job",
            ["item_id", "status", "id"],
            unique=False,
        )


def downgrade() -> None:
    if _table_exists("sam2_track_job"):
        existing_indexes = _existing_indexes("sam2_track_job")
        if "ix_sam2_track_job_item_status_id" in existing_indexes:
            op.drop_index("ix_sam2_track_job_item_status_id", table_name="sam2_track_job")
        if "ix_sam2_track_job_status_id" in existing_indexes:
            op.drop_index("ix_sam2_track_job_status_id", table_name="sam2_track_job")
        op.drop_table("sam2_track_job")

    bind = op.get_bind()
    sam2_job_status_enum.drop(bind, checkfirst=True)
