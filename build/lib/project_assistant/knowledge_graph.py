from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GraphHit:
    node_id: str
    node_type: str
    name: str
    source_path: str
    neighbours: tuple[str, ...]
    metadata: dict = field(default_factory=dict)


class KnowledgeGraph:
    """Deterministic local structural graph for cross-file/code relationships."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
                CREATE INDEX IF NOT EXISTS idx_nodes_source ON nodes(source_path);
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);

                CREATE TABLE IF NOT EXISTS edges (
                    src TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    PRIMARY KEY (src, dst, relation, source_path)
                );
                CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
                CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
                """
            )

    @staticmethod
    def _id(kind: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
        return f"{kind}:{digest}"

    def remove_source(self, source_path: str) -> None:
        with self._connect() as conn:
            # Remove all edges contributed by the file, then file-local nodes.
            conn.execute("DELETE FROM edges WHERE source_path = ?", (source_path,))
            conn.execute("DELETE FROM nodes WHERE source_path = ?", (source_path,))
            conn.execute(
                "DELETE FROM nodes WHERE (source_path = '' OR type = 'repo') "
                "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.src = nodes.id OR e.dst = nodes.id)"
            )

    def index_file(self, repo_name: str, repo_root: Path, path: Path, text: str) -> None:
        source_path = str(path.resolve())
        self.remove_source(source_path)
        try:
            rel = str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            rel = path.name
        repo_id = self._id("repo", f"{repo_name}:{repo_root.resolve()}")
        file_id = self._id("file", source_path)

        with self._connect() as conn:
            self._node(conn, repo_id, "repo", repo_name, str(repo_root.resolve()), {"root": str(repo_root.resolve())})
            self._node(conn, file_id, "file", rel, source_path, {"repo": repo_name, "relative_path": rel})
            self._edge(conn, repo_id, file_id, "CONTAINS", source_path)

            suffix = path.suffix.lower()
            if suffix == ".py":
                self._index_python(conn, file_id, source_path, text)
            elif suffix in {".md", ".markdown"}:
                self._index_markdown(conn, file_id, source_path, text)
            elif suffix in {".yml", ".yaml"}:
                self._index_yaml(conn, file_id, source_path, text)
            elif suffix in {".jcl", ".proc"}:
                self._index_jcl(conn, file_id, source_path, text)
            elif suffix in {".cob", ".cbl", ".cobol"}:
                self._index_cobol(conn, file_id, source_path, text)
            elif suffix in {".rexx", ".rex"}:
                self._index_rexx(conn, file_id, source_path, text)

    def search(self, query: str, limit: int = 10) -> list[GraphHit]:
        raw_terms = re.findall(r"`([^`]+)`|\b([A-Za-z_$#@][A-Za-z0-9_.$#@-]{1,})\b", query)
        terms = [next(v for v in pair if v).lower() for pair in raw_terms]
        terms = list(dict.fromkeys(terms))[:16]
        if not terms:
            return []

        scored: dict[str, tuple[int, sqlite3.Row]] = {}
        with self._connect() as conn:
            for term in terms:
                rows = conn.execute(
                    """
                    SELECT id,type,name,source_path,metadata_json,
                           CASE
                             WHEN lower(name)=? THEN 8
                             WHEN lower(name) LIKE ? THEN 4
                             ELSE 1
                           END AS match_score
                    FROM nodes
                    WHERE lower(name)=? OR lower(name) LIKE ?
                    LIMIT ?
                    """,
                    (term, f"{term}%", term, f"%{term}%", max(limit * 6, 40)),
                ).fetchall()
                for row in rows:
                    score, _ = scored.get(row["id"], (0, row))
                    scored[row["id"]] = (score + int(row["match_score"]), row)

            results: list[GraphHit] = []
            for _, row in sorted(scored.values(), key=lambda item: item[0], reverse=True)[:limit]:
                neighbour_rows = conn.execute(
                    """
                    SELECT n.name,n.type,n.source_path,e.relation
                    FROM edges e
                    JOIN nodes n ON n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
                    WHERE e.src = ? OR e.dst = ?
                    LIMIT 12
                    """,
                    (row["id"], row["id"], row["id"]),
                ).fetchall()
                neighbours = tuple(
                    f"{n['relation']} → {n['type']}:{n['name']}" + (f" [{n['source_path']}]" if n["source_path"] else "")
                    for n in neighbour_rows
                )
                results.append(
                    GraphHit(
                        row["id"], row["type"], row["name"], row["source_path"], neighbours,
                        json.loads(row["metadata_json"] or "{}"),
                    )
                )
            return results

    def related_source_paths(self, hits: list[GraphHit], limit: int = 20) -> list[str]:
        """Return file paths attached to matched nodes and their immediate neighbours."""
        paths: list[str] = []
        seen: set[str] = set()
        with self._connect() as conn:
            for hit in hits:
                if hit.source_path and Path(hit.source_path).is_file() and hit.source_path not in seen:
                    seen.add(hit.source_path)
                    paths.append(hit.source_path)
                rows = conn.execute(
                    """
                    SELECT DISTINCT n.source_path
                    FROM edges e
                    JOIN nodes n ON n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
                    WHERE (e.src = ? OR e.dst = ?) AND n.source_path <> ''
                    LIMIT 8
                    """,
                    (hit.node_id, hit.node_id, hit.node_id),
                ).fetchall()
                for row in rows:
                    path = row["source_path"]
                    if path and Path(path).is_file() and path not in seen:
                        seen.add(path)
                        paths.append(path)
                        if len(paths) >= limit:
                            return paths
        return paths


    def related_locations(self, hits: list[GraphHit], limit: int = 24) -> list[tuple[str, int | None]]:
        locations: list[tuple[str, int | None]] = []
        seen: set[tuple[str, int | None]] = set()
        with self._connect() as conn:
            for hit in hits:
                if hit.source_path:
                    item = (hit.source_path, hit.metadata.get("line"))
                    if item not in seen:
                        seen.add(item)
                        locations.append(item)
                rows = conn.execute(
                    """
                    SELECT n.source_path,n.metadata_json
                    FROM edges e
                    JOIN nodes n ON n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
                    WHERE (e.src = ? OR e.dst = ?) AND n.source_path <> ''
                    LIMIT 12
                    """,
                    (hit.node_id, hit.node_id, hit.node_id),
                ).fetchall()
                for row in rows:
                    metadata = json.loads(row["metadata_json"] or "{}")
                    item = (row["source_path"], metadata.get("line"))
                    if item in seen:
                        continue
                    seen.add(item)
                    locations.append(item)
                    if len(locations) >= limit:
                        return locations
        return locations

    def _node(self, conn, node_id: str, kind: str, name: str, source_path: str, metadata: dict) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO nodes(id,type,name,source_path,metadata_json) VALUES(?,?,?,?,?)",
            (node_id, kind, name, source_path, json.dumps(metadata, sort_keys=True)),
        )

    def _edge(self, conn, src: str, dst: str, relation: str, source_path: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO edges(src,dst,relation,source_path) VALUES(?,?,?,?)",
            (src, dst, relation, source_path),
        )

    def _global_name(self, conn, name: str, kind: str = "symbol_name") -> str:
        node_id = self._id(kind, name.lower())
        conn.execute(
            "INSERT OR IGNORE INTO nodes(id,type,name,source_path,metadata_json) VALUES(?,?,?,?,?)",
            (node_id, kind, name, "", "{}"),
        )
        return node_id

    def _symbol(
        self, conn, file_id: str, source_path: str, kind: str, name: str,
        line: int | None = None, end_line: int | None = None,
    ) -> str:
        sid = self._id(kind, f"{source_path}:{kind}:{name}:{line or 0}")
        self._node(conn, sid, kind, name, source_path, {"line": line, "end_line": end_line})
        self._edge(conn, file_id, sid, "DEFINES", source_path)
        global_id = self._global_name(conn, name)
        self._edge(conn, sid, global_id, "DEFINES_NAME", source_path)
        return sid

    def _index_python(self, conn, file_id: str, source_path: str, text: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return

        symbol_for_node: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sid = self._symbol(conn, file_id, source_path, "function", node.name, node.lineno, getattr(node, "end_lineno", None))
                symbol_for_node[id(node)] = sid
            elif isinstance(node, ast.ClassDef):
                sid = self._symbol(conn, file_id, source_path, "class", node.name, node.lineno, getattr(node, "end_lineno", None))
                symbol_for_node[id(node)] = sid
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mid = self._global_name(conn, alias.name, "module")
                    self._edge(conn, file_id, mid, "IMPORTS", source_path)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mid = self._global_name(conn, node.module, "module")
                self._edge(conn, file_id, mid, "IMPORTS", source_path)

        def visit_calls(node, owner_id: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        called = child.func.id
                    elif isinstance(child.func, ast.Attribute):
                        called = child.func.attr
                    else:
                        called = ""
                    if called:
                        target = self._global_name(conn, called)
                        self._edge(conn, owner_id, target, "CALLS", source_path)
                visit_calls(child, owner_id)

        for node in tree.body:
            owner = symbol_for_node.get(id(node), file_id)
            visit_calls(node, owner)

    def _index_markdown(self, conn, file_id: str, source_path: str, text: str) -> None:
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                self._symbol(conn, file_id, source_path, "section", match.group(2), line_no)

    def _index_yaml(self, conn, file_id: str, source_path: str, text: str) -> None:
        current_task: str | None = None
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^\s*-\s+name:\s*[\"']?(.+?)[\"']?\s*$", line)
            if match:
                current_task = self._symbol(conn, file_id, source_path, "ansible_task", match.group(1), line_no)
                continue
            module = re.match(r"^\s+(?:ansible\.(?:builtin|legacy)\.)?([A-Za-z_][\w.]*)\s*:\s*(?:#.*)?$", line)
            if current_task and module and module.group(1) not in {"name", "when", "vars", "tags", "block", "rescue", "always"}:
                target = self._global_name(conn, module.group(1), "ansible_module")
                self._edge(conn, current_task, target, "USES_MODULE", source_path)

    def _index_jcl(self, conn, file_id: str, source_path: str, text: str) -> None:
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^//([A-Z0-9@$#]+)\s+(JOB|EXEC)\b(.*)$", line, re.IGNORECASE)
            if not match:
                continue
            kind = "jcl_job" if match.group(2).upper() == "JOB" else "jcl_step"
            sid = self._symbol(conn, file_id, source_path, kind, match.group(1), line_no)
            if kind == "jcl_step":
                target_match = re.search(r"\b(?:PGM|PROC)\s*=\s*([A-Z0-9@$#.-]+)", match.group(3), re.IGNORECASE)
                if target_match:
                    target = self._global_name(conn, target_match.group(1), "jcl_target")
                    self._edge(conn, sid, target, "EXECUTES", source_path)

    def _index_cobol(self, conn, file_id: str, source_path: str, text: str) -> None:
        current_owner = file_id
        for line_no, line in enumerate(text.splitlines(), start=1):
            program = re.search(r"\bPROGRAM-ID\.\s+([A-Z0-9-]+)", line, re.IGNORECASE)
            if program:
                current_owner = self._symbol(conn, file_id, source_path, "cobol_program", program.group(1), line_no)
            paragraph = re.match(r"^\s{0,8}([A-Z0-9-]+)\.\s*$", line, re.IGNORECASE)
            if paragraph:
                current_owner = self._symbol(conn, file_id, source_path, "cobol_paragraph", paragraph.group(1), line_no)
            call = re.search(r"\bCALL\s+[\"']([^\"']+)[\"']", line, re.IGNORECASE)
            if call:
                target = self._global_name(conn, call.group(1), "cobol_call_target")
                self._edge(conn, current_owner, target, "CALLS", source_path)
            perform = re.search(r"\bPERFORM\s+([A-Z0-9-]+)", line, re.IGNORECASE)
            if perform:
                target = self._global_name(conn, perform.group(1))
                self._edge(conn, current_owner, target, "PERFORMS", source_path)

    def _index_rexx(self, conn, file_id: str, source_path: str, text: str) -> None:
        current_owner = file_id
        for line_no, line in enumerate(text.splitlines(), start=1):
            label = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(?:/\*.*\*/)?$", line)
            if label:
                current_owner = self._symbol(conn, file_id, source_path, "rexx_label", label.group(1), line_no)
            call = re.search(r"\bCALL\s+([A-Za-z][A-Za-z0-9_]*)", line, re.IGNORECASE)
            if call:
                target = self._global_name(conn, call.group(1))
                self._edge(conn, current_owner, target, "CALLS", source_path)
