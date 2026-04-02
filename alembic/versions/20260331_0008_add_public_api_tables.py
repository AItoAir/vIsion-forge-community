"""Add public API, webhook, export job, and prediction import tables."""

from __future__ import annotations

from alembic import op

from app import models


revision = "20260331_0008"
down_revision = "20260330_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = [
        models.ApiKey.__table__,
        models.ExportJob.__table__,
        models.Webhook.__table__,
        models.PredictionRun.__table__,
        models.Prediction.__table__,
    ]
    for table in tables:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [
        models.Prediction.__table__,
        models.PredictionRun.__table__,
        models.Webhook.__table__,
        models.ExportJob.__table__,
        models.ApiKey.__table__,
    ]
    for table in tables:
        table.drop(bind=bind, checkfirst=True)
