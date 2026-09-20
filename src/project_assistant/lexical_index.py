from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


STOPWORDS = {"the", "and", "for", "with", "from", "this", "that", "where", "what", "which", "does", "into", "about", "when", "why", "how"}


@dataclass(frozen=True)
class LexicalHit:
    id: str
    text: str
    metadata: dict
    bm25: float


class LexicalIndex:
    """Local SQLite FTS5 index for exact-ish code/path/text retrieval."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
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
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);
                CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo);
                CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol);

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    id UNINDEXED,
                    text,
                    repo,
                    relative_path,
                    symbol,
                    kind,
                    tokenize='unicode61'
                );
                """
            )

    def upsert(self, chunk_id: str, text: str, metadata: dict) -> None:
        repo = str(metadata.get("repo", ""))
        rel = str(metadata.get("relative_path", ""))
        source = str(metadata.get("source", ""))
        symbol = str(metadata.get("symbol") or "")
        kind = str(metadata.get("symbol_kind") or metadata.get("kind") or "")
        payload = json.dumps(metadata, sort_keys=True)
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks_fts WHERE id = ?", (chunk_id,))
            conn.execute(
                "INSERT OR REPLACE INTO chunks(id,text,repo,relative_path,source_path,symbol,kind,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                (chunk_id, text, repo, rel, source, symbol, kind, payload),
            )
            conn.execute(
                "INSERT INTO chunks_fts(id,text,repo,relative_path,symbol,kind) VALUES(?,?,?,?,?,?)",
                (chunk_id, text, repo, rel, symbol, kind),
            )

    def delete_source(self, source_path: str) -> None:
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE source_path = ?", (source_path,)).fetchall()]
            for chunk_id in ids:
                conn.execute("DELETE FROM chunks_fts WHERE id = ?", (chunk_id,))
            conn.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))

    def search(self, query: str, limit: int = 20, repos: set[str] | None = None) -> list[LexicalHit]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        sql = (
            "SELECT c.id,c.text,c.metadata_json,bm25(chunks_fts, 1.0, 2.0, 2.5, 4.0, 1.5) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.id "
            "WHERE chunks_fts MATCH ?"
        )
        params: list[object] = [fts_query]
        if repos:
            placeholders = ",".join("?" for _ in repos)
            sql += f" AND c.repo IN ({placeholders})"
            params.extend(sorted(repos))
        sql += " ORDER BY rank ASC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [LexicalHit(r["id"], r["text"], json.loads(r["metadata_json"]), float(r["rank"])) for r in rows]

    def exact(self, term: str, limit: int = 20, repos: set[str] | None = None) -> list[LexicalHit]:
        term = term.strip()
        if not term:
            return []
        sql = (
            "SELECT id,text,metadata_json,0.0 AS rank FROM chunks "
            "WHERE (lower(symbol)=lower(?) OR lower(relative_path) LIKE lower(?) OR text LIKE ?)"
        )
        params: list[object] = [term, f"%{term}%", f"%{term}%"]
        if repos:
            placeholders = ",".join("?" for _ in repos)
            sql += f" AND repo IN ({placeholders})"
            params.extend(sorted(repos))
        sql += " LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [LexicalHit(r["id"], r["text"], json.loads(r["metadata_json"]), float(r["rank"])) for r in rows]

    def chunks_for_sources(self, source_paths: list[str], limit: int = 20) -> list[LexicalHit]:
        if not source_paths:
            return []
        placeholders = ",".join("?" for _ in source_paths)
        sql = (
            f"SELECT id,text,metadata_json,0.0 AS rank FROM chunks WHERE source_path IN ({placeholders}) "
            "ORDER BY source_path, CAST(json_extract(metadata_json, '$.start_line') AS INTEGER) LIMIT ?"
        )
        params: list[object] = list(source_paths) + [limit]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [LexicalHit(r["id"], r["text"], json.loads(r["metadata_json"]), float(r["rank"])) for r in rows]


    def chunks_for_locations(self, locations: list[tuple[str, int | None]], limit: int = 20) -> list[LexicalHit]:
        if not locations:
            return []
        candidates: list[tuple[int, LexicalHit]] = []
        with self._connect() as conn:
            for source_path, line in locations:
                rows = conn.execute(
                    "SELECT id,text,metadata_json,0.0 AS rank FROM chunks WHERE source_path = ?",
                    (source_path,),
                ).fetchall()
                for row in rows:
                    metadata = json.loads(row["metadata_json"])
                    start = int(metadata.get("start_line") or 0)
                    end = int(metadata.get("end_line") or start or 0)
                    if line is None:
                        distance = start
                    elif start <= line <= max(end, start):
                        distance = 0
                    else:
                        distance = min(abs(start - line), abs(end - line))
                    candidates.append((distance, LexicalHit(row["id"], row["text"], metadata, float(row["rank"]))))
        candidates.sort(key=lambda item: item[0])
        seen: set[str] = set()
        result: list[LexicalHit] = []
        for _, hit in candidates:
            if hit.id in seen:
                continue
            seen.add(hit.id)
            result.append(hit)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _fts_query(query: str) -> str:
        # FTS syntax is intentionally constrained: token OR token. Exact symbol
        # lookup is handled separately to avoid exposing user text as FTS syntax.
        tokens = [t for t in re.findall(r"[A-Za-z0-9_$#@.-]{2,}", query) if t.lower() not in STOPWORDS]
        deduped = list(dict.fromkeys(tokens))[:20]
        return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in deduped)
