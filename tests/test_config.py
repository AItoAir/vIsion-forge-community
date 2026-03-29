from __future__ import annotations

import unittest

from app.config import (
    configured_trusted_proxy_hosts,
    normalized_session_cookie_same_site,
)


class ConfigHelpersTests(unittest.TestCase):
    def test_normalized_session_cookie_same_site_accepts_case_insensitive_values(
        self,
    ) -> None:
        self.assertEqual(normalized_session_cookie_same_site(" Strict "), "strict")

    def test_normalized_session_cookie_same_site_rejects_invalid_value(self) -> None:
        with self.assertRaises(RuntimeError):
            normalized_session_cookie_same_site("invalid")

    def test_configured_trusted_proxy_hosts_returns_list(self) -> None:
        self.assertEqual(
            configured_trusted_proxy_hosts("127.0.0.1,10.0.0.5"),
            ["127.0.0.1", "10.0.0.5"],
        )

    def test_configured_trusted_proxy_hosts_supports_wildcard(self) -> None:
        self.assertEqual(configured_trusted_proxy_hosts("*"), "*")


if __name__ == "__main__":
    unittest.main()
