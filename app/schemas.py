from __future__ import annotations

from datetime import datetime
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    AnnotationStatus,
    ItemKind,
    ItemStatus,
    LabelGeometryKind,
    Sam2JobStatus,
)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: ItemKind
    path: str
    w: int
    h: int
    status: ItemStatus


class LabelClassRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    color_hex: str
    shortcut_key: str | None
    is_active: bool
    geometry_kind: LabelGeometryKind


class AnnotationAuditUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


class AnnotationBase(BaseModel):
    client_uid: str | None = None
    label_class_id: int
    frame_index: int | None = None
    x1: float
    y1: float
    x2: float
    y2: float
    polygon_points: list[list[float]] | None = None
    track_id: int | None = None
    propagation_frames: int | None = None
    is_occluded: bool = False
    is_truncated: bool = False
    is_outside: bool = False
    is_lost: bool = False
    status: AnnotationStatus = AnnotationStatus.pending

    @model_validator(mode="after")
    def validate_bbox(self) -> "AnnotationBase":
        if self.client_uid is not None and not self.client_uid.strip():
            raise ValueError("client_uid must not be blank")
        coords = (self.x1, self.y1, self.x2, self.y2)
        if not all(isfinite(v) for v in coords):
            raise ValueError("Coordinates must be finite numbers")
        if self.x2 <= self.x1:
            raise ValueError("x2 must be greater than x1")
        if self.y2 <= self.y1:
            raise ValueError("y2 must be greater than y1")
        if self.polygon_points is not None:
            if len(self.polygon_points) < 3:
                raise ValueError("polygon_points must contain at least 3 points")
            for point in self.polygon_points:
                if len(point) != 2:
                    raise ValueError(
                        "Each polygon point must contain exactly 2 coordinates"
                    )
                if not all(isfinite(v) for v in point):
                    raise ValueError("Polygon coordinates must be finite numbers")
        if self.frame_index is not None and self.frame_index < 0:
            raise ValueError("frame_index must be greater than or equal to 0")
        if self.propagation_frames is not None and self.propagation_frames < 0:
            raise ValueError("propagation_frames must be greater than or equal to 0")
        return self


class AnnotationCreate(AnnotationBase):
    pass


