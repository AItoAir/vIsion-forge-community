"""Add mention metadata for comments."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260330_0007"
down_revision = "20260330_0006"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _table_columns(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    if _table_exists("region_comment"):
        existing_columns = _table_columns("region_comment")
        if "mentions_json" not in existing_columns:
            op.add_column(
                "region_comment",
                sa.Column("mentions_json", sa.Text(), nullable=True),
            )

    if _table_exists("review_comment"):
        existing_columns = _table_columns("review_comment")
        if "mentions_json" not in existing_columns:
            op.add_column(
                "review_comment",
                sa.Column("mentions_json", sa.Text(), nullable=True),
            )


def downgrade() -> None:
    if _table_exists("review_comment"):
        existing_columns = _table_columns("review_comment")
        if "mentions_json" in existing_columns:
            op.drop_column("review_comment", "mentions_json")

    if _table_exists("region_comment"):
        existing_columns = _table_columns("region_comment")
        if "mentions_json" in existing_columns:
            op.drop_column("region_comment", "mentions_json")
