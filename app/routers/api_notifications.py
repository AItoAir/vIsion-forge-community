from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    NotificationListResponse,
    NotificationMarkReadRequest,
    NotificationMarkReadResponse,
)
from ..security import get_current_user
from ..services.notifications import (
    get_notification_list_response,
    mark_notifications_read,
)


router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    limit: int = Query(default=8, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> NotificationListResponse:
    return get_notification_list_response(
        db=db,
        user_id=current_user.id,
        limit=limit,
    )


@router.post("/notifications/read", response_model=NotificationMarkReadResponse)
def read_notifications(
    payload: NotificationMarkReadRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> NotificationMarkReadResponse:
    marked_count = mark_notifications_read(
        db=db,
        user_id=current_user.id,
        notification_ids=payload.ids,
    )
    db.commit()
    summary = get_notification_list_response(
        db=db,
        user_id=current_user.id,
        limit=1,
    )
    return NotificationMarkReadResponse(
        unread_count=summary.unread_count,
        marked_count=marked_count,
    )
