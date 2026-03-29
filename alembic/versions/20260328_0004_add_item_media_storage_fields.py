"""Track converted video storage usage and access times."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260328_0004"
down_revision = "20260328_0003"
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
        if "media_conversion_size_bytes" not in existing_columns:
            batch_op.add_column(
                sa.Column("media_conversion_size_bytes", sa.BigInteger(), nullable=True)
            )
        if "media_conversion_last_accessed_at" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "media_conversion_last_accessed_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )

    updated_columns = _existing_columns("item")
    if {
        "kind",
        "media_conversion_status",
        "media_conversion_last_accessed_at",
        "updated_at",
        "created_at",
    }.issubset(updated_columns):
        op.execute(
            sa.text(
                "UPDATE item "
                "SET media_conversion_last_accessed_at = COALESCE(updated_at, created_at) "
                "WHERE kind = 'video' "
                "AND media_conversion_status = 'ready' "
                "AND media_conversion_last_accessed_at IS NULL"
            )
        )


def downgrade() -> None:
    if not _table_exists("item"):
        return

    existing_columns = _existing_columns("item")
    with op.batch_alter_table("item") as batch_op:
        if "media_conversion_last_accessed_at" in existing_columns:
            batch_op.drop_column("media_conversion_last_accessed_at")
        if "media_conversion_size_bytes" in existing_columns:
            batch_op.drop_column("media_conversion_size_bytes")
