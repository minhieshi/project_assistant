from __future__ import annotations

import tempfile
import ssl
import sys
import unittest
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project_assistant.credentials import PrivateFileCredentialStore
from project_assistant.mcp_client import MCPManager, MCPOAuthStorage, _OAuthCallbackServer
from project_assistant.mcp_registry import MCPConfigError, MCPServerConfig, MCPServerRegistry


class MCPRegistryTests(unittest.TestCase):
    def test_remote_and_local_registry_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp-servers.json"
            registry = MCPServerRegistry(path=path)
            registry.add_remote("atlassian", "https://mcp.atlassian.com/v2/mcp", name="Atlassian")
            registry.add_local("zowe", "/usr/bin/python3", args=["-m", "project_assistant.zowe_mcp"], cwd=tmp)

            items = {item.id: item for item in MCPServerRegistry(path=path).list()}
            self.assertEqual(items["atlassian"].type, "remote")
            self.assertEqual(items["atlassian"].url, "https://mcp.atlassian.com/v2/mcp")
            self.assertEqual(items["zowe"].type, "local")
            self.assertEqual(items["zowe"].args, ["-m", "project_assistant.zowe_mcp"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_remote_registry_rejects_cleartext_non_loopback_and_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            with self.assertRaises(MCPConfigError):
                registry.add_remote("bad", "http://example.com/mcp")
            with self.assertRaises(MCPConfigError):
                registry.add_remote("bad", "https://user:secret@example.com/mcp")
            # Loopback HTTP is allowed for local development.
            self.assertEqual(registry.add_remote("local", "http://127.0.0.1:9999/mcp").id, "local")

    def test_enable_disable_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            registry.add_remote("ceb", "https://ceb.example.test/mcp")
            registry.set_enabled("ceb", False)
            self.assertFalse(MCPServerRegistry(path=registry.path).get("ceb").enabled)

    def test_remote_tls_compat_persists_and_is_remote_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            registry.add_remote("atlassian", "https://mcp.atlassian.com/v2/mcp", tls_compat=True)
            self.assertTrue(MCPServerRegistry(path=registry.path).get("atlassian").tls_compat)
            with self.assertRaises(MCPConfigError):
                registry.upsert(MCPServerConfig(
                    id="local", name="local", type="local", command="python", tls_compat=True
                ))


class MCPOAuthStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_persists_tokens_and_client_info_as_one_bundle(self):
        class FakeModel:
            def __init__(self, value):
                self.value = value

            def model_dump(self, **kwargs):
                return dict(self.value)

        with tempfile.TemporaryDirectory() as tmp:
            store = PrivateFileCredentialStore(Path(tmp) / "auth.json")
            storage = MCPOAuthStorage("atlassian", store)
            await storage.set_tokens(FakeModel({"access_token": "a", "refresh_token": "r"}))
            await storage.set_client_info(FakeModel({"client_id": "client-1"}))
            self.assertEqual(
                store.get("atlassian"),
                {
                    "tokens": {"access_token": "a", "refresh_token": "r"},
                    "client_info": {"client_id": "client-1"},
                },
            )


class OAuthCallbackServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_callback_captures_code_state_and_issuer(self):
        callback = _OAuthCallbackServer(port=0, timeout_seconds=2)
        callback.start()
        try:
            url = f"{callback.redirect_uri}?code=abc123&state=state-1&iss=https%3A%2F%2Fissuer.example"
            response = await __import__("asyncio").to_thread(urllib.request.urlopen, url, timeout=2)
            self.assertEqual(response.status, 200)
            payload = await callback.wait()
            self.assertEqual(payload["code"], "abc123")
            self.assertEqual(payload["state"], "state-1")
            self.assertEqual(payload["iss"], "https://issuer.example")
        finally:
            callback.close()


class MCPManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_compat_clears_only_x509_strict_and_keeps_verification(self):
        class FakeContext:
            def __init__(self, protocol):
                self.protocol = protocol
                self.verify_flags = ssl.VERIFY_X509_STRICT | getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
                self.verify_mode = ssl.CERT_NONE
                self.check_hostname = False

        fake_truststore = SimpleNamespace(SSLContext=FakeContext)
        server = MCPServerConfig(
            id="atlassian", name="Atlassian", type="remote",
            url="https://mcp.atlassian.com/v2/mcp", tls_compat=True,
        ).validate()
        with patch.dict(sys.modules, {"truststore": fake_truststore}):
            context = MCPManager._tls_verify_context(server)
        self.assertEqual(context.verify_flags & ssl.VERIFY_X509_STRICT, 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    async def test_probe_discovers_tools_resources_and_protocol(self):
        class FakeClient:
            protocol_version = "2026-07-28"
            server_info = SimpleNamespace(name="Fake MCP", version="1.2.3")
            instructions = "read-only test"

            async def list_tools(self):
                tool = SimpleNamespace(
                    name="search",
                    title="Search",
                    description="Search docs",
                    input_schema={"type": "object"},
                    annotations=SimpleNamespace(read_only_hint=True),
                )
                return SimpleNamespace(tools=[tool])

            async def list_resources(self):
                resource = SimpleNamespace(
                    uri="doc://one",
                    name="one",
                    title="One",
                    description="First doc",
                    mime_type="text/plain",
                )
                return SimpleNamespace(resources=[resource])

            async def list_resource_templates(self):
                return SimpleNamespace(resource_templates=[])

        class FakeManager(MCPManager):
            @asynccontextmanager
            async def _client_context(self, server):
                yield FakeClient()

        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            registry.add_remote("atlassian", "https://mcp.atlassian.com/v2/mcp")
            manager = FakeManager(
                registry=registry,
                credential_store=PrivateFileCredentialStore(Path(tmp) / "auth.json"),
            )
            result = await manager.probe("atlassian")
            self.assertEqual(result.status, "connected")
            self.assertEqual(result.protocol_version, "2026-07-28")
            self.assertEqual(result.tools[0].name, "search")
            self.assertTrue(result.tools[0].read_only_hint)
            self.assertEqual(result.resources[0].uri, "doc://one")
            configured = manager.configured()[0]
            self.assertEqual(configured["status"], "connected")
            self.assertEqual(configured["last_probe"]["server_name"], "Fake MCP")


    async def test_probe_skips_resource_calls_when_server_does_not_advertise_resources(self):
        class ToolOnlyClient:
            protocol_version = "2026-07-28"
            server_info = SimpleNamespace(name="Tools only", version="1.0")
            instructions = None
            server_capabilities = SimpleNamespace(tools=object(), resources=None)

            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(
                    name="search",
                    title=None,
                    description="Search",
                    input_schema={"type": "object"},
                    annotations=None,
                )])

            async def list_resources(self):
                raise AssertionError("resource discovery should not be called")

            async def list_resource_templates(self):
                raise AssertionError("resource template discovery should not be called")

        class FakeManager(MCPManager):
            @asynccontextmanager
            async def _client_context(self, server):
                yield ToolOnlyClient()

        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            registry.add_remote("tools", "https://tools.example.test/mcp")
            manager = FakeManager(
                registry=registry,
                credential_store=PrivateFileCredentialStore(Path(tmp) / "auth.json"),
            )
            result = await manager.probe("tools")
            self.assertEqual(result.status, "connected")
            self.assertEqual([tool.name for tool in result.tools], ["search"])
            self.assertEqual(result.resources, [])
            self.assertEqual(result.resource_templates, [])


    async def test_probe_flattens_nested_taskgroup_error_and_redacts_oauth_values(self):
        class FailingManager(MCPManager):
            @asynccontextmanager
            async def _client_context(self, server):
                raise ExceptionGroup(
                    "unhandled errors in a TaskGroup",
                    [RuntimeError("OAuth token exchange failed at https://example.test/cb?code=secret-code&token=secret-token")],
                )
                yield  # pragma: no cover

        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            registry.add_remote("atlassian", "https://mcp.atlassian.com/v2/mcp")
            manager = FailingManager(
                registry=registry,
                credential_store=PrivateFileCredentialStore(Path(tmp) / "auth.json"),
            )
            result = await manager.probe("atlassian")
            self.assertEqual(result.status, "auth_required")
            self.assertIn("RuntimeError: OAuth token exchange failed", result.error or "")
            self.assertNotIn("unhandled errors in a TaskGroup", result.error or "")
            self.assertNotIn("secret-code", result.error or "")
            self.assertNotIn("secret-token", result.error or "")
            self.assertIn("<redacted>", result.error or "")

    async def test_disabled_server_does_not_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = MCPServerRegistry(path=Path(tmp) / "mcp.json")
            registry.add_remote("ceb", "https://ceb.example.test/mcp", enabled=False)
            manager = MCPManager(
                registry=registry,
                credential_store=PrivateFileCredentialStore(Path(tmp) / "auth.json"),
            )
            result = await manager.probe("ceb")
            self.assertEqual(result.status, "disabled")


if __name__ == "__main__":
    unittest.main()
