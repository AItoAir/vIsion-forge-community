# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

"""Add item display path and source media type fields."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260404_0009"
down_revision = "20260331_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("item")}

    if "display_path" not in existing_columns:
        op.add_column("item", sa.Column("display_path", sa.String(length=1024), nullable=True))
    if "source_media_type" not in existing_columns:
        op.add_column("item", sa.Column("source_media_type", sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("item")}

    if "source_media_type" in existing_columns:
        op.drop_column("item", "source_media_type")
    if "display_path" in existing_columns:
        op.drop_column("item", "display_path")
