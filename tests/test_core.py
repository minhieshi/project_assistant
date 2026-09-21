from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_assistant.change_gate import ChangeGate
from project_assistant.conversations import ConversationStore
from project_assistant.knowledge_graph import KnowledgeGraph
from project_assistant.security import EgressBlockedError, EgressPolicy, SecurityError, load_or_create_api_token


class CoreTests(unittest.TestCase):
    @staticmethod
    def _git_repo(root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Project Assistant Tests"], check=True)
        (repo / "value.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        return repo

    @staticmethod
    def _patch(path: Path) -> Path:
        patch_path = path / "change.diff"
        patch_path.write_text(
            "diff --git a/value.txt b/value.txt\n"
            "--- a/value.txt\n"
            "+++ b/value.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n",
            encoding="utf-8",
        )
        return patch_path

    def test_change_gate_requires_two_explicit_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._git_repo(root)
            patch_path = self._patch(root)
            store = ConversationStore(root / ".assistant/conversations")
            conv = store.create("Patch gate")
            gate = ChangeGate(root, store, allowed_repo_roots=[repo])
            proposal = gate.create(conv.id, "Change value", "Change value.txt")

            with self.assertRaises(PermissionError):
                gate.stage_patch(proposal.id, patch_path, repo)

            proposal = gate.approve_plan(proposal.id)
            self.assertEqual(proposal.status, "plan_approved")
            proposal = gate.stage_patch(proposal.id, patch_path, repo)
            self.assertEqual(proposal.status, "patch_pending")
            self.assertTrue(proposal.patch_sha256)

            with self.assertRaises(PermissionError):
                gate.apply_patch(proposal.id)

            proposal = gate.approve_patch(proposal.id)
            self.assertEqual(proposal.status, "patch_approved")
            proposal = gate.apply_patch(proposal.id)
            self.assertEqual(proposal.status, "applied")
            self.assertEqual((repo / "value.txt").read_text(encoding="utf-8"), "new\n")

            text = conv.path.read_text(encoding="utf-8")
            self.assertIn("PENDING PLAN APPROVAL", text)
            self.assertIn("PLAN APPROVED", text)
            self.assertIn("DIFF PENDING APPROVAL", text)
            self.assertIn("DIFF APPROVED", text)
            self.assertIn("APPLIED", text)

    def test_tampered_staged_patch_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._git_repo(root)
            patch_path = self._patch(root)
            store = ConversationStore(root / ".assistant/conversations")
            conv = store.create("Tamper")
            gate = ChangeGate(root, store, allowed_repo_roots=[repo])
            proposal = gate.create(conv.id, "Change value", "Change value.txt")
            gate.approve_plan(proposal.id)
            proposal = gate.stage_patch(proposal.id, patch_path, repo)
            staged = root / ".assistant/patches" / str(proposal.patch_file)
            staged.write_text(staged.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
            with self.assertRaises(PermissionError):
                gate.approve_patch(proposal.id)

    def test_head_change_invalidates_staged_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._git_repo(root)
            patch_path = self._patch(root)
            store = ConversationStore(root / ".assistant/conversations")
            conv = store.create("HEAD change")
            gate = ChangeGate(root, store, allowed_repo_roots=[repo])
            proposal = gate.create(conv.id, "Change value", "Change value.txt")
            gate.approve_plan(proposal.id)
            gate.stage_patch(proposal.id, patch_path, repo)
            (repo / "other.txt").write_text("other\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "other.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "move head"], check=True)
            with self.assertRaises(PermissionError):
                gate.approve_patch(proposal.id)

    def test_knowledge_graph_extracts_python_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.py"
            source.write_text("def renew_certificate(name):\n    return name\n", encoding="utf-8")
            graph = KnowledgeGraph(root / "graph.sqlite3")
            graph.index_file("repo", root, source, source.read_text())
            hits = graph.search("renew_certificate")
            self.assertTrue(any(hit.name == "renew_certificate" for hit in hits))

    def test_conversations_are_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp))
            conv = store.create("Certificate work")
            store.append(conv.id, "user", "Why is this failing?")
            store.append(conv.id, "assistant", "Because X.")
            text = conv.path.read_text(encoding="utf-8")
            self.assertIn("# Certificate work", text)
            self.assertIn("## User", text)
            self.assertIn("## Assistant", text)


