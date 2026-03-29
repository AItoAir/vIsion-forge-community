from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog


def log_audit(
    db: Session,
    *,
    actor_id: int | None,
    object_type: str,
    object_id: int,
    action: str,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        object_type=object_type,
        object_id=object_id,
        action=action,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(entry)
    return entry
