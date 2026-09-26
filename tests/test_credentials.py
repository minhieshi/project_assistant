from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_assistant.credentials import (
    CredentialStoreError,
    MacOSKeychainCredentialStore,
    PrivateFileCredentialStore,
    create_mcp_credential_store,
)


class CredentialStoreTests(unittest.TestCase):
    def test_keychain_round_trip_shape(self):
        store = MacOSKeychainCredentialStore()
        payload = {
            "access_token": "access-value",
            "refresh_token": "refresh-value",
            "expires_at": 1234567890,
        }

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "find-generic-password" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload) + "\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("project_assistant.credentials.subprocess.run", side_effect=fake_run):
            store.set("atlassian", payload)
            self.assertEqual(store.get("atlassian"), payload)
            store.delete("atlassian")

        add = calls[0]
        self.assertEqual(add[0], "/usr/bin/security")
        self.assertIn("add-generic-password", add)
        self.assertIn("com.project-assistant.mcp.atlassian", add)
        self.assertIn(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), add)
        self.assertIn("find-generic-password", calls[1])
        self.assertIn("delete-generic-password", calls[2])

    def test_keychain_missing_item_returns_none(self):
        store = MacOSKeychainCredentialStore()
        missing = subprocess.CompletedProcess(["security"], 44, stdout="", stderr="could not be found")
        with patch("project_assistant.credentials.subprocess.run", return_value=missing):
            self.assertIsNone(store.get("seb"))

    def test_private_file_fallback_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp-auth.json"
            store = PrivateFileCredentialStore(path=path)
            payload = {"access_token": "a", "refresh_token": "r"}
            store.set("SEB Internal", payload)
            self.assertEqual(store.get("SEB Internal"), payload)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            store.delete("SEB Internal")
            self.assertIsNone(store.get("SEB Internal"))

    def test_explicit_keychain_backend_fails_when_unavailable(self):
        with patch.object(MacOSKeychainCredentialStore, "available", return_value=False):
            with self.assertRaises(CredentialStoreError):
                create_mcp_credential_store("keychain")

    def test_auto_prefers_keychain_when_available(self):
        with patch.object(MacOSKeychainCredentialStore, "available", return_value=True):
            self.assertIsInstance(create_mcp_credential_store("auto"), MacOSKeychainCredentialStore)


if __name__ == "__main__":
    unittest.main()
