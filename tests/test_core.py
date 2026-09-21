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
            repo = CoreTests._git_repo(root)
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

    def test_change_gate_persists_explicit_approval_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = CoreTests._git_repo(root)
            patch_path = self._patch(root)
            store = ConversationStore(root / ".assistant/conversations")
            conv = store.create("Scoped approval")
            gate = ChangeGate(root, store, allowed_repo_roots=[repo])
            proposal = gate.create(conv.id, "Change value", "1. Update value.txt\n2. Add unrelated file")

            proposal = gate.approve_plan(proposal.id, ["Update value.txt"])
            self.assertEqual(proposal.approved_actions, ["Update value.txt"])
            proposal = gate.stage_patch(proposal.id, patch_path, repo)
            checks = ["Reviewed exact diff", "Approve repo", "Approve hash/base"]
            proposal = gate.approve_patch(proposal.id, checks)
            self.assertEqual(proposal.patch_approval_checks, checks)

            reloaded = gate.get(proposal.id)
            self.assertEqual(reloaded.approved_actions, ["Update value.txt"])
            self.assertEqual(reloaded.patch_approval_checks, checks)
            text = conv.path.read_text(encoding="utf-8")
            self.assertIn("Update value.txt", text)
            self.assertIn("Reviewed exact diff", text)

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
    def test_egress_policy_blocks_high_confidence_secret_but_allows_references(self):
        policy = EgressPolicy()
        with self.assertRaises(EgressBlockedError):
            policy.assert_text_safe('AWS_ACCESS_KEY_ID="AKIA1234567890ABCDEF"')
        # Common enterprise references/identifiers are advisory, not blockers.
        policy.assert_text_safe('api_key = "venafi-production-api-key-reference"')
        policy.assert_text_safe('password = "RACF_PASSWORD_REFERENCE"')
        policy.assert_text_safe('api_key = "${PORTKEY_API_KEY}"')
        self.assertIn("credential-reference", policy.advisory_findings('secret = "vault/path/to/service/account"'))
        self.assertFalse(policy.path_allowed(Path(".env")))
        self.assertFalse(policy.path_allowed(Path("client.pem")))

    def test_outbound_metadata_filter_blocks_only_explicit_non_egress_chunks(self):
        from project_assistant.security import outbound_metadata_allowed
        self.assertTrue(outbound_metadata_allowed({}))
        self.assertTrue(outbound_metadata_allowed({"egress_allowed": True}))
        self.assertFalse(outbound_metadata_allowed({"egress_allowed": False}))

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

        self.assertEqual(app.version, "0.7.7")

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


class IndexSafetyTests(unittest.TestCase):
    def test_chunker_hard_bounds_a_single_very_long_line(self):
        from project_assistant.code_chunking import CodeChunker

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generated.java"
            text = "x" * 20_000
            chunks = CodeChunker(max_chars=2400).chunk_file(path, text)
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(len(chunk.text) <= 2400 for chunk in chunks))
            self.assertTrue(all(chunk.start_line == 1 and chunk.end_line == 1 for chunk in chunks))

    def test_index_policy_excludes_archives_compiled_and_large_generated_source(self):
        from project_assistant.index_policy import FileEligibilityPolicy

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jar = root / "lib.jar"
            jar.write_bytes(b"PK\x03\x04")
            clazz = root / "Thing.class"
            clazz.write_bytes(b"\xca\xfe\xba\xbe")
            generated = root / "HugeGenerated.java"
            generated.write_text("// AUTO-GENERATED - DO NOT EDIT\n" + ("class X {}\n" * 20_000), encoding="utf-8")

            policy = FileEligibilityPolicy()
            self.assertEqual(policy.classify(jar), (False, "archive"))
            self.assertEqual(policy.classify(clazz), (False, "compiled-binary"))
            self.assertEqual(policy.classify(generated), (False, "generated-large-source"))

    def test_index_status_persists_skip_reasons_and_repo_counts(self):
        from project_assistant.index_status import IndexStatusStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index_status.json"
            store = IndexStatusStore(path)
            store.start()
            store.status.scanned += 1
            store.repo("repo-a").scanned += 1
            store.skipped("repo-a", "lib/example.jar", "archive")
            store.finish()

            loaded = IndexStatusStore(path).status
            self.assertEqual(loaded.state, "completed")
            self.assertEqual(loaded.skipped, 1)
            self.assertEqual(loaded.skip_reasons["archive"], 1)
            self.assertEqual(loaded.repos["repo-a"].skipped, 1)





