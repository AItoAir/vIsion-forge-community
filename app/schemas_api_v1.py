from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    ExportJobStatus,
    ItemKind,
    ItemStatus,
    LabelGeometryKind,
    PredictionRunStatus,
)
from .schemas import AnnotationRead, AnnotationSaveResponse, CommentMentionRead


class ApiKeyCreateRequest(BaseModel):
    name: str
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_name(self) -> "ApiKeyCreateRequest":
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("name must not be blank")
        return self


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key_id: str
    secret_last_four: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreateResponse(BaseModel):
    api_key: ApiKeyRead
    token: str


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None

    @model_validator(mode="after")
    def validate_name(self) -> "ProjectCreateRequest":
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("name must not be blank")
        self.description = (self.description or "").strip() or None
        return self


class ProjectUpdateRequest(ProjectCreateRequest):
    pass


class ProjectReadV1(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class LabelClassUpsertRequest(BaseModel):
    name: str
    geometry_kind: LabelGeometryKind = LabelGeometryKind.bbox
    color_hex: str = "#00ff00"
    shortcut_key: str | None = None
    is_active: bool = True
    default_use_fixed_box: bool = False
    default_box_w: int | None = None
    default_box_h: int | None = None
    default_propagation_frames: int = 0

    @model_validator(mode="after")
    def validate_payload(self) -> "LabelClassUpsertRequest":
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("name must not be blank")
        shortcut = (self.shortcut_key or "").strip()
        self.shortcut_key = shortcut[:1] or None
        self.color_hex = (self.color_hex or "").strip() or "#00ff00"
        self.default_propagation_frames = max(0, int(self.default_propagation_frames or 0))
        if self.default_box_w is not None and self.default_box_w <= 0:
            raise ValueError("default_box_w must be > 0")
        if self.default_box_h is not None and self.default_box_h <= 0:
            raise ValueError("default_box_h must be > 0")
        if self.geometry_kind != LabelGeometryKind.bbox:
            self.default_use_fixed_box = False
            self.default_box_w = None
            self.default_box_h = None
        return self


class LabelClassReadV1(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    color_hex: str
    shortcut_key: str | None
    is_active: bool
    geometry_kind: LabelGeometryKind
    default_use_fixed_box: bool
    default_box_w: int | None
    default_box_h: int | None
    default_propagation_frames: int


class ItemReadV1(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: ItemKind
    path: str
    sha256: str
    w: int
    h: int
    duration_sec: float | None = None
    fps: float | None = None
    status: ItemStatus
    annotation_revision: int
    created_at: datetime
    updated_at: datetime


class ItemUploadResponse(BaseModel):
    item: ItemReadV1
    media_conversion: dict[str, Any] | None = None


class ReviewCommentReadV1(BaseModel):
    id: int
    item_id: int
    annotation_id: int | None = None
    reviewer_id: int
    comment: str
    mentions: list[CommentMentionRead] = Field(default_factory=list)
    annotation_revision: int | None = None
    created_at: datetime


class ExportJobCreateRequest(BaseModel):
    format: str = Field(
        pattern="^(json|csv|yolo|lf_video_tracks|lf_project|original_media)$"
    )


class ExportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requested_by: int | None = None
    status: ExportJobStatus
    format: str
    artifact_path: str | None = None
    download_name: str | None = None
    content_type: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def artifact_ready(self) -> bool:
        return bool(self.artifact_path)


class WebhookUpsertRequest(BaseModel):
    name: str
    target_url: str
    events: list[str]
    project_id: int | None = None
    is_active: bool = True
    signing_secret: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "WebhookUpsertRequest":
        self.name = (self.name or "").strip()
        self.target_url = (self.target_url or "").strip()
        if not self.name:
            raise ValueError("name must not be blank")
        if not self.target_url:
            raise ValueError("target_url must not be blank")
        normalized_events = sorted({(event or "").strip() for event in self.events if (event or "").strip()})
        if not normalized_events:
            raise ValueError("events must contain at least one value")
        self.events = normalized_events
        self.signing_secret = (self.signing_secret or "").strip() or None
        return self


class WebhookPatchRequest(BaseModel):
    name: str | None = None
    target_url: str | None = None
    events: list[str] | None = None
    project_id: int | None = None
    is_active: bool | None = None
    signing_secret: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "WebhookPatchRequest":
        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("name must not be blank")
        if self.target_url is not None:
            self.target_url = self.target_url.strip()
            if not self.target_url:
                raise ValueError("target_url must not be blank")
        if self.events is not None:
            normalized_events = sorted(
                {
                    (event or "").strip()
                    for event in self.events
                    if (event or "").strip()
                }
            )
            if not normalized_events:
                raise ValueError("events must contain at least one value")
            self.events = normalized_events
        if self.signing_secret is not None:
            self.signing_secret = self.signing_secret.strip() or None
        return self


class WebhookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    project_id: int | None = None
    name: str
    target_url: str
    events: list[str] = Field(default_factory=list)
    is_active: bool
    last_delivered_at: datetime | None = None
    last_response_status: int | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class PredictionItemReference(BaseModel):
    item_id: int | None = None
    item_path: str | None = None
    item_sha256: str | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "PredictionItemReference":
        if self.item_id is None and not (self.item_path or "").strip() and not (self.item_sha256 or "").strip():
            raise ValueError("One of item_id, item_path, or item_sha256 is required")
        self.item_path = (self.item_path or "").strip() or None
        self.item_sha256 = (self.item_sha256 or "").strip() or None
        return self


class PredictionImportEntry(BaseModel):
    item: PredictionItemReference
    label_class_id: int | None = None
    label_name: str | None = None
    frame_index: int | None = None
    track_id: int | None = None
    propagation_frames: int | None = None
    external_prediction_id: str | None = None
    confidence: float | None = None
    x1: float
    y1: float
    x2: float
    y2: float
    polygon_points: list[list[float]] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "PredictionImportEntry":
        if self.label_class_id is None and not (self.label_name or "").strip():
            raise ValueError("One of label_class_id or label_name is required")
        self.label_name = (self.label_name or "").strip() or None
        self.external_prediction_id = (self.external_prediction_id or "").strip() or None
        coords = (self.x1, self.y1, self.x2, self.y2)
        if not all(isfinite(value) for value in coords):
            raise ValueError("Coordinates must be finite numbers")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("x2/y2 must be greater than x1/y1")
        if self.frame_index is not None and self.frame_index < 0:
            raise ValueError("frame_index must be >= 0")
        if self.propagation_frames is not None and self.propagation_frames < 0:
            raise ValueError("propagation_frames must be >= 0")
        if self.confidence is not None and not isfinite(self.confidence):
            raise ValueError("confidence must be a finite number")
        if self.polygon_points is not None:
            if len(self.polygon_points) < 3:
                raise ValueError("polygon_points must contain at least 3 points")
            for point in self.polygon_points:
                if len(point) != 2 or not all(isfinite(value) for value in point):
                    raise ValueError("Each polygon point must contain 2 finite coordinates")
        return self


class PredictionRunImportRequest(BaseModel):
    name: str
    model_name: str
    model_version: str | None = None
    external_run_id: str | None = None
    metadata: dict[str, Any] | None = None
    predictions: list[PredictionImportEntry]

    @model_validator(mode="after")
    def validate_payload(self) -> "PredictionRunImportRequest":
        self.name = (self.name or "").strip()
        self.model_name = (self.model_name or "").strip()
        self.model_version = (self.model_version or "").strip() or None
        self.external_run_id = (self.external_run_id or "").strip() or None
        if not self.name:
            raise ValueError("name must not be blank")
        if not self.model_name:
            raise ValueError("model_name must not be blank")
        if not self.predictions:
            raise ValueError("predictions must not be empty")
        return self


class PredictionReadV1(BaseModel):
    id: int
    item_id: int
    label_class_id: int
    frame_index: int | None = None
    track_id: int | None = None
    propagation_frames: int | None = None
    external_prediction_id: str | None = None
    confidence: float | None = None
    x1: float
    y1: float
    x2: float
    y2: float
    polygon_points: list[list[float]] | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class PredictionRunReadV1(BaseModel):
    id: int
    project_id: int
    created_by: int | None = None
    name: str
    model_name: str
    model_version: str | None = None
    external_run_id: str | None = None
    status: PredictionRunStatus
    imported_prediction_count: int
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class PredictionRunImportResponse(BaseModel):
    run: PredictionRunReadV1
    predictions: list[PredictionReadV1] = Field(default_factory=list)


__all__ = [
    "AnnotationRead",
    "AnnotationSaveResponse",
    "ApiKeyCreateRequest",
    "ApiKeyCreateResponse",
    "ApiKeyRead",
    "ExportJobCreateRequest",
    "ExportJobRead",
    "ItemReadV1",
    "ItemUploadResponse",
    "LabelClassReadV1",
    "LabelClassUpsertRequest",
    "PredictionImportEntry",
    "PredictionReadV1",
    "PredictionRunImportRequest",
    "PredictionRunImportResponse",
    "PredictionRunReadV1",
    "ProjectCreateRequest",
    "ProjectReadV1",
    "ProjectUpdateRequest",
    "ReviewCommentReadV1",
    "WebhookPatchRequest",
    "WebhookRead",
    "WebhookUpsertRequest",
]
