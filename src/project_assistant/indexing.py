from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

import fitz
from langchain_chroma import Chroma
from langchain_core.documents import Document

from .code_chunking import CodeChunker
from .config import ProjectConfig, SourceRoot
from .knowledge_graph import GRAPH_INDEX_VERSION, KnowledgeGraph
from .index_status import IndexStatusStore
from .index_policy import INDEX_POLICY_VERSION, FileEligibilityPolicy, should_skip_dir
from .lexical_index import LexicalHit, LexicalIndex
from .repo_catalog import RepositoryCatalog
from .retrieval_types import SearchHit
from .security import EgressPolicy, private_file, path_is_within

class IncrementalIndexer:
    def __init__(self, project_dir: Path, config: ProjectConfig, embedding_function):
        self.project_dir = project_dir.resolve()
        self.config = config
        self.embedding_function = embedding_function
        self.chroma_path = self.project_dir / ".assistant/chroma"
        self.manifest_path = self.project_dir / ".assistant/index_manifest.json"
        self.status_store = IndexStatusStore(self.project_dir / ".assistant/index_status.json")
        self.graph = KnowledgeGraph(self.project_dir / ".assistant/knowledge_graph.sqlite3")
        self.lexical = LexicalIndex(self.project_dir / ".assistant/lexical.sqlite3")
        self.catalog = RepositoryCatalog(self.project_dir / ".assistant/repo_catalog.json")
        self.egress = EgressPolicy()
        self.file_policy = FileEligibilityPolicy(self.egress)
        self.db = Chroma(
            persist_directory=str(self.chroma_path),
            embedding_function=embedding_function,
            collection_name="project-assistant",
        )
        self.chunker = CodeChunker(max_chars=max(config.chunk_size * 3, 4200), overlap_lines=8)
        self._git_cache: dict[str, dict[str, str | None]] = {}

    def index_changed(self) -> dict[str, int]:
        # Git state can change independently of file contents (for example a source
        # folder may be git-init'd after it was first indexed). Never reuse Git
        # metadata across indexing runs.
        self._git_cache.clear()
        self.status_store.start()
        manifest = self._load_manifest()
        current: dict[str, tuple[SourceRoot, Path]] = {}
        try:
            for source in self._all_sources():
                root = Path(source.path).resolve()
                if not root.exists():
                    continue
                for path in self._iter_candidates(root, use_git=(root != self.project_dir)):
                    rel = self._relative(path, root)
                    repo_stats = self.status_store.repo(source.name)
                    self.status_store.status.scanned += 1
                    repo_stats.scanned += 1
                    allowed, reason = self._embedding_eligibility(path, root)
                    if not allowed:
                        self.status_store.skipped(source.name, rel, reason or "excluded")
                        continue
                    self.status_store.status.eligible += 1
                    repo_stats.eligible += 1
                    current[str(path.resolve())] = (source, path)

            old_paths = set(manifest)
            current_paths = set(current)
            deleted = old_paths - current_paths
            for source_path in deleted:
                self._delete_manifest_entry(manifest, source_path)
                self.graph.remove_source(source_path)
            self.status_store.status.deleted = len(deleted)

            added = changed = unchanged = local_only = 0
            for source_path, (source, path) in current.items():
                rel = self._relative(path, Path(source.path))
                self.status_store.current(source.name, rel)
                fingerprint = self._fingerprint(path)
                record = manifest.get(source_path)
                if (
                    record
                    and record.get("fingerprint") == fingerprint
                    and record.get("index_policy_version") == INDEX_POLICY_VERSION
                ):
                    # Repository state is independent of file content. Refresh
                    # branch/HEAD metadata even when the file itself is unchanged,
                    # without re-embedding the source. This handles directories that
                    # become Git repos after their initial index and normal HEAD moves.
                    record_changed = self._refresh_record_git_metadata(source, record)
                    # Graph parser upgrades are local-only. Refresh structural
                    # relationships without re-embedding unchanged source files.
                    if record.get("graph_index_version") != GRAPH_INDEX_VERSION:
                        self._refresh_graph(source, path)
                        record["graph_index_version"] = GRAPH_INDEX_VERSION
                        record_changed = True
                    if record_changed:
                        manifest[source_path] = record
                        self._save_manifest(manifest)
                    unchanged += 1
                    self.status_store.status.unchanged += 1
                    self.status_store.repo(source.name).unchanged += 1
                    continue
                if record:
                    self._delete_manifest_entry(manifest, source_path)
                    self.graph.remove_source(source_path)
                    changed += 1
                    self.status_store.status.changed += 1
                else:
                    added += 1
                    self.status_store.status.added += 1

                indexed = self._index_one(source, path, fingerprint)
                manifest[source_path] = indexed
                self._save_manifest(manifest)
                repo_stats = self.status_store.repo(source.name)
                repo_stats.indexed += 1
                repo_stats.chunks += len(indexed.get("chunk_ids", []))
                self.status_store.status.indexed += 1
                self.status_store.status.chunks += len(indexed.get("chunk_ids", []))
                if not indexed.get("vector_indexed", True):
                    local_only += 1
                    if indexed.get("embedding_error"):
                        reason = "embedding-rejected"
                    elif indexed.get("egress_blocked"):
                        reason = "egress-blocked"
                    elif not indexed.get("chunk_ids"):
                        reason = "empty"
                    else:
                        reason = "local-only"
                    self.status_store.local_only(source.name, rel, reason)
                self.status_store.save()

            self._save_manifest(manifest)
            self.catalog.rebuild(self.config.resolved_sources(self.project_dir), manifest)
            self.status_store.finish()
            return {"added": added, "changed": changed, "deleted": len(deleted), "unchanged": unchanged, "local_only": local_only, "skipped": self.status_store.status.skipped, "chunks": self.status_store.status.chunks}
        except Exception as exc:
            self.status_store.fail(f"{type(exc).__name__}: {exc}")
            raise

    def index_specific_file(self, path: Path) -> None:
        self._git_cache.clear()
        path = path.resolve()
        source = self._source_for(path)
        if source is None:
            source = SourceRoot("project", str(self.project_dir))
        manifest = self._load_manifest()
        key = str(path)
        if not path.exists():
            self._delete_manifest_entry(manifest, key)
            self.graph.remove_source(key)
            self._save_manifest(manifest)
            return
        fingerprint = self._fingerprint(path)
        existing = manifest.get(key, {})
        if existing.get("fingerprint") == fingerprint and existing.get("index_policy_version") == INDEX_POLICY_VERSION:
            record_changed = self._refresh_record_git_metadata(source, existing)
            if existing.get("graph_index_version") != GRAPH_INDEX_VERSION:
                self._refresh_graph(source, path)
                existing["graph_index_version"] = GRAPH_INDEX_VERSION
                record_changed = True
            if record_changed:
                manifest[key] = existing
                self._save_manifest(manifest)
            return
        if key in manifest:
            self._delete_manifest_entry(manifest, key)
            self.graph.remove_source(key)
        manifest[key] = self._index_one(source, path, fingerprint)
        self._save_manifest(manifest)
        self.catalog.rebuild(self.config.resolved_sources(self.project_dir), manifest)

    @staticmethod
    def _assistant_kind(metadata: dict) -> str:
        explicit = str(metadata.get("assistant_kind") or "").strip()
        if explicit:
            return explicit
        rel = str(metadata.get("relative_path") or "").replace("\\", "/")
        if rel.startswith(".assistant/conversations/"):
            return "raw_conversation"
        if rel.startswith(".assistant/generated/consolidations/"):
            return "daily_consolidation"
        if rel == ".assistant/generated/user_memory.md":
            return "user_memory"
        return "project"

    @classmethod
    def _filter_assistant_kinds(
        cls,
        hits: list[SearchHit],
        *,
        include: set[str] | None = None,
        exclude: set[str] | None = None,
        limit: int | None = None,
    ) -> list[SearchHit]:
        result: list[SearchHit] = []
        for hit in hits:
            kind = cls._assistant_kind(hit.metadata)
            if include is not None and kind not in include:
                continue
            if exclude is not None and kind in exclude:
                continue
            result.append(hit)
            if limit is not None and len(result) >= limit:
                break
        return result

    def search_assistant_kind(self, query: str, kind: str, k: int = 8) -> list[SearchHit]:
        """Hybrid retrieval constrained to one assistant-memory class.

        Over-fetching keeps this useful with indexes created before assistant_kind
        metadata existed; the path-based classifier remains backwards compatible.
        """
        fetch = max(32, k * 8)
        try:
            vector = self._filter_assistant_kinds(self.vector_search(query, k=fetch), include={kind}, limit=fetch)
        except Exception:
            vector = []
        lexical = self._filter_assistant_kinds(self.lexical_search(query, k=fetch), include={kind}, limit=fetch)
        exact = self._filter_assistant_kinds(self.exact_search(query, k=fetch), include={kind}, limit=fetch)
        return self.fuse(vector, lexical, exact, limit=k)

    def vector_search(self, query: str, k: int | None = None, repos: set[str] | None = None) -> list[SearchHit]:
        k = k or self.config.vector_top_k
        fetch_k = max(k, k * 4 if repos else k)
        pairs = self.db.similarity_search_with_score(query, k=fetch_k)
        hits = [SearchHit(doc.page_content, doc.metadata, float(score), ("vector",)) for doc, score in pairs]
        if repos:
            hits = [hit for hit in hits if hit.metadata.get("repo") in repos]
        return hits[:k]

    def lexical_search(self, query: str, k: int = 24, repos: set[str] | None = None) -> list[SearchHit]:
        return [self._lexical_hit(hit, "lexical") for hit in self.lexical.search(query, limit=k, repos=repos)]

    def exact_search(self, query: str, k: int = 16, repos: set[str] | None = None) -> list[SearchHit]:
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for term in self._exact_terms(query):
            for hit in self.lexical.exact(term, limit=k, repos=repos):
                if hit.id in seen:
                    continue
                seen.add(hit.id)
                hits.append(self._lexical_hit(hit, "exact"))
                if len(hits) >= k:
                    return hits
        return hits

    def search(self, query: str, k: int | None = None, repos: set[str] | None = None) -> list[SearchHit]:
        """Hybrid vector + lexical + exact retrieval using reciprocal-rank fusion."""
        k = k or self.config.rag_top_n
        try:
            vector = self.vector_search(query, k=max(self.config.vector_top_k, k * 2), repos=repos)
        except Exception:
            # Semantic search is an enhancement. The already-persisted local FTS
            # and exact indexes must remain usable if Portkey/Bedrock is unavailable.
            vector = []
        lexical = self.lexical_search(query, k=max(24, k * 3), repos=repos)
        exact = self.exact_search(query, k=max(12, k * 2), repos=repos)
        return self.fuse(vector, lexical, exact, limit=k)

    def chunks_for_sources(self, source_paths: list[str], limit: int = 20) -> list[SearchHit]:
        return [self._lexical_hit(hit, "graph-expansion") for hit in self.lexical.chunks_for_sources(source_paths, limit=limit)]


    def chunks_for_locations(self, locations: list[tuple[str, int | None]], limit: int = 20) -> list[SearchHit]:
        return [self._lexical_hit(hit, "graph-expansion") for hit in self.lexical.chunks_for_locations(locations, limit=limit)]

    @staticmethod
    def fuse(vector: list[SearchHit], lexical: list[SearchHit], exact: list[SearchHit], limit: int) -> list[SearchHit]:
        scores: dict[str, float] = {}
        hits: dict[str, SearchHit] = {}
        channels: dict[str, list[str]] = {}
        for channel, items, weight in (("exact", exact, 4.0), ("lexical", lexical, 2.4), ("vector", vector, 1.6)):
            for rank, hit in enumerate(items, start=1):
                chunk_id = str(hit.metadata.get("id") or hashlib.sha1((hit.metadata.get("source", "") + hit.text[:128]).encode()).hexdigest())
                scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (50 + rank)
                hits.setdefault(chunk_id, hit)
                channels.setdefault(chunk_id, []).append(channel)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [
            SearchHit(hits[cid].text, hits[cid].metadata, scores[cid], tuple(dict.fromkeys(channels[cid])))
            for cid in ordered
        ]

    def _all_sources(self) -> list[SourceRoot]:
        sources = self.config.resolved_sources(self.project_dir)
        local = SourceRoot("project", str(self.project_dir))
        return sources + [local]

    def _source_for(self, path: Path) -> SourceRoot | None:
        for source in self.config.resolved_sources(self.project_dir):
            try:
                path.relative_to(Path(source.path).resolve())
                return source
            except ValueError:
                pass
        return None

    def _iter_candidates(self, root: Path, use_git: bool = True) -> Iterable[Path]:
        root = root.resolve()
        if use_git:
            git_files = self._git_files(root)
            if git_files is not None:
                yield from git_files
                return
        for current_root, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]
            for filename in files:
                unresolved = Path(current_root) / filename
                try:
                    path = unresolved.resolve()
                except OSError:
                    continue
                if not path_is_within(root, path):
                    continue
                if root == self.project_dir:
                    rel = path.relative_to(root)
                    if rel.parts and rel.parts[0] == ".assistant":
                        if len(rel.parts) < 2 or rel.parts[1] not in {"conversations", "generated"}:
                            continue
                yield path

    def _iter_files(self, root: Path, use_git: bool = True) -> Iterable[Path]:
        # Backwards-compatible helper used by tests/callers that only want files
        # eligible for local parsing + remote embeddings.
        for path in self._iter_candidates(root, use_git=use_git):
            allowed, _ = self._embedding_eligibility(path, root.resolve())
            if allowed:
                yield path

    def _embedding_eligibility(self, path: Path, root: Path) -> tuple[bool, str | None]:
        return self.file_policy.classify(path)

    def _index_one(self, source: SourceRoot, path: Path, fingerprint: str) -> dict:
        docs = self._load_documents(source, path)
        chunk_ids: list[str] = []
        for i, chunk in enumerate(docs):
            locator = chunk.metadata.get("start_line") or chunk.metadata.get("page_start") or chunk.metadata.get("page") or 0
            symbol = chunk.metadata.get("symbol") or ""
            content_hash = hashlib.sha1(chunk.page_content.encode("utf-8")).hexdigest()[:16]
            raw = f"{path.resolve()}:{locator}:{symbol}:{content_hash}"
            cid = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            chunk.metadata["id"] = cid
            chunk.metadata["chunk_index"] = i
            chunk_ids.append(cid)

        # Lexical/graph indexes are local. Only high-confidence credential
        # material blocks remote embeddings. Ordinary credential references
        # (vault names, secret identifiers, environment-variable references, etc.)
        # are advisory and remain eligible for RAG. Mark every lexical chunk so
        # the context compiler can keep hard-blocked local-only content off the
        # outbound GPT path.
        hard_findings = sorted({finding for chunk in docs for finding in self.egress.hard_findings(chunk.page_content)})
        advisory_findings = sorted({finding for chunk in docs for finding in self.egress.advisory_findings(chunk.page_content)})
        egress_allowed = not hard_findings
        for chunk, cid in zip(docs, chunk_ids):
            chunk.metadata["egress_allowed"] = egress_allowed
            chunk.metadata["egress_advisory"] = ",".join(advisory_findings)
            self.lexical.upsert(cid, chunk.page_content, chunk.metadata)

        findings = hard_findings
        vector_indexed = bool(docs) and egress_allowed
        embedding_error = None
        if vector_indexed:
            try:
                self.db.add_documents(docs, ids=chunk_ids)
            except Exception as exc:
                # Keep lexical/graph retrieval available and continue the wider
                # indexing run when a provider rejects one particular source file.
                vector_indexed = False
                embedding_error = f"{type(exc).__name__}: {exc}"
                try:
                    self.db.delete(ids=chunk_ids)
                except Exception:
                    pass
                self._security_event(path, "remote embedding skipped: " + embedding_error)
        elif findings:
            self._security_event(path, "embedding blocked: " + ", ".join(findings))

        self._refresh_graph(source, path)

        git = self._git_metadata(Path(source.path)) if source.name != "project" else {"branch": None, "commit": None}
        return {
            "fingerprint": fingerprint,
            "index_policy_version": INDEX_POLICY_VERSION,
            "graph_index_version": GRAPH_INDEX_VERSION,
            "chunk_ids": chunk_ids,
            "source": source.name,
            "relative_path": self._relative(path, Path(source.path)),
            "git_branch": git.get("branch"),
            "git_commit": git.get("commit"),
            "vector_indexed": vector_indexed,
            "egress_blocked": findings,
            "egress_advisory": advisory_findings,
            "embedding_error": embedding_error,
        }


    def _refresh_record_git_metadata(self, source: SourceRoot, record: dict) -> bool:
        """Refresh manifest Git metadata without touching embeddings.

        A source root can become a Git repository after its first index, and HEAD can
        move while all tracked file contents remain unchanged. Persist live repo state
        separately from content fingerprints so reindexing does not require a remote
        embedding call just to verify branch/commit metadata.
        """
        git = self._git_metadata(Path(source.path)) if source.name != "project" else {"branch": None, "commit": None}
        branch = git.get("branch")
        commit = git.get("commit")
        changed = record.get("git_branch") != branch or record.get("git_commit") != commit
        record["git_branch"] = branch
        record["git_commit"] = commit
        return changed

    def _refresh_graph(self, source: SourceRoot, path: Path) -> None:
        if path.suffix.lower() == ".pdf":
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        self.graph.index_file(source.name, Path(source.path), path, text)

    def _load_documents(self, source: SourceRoot, path: Path) -> list[Document]:
        git = self._git_metadata(Path(source.path)) if source.name != "project" else {"branch": None, "commit": None}
        relative_path = self._relative(path, Path(source.path))
        base = {
            "source": str(path.resolve()),
            "repo": source.name,
            "relative_path": relative_path,
            "assistant_kind": self._assistant_kind({"relative_path": relative_path}),
            "git_branch": git.get("branch") or "",
            "git_commit": git.get("commit") or "",
        }
        if path.suffix.lower() != ".pdf":
            text = path.read_text(encoding="utf-8", errors="replace")
            pieces = self.chunker.chunk_file(path, text)
            return [
                Document(
                    page_content=piece.text,
                    metadata={
                        **base,
                        "start_line": piece.start_line,
                        "end_line": piece.end_line,
                        "symbol": piece.symbol or "",
                        "symbol_kind": piece.symbol_kind or "",
                        "language": piece.language,
                    },
                )
                for piece in pieces
            ]

        pdf = fitz.open(path)
        pages: list[Document] = []
        for i, page in enumerate(pdf, start=1):
            r = page.rect
            clip = fitz.Rect(r.x0, r.y0 + 50, r.x1, r.y1 - 50)
            text = page.get_text("text", sort=True, clip=clip).strip()
            if text:
                pages.append(Document(page_content=text, metadata={**base, "page": i, "language": "pdf"}))

        grouped: list[Document] = []
        window, stride = 3, 2
        for start in range(0, len(pages), stride):
            group = pages[start:start + window]
            if not group:
                break
            text = "\n\n".join(f"<!-- page:{d.metadata['page']} -->\n{d.page_content}" for d in group)
            grouped.append(Document(page_content=text, metadata={**base, "page_start": group[0].metadata["page"], "page_end": group[-1].metadata["page"], "language": "pdf"}))
            if start + window >= len(pages):
                break
        return self._bound_documents(grouped)

    def _bound_documents(self, docs: list[Document]) -> list[Document]:
        limit = self.chunker.max_chars
        bounded: list[Document] = []
        for doc in docs:
            text = doc.page_content
            if len(text) <= limit:
                bounded.append(doc)
                continue
            for index, start in enumerate(range(0, len(text), limit), start=1):
                part = text[start:start + limit].strip()
                if not part:
                    continue
                bounded.append(Document(page_content=part, metadata={**doc.metadata, "part": index}))
        return bounded

    def _security_event(self, path: Path, message: str) -> None:
        log = self.project_dir / ".assistant/security_events.log"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{path.name}: {message}\n")
        private_file(log)

    def _git_files(self, root: Path) -> list[Path] | None:
        try:
            git_root_raw = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            git_root = Path(git_root_raw).resolve()
            prefix = str(root.relative_to(git_root)) if root != git_root else "."
            command = ["git", "-C", str(git_root), "ls-files", "-z"]
            if prefix != ".":
                command += ["--", prefix]
            raw = subprocess.run(command, check=True, capture_output=True).stdout
            result = []
            for item in raw.decode("utf-8", errors="surrogateescape").split("\0"):
                if not item:
                    continue
                path = (git_root / item).resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                if path.is_file():
                    result.append(path)
            return result
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            return None

    def _git_metadata(self, root: Path) -> dict[str, str | None]:
        key = str(root.resolve())
        if key in self._git_cache:
            return self._git_cache[key]
        result: dict[str, str | None] = {"branch": None, "commit": None}
        try:
            result["branch"] = subprocess.run(
                ["git", "-C", key, "branch", "--show-current"], check=True, capture_output=True, text=True
            ).stdout.strip() or None
            result["commit"] = subprocess.run(
                ["git", "-C", key, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            ).stdout.strip() or None
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        self._git_cache[key] = result
        return result

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            return path.name

    @staticmethod
    def _fingerprint(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def _delete_manifest_entry(self, manifest: dict, source_path: str) -> None:
        record = manifest.pop(source_path, None)
        if record and record.get("chunk_ids"):
            self.db.delete(ids=record["chunk_ids"])
        self.lexical.delete_source(source_path)

    def _load_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _save_manifest(self, manifest: dict) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        private_file(self.manifest_path)

    @staticmethod
    def _lexical_hit(hit: LexicalHit, channel: str) -> SearchHit:
        return SearchHit(hit.text, hit.metadata, hit.bm25, (channel,))

    @staticmethod
    def _exact_terms(query: str) -> list[str]:
        terms: list[str] = []
        terms.extend(re.findall(r"`([^`]{2,120})`", query))
        terms.extend(re.findall(r"\b[A-Z][A-Z0-9_$#@-]{3,}\b", query))
        terms.extend(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\s*(?=\()", query))
        terms.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b", query))
        terms.extend(re.findall(r"\b[\w.-]+/[\w./-]+\b", query))
        return list(dict.fromkeys(term.strip() for term in terms if term.strip()))[:12]