class PlannerFailureTests(unittest.TestCase):
    def test_compile_context_survives_retrieval_planner_failure(self):
        import sys
        import types
        fake_chroma = types.ModuleType("langchain_chroma")
        fake_chroma.Chroma = object
        fake_docs = types.ModuleType("langchain_core.documents")
        fake_docs.Document = object
        with patch.dict(sys.modules, {"langchain_chroma": fake_chroma, "langchain_core.documents": fake_docs}):
            from project_assistant.assistant import ProjectAssistant
        from project_assistant.context_compiler import CompiledContext, RetrievalSeed

        seed = RetrievalSeed((), (), (), (), ())
        class Compiler:
            def initial_retrieval(self, query, conversation_id=None):
                return seed
            def compile(self, *args, **kwargs):
                return CompiledContext("local context", 3, (), ())
        class Conversations:
            def recent_text(self, *args, **kwargs):
                return "recent"
        class BrokenAgent:
            def plan_and_retrieve(self, *args, **kwargs):
                raise RuntimeError("planner unavailable")

        assistant = object.__new__(ProjectAssistant)
        assistant.compiler = Compiler()
        assistant.conversations = Conversations()
        assistant.retrieval_agent = BrokenAgent()
        with patch.dict(os.environ, {"RETRIEVAL_AGENT_ENABLED": "1"}, clear=False):
            compiled = assistant.compile_context("question", "c1", agentic=True)
        self.assertEqual(compiled.text, "local context")

class TitanEmbeddingTests(unittest.TestCase):
    def test_embedding_adapter_exactly_matches_known_good_curl_shape(self):
        import json
        from project_assistant.config import PortkeySettings
        from project_assistant.portkey import PortkeyTitanEmbeddings

        requests = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "data": [{"embedding": [0.1, 0.2, 0.3]}]
                }).encode("utf-8")

        def fake_urlopen(request):
            requests.append(request)
            return FakeResponse()

        settings = PortkeySettings(
            base_url="https://gateway.example.invalid/v1",
            api_key="test-portkey-key",
            chat_model="gpt-5.6",
            embedding_model="@bedrock-au/amazon.titan-embed-text-v2:0",
            embedding_virtual_key="must-not-be-sent",
            embedding_config_id="must-not-be-sent",
            extra_headers={"x-must-not-be-sent": "nope"},
        )
        embeddings = PortkeyTitanEmbeddings(settings, urlopen_override=fake_urlopen)

        vector = embeddings.embed_query("test")

        self.assertEqual(vector, [0.1, 0.2, 0.3])
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.full_url, "https://gateway.example.invalid/v1/embeddings")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-portkey-api-key"), "test-portkey-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "model": "@bedrock-au/amazon.titan-embed-text-v2:0",
                "input": "test",
            },
        )
        self.assertEqual(set(json.loads(request.data.decode("utf-8"))), {"model", "input"})

    def test_embedding_documents_make_one_exact_request_per_text(self):
        import json
        from project_assistant.config import PortkeySettings
        from project_assistant.portkey import PortkeyTitanEmbeddings

        bodies = []

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, exc_type, exc, tb): return False
            def read(self):
                return json.dumps({"data": [{"embedding": [1, 2]}]}).encode("utf-8")

        def fake_urlopen(request):
            bodies.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        settings = PortkeySettings(
            base_url="https://gateway.example.invalid/v1/",
            api_key="test-portkey-key",
            chat_model="gpt-5.6",
            embedding_model="@bedrock-au/amazon.titan-embed-text-v2:0",
        )
        embeddings = PortkeyTitanEmbeddings(settings, urlopen_override=fake_urlopen)
        vectors = embeddings.embed_documents(["first", "second"])

        self.assertEqual(vectors, [[1.0, 2.0], [1.0, 2.0]])
        self.assertEqual(bodies, [
            {"model": "@bedrock-au/amazon.titan-embed-text-v2:0", "input": "first"},
            {"model": "@bedrock-au/amazon.titan-embed-text-v2:0", "input": "second"},
        ])

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



