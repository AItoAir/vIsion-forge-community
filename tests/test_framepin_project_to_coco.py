# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_PATH = REPO_ROOT / "tools" / "framepin_project_to_coco.py"


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("framepin_project_to_coco", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load tool module from {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


framepin_project_to_coco = _load_tool_module()


class FramePinProjectToCocoTests(unittest.TestCase):
    def test_convert_polygon_and_bbox_annotations_into_coco(self) -> None:
        payload = {
            "schema": "lf_project_v2",
            "project": {"id": 7, "name": "Sample project"},
            "statuses": ["pending", "approved", "rejected", "mixed"],
            "label_classes": [
                {"id": 11, "idx": 0, "name": "person", "geom": "bbox", "color": "#00ff00", "key": "1"},
                {"id": 12, "idx": 1, "name": "tag_roi", "geom": "polygon", "color": "#ff0000", "key": "2"},
                {"id": 13, "idx": 2, "name": "scene_tag", "geom": "tag", "color": "#0000ff", "key": "3"},
            ],
            "items": [
                {
                    "id": 101,
                    "path": "uploads/project_7/sample.png",
                    "display_path": None,
                    "source_media_type": None,
                    "kind": "image",
                    "w": 100,
                    "h": 80,
                    "status": "done",
                    "anns": [
                        [0, 10, 5, 50, 55, 1],
                        [1, 20, 10, 90, 70, 0],
                        [2, 0, 0, 100, 80, 1],
                    ],
                    "ann_polygons": [
                        None,
                        [20, 10, 90, 10, 90, 70, 20, 70],
                        None,
                    ],
                    "ann_flags": [
                        {},
                        {"occluded": True},
                        {},
                    ],
                }
            ],
        }

        coco_payload, stats = framepin_project_to_coco.convert_lf_project_to_coco(payload)

        self.assertEqual(1, stats.image_count)
        self.assertEqual(2, stats.annotation_count)
        self.assertEqual(1, stats.skipped_unsupported_geometries)

        self.assertEqual(1, len(coco_payload["images"]))
        self.assertEqual(2, len(coco_payload["categories"]))
        self.assertEqual(["person", "tag_roi"], [category["name"] for category in coco_payload["categories"]])

        bbox_annotation = coco_payload["annotations"][0]
        polygon_annotation = coco_payload["annotations"][1]

        self.assertEqual([10.0, 5.0, 40.0, 50.0], bbox_annotation["bbox"])
        self.assertEqual([], bbox_annotation["segmentation"])
        self.assertEqual(2000.0, bbox_annotation["area"])
        self.assertEqual("approved", bbox_annotation["framepin_annotation_status"])

        self.assertEqual([20.0, 10.0, 70.0, 60.0], polygon_annotation["bbox"])
        self.assertEqual([[20.0, 10.0, 90.0, 10.0, 90.0, 70.0, 20.0, 70.0]], polygon_annotation["segmentation"])
        self.assertEqual(4200.0, polygon_annotation["area"])
        self.assertEqual({"occluded": True}, polygon_annotation["framepin_flags"])
        self.assertEqual(12, polygon_annotation["framepin_label_class_id"])

    def test_status_filter_and_clipping_reduce_output(self) -> None:
        payload = {
            "schema": "lf_project_v2",
            "project": {"id": 8, "name": "Filtered project"},
            "statuses": ["pending", "approved", "rejected", "mixed"],
            "label_classes": [
                {"id": 21, "idx": 0, "name": "box", "geom": "bbox", "color": "#00ff00", "key": "1"},
            ],
            "items": [
                {
                    "id": 201,
                    "path": "uploads/project_8/clipped.png",
                    "display_path": None,
                    "source_media_type": None,
                    "kind": "image",
                    "w": 100,
                    "h": 50,
                    "status": "in_progress",
                    "anns": [
                        [0, -10, -5, 110, 20, 1],
                        [0, 10, 10, 20, 20, 0],
                    ],
                }
            ],
        }

        coco_payload, stats = framepin_project_to_coco.convert_lf_project_to_coco(
            payload,
            include_statuses={"approved"},
            clip_to_image=True,
        )

        self.assertEqual(1, stats.annotation_count)
        self.assertEqual(1, stats.skipped_filtered_statuses)

        annotation = coco_payload["annotations"][0]
        self.assertEqual([0.0, 0.0, 100.0, 20.0], annotation["bbox"])
        self.assertEqual(2000.0, annotation["area"])
        self.assertEqual("approved", annotation["framepin_annotation_status"])


if __name__ == "__main__":
    unittest.main()
