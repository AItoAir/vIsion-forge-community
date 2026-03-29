from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import Item, ItemKind, ItemStatus
import app.services.media as media


def make_video_item(**overrides) -> Item:
    payload = {
        "project_id": 1,
        "kind": ItemKind.video,
        "path": "uploads/project_1/demo.mp4",
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


class MediaConversionTests(unittest.TestCase):
    def test_detect_frame_rate_mode_flags_vfr(self) -> None:
        frame_rate_mode = media._detect_frame_rate_mode(
            {"avg_frame_rate": "30000/1001", "r_frame_rate": "60/1"}
        )
        self.assertEqual(frame_rate_mode, media.FRAME_RATE_MODE_VFR)

    def test_sync_item_media_conversion_state_marks_vfr_as_failed(self) -> None:
        item = make_video_item(frame_rate_mode=media.FRAME_RATE_MODE_VFR)

        changed = media.sync_item_media_conversion_state(item)

        self.assertTrue(changed)
        self.assertEqual(
            item.media_conversion_status, media.MEDIA_CONVERSION_STATUS_FAILED
        )
        self.assertIn("constant frame rate", item.media_conversion_error or "")

    def test_sync_item_media_conversion_state_requeues_missing_converted_video(self) -> None:
        item = make_video_item(
            media_conversion_status=media.MEDIA_CONVERSION_STATUS_READY,
            media_conversion_profile=media.labeling_proxy_profile_token(),
        )

        changed = media.sync_item_media_conversion_state(item)

        self.assertTrue(changed)
        self.assertEqual(
            item.media_conversion_status, media.MEDIA_CONVERSION_STATUS_PENDING
        )

    def test_build_annotation_media_state_uses_converted_path_when_ready(self) -> None:
        item = make_video_item(
            media_conversion_status=media.MEDIA_CONVERSION_STATUS_READY,
            media_conversion_profile=media.labeling_proxy_profile_token(),
        )

        state = media.build_annotation_media_state(item)

        self.assertTrue(state.ready)
        self.assertIn(".label_proxy.", state.display_media_path)

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