class SecurityTests(unittest.TestCase):
    def test_egress_policy_blocks_clear_secret_but_allows_placeholder(self):
        policy = EgressPolicy()
        with self.assertRaises(EgressBlockedError):
            policy.assert_text_safe('api_key = "sk_live_1234567890abcdef"')
        policy.assert_text_safe('api_key = "${PORTKEY_API_KEY}"')
        self.assertFalse(policy.path_allowed(Path(".env")))
        self.assertFalse(policy.path_allowed(Path("client.pem")))

    def test_project_config_rejects_internal_path_escape(self):
        from project_assistant.config import ProjectConfig, init_project

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            init_project(root, "Example")
            config_path = root / ".assistant/project.json"
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["project_memory_path"] = "../../outside.md"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(SecurityError):
                ProjectConfig.load(root)

    def test_local_api_token_is_generated_in_private_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PROJECT_ASSISTANT_HOME": tmp}, clear=False):
                with patch.dict(os.environ, {"PROJECT_ASSISTANT_API_TOKEN": "", "PROJECT_ASSISTANT_TOKEN_FILE": ""}, clear=False):
                    token = load_or_create_api_token()
                    self.assertGreaterEqual(len(token), 32)
                    token_file = Path(tmp) / "api-token"
                    self.assertEqual(token_file.read_text(encoding="utf-8").strip(), token)
                    if os.name == "posix":
                        self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)


class RetrievalTests(unittest.TestCase):
    def test_python_chunker_preserves_symbols_and_lines(self):
        from project_assistant.code_chunking import CodeChunker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "certs.py"
            text = (
                "import os\n\n"
                "def renew_certificate(name):\n"
                "    return name\n\n"
                "class Manager:\n"
                "    def validate(self, cert):\n"
                "        return bool(cert)\n"
            )
            path.write_text(text, encoding="utf-8")
            chunks = CodeChunker(max_chars=2000).chunk_file(path, text)
            symbols = {c.symbol for c in chunks}
            self.assertIn("renew_certificate", symbols)
            self.assertIn("Manager", symbols)
            renew = next(c for c in chunks if c.symbol == "renew_certificate")
            self.assertEqual((renew.start_line, renew.end_line), (3, 4))

    def test_jcl_chunker_uses_job_and_exec_steps(self):
        from project_assistant.code_chunking import CodeChunker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jcl"
            text = "//TESTJOB JOB CLASS=A\n//STEP1 EXEC PGM=IKJEFT01\n//SYSTSPRT DD SYSOUT=*\n//STEP2 EXEC PGM=IEFBR14\n"
            chunks = CodeChunker(max_chars=2000).chunk_file(path, text)
            symbols = {c.symbol for c in chunks}
            self.assertTrue({"TESTJOB", "STEP1", "STEP2"}.issubset(symbols))

    def test_lexical_index_finds_exact_symbol(self):
        from project_assistant.lexical_index import LexicalIndex
        with tempfile.TemporaryDirectory() as tmp:
            index = LexicalIndex(Path(tmp) / "lex.sqlite3")
            metadata = {
                "id": "a", "repo": "automation", "relative_path": "certs.py",
                "source": "/tmp/certs.py", "symbol": "renew_certificate",
                "symbol_kind": "function", "start_line": 10, "end_line": 20,
            }
            index.upsert("a", "def renew_certificate(name):\n    return name", metadata)
            hits = index.exact("renew_certificate")
            self.assertEqual(hits[0].metadata["symbol"], "renew_certificate")

    def test_repo_catalog_routes_using_paths_and_retrieval_evidence(self):
        from project_assistant.config import SourceRoot
        from project_assistant.repo_catalog import RepositoryCatalog
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "automation"; a.mkdir()
            b = root / "billing"; b.mkdir()
            (a / "README.md").write_text("zCX provisioning certificate RACF automation", encoding="utf-8")
            manifest = {
                "/x/zcx.yml": {"source": "automation", "relative_path": "roles/zcx/tasks/main.yml", "git_branch": "main", "git_commit": "abc"},
                "/x/pay.py": {"source": "billing", "relative_path": "src/payments.py", "git_branch": "main", "git_commit": "def"},
            }
            catalogue = RepositoryCatalog(root / "catalog.json")
            catalogue.rebuild([SourceRoot("automation", str(a)), SourceRoot("billing", str(b))], manifest)
            lexical = [SimpleNamespace(metadata={"repo": "automation"})]
            routes = catalogue.route("why is zCX provisioning failing", lexical_hits=lexical, limit=2)
            self.assertEqual(routes[0].name, "automation")


