from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class AppImportSmokeTests(unittest.TestCase):
    def test_app_main_imports_with_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "vision_forge_validate.db"
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

        if result.returncode != 0:
            self.fail(
                "Importing app.main failed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
