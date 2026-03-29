from __future__ import annotations

import enum
import json
from datetime import datetime
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def generate_annotation_client_uid() -> str:
    return uuid4().hex


class UserRole(str, enum.Enum):
    annotator = "annotator"
    reviewer = "reviewer"
    project_admin = "project_admin"
    system_admin = "system_admin"


class ItemKind(str, enum.Enum):
    image = "image"
    video = "video"


class ItemStatus(str, enum.Enum):
    unlabeled = "unlabeled"
    in_progress = "in_progress"
    done = "done"
    needs_review = "needs_review"


class AnnotationStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Sam2JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class LabelGeometryKind(str, enum.Enum):
    bbox = "bbox"
    polygon = "polygon"
    tag = "tag"


class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    users: Mapped[list["User"]] = relationship(back_populates="team")


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    team: Mapped["Team | None"] = relationship(back_populates="users")
    projects_owned: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return (self.name or "").strip() or self.email


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="projects_owned")
    items: Mapped[list["Item"]] = relationship(back_populates="project")
    label_classes: Mapped[list["LabelClass"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    kind: Mapped[ItemKind] = mapped_column(Enum(ItemKind), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    w: Mapped[int] = mapped_column(Integer, nullable=False)
    h: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    media_conversion_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="not_required",
        server_default=text("'not_required'"),
    )
    media_conversion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_conversion_profile: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    media_conversion_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    media_conversion_last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    frame_rate_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus), default=ItemStatus.unlabeled, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    annotation_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    project: Mapped[Project] = relationship(back_populates="items")
    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    region_comments: Mapped[list["RegionComment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    review_comments: Mapped[list["ReviewComment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return PurePosixPath(self.path).name or self.path


class LabelClass(Base):
    __tablename__ = "label_class"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False, default="#00ff00")
    shortcut_key: Mapped[str | None] = mapped_column(String(1), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    geometry_kind: Mapped[LabelGeometryKind] = mapped_column(
        Enum(LabelGeometryKind), nullable=False, default=LabelGeometryKind.bbox
    )
    default_use_fixed_box: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=text("false"),
    )
    default_box_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_box_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_propagation_frames: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    project: Mapped[Project] = relationship(back_populates="label_classes")
    annotations: Mapped[list["Annotation"]] = relationship(back_populates="label_class")


class Annotation(Base):
    __tablename__ = "annotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"), nullable=False)
    label_class_id: Mapped[int] = mapped_column(ForeignKey("label_class.id"), nullable=False)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    propagation_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_uid: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=generate_annotation_client_uid,
    )
    is_occluded: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=text("false"),
    )
    is_truncated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=text("false"),
    )
    is_outside: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=text("false"),
    )
    is_lost: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=text("false"),
    )
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    x2: Mapped[float] = mapped_column(Float, nullable=False)
    y2: Mapped[float] = mapped_column(Float, nullable=False)
    points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    status: Mapped[AnnotationStatus] = mapped_column(
        Enum(AnnotationStatus), default=AnnotationStatus.pending, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    item: Mapped[Item] = relationship(back_populates="annotations")
    label_class: Mapped[LabelClass] = relationship(back_populates="annotations")
    created_by_user: Mapped["User | None"] = relationship(foreign_keys=[created_by])
    updated_by_user: Mapped["User | None"] = relationship(foreign_keys=[updated_by])
    review_comments: Mapped[list["ReviewComment"]] = relationship(
        back_populates="annotation"
    )

    @property
    def polygon_points(self) -> list[list[float]] | None:
        if not self.points_json:
            return None
        try:
            value = json.loads(self.points_json)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, list) else None

    @polygon_points.setter
    def polygon_points(self, value: list[list[float]] | None) -> None:
        self.points_json = (
            json.dumps(value, ensure_ascii=False) if value else None
        )


class RegionComment(Base):
    __tablename__ = "region_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"), nullable=False)
    client_uid: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=generate_annotation_client_uid,
    )
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    x2: Mapped[float] = mapped_column(Float, nullable=False)
    y2: Mapped[float] = mapped_column(Float, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    item: Mapped[Item] = relationship(back_populates="region_comments")
    created_by_user: Mapped["User | None"] = relationship(foreign_keys=[created_by])
    updated_by_user: Mapped["User | None"] = relationship(foreign_keys=[updated_by])


class ReviewComment(Base):
    __tablename__ = "review_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"), nullable=False)
    annotation_id: Mapped[int | None] = mapped_column(
        ForeignKey("annotation.id"), nullable=True
    )
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    annotation_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    item: Mapped[Item] = relationship(back_populates="review_comments")
    annotation: Mapped["Annotation | None"] = relationship(back_populates="review_comments")
    reviewer: Mapped[User] = relationship()

    @property
    def snapshot_payload(self) -> dict | None:
        if not self.snapshot_json:
            return None
        try:
            value = json.loads(self.snapshot_json)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped["User | None"] = relationship()


class Sam2TrackJob(Base):
    __tablename__ = "sam2_track_job"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("item.id"), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    status: Mapped[Sam2JobStatus] = mapped_column(
        Enum(Sam2JobStatus),
        nullable=False,
        default=Sam2JobStatus.queued,
        server_default=text("'queued'"),
    )
    label_class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_start_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_end_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_annotation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    item: Mapped[Item] = relationship()
    requested_by_user: Mapped["User | None"] = relationship(foreign_keys=[requested_by])