class GraphExpansionTests(unittest.TestCase):
    def test_cross_file_call_graph_expands_to_definition_and_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            caller = root / "caller.py"
            target = root / "certs.py"
            caller.write_text('def run():\n    return renew_certificate("x")\n', encoding="utf-8")
            target.write_text('def renew_certificate(name):\n    return name\n', encoding="utf-8")
            graph = KnowledgeGraph(root / "graph.sqlite3")
            graph.index_file("repo", root, caller, caller.read_text())
            graph.index_file("repo", root, target, target.read_text())
            hits = graph.search("renew_certificate")
            locations = graph.related_locations(hits[:3])
            paths = {Path(path).name for path, _ in locations}
            self.assertIn("caller.py", paths)
            self.assertIn("certs.py", paths)


class WebFoundationTests(unittest.TestCase):
    def test_conversation_entries_are_parsed_for_web_ui(self):
        store_dir = None
        with tempfile.TemporaryDirectory() as tmp:
            store_dir = Path(tmp)
            store = ConversationStore(store_dir)
            conv = store.create("Web chat")
            store.append(conv.id, "user", "hello")
            store.append(conv.id, "assistant", "hi")
            store.append_event(conv.id, "Change proposal abc — PLAN APPROVED", "approved")
            entries = store.entries(conv.id)
            self.assertEqual([entry.role for entry in entries], ["user", "assistant", "event"])
            self.assertEqual(entries[0].body, "hello")
            self.assertEqual(entries[1].body, "hi")

    def test_managed_projects_are_git_repos_and_rediscovered_after_restart(self):
        from project_assistant.workspace import WorkspaceRegistry

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "assistant-home"
            registry = WorkspaceRegistry(home=home)
            created = registry.create("Mainframe Platform")
            project = Path(created.path)
            self.assertTrue((project / ".git").is_dir())
            self.assertTrue((project / ".assistant/project.json").exists())
            self.assertEqual(created.kind, "managed")

            # A fresh registry instance has no process memory; it discovers the
            # project by scanning the managed projects root.
            restarted = WorkspaceRegistry(home=home)
            projects = restarted.list()
            self.assertEqual([(p.id, p.name, p.kind) for p in projects], [(created.id, "Mainframe Platform", "managed")])

    def test_imported_git_repo_is_persisted_without_copying_it(self):
        from project_assistant.workspace import WorkspaceRegistry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "assistant-home"
            repo = root / "existing-repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")

            registry = WorkspaceRegistry(home=home)
            imported = registry.import_repo(repo, "Existing system")
            self.assertEqual(Path(imported.path), repo.resolve())
            self.assertEqual(imported.kind, "imported")
            # Imported code repos keep assistant metadata private under .assistant.
            self.assertTrue((repo / ".assistant/assistant_system.md").exists())
            self.assertTrue((repo / ".assistant/PROJECT.md").exists())
            self.assertFalse((repo / "assistant_system.md").exists())

            restarted = WorkspaceRegistry(home=home)
            self.assertEqual(restarted.get(imported.id).path, str(repo.resolve()))

    def test_imported_project_can_be_converted_to_source_without_moving_repo(self):
        from project_assistant.config import ProjectConfig
        from project_assistant.workspace import WorkspaceRegistry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "assistant-home"
            source_repo = root / "source-code"
            source_repo.mkdir()
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            (source_repo / "app.py").write_text("print('source')\n", encoding="utf-8")

            registry = WorkspaceRegistry(home=home)
            target = registry.create("Real project")
            mistaken = registry.import_repo(source_repo, "Shared source")

            updated, source = registry.convert_imported_to_source(mistaken.id, target.id, "shared-source")
            self.assertEqual(updated.id, target.id)
            self.assertEqual(source.name, "shared-source")
            self.assertEqual(Path(source.path), source_repo.resolve())
            self.assertTrue(source_repo.exists())
            self.assertTrue((source_repo / ".git").is_dir())

            config = ProjectConfig.load(Path(target.path))
            resolved = {item.name: Path(item.path) for item in config.resolved_sources(Path(target.path))}
            self.assertEqual(resolved["shared-source"], source_repo.resolve())
            with self.assertRaises(KeyError):
                registry.get(mistaken.id)

            restarted = WorkspaceRegistry(home=home)
            self.assertEqual([item.id for item in restarted.list()], [target.id])

    def test_failed_conversion_does_not_forget_imported_project(self):
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.workspace import WorkspaceRegistry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "assistant-home"
            repo = root / "source-code"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)

            registry = WorkspaceRegistry(home=home)
            target = registry.create("Target")
            mistaken = registry.import_repo(repo, "Source")
            target_config = ProjectConfig.load(Path(target.path))
            target_config.source_roots.append(SourceRoot(name="existing", path=str(repo.resolve())))
            target_config.save(Path(target.path))

            with self.assertRaises(ValueError):
                registry.convert_imported_to_source(mistaken.id, target.id)
            self.assertEqual(registry.get(mistaken.id).id, mistaken.id)

    def test_project_can_be_renamed_without_changing_repository_identity(self):
        from project_assistant.workspace import WorkspaceRegistry

        with tempfile.TemporaryDirectory() as tmp:
            registry = WorkspaceRegistry(home=Path(tmp) / "assistant-home")
            project = registry.create("Old name")
            renamed = registry.rename(project.id, "New name")
            self.assertEqual(renamed.id, project.id)
            self.assertEqual(renamed.path, project.path)
            self.assertEqual(renamed.name, "New name")
            self.assertEqual(WorkspaceRegistry(home=Path(tmp) / "assistant-home").get(project.id).name, "New name")

    def test_v04_registry_is_migrated_so_existing_project_reappears(self):
        from project_assistant.config import init_project
        from project_assistant.workspace import WorkspaceRegistry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "assistant-home"
            home.mkdir()
            old_project = root / "old-project"
            init_project(old_project, "Old project")
            (home / "registry.json").write_text(json.dumps({"projects": [{"path": str(old_project)}]}), encoding="utf-8")

            registry = WorkspaceRegistry(home=home)
            projects = registry.list()
            self.assertTrue(any(p.path == str(old_project.resolve()) for p in projects))
            self.assertTrue((home / "imports.json").exists())

    def test_api_app_imports_without_initialising_rag(self):
        from project_assistant.api.app import app

        self.assertEqual(app.version, "0.6.4")

    def test_portkey_url_is_explicit_and_does_not_default_public(self):
        from project_assistant.config import PortkeySettings

        with patch.dict(os.environ, {"PORTKEY_BASE_URL": ""}, clear=False):
            settings = PortkeySettings.from_env()
            self.assertEqual(settings.base_url, "")

    def test_api_requires_local_token(self):
        from fastapi.testclient import TestClient
        from project_assistant.api.app import API_TOKEN, app

        client = TestClient(app)
        self.assertEqual(client.get("/api/projects").status_code, 401)
        response = client.get("/api/projects", headers={"x-project-assistant-token": API_TOKEN})
        self.assertEqual(response.status_code, 200)