def test_non_git_directory_skip_policy():
    from project_assistant.index_policy import should_skip_dir

    for name in ("node_modules", "build", ".git", "__pycache__", ".venv", "target"):
        assert should_skip_dir(name)
    for name in ("src", "docs", "roles", "playbooks"):
        assert not should_skip_dir(name)


class DictationTests(unittest.TestCase):
    @staticmethod
    def _wav() -> bytes:
        # Minimal PCM WAV: mono, 16 kHz, 16-bit, one silent sample.
        import struct
        data = struct.pack("<h", 0)
        return (
            b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data
        )

    def test_context_queries_include_previous_user_turn_and_error_focus(self):
        from project_assistant.config import ProjectConfig
        from project_assistant.context_compiler import ContextCompiler

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStore(root / "conversations")
            conv = store.create("ANSWER failure")
            store.append(conv.id, "user", "The provisioning playbook calls ANSWER and AssetLookup.java")
            store.append(conv.id, "assistant", "Looking at it.")
            store.append(conv.id, "user", "Now it fails with RC=8")
            compiler = ContextCompiler(root, ProjectConfig(name="x"), None, store, None)  # type: ignore[arg-type]
            queries = compiler.build_queries("Now it fails with RC=8", conv.id)
            joined = "\n".join(queries)
            self.assertIn("Previous user context", joined)
            self.assertIn("RC=8", joined)
            self.assertIn("AssetLookup.java", joined)
            self.assertTrue(any("error handling" in query for query in queries))

    def test_java_graph_extracts_classes_methods_imports_and_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "AssetLookup.java"
            source.write_text(
                "package com.example.platform;\n"
                "import com.example.shared.Client;\n"
                "public class AssetLookup {\n"
                "  public Result lookup(String id) {\n"
                "    return fetchAsset(id);\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            graph = KnowledgeGraph(root / "graph.sqlite3")
            graph.index_file("java-repo", root, source, source.read_text())
            self.assertTrue(any(hit.name == "AssetLookup" and hit.node_type == "java_class" for hit in graph.search("AssetLookup")))
            self.assertTrue(any(hit.name == "lookup" and hit.node_type == "java_method" for hit in graph.search("lookup")))
            call_hits = graph.search("fetchAsset")
            self.assertTrue(call_hits)
            self.assertTrue(any("CALLS" in neighbour for hit in call_hits for neighbour in hit.neighbours))

    def test_retrieval_planner_parses_read_only_actions_only(self):
        from project_assistant.retrieval_agent import RetrievalAgent

        raw = '''```json
        {"actions":[
          {"tool":"find_symbol","target":"AssetLookup"},
          {"tool":"read_file","target":"roles/assets/tasks/main.yml","repo":"automation"},
          {"tool":"run_shell","query":"rm -rf /"}
        ]}
        ```'''
        actions = RetrievalAgent._parse_actions(raw)
        self.assertEqual([a.tool for a in actions], ["find_symbol", "read_file"])
        self.assertEqual(actions[1].repo, "automation")

    def test_retrieval_agent_executes_at_most_two_semantic_searches(self):
        from project_assistant.retrieval_types import SearchHit
        from project_assistant.retrieval_agent import RetrievalAgent

        class FakeModel:
            def complete(self, system, user):
                return json.dumps({"actions": [
                    {"tool": "search_project", "query": "one"},
                    {"tool": "search_project", "query": "two"},
                    {"tool": "search_project", "query": "three"},
                    {"tool": "search_exact", "query": "IKJEFT01"},
                ]})

        class FakeToolkit:
            def __init__(self):
                self.calls = []
            def execute(self, action, limit=12):
                self.calls.append(action.tool + ":" + (action.query or action.target))
                return [SearchHit(action.query or action.target, {"id": self.calls[-1], "repo": "r", "relative_path": "x"}, 0.0, (action.tool,))]

        toolkit = FakeToolkit()
        agent = RetrievalAgent(FakeModel(), toolkit)  # type: ignore[arg-type]
        result = agent.plan_and_retrieve("question", "recent", [], [])
        self.assertEqual(sum(call.startswith("search_project:") for call in toolkit.calls), 2)
        self.assertIn("search_exact:IKJEFT01", toolkit.calls)
        self.assertEqual(len(result.actions), 3)

class RetrievalCompilerV2Tests(unittest.TestCase):
    def test_multi_query_semantic_search_is_capped_and_routing_does_not_exclude_other_repos(self):
        from project_assistant.config import ProjectConfig
        from project_assistant.context_compiler import ContextCompiler
        from project_assistant.repo_catalog import RepoRoute
        from project_assistant.retrieval_types import SearchHit

        class FakeCatalog:
            def route(self, *args, **kwargs):
                return [RepoRoute("primary", 5.0, ("test",))]

        class FakeIndexer:
            def __init__(self):
                self.vector_calls = 0
                self.catalog = FakeCatalog()
            def vector_search(self, query, k=10):
                self.vector_calls += 1
                repo = "secondary" if self.vector_calls == 1 else "primary"
                return [SearchHit(query, {"id": f"v{self.vector_calls}", "repo": repo, "source": f"/{repo}.py", "relative_path": f"{repo}.py", "start_line": 1, "end_line": 2}, 0.0, ("vector",))]
            def lexical_search(self, query, k=10):
                return []
            def exact_search(self, query, k=10):
                return []
            def fuse(self, vector, lexical, exact, limit):
                return (vector + lexical + exact)[:limit]
            def chunks_for_locations(self, locations, limit=20):
                return []
            def chunks_for_sources(self, paths, limit=20):
                return []

        class FakeGraph:
            def search(self, query, limit=10):
                return []
            def related_locations(self, hits, limit=20):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "conversations")
            conv = store.create("Cross repo")
            store.append(conv.id, "user", "Earlier context mentions AssetLookup.java and RC=8 in the automation playbook")
            store.append(conv.id, "assistant", "ok")
            store.append(conv.id, "user", "Why is it failing now?")
            fake = FakeIndexer()
            compiler = ContextCompiler(Path(tmp), ProjectConfig(name="x"), fake, store, FakeGraph())  # type: ignore[arg-type]
            seed = compiler.initial_retrieval("Why is it failing now?", conv.id)
            self.assertLessEqual(fake.vector_calls, 3)
            self.assertTrue(any(hit.metadata.get("repo") == "secondary" for hit in seed.direct_hits))


