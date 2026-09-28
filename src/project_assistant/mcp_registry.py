from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .security import default_home, private_dir, private_file


_SERVER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class MCPConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MCPServerConfig:
    id: str
    name: str
    type: str
    enabled: bool = True
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    cwd: str | None = None

    def validate(self) -> "MCPServerConfig":
        if not _SERVER_ID.fullmatch(self.id):
            raise MCPConfigError("MCP server id must use only letters, numbers, '.', '_' or '-' (max 64 characters)")
        if not self.name.strip():
            raise MCPConfigError("MCP server name must not be empty")
        if self.type not in {"remote", "local"}:
            raise MCPConfigError("MCP server type must be 'remote' or 'local'")

        if self.type == "remote":
            if not self.url:
                raise MCPConfigError("Remote MCP servers require a URL")
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise MCPConfigError("Remote MCP URL must be an absolute http/https URL")
            if parsed.username or parsed.password:
                raise MCPConfigError("Do not put credentials in an MCP URL")
            host = parsed.hostname.lower()
            if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost", "::1"}:
                raise MCPConfigError("Remote MCP URLs must use HTTPS unless they are loopback-only")
            if self.command or self.cwd or self.args:
                raise MCPConfigError("Remote MCP servers cannot define local command settings")

        if self.type == "local":
            if not self.command or not self.command.strip():
                raise MCPConfigError("Local MCP servers require a command")
            if self.url:
                raise MCPConfigError("Local MCP servers cannot define a URL")
            if self.cwd:
                cwd = Path(self.cwd).expanduser().resolve()
                if not cwd.is_dir():
                    raise MCPConfigError(f"Local MCP working directory does not exist: {cwd}")
        return self

    @classmethod
    def from_dict(cls, raw: dict) -> "MCPServerConfig":
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name") or raw.get("id") or ""),
            type=str(raw.get("type", "")),
            enabled=bool(raw.get("enabled", True)),
            url=str(raw["url"]) if raw.get("url") is not None else None,
            command=str(raw["command"]) if raw.get("command") is not None else None,
            args=[str(item) for item in raw.get("args", [])],
            cwd=str(raw["cwd"]) if raw.get("cwd") is not None else None,
        ).validate()

    def to_dict(self) -> dict:
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


class MCPServerRegistry:
    """Global MCP server catalogue.

    Server definitions are application-level rather than project-level because the
    same Atlassian/CEB/local server is typically useful to multiple projects.
    Credentials are deliberately stored elsewhere (Keychain by default).
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (default_home() / "mcp-servers.json")

    def _load(self) -> list[MCPServerConfig]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPConfigError(f"Unable to read MCP server registry: {self.path}") from exc
        items = raw.get("servers", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise MCPConfigError(f"Invalid MCP server registry: {self.path}")
        return [MCPServerConfig.from_dict(item) for item in items]

    def _save(self, servers: list[MCPServerConfig]) -> None:
        private_dir(self.path.parent)
        payload = {"version": 1, "servers": [server.to_dict() for server in servers]}
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        private_file(temp)
        temp.replace(self.path)
        private_file(self.path)

    def list(self) -> list[MCPServerConfig]:
        return sorted(self._load(), key=lambda item: item.name.lower())

    def get(self, server_id: str) -> MCPServerConfig:
        for server in self._load():
            if server.id == server_id:
                return server
        raise KeyError(f"Unknown MCP server: {server_id}")

    def upsert(self, server: MCPServerConfig) -> MCPServerConfig:
        server = server.validate()
        servers = self._load()
        for idx, current in enumerate(servers):
            if current.id == server.id:
                servers[idx] = server
                self._save(servers)
                return server
        servers.append(server)
        self._save(servers)
        return server

    def add_remote(self, server_id: str, url: str, *, name: str | None = None, enabled: bool = True) -> MCPServerConfig:
        return self.upsert(MCPServerConfig(
            id=server_id.strip(),
            name=(name or server_id).strip(),
            type="remote",
            url=url.strip(),
            enabled=enabled,
        ))

    def add_local(
        self,
        server_id: str,
        command: str,
        *,
        args: list[str] | None = None,
        cwd: str | None = None,
        name: str | None = None,
        enabled: bool = True,
    ) -> MCPServerConfig:
        return self.upsert(MCPServerConfig(
            id=server_id.strip(),
            name=(name or server_id).strip(),
            type="local",
            command=command.strip(),
            args=list(args or []),
            cwd=cwd,
            enabled=enabled,
        ))

    def remove(self, server_id: str) -> None:
        servers = self._load()
        filtered = [server for server in servers if server.id != server_id]
        if len(filtered) == len(servers):
            raise KeyError(f"Unknown MCP server: {server_id}")
        self._save(filtered)

    def set_enabled(self, server_id: str, enabled: bool) -> MCPServerConfig:
        current = self.get(server_id)
        updated = MCPServerConfig(**{**asdict(current), "enabled": enabled}).validate()
        return self.upsert(updated)
