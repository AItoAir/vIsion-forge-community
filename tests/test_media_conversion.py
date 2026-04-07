# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import Item, ItemKind, ItemStatus
import app.services.media as media


def make_video_item(**overrides) -> Item:
    payload = {
        "project_id": 1,
        "kind": ItemKind.video,
        "path": "uploads/project_1/demo.mp4",
        "display_path": None,
        "source_media_type": None,
        "sha256": "a" * 64,
        "w": 1920,
        "h": 1080,
        "duration_sec": 12.0,
        "fps": 30.0,
        "media_conversion_status": media.MEDIA_CONVERSION_STATUS_PENDING,
        "media_conversion_error": None,
        "media_conversion_profile": None,
        "media_conversion_size_bytes": None,
        "media_conversion_last_accessed_at": None,
        "frame_rate_mode": media.FRAME_RATE_MODE_UNKNOWN,
        "status": ItemStatus.unlabeled,
    }
    payload.update(overrides)
    return Item(**payload)


def make_image_item(**overrides) -> Item:
    payload = {
        "project_id": 1,
        "kind": ItemKind.image,
        "path": "uploads/project_1/demo.heic",
        "display_path": None,
        "source_media_type": None,
        "sha256": "b" * 64,
        "w": 512,
        "h": 512,
        "duration_sec": None,
        "fps": None,
        "media_conversion_status": media.MEDIA_CONVERSION_STATUS_NOT_REQUIRED,
        "media_conversion_error": None,
        "media_conversion_profile": None,
        "media_conversion_size_bytes": None,
        "media_conversion_last_accessed_at": None,
        "frame_rate_mode": None,
        "status": ItemStatus.unlabeled,
    }
    payload.update(overrides)
    return Item(**payload)


