from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from markupsafe import Markup, escape


MENTION_EMAIL_PATTERN = re.compile(
    r"(?<![\w@])@([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
)


def _normalize_candidate(candidate: Any) -> Optional[Dict[str, Any]]:
    if candidate is None:
        return None

    if isinstance(candidate, dict):
        user_id = candidate.get("id")
        email = candidate.get("email")
        name = candidate.get("name")
        display_name = candidate.get("display_name")
    else:
        user_id = getattr(candidate, "id", None)
        email = getattr(candidate, "email", None)
        name = getattr(candidate, "name", None)
        display_name = getattr(candidate, "display_name", None)

    try:
        normalized_user_id = int(user_id)
    except (TypeError, ValueError):
        return None

    normalized_email = str(email or "").strip().lower()
    if not normalized_email:
        return None

    normalized_name = str(name or "").strip() or None
    normalized_display_name = (
        str(display_name or "").strip()
        or normalized_name
        or normalized_email
    )

    return {
        "id": normalized_user_id,
        "email": normalized_email,
        "name": normalized_name,
        "display_name": normalized_display_name,
        "mention_text": f"@{normalized_email}",
    }


def build_mention_candidates(users: Iterable[Any]) -> List[Dict[str, Any]]:
    candidates_by_id: Dict[int, Dict[str, Any]] = {}
    for user in users:
        normalized = _normalize_candidate(user)
        if normalized is None:
            continue
        candidates_by_id[normalized["id"]] = normalized

    return sorted(
        candidates_by_id.values(),
        key=lambda candidate: (
            candidate["display_name"].lower(),
            candidate["email"],
            candidate["id"],
        ),
    )


def build_project_mention_candidates(db: Any, project: Any) -> List[Dict[str, Any]]:
    owner = getattr(project, "owner", None)
    team_id = getattr(owner, "team_id", None)
    if team_id is None:
        return []

    from sqlalchemy import select

    from ..models import User

    users = (
        db.execute(
            select(User)
            .where(
                User.team_id == team_id,
                User.is_active.is_(True),
            )
            .order_by(User.name.asc(), User.email.asc(), User.id.asc())
        )
        .scalars()
        .all()
    )
    return build_mention_candidates(users)


def normalize_mentions_metadata(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized_mentions: List[Dict[str, Any]] = []
    for raw_mention in value:
        if not isinstance(raw_mention, dict):
            continue

        try:
            user_id = int(raw_mention.get("user_id"))
        except (TypeError, ValueError):
            continue

        email = str(raw_mention.get("email") or "").strip().lower()
        if not email:
            continue

        display_name = str(raw_mention.get("display_name") or "").strip() or email
        mention_text = str(raw_mention.get("mention_text") or "").strip() or f"@{email}"

        try:
            start = int(raw_mention.get("start"))
            end = int(raw_mention.get("end"))
        except (TypeError, ValueError):
            start = -1
            end = -1

        normalized_mentions.append(
            {
                "user_id": user_id,
                "email": email,
                "display_name": display_name,
                "mention_text": mention_text,
                "start": start,
                "end": end,
            }
        )

    normalized_mentions.sort(
        key=lambda mention: (mention["start"], mention["end"], mention["user_id"])
    )
    return normalized_mentions


def mentions_json_dumps(value: Optional[Sequence[Dict[str, Any]]]) -> Optional[str]:
    normalized_mentions = normalize_mentions_metadata(list(value or []))
    if not normalized_mentions:
        return None
    return json.dumps(normalized_mentions, ensure_ascii=False, separators=(",", ":"))


def mentions_json_loads(value: Optional[str]) -> List[Dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return normalize_mentions_metadata(parsed)


def normalize_comment_and_mentions(
    comment_text: str,
    mention_candidates: Optional[Sequence[Dict[str, Any]]],
) -> Tuple[str, List[Dict[str, Any]]]:
    normalized_comment = str(comment_text or "").strip()
    if not normalized_comment:
        return "", []

    candidates_by_email = {
        str(candidate.get("email") or "").strip().lower(): candidate
        for candidate in (mention_candidates or [])
        if isinstance(candidate, dict)
    }

    output_parts: List[str] = []
    mentions: List[Dict[str, Any]] = []
    output_length = 0
    last_index = 0

    for match in MENTION_EMAIL_PATTERN.finditer(normalized_comment):
        matched_email = match.group(1).strip().lower()
        candidate = candidates_by_email.get(matched_email)
        if candidate is None:
            continue

        prefix = normalized_comment[last_index : match.start()]
        output_parts.append(prefix)
        output_length += len(prefix)

        mention_text = str(candidate.get("mention_text") or f"@{matched_email}")
        start = output_length
        end = start + len(mention_text)
        output_parts.append(mention_text)
        output_length = end
        last_index = match.end()

        mentions.append(
            {
                "user_id": int(candidate["id"]),
                "email": str(candidate["email"]),
                "display_name": str(candidate.get("display_name") or candidate["email"]),
                "mention_text": mention_text,
                "start": start,
                "end": end,
            }
        )

    suffix = normalized_comment[last_index:]
    output_parts.append(suffix)
    normalized_comment = "".join(output_parts)
    return normalized_comment, normalize_mentions_metadata(mentions)


def mentioned_user_ids(mentions: Optional[Sequence[Dict[str, Any]]]) -> Set[int]:
    user_ids: Set[int] = set()
    for mention in normalize_mentions_metadata(list(mentions or [])):
        user_ids.add(int(mention["user_id"]))
    return user_ids


def comment_preview(comment_text: str, *, max_length: int = 96) -> str:
    normalized = " ".join(str(comment_text or "").split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."


def _mention_display_text(mention: Dict[str, Any]) -> str:
    display_name = str(mention.get("display_name") or "").strip()
    email = str(mention.get("email") or "").strip()
    if display_name and display_name != email:
        return f"@{display_name}"
    return str(mention.get("mention_text") or f"@{email}").strip() or f"@{email}"


def render_comment_html(
    comment_text: str,
    mentions: Optional[Sequence[Dict[str, Any]]],
) -> Markup:
    normalized_comment = str(comment_text or "")
    normalized_mentions = normalize_mentions_metadata(list(mentions or []))
    parts: List[str] = []
    cursor = 0

    for mention in normalized_mentions:
        start = int(mention.get("start", -1))
        end = int(mention.get("end", -1))
        mention_text = str(mention.get("mention_text") or "")
        if start < cursor or end <= start or end > len(normalized_comment):
            continue
        if normalized_comment[start:end] != mention_text:
            continue

        parts.append(escape(normalized_comment[cursor:start]))
        classes = "comment-mention"
        title = escape(str(mention.get("email") or ""))
        display_text = escape(_mention_display_text(mention))
        parts.append(
            f'<span class="{classes}" title="{title}">{display_text}</span>'
        )
        cursor = end

    parts.append(escape(normalized_comment[cursor:]))
    return Markup("".join(parts).replace("\n", "<br>"))
