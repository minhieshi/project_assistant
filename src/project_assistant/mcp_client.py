from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlparse

from .credentials import CredentialStore, create_mcp_credential_store
from .mcp_registry import MCPServerConfig, MCPServerRegistry


class MCPConnectionError(RuntimeError):
    pass


class MCPDependencyError(MCPConnectionError):
    pass


class MCPAuthenticationError(MCPConnectionError):
    pass


@dataclass
class MCPToolInfo:
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    read_only_hint: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "read_only_hint": self.read_only_hint,
        }


@dataclass
class MCPResourceInfo:
    uri: str
    name: str | None = None
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "mime_type": self.mime_type,
        }


@dataclass
class MCPProbeResult:
    server_id: str
    status: str
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    instructions: str | None = None
    tools: list[MCPToolInfo] = field(default_factory=list)
    resources: list[MCPResourceInfo] = field(default_factory=list)
    resource_templates: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "status": self.status,
            "protocol_version": self.protocol_version,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "instructions": self.instructions,
            "tools": [item.to_dict() for item in self.tools],
            "resources": [item.to_dict() for item in self.resources],
            "resource_templates": self.resource_templates,
            "error": self.error,
        }


class MCPOAuthStorage:
    """Adapter between the MCP SDK TokenStorage protocol and our credential store."""

    def __init__(self, server_id: str, store: CredentialStore):
        self.server_id = server_id
        self.store = store
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.store.get(self.server_id) or {})

    def _save_member(self, key: str, value: Any) -> None:
        with self._lock:
            bundle = dict(self.store.get(self.server_id) or {})
            bundle[key] = value
            self.store.set(self.server_id, bundle)

    async def get_tokens(self):
        raw = self._load().get("tokens")
        if not isinstance(raw, dict):
            return None
        try:
            from mcp.shared.auth import OAuthToken
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised by manager dependency check
            raise MCPDependencyError("MCP Python SDK v2 is not installed") from exc
        return OAuthToken.model_validate(raw)

    async def set_tokens(self, tokens) -> None:
        self._save_member("tokens", tokens.model_dump(mode="json", by_alias=True, exclude_none=True))

    async def get_client_info(self):
        raw = self._load().get("client_info")
        if not isinstance(raw, dict):
            return None
        try:
            from mcp.shared.auth import OAuthClientInformationFull
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise MCPDependencyError("MCP Python SDK v2 is not installed") from exc
        return OAuthClientInformationFull.model_validate(raw)

    async def set_client_info(self, client_info) -> None:
        self._save_member("client_info", client_info.model_dump(mode="json", by_alias=True, exclude_none=True))

    def clear(self) -> None:
        self.store.delete(self.server_id)


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    # OAuth re-authentication can happen shortly after a previous callback listener
    # closed. Reusing the loopback address avoids TIME_WAIT making that annoying.
    allow_reuse_address = True