class RetrievalFailureTests(unittest.TestCase):
    def test_context_compiler_falls_back_when_vector_embedding_fails(self):
        from project_assistant.config import ProjectConfig
        from project_assistant.context_compiler import ContextCompiler
        from project_assistant.repo_catalog import RepoRoute
        from project_assistant.retrieval_types import SearchHit

        class FakeCatalog:
            def route(self, *args, **kwargs):
                return [RepoRoute("repo", 1.0, ("lexical",))]

        class FakeIndexer:
            def __init__(self):
                self.catalog = FakeCatalog()
            def vector_search(self, query, k=10):
                raise RuntimeError("Portkey embedding HTTP 502: <html><body>Bad Gateway</body></html>")
            def lexical_search(self, query, k=10):
                return [SearchHit("lexical answer", {"id":"l1","repo":"repo","source":"/repo/a.java","relative_path":"a.java","start_line":1,"end_line":2}, 0.0, ("lexical",))]
            def exact_search(self, query, k=10):
                return []
            def fuse(self, vector, lexical, exact, limit):
                return (vector + lexical + exact)[:limit]
            def chunks_for_locations(self, locations, limit=20):
                return []
            def chunks_for_sources(self, paths, limit=20):
                return []

        class FakeGraph:
            def search(self, query, limit=10):
                return []
            def related_locations(self, hits, limit=20):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStore(root / "conversations")
            compiler = ContextCompiler(root, ProjectConfig(name="x"), FakeIndexer(), store, FakeGraph())  # type: ignore[arg-type]
            seed = compiler.initial_retrieval("why did this fail?", None)
            self.assertTrue(seed.direct_hits)
            self.assertTrue(seed.warnings)
            self.assertNotIn("<html>", seed.warnings[0])
            compiled = compiler.compile("why did this fail?", seed=seed)
            self.assertIn("lexical answer", compiled.text)
            self.assertTrue(compiled.retrieval_warnings)

    def test_hybrid_search_falls_back_to_lexical_if_vector_search_fails(self):
        import sys
        import types
        fake_chroma = types.ModuleType("langchain_chroma")
        fake_chroma.Chroma = object
        fake_docs = types.ModuleType("langchain_core.documents")
        fake_docs.Document = object
        fake_fitz = types.ModuleType("fitz")
        with patch.dict(sys.modules, {"langchain_chroma": fake_chroma, "langchain_core.documents": fake_docs, "fitz": fake_fitz}):
            from project_assistant.indexing import IncrementalIndexer
        from project_assistant.retrieval_types import SearchHit

        indexer = object.__new__(IncrementalIndexer)
        indexer.config = type("Config", (), {"rag_top_n": 12, "vector_top_k": 24})()
        indexer.vector_search = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("502"))
        indexer.lexical_search = lambda *args, **kwargs: [SearchHit("local", {"id":"x"}, 0.0, ("lexical",))]
        indexer.exact_search = lambda *args, **kwargs: []
        indexer.fuse = lambda vector, lexical, exact, limit: (vector + lexical + exact)[:limit]
        hits = indexer.search("test")
        self.assertEqual(hits[0].text, "local")

    def test_embedding_http_html_is_sanitised(self):
        from project_assistant.portkey import PortkeyEmbeddings
        detail = PortkeyEmbeddings._safe_http_detail("<html><body><h1>502 Bad Gateway</h1></body></html>")
        self.assertEqual(detail, "502 Bad Gateway")


