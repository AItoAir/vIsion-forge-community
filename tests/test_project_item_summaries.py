from __future__ import annotations

import unittest

from app.models import Item, ItemKind, ItemStatus
from app.routers.web_projects import (
    ITEM_LABEL_SUMMARY_PREVIEW_LIMIT,
    _build_item_label_summaries,
)


def make_item(*, item_id: int, kind: ItemKind) -> Item:
    return Item(
        id=item_id,
        project_id=1,
        kind=kind,
        path=f"uploads/project_1/item_{item_id}",
        sha256="a" * 64,
        w=1280,
        h=720,
        duration_sec=12.0 if kind == ItemKind.video else None,
        fps=30.0 if kind == ItemKind.video else None,
        media_conversion_status="not_required",
        media_conversion_error=None,
        media_conversion_profile=None,
        media_conversion_size_bytes=None,
        media_conversion_last_accessed_at=None,
        frame_rate_mode=None,
        status=ItemStatus.unlabeled,
    )


class ProjectItemLabelSummaryTests(unittest.TestCase):
    def test_video_label_summary_counts_objects_and_distinct_frames(self) -> None:
        item = make_item(item_id=10, kind=ItemKind.video)

        summaries = _build_item_label_summaries(
            [item],
            [
                (10, 2, "Car", "#2563eb", 3, 1),
                (10, 1, "Person", "#ef4444", 0, 2),
                (10, 1, "Person", "#ef4444", 1, 0),
                (10, 1, "Person", "#ef4444", 5, 0),
            ],
        )

        labels = summaries[item.id]["labels"]
        self.assertEqual([entry["name"] for entry in labels], ["Car", "Person"])

        car_summary = labels[0]
        self.assertEqual(car_summary["object_count"], 2)
        self.assertEqual(car_summary["frame_count"], 2)

        person_summary = labels[1]
        self.assertEqual(person_summary["object_count"], 5)
        self.assertEqual(person_summary["frame_count"], 4)

    def test_image_label_summary_uses_single_frame_and_hides_extra_labels(self) -> None:
        item = make_item(item_id=11, kind=ItemKind.image)

        summaries = _build_item_label_summaries(
            [item],
            [
                (11, 1, "Apple", "#111111", None, None),
                (11, 1, "Apple", "#111111", None, None),
                (11, 2, "Banana", "#222222", None, None),
                (11, 3, "Cat", "#333333", None, None),
                (11, 4, "Dog", "#444444", None, None),
            ],
        )

        labels = summaries[item.id]["labels"]
        self.assertEqual([entry["name"] for entry in labels], ["Apple", "Banana", "Cat", "Dog"])
        self.assertEqual(labels[0]["object_count"], 2)
        self.assertEqual(labels[0]["frame_count"], 1)
        self.assertEqual(labels[1]["frame_count"], 1)

        self.assertEqual(
            [entry["name"] for entry in summaries[item.id]["preview_labels"]],
            ["Apple", "Banana", "Cat"],
        )
        self.assertEqual(
            [entry["name"] for entry in summaries[item.id]["hidden_labels"]],
            ["Dog"],
        )
        self.assertEqual(
            summaries[item.id]["hidden_count"],
            max(0, len(labels) - ITEM_LABEL_SUMMARY_PREVIEW_LIMIT),
        )


if __name__ == "__main__":
    unittest.main()
