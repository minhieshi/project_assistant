from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .security import private_dir, private_file, default_home


class CredentialStoreError(RuntimeError):
    """Raised when persisted credentials cannot be read or written safely."""


class CredentialNotFound(CredentialStoreError):
    """Raised when no credential exists for the requested server."""


class CredentialStore(Protocol):
    def get(self, server_id: str) -> dict[str, Any] | None: ...
    def set(self, server_id: str, value: dict[str, Any]) -> None: ...
    def delete(self, server_id: str) -> None: ...


_SAFE_SERVER_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _normalise_server_id(server_id: str) -> str:
    value = _SAFE_SERVER_ID.sub("-", server_id.strip()).strip("-.")
    if not value:
        raise ValueError("server_id must contain at least one usable character")
    return value[:120]


@dataclass(frozen=True)
class MacOSKeychainCredentialStore:
    """Persist MCP OAuth state as a generic password in the user's macOS Keychain.

    The whole OAuth bundle is stored as one JSON value so access-token, refresh-token,
    expiry metadata and OAuth client registration metadata can be updated together.
    Project configuration contains no credential material.
    """

    service_prefix: str = "com.project-assistant.mcp"
    account: str = "oauth"
    security_binary: str = "/usr/bin/security"
    timeout_seconds: float = 10.0

    @classmethod
    def available(cls) -> bool:
        return platform.system() == "Darwin" and Path("/usr/bin/security").is_file()

    def _service(self, server_id: str) -> str:
        return f"{self.service_prefix}.{_normalise_server_id(server_id)}"

    def _run(self, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.security_binary, *args],
                check=check,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise CredentialStoreError("macOS Keychain utility is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise CredentialStoreError("macOS Keychain operation timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise CredentialStoreError(f"macOS Keychain operation failed{': ' + detail if detail else ''}") from exc

    def get(self, server_id: str) -> dict[str, Any] | None:
        service = self._service(server_id)
        result = self._run([
            "find-generic-password",
            "-a",
            self.account,
            "-s",
            service,
            "-w",
        ])
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            missing = result.returncode == 44 or "could not be found" in detail.lower() or "item not found" in detail.lower()
            if missing:
                return None
            raise CredentialStoreError(f"Unable to read Keychain credential{': ' + detail if detail else ''}")
        raw = result.stdout.rstrip("\n")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CredentialStoreError(f"Invalid credential data in Keychain for MCP server {server_id!r}") from exc
        if not isinstance(value, dict):
            raise CredentialStoreError(f"Invalid credential data in Keychain for MCP server {server_id!r}")
        return value

    def set(self, server_id: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise TypeError("credential value must be a dictionary")
        service = self._service(server_id)
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        # -U updates an existing generic-password item or creates it when absent.
        # The value is never written to project files or application logs.
        self._run([
            "add-generic-password",
            "-U",
            "-a",
            self.account,
            "-s",
            service,
            "-l",
            f"Project Assistant MCP: {server_id}",
            "-w",
            payload,
        ], check=True)

    def delete(self, server_id: str) -> None:
        service = self._service(server_id)
        result = self._run([
            "delete-generic-password",
            "-a",
            self.account,
            "-s",
            service,
        ])
        if result.returncode not in {0, 44}:
            detail = (result.stderr or "").strip()
            raise CredentialStoreError(f"Unable to delete Keychain credential{': ' + detail if detail else ''}")


@dataclass(frozen=True)
class PrivateFileCredentialStore:
    """Portable opt-in fallback for environments without macOS Keychain."""

    path: Path = default_home() / "mcp-auth.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError(f"Unable to read MCP credential store: {self.path}") from exc
        if not isinstance(raw, dict):
            raise CredentialStoreError(f"Invalid MCP credential store: {self.path}")
        private_file(self.path)
        return raw

    def _save(self, data: dict[str, Any]) -> None:
        private_dir(self.path.parent)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        private_file(temp)
        os.replace(temp, self.path)
        private_file(self.path)

    def get(self, server_id: str) -> dict[str, Any] | None:
        value = self._load().get(_normalise_server_id(server_id))
        if value is None:
            return None
        if not isinstance(value, dict):
            raise CredentialStoreError(f"Invalid credential data for MCP server {server_id!r}")
        return value

    def set(self, server_id: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise TypeError("credential value must be a dictionary")
        data = self._load()
        data[_normalise_server_id(server_id)] = value
        self._save(data)

    def delete(self, server_id: str) -> None:
        data = self._load()
        data.pop(_normalise_server_id(server_id), None)
        self._save(data)


def create_mcp_credential_store(backend: str | None = None) -> CredentialStore:
    """Create the credential store used by MCP OAuth handling.

    Defaults to macOS Keychain on macOS. `PROJECT_ASSISTANT_MCP_AUTH_STORE=file`
    provides an explicit private-file fallback for development/non-macOS systems.
    """

    selected = (backend or os.getenv("PROJECT_ASSISTANT_MCP_AUTH_STORE") or "auto").strip().lower()
    if selected not in {"auto", "keychain", "file"}:
        raise CredentialStoreError("PROJECT_ASSISTANT_MCP_AUTH_STORE must be auto, keychain, or file")

    if selected in {"auto", "keychain"} and MacOSKeychainCredentialStore.available():
        return MacOSKeychainCredentialStore()
    if selected == "keychain":
        raise CredentialStoreError("macOS Keychain was requested but is not available")
    return PrivateFileCredentialStore()
