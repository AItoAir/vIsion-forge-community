from __future__ import annotations

import asyncio
import hashlib
import math
import threading
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import WebSocket


COLLABORATION_COLORS = (
    "#39a0ed",
    "#ff7a59",
    "#51cf66",
    "#f59f00",
    "#e64980",
    "#9775fa",
    "#15aabf",
    "#fa5252",
)

MAX_ACTION_LENGTH = 48
MAX_TOOL_LENGTH = 48
MAX_UID_LENGTH = 64
MAX_POLYGON_POINTS = 96


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_color(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return COLLABORATION_COLORS[digest[0] % len(COLLABORATION_COLORS)]


def _clean_int(value, *, minimum: int | None = None) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return parsed


def _clean_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _clean_string(value, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def _clean_cursor(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None

    x = _clean_float(payload.get("x"))
    y = _clean_float(payload.get("y"))
    if x is None or y is None:
        return None

    return {
        "x": x,
        "y": y,
        "visible": bool(payload.get("visible", True)),
    }


def _clean_polygon_points(points) -> list[list[float]] | None:
    if not isinstance(points, list):
        return None

    normalized: list[list[float]] = []
    for point in points[:MAX_POLYGON_POINTS]:
        if not isinstance(point, list) or len(point) != 2:
            continue
        x = _clean_float(point[0])
        y = _clean_float(point[1])
        if x is None or y is None:
            continue
        normalized.append([x, y])

    return normalized or None


def _clean_draft(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None

    geometry_kind = _clean_string(payload.get("geometry_kind"), max_length=24)
    if geometry_kind not in {"bbox", "polygon", "tag"}:
        return None

    draft = {
        "geometry_kind": geometry_kind,
        "label_class_id": _clean_int(payload.get("label_class_id"), minimum=1),
        "track_id": _clean_int(payload.get("track_id"), minimum=1),
        "client_uid": _clean_string(payload.get("client_uid"), max_length=MAX_UID_LENGTH),
    }

    x1 = _clean_float(payload.get("x1"))
    y1 = _clean_float(payload.get("y1"))
    x2 = _clean_float(payload.get("x2"))
    y2 = _clean_float(payload.get("y2"))
    if None not in {x1, y1, x2, y2}:
        draft.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    polygon_points = _clean_polygon_points(payload.get("polygon_points"))
    if polygon_points:
        draft["polygon_points"] = polygon_points

    return draft


def sanitize_presence_state(payload) -> dict:
    if not isinstance(payload, dict):
        return {}

    sanitized = {
        "frame_index": _clean_int(payload.get("frame_index"), minimum=0),
        "current_time_sec": _clean_float(payload.get("current_time_sec")),
        "label_class_id": _clean_int(payload.get("label_class_id"), minimum=1),
        "active_track_id": _clean_int(payload.get("active_track_id"), minimum=1),
        "active_annotation_uid": _clean_string(
            payload.get("active_annotation_uid"),
            max_length=MAX_UID_LENGTH,
        ),
        "action": _clean_string(payload.get("action"), max_length=MAX_ACTION_LENGTH)
        or "idle",
        "tool": _clean_string(payload.get("tool"), max_length=MAX_TOOL_LENGTH),
        "playing": bool(payload.get("playing", False)),
        "cursor": _clean_cursor(payload.get("cursor")),
        "draft": _clean_draft(payload.get("draft")),
    }

    if sanitized["frame_index"] is None:
        sanitized["current_time_sec"] = None
    return sanitized


class CollaborationHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[int, dict[str, WebSocket]] = {}
        self._participants: dict[int, dict[str, dict]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _snapshot_participants(self, item_id: int) -> list[dict]:
        participants = list(self._participants.get(item_id, {}).values())
        participants.sort(
            key=lambda participant: (
                participant.get("email") or "",
                participant.get("participant_id") or "",
            )
        )
        return [deepcopy(participant) for participant in participants]

    async def connect(
        self,
        *,
        websocket: WebSocket,
        item_id: int,
        user_id: int,
        email: str,
        role: str,
        team_id: int | None,
    ) -> str:
        await websocket.accept()
        participant_id = uuid4().hex
        timestamp = _utc_now_iso()
        participant = {
            "participant_id": participant_id,
            "user_id": user_id,
            "email": email,
            "role": role,
            "team_id": team_id,
            "color": _stable_color(f"{team_id}:{user_id}:{email}"),
            "frame_index": None,
            "current_time_sec": None,
            "label_class_id": None,
            "active_track_id": None,
            "active_annotation_uid": None,
            "action": "connected",
            "tool": None,
            "playing": False,
            "cursor": None,
            "draft": None,
            "connected_at": timestamp,
            "updated_at": timestamp,
        }

        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._connections.setdefault(item_id, {})[participant_id] = websocket
            self._participants.setdefault(item_id, {})[participant_id] = participant
            snapshot = self._snapshot_participants(item_id)

        await self._safe_send(
            websocket,
            {
                "type": "collaboration.hello",
                "item_id": item_id,
                "participant_id": participant_id,
                "participants": snapshot,
                "server_time": timestamp,
            },
        )
        await self._broadcast_participant_state(
            item_id,
            participant,
            exclude_participant_id=participant_id,
        )
        return participant_id

    async def update_presence(
        self,
        *,
        item_id: int,
        participant_id: str,
        payload,
    ) -> None:
        state = sanitize_presence_state(payload)
        if not state:
            return

        with self._lock:
            participant = self._participants.get(item_id, {}).get(participant_id)
            if participant is None:
                return
            participant.update(state)
            participant["updated_at"] = _utc_now_iso()
            snapshot = deepcopy(participant)

        await self._broadcast_participant_state(
            item_id,
            snapshot,
            exclude_participant_id=participant_id,
        )

    async def disconnect(
        self,
        *,
        item_id: int,
        participant_id: str,
    ) -> None:
        participant = None
        websocket = None

        with self._lock:
            item_connections = self._connections.get(item_id)
            if item_connections is not None:
                websocket = item_connections.pop(participant_id, None)
                if not item_connections:
                    self._connections.pop(item_id, None)

            item_participants = self._participants.get(item_id)
            if item_participants is not None:
                participant = item_participants.pop(participant_id, None)
                if not item_participants:
                    self._participants.pop(item_id, None)

        if websocket is not None:
            try:
                await websocket.close()
            except RuntimeError:
                pass
            except Exception:
                pass

        if participant is None:
            return

        await self._broadcast_event(
            item_id,
            {
                "type": "collaboration.participant_left",
                "participant_id": participant_id,
                "user_id": participant.get("user_id"),
            },
        )

    async def _broadcast_participant_state(
        self,
        item_id: int,
        participant: dict,
        *,
        exclude_participant_id: str | None = None,
    ) -> None:
        await self._broadcast_event(
            item_id,
            {
                "type": "collaboration.participant_state",
                "participant": deepcopy(participant),
            },
            exclude_participant_id=exclude_participant_id,
        )

    async def _safe_send(self, websocket: WebSocket, payload: dict) -> None:
        await websocket.send_json(payload)

    async def _broadcast_event(
        self,
        item_id: int,
        payload: dict,
        *,
        exclude_participant_id: str | None = None,
    ) -> None:
        with self._lock:
            targets = list(self._connections.get(item_id, {}).items())

        stale_participant_ids: list[str] = []
        for participant_id, websocket in targets:
            if exclude_participant_id and participant_id == exclude_participant_id:
                continue
            try:
                await self._safe_send(websocket, payload)
            except Exception:
                stale_participant_ids.append(participant_id)

        for stale_participant_id in stale_participant_ids:
            await self.disconnect(item_id=item_id, participant_id=stale_participant_id)

    def _schedule(self, coro) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def publish_annotation_commit(
        self,
        *,
        item_id: int,
        revision: int,
        annotations: list[dict],
        item_status: str,
        actor_user_id: int | None,
    ) -> None:
        self._schedule(
            self._broadcast_event(
                item_id,
                {
                    "type": "collaboration.annotations_committed",
                    "item_id": item_id,
                    "revision": revision,
                    "annotations": annotations,
                    "item_status": item_status,
                    "actor_user_id": actor_user_id,
                    "committed_at": _utc_now_iso(),
                },
            )
        )


collaboration_hub = CollaborationHub()
