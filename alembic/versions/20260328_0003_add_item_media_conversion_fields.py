"""Add persisted media conversion state for video items."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260328_0003"
down_revision = "20260328_0002"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _existing_columns(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    if not _table_exists("item"):
        return

    existing_columns = _existing_columns("item")
    with op.batch_alter_table("item") as batch_op:
        if "media_conversion_status" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "media_conversion_status",
                    sa.String(length=32),
                    nullable=False,
                    server_default="not_required",
                )
            )
        if "media_conversion_error" not in existing_columns:
            batch_op.add_column(
                sa.Column("media_conversion_error", sa.Text(), nullable=True)
            )
        if "media_conversion_profile" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "media_conversion_profile",
                    sa.String(length=255),
                    nullable=True,
                )
            )
        if "frame_rate_mode" not in existing_columns:
            batch_op.add_column(
                sa.Column("frame_rate_mode", sa.String(length=16), nullable=True)
            )

    updated_columns = _existing_columns("item")
    if {"kind", "media_conversion_status"}.issubset(updated_columns):
        op.execute(
            sa.text(
                "UPDATE item SET media_conversion_status = 'pending' "
                "WHERE kind = 'video'"
            )
        )
    if {"kind", "frame_rate_mode"}.issubset(updated_columns):
        op.execute(
            sa.text(
                "UPDATE item SET frame_rate_mode = 'unknown' "
                "WHERE kind = 'video' AND (frame_rate_mode IS NULL OR frame_rate_mode = '')"
            )
        )


def downgrade() -> None:
    if not _table_exists("item"):
        return

    existing_columns = _existing_columns("item")
    with op.batch_alter_table("item") as batch_op:
        if "frame_rate_mode" in existing_columns:
            batch_op.drop_column("frame_rate_mode")
        if "media_conversion_profile" in existing_columns:
            batch_op.drop_column("media_conversion_profile")
        if "media_conversion_error" in existing_columns:
            batch_op.drop_column("media_conversion_error")
        if "media_conversion_status" in existing_columns:
            batch_op.drop_column("media_conversion_status")