class AnnotationRead(AnnotationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime
    created_by_user: AnnotationAuditUserRead | None = None
    updated_by_user: AnnotationAuditUserRead | None = None


class AnnotationSaveResponse(BaseModel):
    annotation_count: int
    item_status: ItemStatus
    revision: int
    annotations: list[AnnotationRead] = Field(default_factory=list)


class AnnotationsPatchRequest(BaseModel):
    base_revision: int = Field(default=0, ge=0)
    upserts: list[AnnotationCreate] = Field(default_factory=list)
    deletes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_patch(self) -> "AnnotationsPatchRequest":
        normalized_deletes: list[str] = []
        for client_uid in self.deletes:
            normalized_client_uid = (client_uid or "").strip()
            if not normalized_client_uid:
                raise ValueError("delete client_uids must not be blank")
            normalized_deletes.append(normalized_client_uid)

        if len(set(normalized_deletes)) != len(normalized_deletes):
            raise ValueError("delete client_uids must be unique")

        self.deletes = normalized_deletes
        return self


class RegionCommentBase(BaseModel):
    client_uid: str | None = None
    frame_index: int | None = None
    x1: float
    y1: float
    x2: float
    y2: float
    comment: str

    @model_validator(mode="after")
    def validate_region_comment(self) -> "RegionCommentBase":
        if self.client_uid is not None and not self.client_uid.strip():
            raise ValueError("client_uid must not be blank")
        coords = (self.x1, self.y1, self.x2, self.y2)
        if not all(isfinite(v) for v in coords):
            raise ValueError("Coordinates must be finite numbers")
        if self.x2 <= self.x1:
            raise ValueError("x2 must be greater than x1")
        if self.y2 <= self.y1:
            raise ValueError("y2 must be greater than y1")
        normalized_comment = (self.comment or "").strip()
        if not normalized_comment:
            raise ValueError("comment must not be blank")
        self.comment = normalized_comment
        if self.frame_index is not None and self.frame_index < 0:
            raise ValueError("frame_index must be greater than or equal to 0")
        return self


class RegionCommentCreate(RegionCommentBase):
    pass


class CommentMentionRead(BaseModel):
    user_id: int
    email: str
    display_name: str
    mention_text: str
    start: int
    end: int


class RegionCommentRead(RegionCommentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime
    created_by_user: AnnotationAuditUserRead | None = None
    updated_by_user: AnnotationAuditUserRead | None = None
    mentions: list[CommentMentionRead] = Field(default_factory=list)


class RegionCommentsPatchRequest(BaseModel):
    upserts: list[RegionCommentCreate] = Field(default_factory=list)
    deletes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_patch(self) -> "RegionCommentsPatchRequest":
        normalized_deletes: list[str] = []
        for client_uid in self.deletes:
            normalized_client_uid = (client_uid or "").strip()
            if not normalized_client_uid:
                raise ValueError("delete client_uids must not be blank")
            normalized_deletes.append(normalized_client_uid)

        if len(set(normalized_deletes)) != len(normalized_deletes):
            raise ValueError("delete client_uids must be unique")

        self.deletes = normalized_deletes
        return self


class RegionCommentSaveResponse(BaseModel):
    comment_count: int
    comments: list[RegionCommentRead] = Field(default_factory=list)


class Sam2PromptPoint(BaseModel):
    x: float
    y: float
    label: int = 1

    @model_validator(mode="after")
    def validate_point(self) -> "Sam2PromptPoint":
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("SAM2 prompt points must be finite numbers")
        if self.label not in {0, 1}:
            raise ValueError("SAM2 prompt point label must be 0 or 1")
        return self


class Sam2PromptRequest(BaseModel):
    label_class_id: int
    frame_index: int | None = None
    box_xyxy: list[float] | None = None
    prompt_points: list[Sam2PromptPoint] = Field(default_factory=list)
    track_id: int | None = None
    track_start_frame: int | None = None
    track_end_frame: int | None = None
    include_reverse: bool = True
    simplify_tolerance: float | None = None

    @model_validator(mode="after")
    def validate_prompt(self) -> "Sam2PromptRequest":
        if self.box_xyxy is not None:
            if len(self.box_xyxy) != 4:
                raise ValueError("box_xyxy must contain exactly 4 coordinates")
            if not all(isfinite(float(value)) for value in self.box_xyxy):
                raise ValueError("box_xyxy must contain finite numbers")
            if float(self.box_xyxy[0]) == float(self.box_xyxy[2]):
                raise ValueError("box_xyxy must have non-zero width")
            if float(self.box_xyxy[1]) == float(self.box_xyxy[3]):
                raise ValueError("box_xyxy must have non-zero height")

        if not self.prompt_points and self.box_xyxy is None:
            raise ValueError("At least one prompt point or a box_xyxy prompt is required")
        if self.frame_index is not None and self.frame_index < 0:
            raise ValueError("frame_index must be >= 0")
        if self.track_id is not None and self.track_id <= 0:
            raise ValueError("track_id must be > 0")
        if self.track_start_frame is not None and self.track_start_frame < 0:
            raise ValueError("track_start_frame must be >= 0")
        if self.track_end_frame is not None and self.track_end_frame < 0:
            raise ValueError("track_end_frame must be >= 0")
        if (
            self.track_start_frame is not None
            and self.track_end_frame is not None
            and self.track_start_frame > self.track_end_frame
        ):
            raise ValueError("track_start_frame must be <= track_end_frame")
        if self.frame_index is not None and self.track_start_frame is not None:
            if self.frame_index < self.track_start_frame:
                raise ValueError("frame_index must be >= track_start_frame")
        if self.frame_index is not None and self.track_end_frame is not None:
            if self.frame_index > self.track_end_frame:
                raise ValueError("frame_index must be <= track_end_frame")
        if self.simplify_tolerance is not None and self.simplify_tolerance < 0:
            raise ValueError("simplify_tolerance must be >= 0")
        return self


class Sam2AnnotationRead(BaseModel):
    label_class_id: int
    frame_index: int | None = None
    track_id: int | None = None
    x1: float
    y1: float
    x2: float
    y2: float
    polygon_points: list[list[float]]


class Sam2PromptResponse(BaseModel):
    item_id: int
    mode: str
    frame_index: int | None = None
    annotation_count: int
    annotations: list[Sam2AnnotationRead]


class Sam2TrackJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    requested_by: int | None = None
    status: Sam2JobStatus
    label_class_id: int
    track_id: int | None = None
    frame_index: int | None = None
    track_start_frame: int | None = None
    track_end_frame: int | None = None
    error_message: str | None = None
    result_annotation_count: int | None = None
    applied_revision: int | None = None
    queue_position: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class Sam2TrackJobEnqueueResponse(BaseModel):
    item_id: int
    job: Sam2TrackJobRead
    running_count: int
    queued_count: int
    max_concurrent_jobs: int
    max_queue_size: int


class Sam2TrackJobStatusResponse(BaseModel):
    item_id: int
    item_annotation_revision: int
    item_status: ItemStatus
    running_count: int
    queued_count: int
    max_concurrent_jobs: int
    max_queue_size: int
    item_jobs: list[Sam2TrackJobRead] = Field(default_factory=list)
    latest_finished_job: Sam2TrackJobRead | None = None


class NotificationRead(BaseModel):
    id: int
    event_type: str
    title: str
    body: str
    link_path: str | None = None
    project_id: int | None = None
    item_id: int | None = None
    sam2_track_job_id: int | None = None
    created_at: datetime
    read_at: datetime | None = None
    is_unread: bool = True


class NotificationListResponse(BaseModel):
    unread_count: int
    notifications: list[NotificationRead] = Field(default_factory=list)


class NotificationMarkReadRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ids(self) -> "NotificationMarkReadRequest":
        normalized_ids: list[int] = []
        for value in self.ids:
            notification_id = int(value)
            if notification_id <= 0:
                raise ValueError("notification ids must be positive integers")
            normalized_ids.append(notification_id)

        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("notification ids must be unique")

        self.ids = normalized_ids
        return self


class NotificationMarkReadResponse(BaseModel):
    unread_count: int
    marked_count: int