class MediaConversionTests(unittest.TestCase):
    class _FakeQuery:
        def __init__(self, items):
            self._items = items

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return list(self._items)

    class _FakeDb:
        def __init__(self, items):
            self._items = items
            self.added = []

        def query(self, _model):
            return MediaConversionTests._FakeQuery(self._items)

        def add(self, item):
            self.added.append(item)

    def test_detect_frame_rate_mode_flags_vfr(self) -> None:
        frame_rate_mode = media._detect_frame_rate_mode(
            {"avg_frame_rate": "30000/1001", "r_frame_rate": "60/1"}
        )
        self.assertEqual(frame_rate_mode, media.FRAME_RATE_MODE_VFR)

    def test_sync_item_media_conversion_state_marks_vfr_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            source_path = static_dir / "uploads" / "project_1" / "demo.mp4"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"vfr-video")
            item = make_video_item(frame_rate_mode=media.FRAME_RATE_MODE_VFR)

            with patch.object(media, "static_root", return_value=static_dir):
                changed = media.sync_item_media_conversion_state(item)

            self.assertTrue(changed)
            self.assertEqual(
                item.media_conversion_status, media.MEDIA_CONVERSION_STATUS_FAILED
            )
            self.assertIn("constant frame rate", item.media_conversion_error or "")

    def test_sync_item_media_conversion_state_requeues_missing_converted_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            source_path = static_dir / "uploads" / "project_1" / "demo.mp4"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"original-video")
            item = make_video_item(
                media_conversion_status=media.MEDIA_CONVERSION_STATUS_READY,
                media_conversion_profile=media.labeling_proxy_profile_token(),
            )

            with patch.object(media, "static_root", return_value=static_dir):
                changed = media.sync_item_media_conversion_state(item)

            self.assertTrue(changed)
            self.assertEqual(
                item.media_conversion_status, media.MEDIA_CONVERSION_STATUS_PENDING
            )

    def test_resolve_media_source_path_returns_existing_static_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            source_path = static_dir / "uploads" / "project_1" / "demo.mp4"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"video-bytes")
            item = make_video_item(path="uploads/project_1/demo.mp4")

            with patch.object(media, "static_root", return_value=static_dir):
                resolved = media.resolve_media_source_path(item)

            self.assertEqual(source_path, resolved)

    def test_sync_item_media_conversion_state_marks_missing_source_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            item = make_video_item(
                path="uploads/project_1/missing.mp4",
                media_conversion_status=media.MEDIA_CONVERSION_STATUS_READY,
                media_conversion_profile=media.labeling_proxy_profile_token(),
            )

            with patch.object(media, "static_root", return_value=static_dir):
                changed = media.sync_item_media_conversion_state(item)

            self.assertTrue(changed)
            self.assertEqual(
                item.media_conversion_status, media.MEDIA_CONVERSION_STATUS_FAILED
            )
            self.assertIn("Video source was not found", item.media_conversion_error or "")

    def test_sync_item_media_conversion_state_requeues_when_missing_source_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            source_path = static_dir / "uploads" / "project_1" / "demo.mp4"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"restored-video")
            item = make_video_item(
                path="uploads/project_1/demo.mp4",
                media_conversion_status=media.MEDIA_CONVERSION_STATUS_FAILED,
                media_conversion_error="Video source was not found: uploads/project_1/demo.mp4",
                media_conversion_profile=media.labeling_proxy_profile_token(),
            )

            with patch.object(media, "static_root", return_value=static_dir):
                changed = media.sync_item_media_conversion_state(item)

            self.assertTrue(changed)
            self.assertEqual(
                item.media_conversion_status, media.MEDIA_CONVERSION_STATUS_PENDING
            )
            self.assertIsNone(item.media_conversion_error)
            self.assertTrue(source_path.is_file())

    def test_build_annotation_media_state_uses_converted_path_when_ready(self) -> None:
        item = make_video_item(
            media_conversion_status=media.MEDIA_CONVERSION_STATUS_READY,
            media_conversion_profile=media.labeling_proxy_profile_token(),
        )

        state = media.build_annotation_media_state(item)

        self.assertTrue(state.ready)
        self.assertIn(".label_proxy.", state.display_media_path)

    def test_build_annotation_media_state_prefers_display_path_when_video_not_ready(self) -> None:
        item = make_video_item(
            path="uploads/project_1/study.nii.gz",
            display_path="uploads/project_1/study.nii.medical_preview.mp4",
            source_media_type="nifti",
            media_conversion_status=media.MEDIA_CONVERSION_STATUS_PENDING,
        )

        state = media.build_annotation_media_state(item)

        self.assertEqual(
            "uploads/project_1/study.nii.medical_preview.mp4",
            state.display_media_path,
        )

    def test_resolve_annotation_media_path_prefers_display_path_for_image(self) -> None:
        item = Item(
            project_id=1,
            kind=ItemKind.image,
            path="uploads/project_1/study.dcm",
            display_path="uploads/project_1/study.medical_preview.png",
            source_media_type="dicom",
            sha256="b" * 64,
            w=512,
            h=512,
            status=ItemStatus.unlabeled,
        )

        self.assertEqual(
            "uploads/project_1/study.medical_preview.png",
            media.resolve_annotation_media_path(item),
        )

    def test_resolve_annotation_media_path_prefers_generated_preview_for_legacy_heic_image(self) -> None:
        item = make_image_item(path="uploads/project_1/IMG_0125.HEIC")

        self.assertEqual(
            "uploads/project_1/IMG_0125.display_preview.png",
            media.resolve_annotation_media_path(item),
        )

    def test_preferred_item_media_variant_uses_display_for_legacy_heic_image(self) -> None:
        item = make_image_item(path="uploads/project_1/IMG_0125.HEIC")

        self.assertEqual("display", media.preferred_item_media_variant(item))

    def test_ensure_browser_compatible_image_preview_generates_and_persists_preview_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            source_path = static_dir / "uploads" / "project_1" / "IMG_0125.HEIC"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"legacy-heic")
            item = make_image_item(path="uploads/project_1/IMG_0125.HEIC")

            def _fake_write_preview(source: Path, preview: Path) -> None:
                self.assertEqual(source_path, source)
                preview.write_bytes(b"preview-bytes")

            with (
                patch.object(media, "static_root", return_value=static_dir),
                patch.object(
                    media,
                    "_write_legacy_image_display_preview",
                    side_effect=_fake_write_preview,
                ),
            ):
                changed = media.ensure_browser_compatible_image_preview(item)

            self.assertTrue(changed)
            self.assertEqual(
                "uploads/project_1/IMG_0125.display_preview.png",
                item.display_path,
            )
            self.assertEqual(
                b"preview-bytes",
                (
                    static_dir
                    / "uploads"
                    / "project_1"
                    / "IMG_0125.display_preview.png"
                ).read_bytes(),
            )

    def test_guess_media_type_recognizes_heic(self) -> None:
        self.assertEqual("image/heic", media.guess_media_type("IMG_0125.HEIC"))

    def test_collect_labeling_proxy_storage_candidates_maps_display_path_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            proxy_relative_path = (
                "uploads/project_1/study.nii.medical_preview"
                f".label_proxy.{media.labeling_proxy_profile_token()}.mp4"
            )
            proxy_path = static_dir / proxy_relative_path
            proxy_path.parent.mkdir(parents=True, exist_ok=True)
            proxy_path.write_bytes(b"proxy")
            item = make_video_item(
                path="uploads/project_1/study.nii.gz",
                display_path="uploads/project_1/study.nii.medical_preview.mp4",
                source_media_type="nifti",
            )
            fake_db = self._FakeDb([item])

            with patch.object(media, "static_root", return_value=static_dir):
                candidates = media._collect_labeling_proxy_storage_candidates(fake_db)

            self.assertEqual(1, len(candidates))
            self.assertIs(item, candidates[0].item)
            self.assertFalse(candidates[0].orphaned)
            self.assertEqual(proxy_path, candidates[0].proxy_path)

    def test_evict_labeling_proxy_candidate_uses_display_path_proxy_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            item = make_video_item(
                path="uploads/project_1/study.nii.gz",
                display_path="uploads/project_1/study.nii.medical_preview.mp4",
                source_media_type="nifti",
                media_conversion_status=media.MEDIA_CONVERSION_STATUS_READY,
            )
            proxy_relative_path = media.labeling_proxy_relative_path(
                media.annotation_source_relative_path(item)
            )
            proxy_path = static_dir / proxy_relative_path
            proxy_path.parent.mkdir(parents=True, exist_ok=True)
            proxy_path.write_bytes(b"proxy")
            candidate = media.LabelingProxyStorageCandidate(
                proxy_path=proxy_path,
                size_bytes=proxy_path.stat().st_size,
                item=item,
                last_accessed_at=None,
                orphaned=False,
            )
            fake_db = self._FakeDb([item])

            with patch.object(media, "static_root", return_value=static_dir):
                media._evict_labeling_proxy_candidate(fake_db, candidate, reason="budget")

            self.assertFalse(proxy_path.exists())
            self.assertEqual(media.MEDIA_CONVERSION_STATUS_PENDING, item.media_conversion_status)
            self.assertIsNone(item.media_conversion_error)
            self.assertIsNone(item.media_conversion_size_bytes)
            self.assertEqual([item], fake_db.added)

    def test_plan_labeling_proxy_storage_evictions_prefers_orphans_then_lru(self) -> None:
        now = datetime(2026, 3, 28, tzinfo=timezone.utc)
        keep_item = make_video_item(id=11)
        old_item = make_video_item(id=12)
        newer_item = make_video_item(id=13)
        candidates = [
            media.LabelingProxyStorageCandidate(
                proxy_path=Path("/tmp/orphan.label_proxy.mp4"),
                size_bytes=200,
                item=None,
                last_accessed_at=now - timedelta(days=30),
                orphaned=True,
            ),
            media.LabelingProxyStorageCandidate(
                proxy_path=Path("/tmp/old.label_proxy.mp4"),
                size_bytes=350,
                item=old_item,
                last_accessed_at=now - timedelta(days=10),
                orphaned=False,
            ),
            media.LabelingProxyStorageCandidate(
                proxy_path=Path("/tmp/newer.label_proxy.mp4"),
                size_bytes=250,
                item=newer_item,
                last_accessed_at=now - timedelta(days=2),
                orphaned=False,
            ),
            media.LabelingProxyStorageCandidate(
                proxy_path=Path("/tmp/keep.label_proxy.mp4"),
                size_bytes=300,
                item=keep_item,
                last_accessed_at=now - timedelta(days=20),
                orphaned=False,
            ),
        ]

        plan = media.plan_labeling_proxy_storage_evictions(
            candidates,
            budget_bytes=700,
            reserve_bytes=0,
            ttl_days=None,
            exclude_item_ids={keep_item.id},
            now=now,
        )

        self.assertEqual(
            [(entry[0].proxy_path.name, entry[1]) for entry in plan],
            [
                ("orphan.label_proxy.mp4", "orphan"),
                ("old.label_proxy.mp4", "budget"),
            ],
        )

    def test_plan_labeling_proxy_storage_evictions_uses_ttl_before_budget(self) -> None:
        now = datetime(2026, 3, 28, tzinfo=timezone.utc)
        expired_item = make_video_item(id=21)
        fresh_item = make_video_item(id=22)
        candidates = [
            media.LabelingProxyStorageCandidate(
                proxy_path=Path("/tmp/expired.label_proxy.mp4"),
                size_bytes=128,
                item=expired_item,
                last_accessed_at=now - timedelta(days=30),
                orphaned=False,
            ),
            media.LabelingProxyStorageCandidate(
                proxy_path=Path("/tmp/fresh.label_proxy.mp4"),
                size_bytes=128,
                item=fresh_item,
                last_accessed_at=now - timedelta(days=1),
                orphaned=False,
            ),
        ]

        plan = media.plan_labeling_proxy_storage_evictions(
            candidates,
            budget_bytes=1024,
            reserve_bytes=0,
            ttl_days=14,
            exclude_item_ids=set(),
            now=now,
        )

        self.assertEqual(
            [(entry[0].proxy_path.name, entry[1]) for entry in plan],
            [("expired.label_proxy.mp4", "ttl")],
        )


if __name__ == "__main__":
    unittest.main()
