# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_311_PLUS = sys.version_info >= (3, 11)


def _cleanup_tree(path: Path) -> None:
    for _attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            time.sleep(0.2)
    shutil.rmtree(path, ignore_errors=True)


class AppImportSmokeTests(unittest.TestCase):
    @unittest.skipUnless(PYTHON_311_PLUS, "app.main import smoke test requires Python 3.11+")
    def test_app_main_imports_with_sqlite(self) -> None:
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            db_path = tmp_dir / "frame_pin_validate.db"
            env = os.environ.copy()
            env.update(
                {
                    "DATABASE_URL": f"sqlite+pysqlite:///{db_path.as_posix()}",
                    "ENV": "dev",
                    "SECRET_KEY": "smoke-test-secret",
                    "SESSION_COOKIE_HTTPS_ONLY": "0",
                    "SESSION_COOKIE_SAME_SITE": "lax",
                    "TRUST_PROXY_HEADERS": "0",
                    "TRUSTED_PROXY_IPS": "127.0.0.1",
                    "BOOTSTRAP_DEFAULT_ADMIN_ENABLED": "0",
                    "BOOTSTRAP_DEFAULT_ADMIN_EMAIL": "",
                    "BOOTSTRAP_DEFAULT_ADMIN_PASSWORD": "",
                    "APP_EXTENSION_HOOKS": "",
                    "CORS_ALLOW_ORIGINS": "",
                    "PASSWORD_SALT": "",
                    "SAM2_ENABLED": "0",
                }
            )

            result = subprocess.run(
                [sys.executable, "-c", "import app.main"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        finally:
            _cleanup_tree(tmp_dir)

        if result.returncode != 0:
            self.fail(
                "Importing app.main failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )

    @unittest.skipUnless(PYTHON_311_PLUS, "legacy schema smoke test requires Python 3.11+")
    def test_app_main_backfills_new_item_columns_for_legacy_sqlite_schema(self) -> None:
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            db_path = tmp_dir / "frame_pin_legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE item (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        kind VARCHAR(16) NOT NULL,
                        path VARCHAR(1024) NOT NULL,
                        sha256 VARCHAR(64) NOT NULL,
                        w INTEGER NOT NULL,
                        h INTEGER NOT NULL,
                        duration_sec FLOAT,
                        fps FLOAT,
                        media_conversion_status VARCHAR(32) DEFAULT 'not_required' NOT NULL,
                        media_conversion_error TEXT,
                        media_conversion_profile VARCHAR(255),
                        media_conversion_size_bytes BIGINT,
                        media_conversion_last_accessed_at TIMESTAMP,
                        frame_rate_mode VARCHAR(16),
                        status VARCHAR(32) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        annotation_revision INTEGER DEFAULT 0 NOT NULL
                    );
                    """
                )

            env = os.environ.copy()
            env.update(
                {
                    "DATABASE_URL": f"sqlite+pysqlite:///{db_path.as_posix()}",
                    "ENV": "dev",
                    "SECRET_KEY": "smoke-test-secret",
                    "SESSION_COOKIE_HTTPS_ONLY": "0",
                    "SESSION_COOKIE_SAME_SITE": "lax",
                    "TRUST_PROXY_HEADERS": "0",
                    "TRUSTED_PROXY_IPS": "127.0.0.1",
                    "BOOTSTRAP_DEFAULT_ADMIN_ENABLED": "0",
                    "BOOTSTRAP_DEFAULT_ADMIN_EMAIL": "",
                    "BOOTSTRAP_DEFAULT_ADMIN_PASSWORD": "",
                    "APP_EXTENSION_HOOKS": "",
                    "CORS_ALLOW_ORIGINS": "",
                    "PASSWORD_SALT": "",
                    "SAM2_ENABLED": "0",
                }
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sqlite3, app.main; "
                        f"conn = sqlite3.connect(r'{db_path.as_posix()}'); "
                        "cols = {row[1] for row in conn.execute(\"PRAGMA table_info(item)\")}; "
                        "assert 'display_path' in cols; "
                        "assert 'source_media_type' in cols; "
                        "conn.close()"
                    ),
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        finally:
            _cleanup_tree(tmp_dir)

        if result.returncode != 0:
            self.fail(
                "Importing app.main did not backfill legacy item columns.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
