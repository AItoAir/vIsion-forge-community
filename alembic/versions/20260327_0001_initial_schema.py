"""Create the initial Community Edition schema.

This initial migration is intentionally idempotent so existing internal
databases can be stamped and aligned without table recreation errors.
"""

from __future__ import annotations

from alembic import op

from app.database import Base
from app import models  # noqa: F401


revision = "20260327_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