class _OAuthCallbackServer:
    """Short-lived loopback HTTP listener used only during interactive OAuth."""

    def __init__(self, *, host: str = "127.0.0.1", port: int | None = None, timeout_seconds: int | None = None):
        self.host = host
        self.port = port if port is not None else int(os.getenv("PROJECT_ASSISTANT_MCP_CALLBACK_PORT", "8765"))
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else int(os.getenv("PROJECT_ASSISTANT_MCP_AUTH_TIMEOUT_SECONDS", "300"))
        self.path = "/mcp/oauth/callback"
        self._queue: queue.Queue[dict[str, str | None]] = queue.Queue(maxsize=1)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def start(self) -> None:
        if self._server is not None:
            return
        callback_queue = self._queue
        expected_path = self.path

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                payload: dict[str, str | None] = {
                    "code": query.get("code", [None])[0],
                    "state": query.get("state", [None])[0],
                    "iss": query.get("iss", [None])[0],
                    "error": query.get("error", [None])[0],
                    "error_description": query.get("error_description", [None])[0],
                }
                try:
                    callback_queue.put_nowait(payload)
                except queue.Full:
                    pass
                failed = bool(payload.get("error"))
                heading = "Project Assistant authentication failed" if failed else "Project Assistant connected"
                message = (
                    "Return to Project Assistant to see the authentication error."
                    if failed
                    else "You can close this tab and return to Project Assistant."
                )
                body = (
                    "<!doctype html><html><body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:40px'>"
                    f"<h2>{heading}</h2><p>{message}</p>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(400 if failed else 200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._server = _ReusableThreadingHTTPServer((self.host, self.port), Handler)
            self.port = int(self._server.server_address[1])
        except OSError as exc:
            raise MCPAuthenticationError(
                f"Unable to start the local OAuth callback listener on {self.host}:{self.port}. "
                "Close the process using that port or set PROJECT_ASSISTANT_MCP_CALLBACK_PORT to another unused loopback port."
            ) from exc
        self._thread = threading.Thread(target=self._server.serve_forever, name="project-assistant-mcp-oauth", daemon=True)
        self._thread.start()

    async def wait(self) -> dict[str, str | None]:
        try:
            return await asyncio.to_thread(self._queue.get, True, self.timeout_seconds)
        except queue.Empty as exc:
            raise MCPAuthenticationError("MCP authentication timed out waiting for the browser callback") from exc

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class MCPManager:
    def __init__(
        self,
        registry: MCPServerRegistry | None = None,
        credential_store: CredentialStore | None = None,
    ):
        self.registry = registry or MCPServerRegistry()
        self.credential_store = credential_store or create_mcp_credential_store()
        self._last: dict[str, MCPProbeResult] = {}
        # A single fixed callback port is intentionally shared, so serialise browser
        # flows. Silent token-backed probes still pass through this lock briefly.
        self._oauth_lock = asyncio.Lock()

    def configured(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for server in self.registry.list():
            last = self._last.get(server.id)
            has_credentials: bool | None = False
            if server.type == "remote":
                try:
                    has_credentials = self.credential_store.get(server.id) is not None
                except Exception:
                    has_credentials = None
            rows.append({
                **server.to_dict(),
                "status": "disabled" if not server.enabled else (last.status if last else "not_checked"),
                "last_probe": last.to_dict() if last else None,
                "has_credentials": has_credentials,
            })
        return rows

    def logout(self, server_id: str) -> None:
        self.registry.get(server_id)
        self.credential_store.delete(server_id)
        self._last.pop(server_id, None)

    @staticmethod
    def _sdk_available() -> None:
        try:
            import mcp  # noqa: F401
            import httpx2  # noqa: F401
        except ModuleNotFoundError as exc:
            raise MCPDependencyError(
                "MCP connectivity requires the MCP Python SDK v2. Reinstall Project Assistant so the 'mcp>=2,<3' dependency is installed."
            ) from exc

    @asynccontextmanager
    async def _client_context(self, server: MCPServerConfig) -> AsyncIterator[Any]:
        self._sdk_available()
        from mcp import Client, StdioServerParameters

        if server.type == "local":
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=server.command or "",
                args=list(server.args),
                cwd=Path(server.cwd).expanduser().resolve() if server.cwd else None,
            )
            # Use the explicit transport form. It keeps the subprocess lifecycle
            # unambiguous across MCP SDK v2 point releases.
            async with Client(stdio_client(params)) as client:
                yield client
            return

        from pydantic import AnyUrl
        import httpx2
        from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared.auth import OAuthClientMetadata

        async with self._oauth_lock:
            callback = _OAuthCallbackServer()
            storage = MCPOAuthStorage(server.id, self.credential_store)

            async def open_browser(auth_url: str) -> None:
                # Do not bind the callback port for silent token-backed requests.
                # It is only needed if OAuth actually requires human interaction.
                callback.start()
                opened = await asyncio.to_thread(webbrowser.open, auth_url, new=2, autoraise=True)
                if not opened:
                    raise MCPAuthenticationError("Could not open the browser for MCP authentication")

            async def wait_for_callback() -> AuthorizationCodeResult:
                payload = await callback.wait()
                if payload.get("error"):
                    detail = payload.get("error_description") or payload.get("error") or "OAuth authorization failed"
                    raise MCPAuthenticationError(str(detail))
                code = payload.get("code")
                if not code:
                    raise MCPAuthenticationError("OAuth callback did not contain an authorization code")
                return AuthorizationCodeResult(
                    code=code,
                    state=payload.get("state"),
                    iss=payload.get("iss"),
                )

            oauth = OAuthClientProvider(
                server_url=server.url or "",
                client_metadata=OAuthClientMetadata(
                    client_name="Project Assistant",
                    redirect_uris=[AnyUrl(callback.redirect_uri)],
                    application_type="native",
                ),
                storage=storage,
                redirect_handler=open_browser,
                callback_handler=wait_for_callback,
            )
            try:
                async with httpx2.AsyncClient(auth=oauth) as http_client:
                    transport = streamable_http_client(server.url or "", http_client=http_client)
                    async with Client(transport) as client:
                        yield client
            finally:
                callback.close()

    @staticmethod
    def _model_value(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", by_alias=True, exclude_none=True)
        return value

    async def probe(self, server_id: str) -> MCPProbeResult:
        server = self.registry.get(server_id)
        if not server.enabled:
            result = MCPProbeResult(server_id=server.id, status="disabled")
            self._last[server.id] = result
            return result
        try:
            async with self._client_context(server) as client:
                capabilities = getattr(client, "server_capabilities", None)
                supports_tools = capabilities is None or getattr(capabilities, "tools", None) is not None
                supports_resources = capabilities is None or getattr(capabilities, "resources", None) is not None

                tools_result = await client.list_tools() if supports_tools else None
                resources_result = await client.list_resources() if supports_resources else None
                templates_result = await client.list_resource_templates() if supports_resources else None

                tools: list[MCPToolInfo] = []
                for tool in (tools_result.tools if tools_result is not None else []):
                    annotations = getattr(tool, "annotations", None)
                    read_only_hint = getattr(annotations, "read_only_hint", None) if annotations is not None else None
                    tools.append(MCPToolInfo(
                        name=str(tool.name),
                        title=getattr(tool, "title", None),
                        description=getattr(tool, "description", None),
                        input_schema=dict(getattr(tool, "input_schema", {}) or {}),
                        read_only_hint=read_only_hint,
                    ))

                resources = [
                    MCPResourceInfo(
                        uri=str(resource.uri),
                        name=getattr(resource, "name", None),
                        title=getattr(resource, "title", None),
                        description=getattr(resource, "description", None),
                        mime_type=getattr(resource, "mime_type", None),
                    )
                    for resource in (resources_result.resources if resources_result is not None else [])
                ]
                templates = [
                    self._model_value(item)
                    for item in (templates_result.resource_templates if templates_result is not None else [])
                ]
                info = getattr(client, "server_info", None)
                result = MCPProbeResult(
                    server_id=server.id,
                    status="connected",
                    protocol_version=str(getattr(client, "protocol_version", "") or "") or None,
                    server_name=getattr(info, "name", None) if info else None,
                    server_version=getattr(info, "version", None) if info else None,
                    instructions=getattr(client, "instructions", None),
                    tools=tools,
                    resources=resources,
                    resource_templates=templates,
                )
        except MCPConnectionError as exc:
            result = MCPProbeResult(server_id=server.id, status="auth_required" if isinstance(exc, MCPAuthenticationError) else "error", error=str(exc))
        except Exception as exc:
            # Do not include credential/token material. SDK/network exceptions should
            # normally contain endpoint/status information only; still cap the text.
            message = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:700]
            authish = any(term in message.lower() for term in ("oauth", "unauthorized", "unauthorised", "401", "authorization"))
            result = MCPProbeResult(server_id=server.id, status="auth_required" if authish else "error", error=message)
        self._last[server.id] = result
        return result

    async def read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        server = self.registry.get(server_id)
        if not server.enabled:
            raise MCPConnectionError(f"MCP server is disabled: {server_id}")
        async with self._client_context(server) as client:
            result = await client.read_resource(uri)
            return self._model_value(result)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Low-level escape hatch for future retrieval integration.

        This is intentionally not exposed to the current chat/retrieval agent yet.
        The next layer will add a local per-server allowlist before model-driven tool
        execution is enabled.
        """
        server = self.registry.get(server_id)
        if not server.enabled:
            raise MCPConnectionError(f"MCP server is disabled: {server_id}")
        async with self._client_context(server) as client:
            result = await client.call_tool(tool_name, arguments)
            return self._model_value(result)