class TitanEmbeddingTests(unittest.TestCase):
    def test_embedding_adapter_calls_completion_create_with_full_model_and_raw_input_only(self):
        from types import SimpleNamespace
        from project_assistant.config import PortkeySettings
        from project_assistant.portkey import PortkeyTitanEmbeddings

        calls = []

        class Endpoint:
            def create(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])

        client = SimpleNamespace(completion=Endpoint())
        settings = PortkeySettings(
            base_url="https://gateway.example.invalid/v1",
            api_key="test-portkey-key",
            chat_model="gpt-5.6",
            embedding_model="@bedrock-au/amazon.titan-embed-text-v2:0",
        )
        embeddings = PortkeyTitanEmbeddings(settings, client_override=client)

        vectors = embeddings.embed_documents(["first raw document", "second raw document"])
        query_vector = embeddings.embed_query("raw query")

        self.assertEqual(vectors, [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]])
        self.assertEqual(query_vector, [0.1, 0.2, 0.3])
        self.assertEqual(
            calls,
            [
                {"model": "@bedrock-au/amazon.titan-embed-text-v2:0", "input": "first raw document"},
                {"model": "@bedrock-au/amazon.titan-embed-text-v2:0", "input": "second raw document"},
                {"model": "@bedrock-au/amazon.titan-embed-text-v2:0", "input": "raw query"},
            ],
        )

    def test_embedding_sdk_constructor_uses_environment_api_key_without_provider_splitting(self):
        import sys
        from types import ModuleType, SimpleNamespace
        from project_assistant.config import PortkeySettings
        from project_assistant.portkey import PortkeyTitanEmbeddings

        constructor_calls = []

        class FakePortkey:
            def __init__(self, **kwargs):
                constructor_calls.append(kwargs)
                self.completion = SimpleNamespace(create=lambda **_: None)

        fake_module = ModuleType("portkey_ai")
        fake_module.Portkey = FakePortkey
        settings = PortkeySettings(
            base_url="https://gateway.example.invalid/v1",
            api_key="test-portkey-key",
            chat_model="gpt-5.6",
            embedding_model="@bedrock-au/amazon.titan-embed-text-v2:0",
            embedding_virtual_key="ignored-for-this-minimal-path",
            embedding_config_id="ignored-for-this-minimal-path",
        )
        embeddings = PortkeyTitanEmbeddings(settings)
        with patch.dict(sys.modules, {"portkey_ai": fake_module}):
            embeddings._client()

        self.assertEqual(
            constructor_calls,
            [{
                "api_key": "test-portkey-key",
                "base_url": "https://gateway.example.invalid/v1",
            }],
        )

    def test_embedding_response_can_be_direct_embedding_shape(self):
        from project_assistant.portkey import PortkeyTitanEmbeddings
        self.assertEqual(PortkeyTitanEmbeddings._extract_embedding({"embedding": [1, 2]}), [1.0, 2.0])


