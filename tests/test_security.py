from __future__ import annotations

import hashlib
import unittest

from app.config import settings
import app.security as security


class PasswordHashingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_password_salt = settings.password_salt
        settings.password_salt = "legacy-test-salt"

    def tearDown(self) -> None:
        settings.password_salt = self.original_password_salt

    def test_hash_password_uses_argon2id(self) -> None:
        password_hash = security.hash_password("demo-password")

        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertTrue(security.verify_password("demo-password", password_hash))

    def test_verify_password_and_rehash_migrates_legacy_sha256_hash(self) -> None:
        legacy_hash = hashlib.sha256(
            b"legacy-test-salt:demo-password"
        ).hexdigest()

        password_valid, upgraded_hash = security.verify_password_and_rehash(
            "demo-password",
            legacy_hash,
        )

        self.assertTrue(password_valid)
        self.assertIsNotNone(upgraded_hash)
        self.assertTrue((upgraded_hash or "").startswith("$argon2id$"))
        self.assertTrue(security.verify_password("demo-password", upgraded_hash or ""))

    def test_password_hash_needs_rehash_returns_true_for_legacy_hash(self) -> None:
        legacy_hash = hashlib.sha256(
            b"legacy-test-salt:demo-password"
        ).hexdigest()

        self.assertTrue(security.password_hash_needs_rehash(legacy_hash))


if __name__ == "__main__":
    unittest.main()
