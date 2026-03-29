from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_EXACT_PATHS = {
    ".env",
}

FORBIDDEN_PREFIXES = (
    "logs/",
    "models/",
    "sam2/",
    "sam2_outputs/",
    "static/uploads/project_",
)

FORBIDDEN_SUFFIXES = (
    ".pt",
    ".pth",
    ".ckpt",
    ".zip",
)

FORBIDDEN_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(^|/)(?:exports?|exported|data/exports?)(/|$)"),
        "tracked export artifact directory",
    ),
    (
        re.compile(r"(^|/)(?:enterprise|private)(/|[._-])"),
        "tracked private or enterprise marker",
    ),
    (
        re.compile(r"(^|/)compose\.(?:enterprise|private)(?:\.[^/]+)?$"),
        "tracked private compose overlay",
    ),
)

FORBIDDEN_JSON_EXPORT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(^|/).*(?:export|dataset)[^/]*\.(?:json|jsonl)$"),
        "tracked export json artifact",
    ),
)

PLACEHOLDER_TOKENS = (
    "<organization",
    "<company",
    "<licensor",
    "your company",
    "your-company",
    "todo",
    "tbd",
    "placeholder",
    "replace me",
)

PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "your-company.com",
    "company.com",
}

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def normalized_path(path: str) -> str:
    return path.replace("\\", "/")


def read_repo_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_env_settings(relative_path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in read_repo_text(relative_path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    return any(token in normalized for token in PLACEHOLDER_TOKENS)


def find_path_violations(paths: list[str]) -> list[str]:
    violations: list[str] = []

    for path in paths:
        normalized = normalized_path(path)

        if normalized in FORBIDDEN_EXACT_PATHS:
            violations.append(f"{normalized}: tracked local env file")
            continue

        if normalized.startswith(FORBIDDEN_PREFIXES):
            violations.append(f"{normalized}: forbidden tracked directory or asset")
            continue

        if normalized.endswith(FORBIDDEN_SUFFIXES):
            violations.append(f"{normalized}: forbidden tracked binary or archive")
            continue

        for pattern, reason in FORBIDDEN_PATH_PATTERNS:
            if pattern.search(normalized):
                violations.append(f"{normalized}: {reason}")
                break
        else:
            for pattern, reason in FORBIDDEN_JSON_EXPORT_PATTERNS:
                if pattern.search(normalized):
                    violations.append(f"{normalized}: {reason}")
                    break

    return violations


def next_nonempty_line_after(text: str, marker: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != marker:
            continue
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                return candidate.strip()
        return ""
    return ""


def section_between(text: str, start_marker: str, end_marker: str) -> str:
    start_index = text.find(start_marker)
    if start_index == -1:
        return ""

    section = text[start_index + len(start_marker) :]
    end_index = section.find(end_marker)
    if end_index == -1:
        return section
    return section[:end_index]


def valid_contact_emails(text: str) -> list[str]:
    emails = [match.group(0) for match in EMAIL_PATTERN.finditer(text)]
    valid_emails: list[str] = []

    for email in emails:
        domain = email.rsplit("@", 1)[-1].lower()
        if domain in PLACEHOLDER_EMAIL_DOMAINS:
            continue
        if looks_like_placeholder(email):
            continue
        valid_emails.append(email)

    return valid_emails


def find_metadata_violations(paths: list[str]) -> list[str]:
    violations: list[str] = []
    tracked = {normalized_path(path) for path in paths}

    required_release_docs = {
        "LICENSE",
        "LICENSE.BSL-1.1",
        "COMMERCIAL_LICENSE.md",
        "THIRD_PARTY_NOTICES.md",
        "MODEL_LICENSES.md",
    }
    missing_release_docs = sorted(required_release_docs - tracked)
    if missing_release_docs:
        violations.append(
            "missing required release documentation: "
            + ", ".join(missing_release_docs)
        )

    dockerfile_text = read_repo_text("Dockerfile")
    if "LICENSE.BSL-1.1" not in dockerfile_text:
        violations.append(
            "Dockerfile must include LICENSE.BSL-1.1 in the distributed image"
        )

    app_main_text = read_repo_text("app/main.py")
    if 'allow_origins=["*"]' in app_main_text or "allow_origins=['*']" in app_main_text:
        violations.append(
            "app/main.py must not hard-code wildcard CORS origins for public releases"
        )

    license_text = read_repo_text("LICENSE")
    licensor = next_nonempty_line_after(license_text, "Licensor:")
    if looks_like_placeholder(licensor):
        violations.append("LICENSE contains a missing or placeholder Licensor value")

    commercial_text = read_repo_text("COMMERCIAL_LICENSE.md")
    contact_section = section_between(
        commercial_text,
        "## Contact",
        "When contacting",
    )
    if not valid_contact_emails(contact_section):
        violations.append(
            "COMMERCIAL_LICENSE.md must include a non-placeholder licensing contact"
        )

    cloud_env = read_env_settings(".env.cloud.example")
    if cloud_env.get("BOOTSTRAP_DEFAULT_ADMIN_ENABLED", "").lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }:
        violations.append(
            ".env.cloud.example must keep BOOTSTRAP_DEFAULT_ADMIN_ENABLED disabled by default"
        )

    return violations


def main() -> int:
    paths = tracked_files()
    violations = find_path_violations(paths)
    violations.extend(find_metadata_violations(paths))

    if not violations:
        print("Public release check passed.")
        return 0

    print("Public release check failed.")
    for violation in violations:
        print(f"- {violation}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
