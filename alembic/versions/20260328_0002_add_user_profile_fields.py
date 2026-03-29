"""Add profile fields for self-service account settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260328_0002"
down_revision = "20260327_0001"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _existing_columns(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    if not _table_exists("user"):
        return

    existing_columns = _existing_columns("user")
    with op.batch_alter_table("user") as batch_op:
        if "name" not in existing_columns:
            batch_op.add_column(sa.Column("name", sa.String(length=255), nullable=True))
        if "department" not in existing_columns:
            batch_op.add_column(
                sa.Column("department", sa.String(length=255), nullable=True)
            )


def downgrade() -> None:
    if not _table_exists("user"):
        return

    existing_columns = _existing_columns("user")
    with op.batch_alter_table("user") as batch_op:
        if "department" in existing_columns:
            batch_op.drop_column("department")
        if "name" in existing_columns:
            batch_op.drop_column("name")
