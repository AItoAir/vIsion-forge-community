from __future__ import annotations

import unittest

from app.services.comment_mentions import (
    build_mention_candidates,
    normalize_comment_and_mentions,
    render_comment_html,
)


class CommentMentionHelpersTests(unittest.TestCase):
    def test_build_mention_candidates_uses_display_name_and_email(self) -> None:
        candidates = build_mention_candidates(
            [
                {"id": 2, "email": "reviewer@example.com", "name": "Reviewer"},
                {"id": 1, "email": "owner@example.com", "display_name": "Owner User"},
            ]
        )

        self.assertEqual(2, len(candidates))
        self.assertEqual("owner@example.com", candidates[0]["email"])
        self.assertEqual("@owner@example.com", candidates[0]["mention_text"])
        self.assertEqual("Owner User", candidates[0]["display_name"])

    def test_normalize_comment_and_mentions_canonicalizes_known_mentions(self) -> None:
        candidates = build_mention_candidates(
            [
                {"id": 1, "email": "owner@example.com", "display_name": "Owner"},
                {"id": 2, "email": "reviewer@example.com", "display_name": "Reviewer"},
            ]
        )

        normalized_comment, mentions = normalize_comment_and_mentions(
            "Please sync with @Owner@Example.com and @reviewer@example.com today.",
            candidates,
        )

        self.assertEqual(
            "Please sync with @owner@example.com and @reviewer@example.com today.",
            normalized_comment,
        )
        self.assertEqual(2, len(mentions))
        self.assertEqual(1, mentions[0]["user_id"])
        self.assertEqual("Owner", mentions[0]["display_name"])
        self.assertEqual("@owner@example.com", mentions[0]["mention_text"])
        self.assertEqual(
            "@owner@example.com",
            normalized_comment[mentions[0]["start"] : mentions[0]["end"]],
        )

    def test_render_comment_html_wraps_mentions_and_preserves_line_breaks(self) -> None:
        html = render_comment_html(
            "Hello @owner@example.com\nThanks.",
            [
                {
                    "user_id": 1,
                    "email": "owner@example.com",
                    "display_name": "Owner",
                    "mention_text": "@owner@example.com",
                    "start": 6,
                    "end": 24,
                }
            ],
        )

        rendered = str(html)
        self.assertIn('class="comment-mention"', rendered)
        self.assertIn("@Owner", rendered)
        self.assertIn("<br>", rendered)


if __name__ == "__main__":
    unittest.main()