class ChatRouteTests(unittest.TestCase):
    def test_chat_completions_sends_high_reasoning(self):
        from types import SimpleNamespace
        from project_assistant.config import PortkeySettings
        from project_assistant.portkey import PortkeyChatModel

        calls = []

        class Endpoint:
            def create(self, **kwargs):
                calls.append(kwargs)
                if kwargs.get("stream"):
                    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"))])]
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=Endpoint()))
        settings = PortkeySettings(
            base_url="https://gateway.example.invalid/v1",
            api_key="",
            chat_model="@enterprise/gpt-5-6-sol",
            embedding_model="",
            api_mode="chat_completions",
            reasoning_effort="high",
        )
        model = PortkeyChatModel(settings, client_override=client)
        self.assertEqual(model.complete("system", "user"), "OK")
        self.assertEqual("".join(model.stream("system", "user")), "OK")
        self.assertEqual(calls[0]["reasoning_effort"], "high")
        self.assertEqual(calls[1]["reasoning_effort"], "high")
        self.assertTrue(calls[1]["stream"])

    def test_responses_sends_high_reasoning(self):
        from types import SimpleNamespace
        from project_assistant.config import PortkeySettings
        from project_assistant.portkey import PortkeyChatModel

        calls = []

        class Endpoint:
            def create(self, **kwargs):
                calls.append(kwargs)
                if kwargs.get("stream"):
                    return [SimpleNamespace(type="response.output_text.delta", delta="OK")]
                return SimpleNamespace(output_text="OK")

        client = SimpleNamespace(responses=Endpoint())
        settings = PortkeySettings(
            base_url="https://gateway.example.invalid/v1",
            api_key="",
            chat_model="@enterprise/gpt-5-6-sol",
            embedding_model="",
            api_mode="responses",
            reasoning_effort="high",
        )
        model = PortkeyChatModel(settings, client_override=client)
        self.assertEqual(model.complete("system", "user"), "OK")
        self.assertEqual("".join(model.stream("system", "user")), "OK")
        self.assertEqual(calls[0]["reasoning"], {"effort": "high"})
        self.assertEqual(calls[1]["reasoning"], {"effort": "high"})
        self.assertTrue(calls[1]["stream"])

    def test_reasoning_effort_defaults_to_high_and_is_capped_to_supported_values(self):
        from project_assistant.config import PortkeySettings

        with patch.dict(os.environ, {"PORTKEY_REASONING_EFFORT": "", "PORTKEY_API_MODE": "chat_completions"}, clear=False):
            self.assertEqual(PortkeySettings.from_env().reasoning_effort, "high")

        with patch.dict(os.environ, {"PORTKEY_REASONING_EFFORT": "high", "PORTKEY_API_MODE": "chat_completions"}, clear=False):
            self.assertEqual(PortkeySettings.from_env().reasoning_effort, "high")

        with patch.dict(os.environ, {"PORTKEY_REASONING_EFFORT": "max", "PORTKEY_API_MODE": "chat_completions"}, clear=False):
            with self.assertRaises(ValueError):
                PortkeySettings.from_env()


if __name__ == "__main__":
    unittest.main()