class LiveSourceAccessTests(unittest.TestCase):
    def test_registered_external_source_can_be_read_without_index_lookup(self):
        from types import SimpleNamespace
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.retrieval_agent import RetrievalAction, RetrievalToolkit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "assistant-project"; project.mkdir()
            external = root / "java-platform"; external.mkdir()
            source = external / "src" / "AssetManager.java"
            source.parent.mkdir(parents=True)
            source.write_text("class AssetManager { String lookup() { return \"live\"; } }\n", encoding="utf-8")
            config = ProjectConfig(name="x", source_roots=[SourceRoot("java-platform", str(external))])
            toolkit = RetrievalToolkit(project, config, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]

            hits = toolkit.execute(RetrievalAction("read_file", target="src/AssetManager.java", repo="java-platform"))
            self.assertEqual(len(hits), 1)
            self.assertIn('return "live"', hits[0].text)
            self.assertEqual(hits[0].metadata["repo"], "java-platform")
            self.assertIn("live-read", hits[0].channels)

    def test_registered_plain_folder_grep_finds_unindexed_source(self):
        from types import SimpleNamespace
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.retrieval_agent import RetrievalAction, RetrievalToolkit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "assistant-project"; project.mkdir()
            external = root / "jcl-library"; external.mkdir()
            (external / "RUNJOB.jcl").write_text("//STEP1 EXEC PGM=IKJEFT01\n", encoding="utf-8")
            config = ProjectConfig(name="x", source_roots=[SourceRoot("jcl-library", str(external))])
            toolkit = RetrievalToolkit(project, config, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]

            hits = toolkit.execute(RetrievalAction("grep_project", query="IKJEFT01", repo="jcl-library"))
            self.assertTrue(hits)
            self.assertIn("jcl-library:RUNJOB.jcl:1", hits[0].text)

    def test_live_read_rejects_symlink_escape(self):
        from types import SimpleNamespace
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.retrieval_agent import RetrievalAction, RetrievalToolkit
        from project_assistant.security import SecurityError

        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unsupported")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "assistant-project"; project.mkdir()
            external = root / "source"; external.mkdir()
            outside = root / "outside.txt"; outside.write_text("do not read\n", encoding="utf-8")
            link = external / "escape.txt"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable")
            config = ProjectConfig(name="x", source_roots=[SourceRoot("source", str(external))])
            toolkit = RetrievalToolkit(project, config, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
            with self.assertRaises(SecurityError):
                toolkit.execute(RetrievalAction("read_file", target="escape.txt", repo="source"))

    def test_controlled_git_reads_use_registered_repo_only(self):
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.source_access import RegisteredSourceAccess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "assistant-project"; project.mkdir()
            repo = CoreTests._git_repo(root)
            (repo / "value.txt").write_text("changed\n", encoding="utf-8")
            access = RegisteredSourceAccess(project, ProjectConfig(name="x", source_roots=[SourceRoot("repo", str(repo))]))
            status = access.git_status("repo")[0].text
            diff = access.git_diff("repo")[0].text
            self.assertIn("value.txt", status)
            self.assertIn("-old", diff)
            self.assertIn("+changed", diff)


class MultiRoundRetrievalTests(unittest.TestCase):
    def test_agent_can_retrieve_across_multiple_rounds_until_sufficient(self):
        from project_assistant.retrieval_agent import RetrievalAgent
        from project_assistant.retrieval_types import SearchHit

        class FakeModel:
            def __init__(self):
                self.calls = 0
            def complete(self, system, user):
                self.calls += 1
                if self.calls == 1:
                    return json.dumps({"sufficient": False, "actions": [
                        {"tool": "read_file", "repo": "java", "target": "AssetManager.java"}
                    ]})
                if self.calls == 2:
                    self.assert_source_visible = "AssetManager.java" in user
                    return json.dumps({"sufficient": False, "actions": [
                        {"tool": "grep_project", "repo": "ansible", "query": "createAsset"}
                    ]})
                return json.dumps({"sufficient": True, "actions": []})

        class FakeToolkit:
            def __init__(self):
                self.calls = []
            def source_names(self):
                return ("project", "java", "ansible")
            def execute(self, action, limit=12):
                self.calls.append(action.tool)
                rel = action.target or ("grep-results" if action.tool == "grep_project" else action.query)
                return [SearchHit(
                    f"evidence from {rel}",
                    {"id": f"{action.tool}:{rel}", "repo": action.repo or "project", "relative_path": rel, "egress_allowed": True},
                    1.0,
                    (action.tool,),
                )]

        model = FakeModel()
        toolkit = FakeToolkit()
        agent = RetrievalAgent(model, toolkit)  # type: ignore[arg-type]
        result = agent.plan_and_retrieve("fix it", "recent", [], [], max_rounds=3)
        self.assertEqual(toolkit.calls, ["read_file", "grep_project"])
        self.assertEqual(result.rounds, 3)
        self.assertEqual([action.round_no for action in result.actions], [1, 2])
        self.assertTrue(getattr(model, "assert_source_visible", False))
        self.assertEqual(len(result.hits), 2)

class GitStateRefreshTests(unittest.TestCase):
    def test_manifest_git_metadata_refreshes_after_plain_folder_becomes_repo(self):
        import sys
        import types
        fake_chroma = types.ModuleType("langchain_chroma")
        fake_chroma.Chroma = object
        fake_docs = types.ModuleType("langchain_core.documents")
        fake_docs.Document = object
        with patch.dict(sys.modules, {"langchain_chroma": fake_chroma, "langchain_core.documents": fake_docs}):
            from project_assistant.indexing import IncrementalIndexer
        from project_assistant.config import SourceRoot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy-source"
            source.mkdir()
            (source / "main.txt").write_text("hello\n", encoding="utf-8")

            indexer = object.__new__(IncrementalIndexer)
            indexer._git_cache = {}
            record = {"git_branch": None, "git_commit": None}
            source_root = SourceRoot("legacy", str(source))
            self.assertFalse(indexer._refresh_record_git_metadata(source_root, record))

            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Project Assistant Tests"], check=True)
            subprocess.run(["git", "-C", str(source), "add", "main.txt"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "initial"], check=True)
            indexer._git_cache.clear()

            self.assertTrue(indexer._refresh_record_git_metadata(source_root, record))
            self.assertTrue(record["git_commit"])
            self.assertEqual(record["git_commit"], subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip())

    def test_repository_state_marks_plain_folder_not_applicable_and_git_repo_verified(self):
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.source_access import RegisteredSourceAccess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "assistant-project"; project.mkdir()
            plain = root / "plain"; plain.mkdir()
            repo = CoreTests._git_repo(root)
            config = ProjectConfig(name="x", source_roots=[SourceRoot("plain", str(plain)), SourceRoot("repo", str(repo))])
            access = RegisteredSourceAccess(project, config)

            plain_state = access.repository_state("plain")
            repo_state = access.repository_state("repo")
            self.assertFalse(plain_state["is_git"])
            self.assertEqual(plain_state["working_tree"], "not-applicable")
            self.assertTrue(repo_state["is_git"])
            self.assertTrue(repo_state["head"])
            self.assertEqual(repo_state["working_tree"], "clean")

class ProposalGitVerificationTests(unittest.TestCase):
    def test_change_proposal_git_snapshot_uses_live_registered_repo_state(self):
        import sys
        import types
        from types import SimpleNamespace
        fake_chroma = types.ModuleType("langchain_chroma")
        fake_chroma.Chroma = object
        fake_docs = types.ModuleType("langchain_core.documents")
        fake_docs.Document = object
        fake_fitz = types.ModuleType("fitz")
        with patch.dict(sys.modules, {"langchain_chroma": fake_chroma, "langchain_core.documents": fake_docs, "fitz": fake_fitz}):
            from project_assistant.assistant import ProjectAssistant
        from project_assistant.config import ProjectConfig, SourceRoot
        from project_assistant.retrieval_types import SearchHit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "assistant-project"; project.mkdir()
            repo = CoreTests._git_repo(root)
            assistant = object.__new__(ProjectAssistant)
            assistant.project_dir = project
            assistant.config = ProjectConfig(name="x", source_roots=[SourceRoot("repo", str(repo))])
            context = SimpleNamespace(
                retrieved_hits=(SearchHit("code", {"repo": "repo"}, 1.0, ("live-read",)),),
                routed_repos=(),
            )
            snapshot = assistant._live_git_state_for_context(context)
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            self.assertIn("repo: branch=", snapshot)
            self.assertIn(f"HEAD={head}", snapshot)
            self.assertIn("working_tree=clean", snapshot)
