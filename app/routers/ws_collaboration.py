from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..csrf import websocket_origin_allowed
from ..database import SessionLocal
from ..models import Item, User, UserRole
from ..security import ensure_project_team_access
from ..services.collaboration import collaboration_hub


router = APIRouter(include_in_schema=False)


def _role_allows_collaboration(role: UserRole) -> bool:
    return role in {
        UserRole.annotator,
        UserRole.reviewer,
        UserRole.project_admin,
        UserRole.system_admin,
    }


@router.websocket("/ws/items/{item_id}/presence")
async def item_presence_socket(websocket: WebSocket, item_id: int):
    if not websocket_origin_allowed(websocket):
        await websocket.close(code=4403)
        return

    with SessionLocal() as db:
        session = getattr(websocket, "session", None) or websocket.scope.get("session") or {}
        user_id = session.get("user_id")
        if not user_id:
            await websocket.close(code=4401)
            return

        current_user = db.get(User, user_id)
        if current_user is None or not current_user.is_active:
            await websocket.close(code=4401)
            return

        if not _role_allows_collaboration(current_user.role):
            await websocket.close(code=4403)
            return

        item = db.get(Item, item_id)
        if item is None:
            await websocket.close(code=4404)
            return

        try:
            ensure_project_team_access(item.project, current_user)
        except HTTPException:
            await websocket.close(code=4404)
            return

        user_payload = {
            "user_id": current_user.id,
            "email": current_user.email,
            "role": current_user.role.value,
            "team_id": current_user.team_id,
        }

    participant_id = await collaboration_hub.connect(
        websocket=websocket,
        item_id=item_id,
        **user_payload,
    )

    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue

            message_type = message.get("type")
            if message_type == "presence.update":
                await collaboration_hub.update_presence(
                    item_id=item_id,
                    participant_id=participant_id,
                    payload=message.get("state"),
                )
    except WebSocketDisconnect:
        pass
    finally:
        await collaboration_hub.disconnect(
            item_id=item_id,
            participant_id=participant_id,
        )
