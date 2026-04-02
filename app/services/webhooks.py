from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib import error, request as urllib_request
from uuid import uuid4

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Webhook


SUPPORTED_WEBHOOK_EVENTS = {
    "annotations.updated",
    "export.completed",
    "item.created",
    "item.status_changed",
    "review.rejected",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_webhook_body(
    *,
    event_type: str,
    payload: dict,
) -> bytes:
    envelope = {
        "id": uuid4().hex,
        "type": event_type,
        "created_at": _utcnow().isoformat(),
        "data": payload,
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _build_signature(secret: str | None, body: bytes) -> str | None:
    normalized_secret = (secret or "").strip()
    if not normalized_secret:
        return None
    digest = hmac.new(
        normalized_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _deliver_webhook_request(
    *,
    target_url: str,
    body: bytes,
    signature: str | None,
) -> int:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "FramePin-Webhooks/1.0",
    }
    if signature is not None:
        headers["X-Frame-Pin-Signature"] = signature

    req = urllib_request.Request(
        target_url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib_request.urlopen(
        req,
        timeout=max(1, int(settings.public_api_webhook_timeout_seconds or 5)),
    ) as response:
        return int(getattr(response, "status", response.getcode()))


def dispatch_webhook_event(
    db: Session,
    *,
    event_type: str,
    payload: dict,
    project_id: int | None,
) -> int:
    if event_type not in SUPPORTED_WEBHOOK_EVENTS:
        return 0

    webhooks = [
        webhook
        for webhook in db.query(Webhook).order_by(Webhook.id.asc()).all()
        if webhook.is_active
        and event_type in webhook.events
        and (webhook.project_id is None or webhook.project_id == project_id)
    ]
    if not webhooks:
        return 0

    body = _build_webhook_body(event_type=event_type, payload=payload)
    delivered_count = 0
    for webhook in webhooks:
        try:
            status_code = _deliver_webhook_request(
                target_url=webhook.target_url,
                body=body,
                signature=_build_signature(webhook.signing_secret, body),
            )
            webhook.last_response_status = status_code
            webhook.last_error = None
            webhook.last_delivered_at = _utcnow()
            delivered_count += 1
        except error.HTTPError as exc:
            webhook.last_response_status = int(exc.code)
            webhook.last_error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive fallback
            webhook.last_response_status = None
            webhook.last_error = str(exc)
        db.add(webhook)

    return delivered_count
