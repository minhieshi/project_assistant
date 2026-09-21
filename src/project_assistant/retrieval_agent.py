from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .config import ProjectConfig
from .retrieval_types import SearchHit
from .knowledge_graph import KnowledgeGraph
from .portkey import ChatModel
from .security import outbound_metadata_allowed

if TYPE_CHECKING:
    from .indexing import IncrementalIndexer


ALLOWED_TOOLS = {
    "search_project",
    "search_exact",
    "find_symbol",
    "find_references",
    "read_file",
    "read_file_range",
}


@dataclass(frozen=True)
class RetrievalAction:
    tool: str
    query: str = ""
    target: str = ""
    repo: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def label(self) -> str:
        subject = self.query or self.target
        suffix = f" repo={self.repo}" if self.repo else ""
        if self.start_line:
            suffix += f" lines={self.start_line}-{self.end_line or self.start_line}"
        return f"{self.tool}({subject!r}{suffix})"


@dataclass(frozen=True)
class RetrievalAgentResult:
    hits: tuple[SearchHit, ...]
    actions: tuple[RetrievalAction, ...]
    planner_raw: str = ""


class RetrievalToolkit:
    """Read-only access to already indexed project knowledge.

    The toolkit never opens arbitrary filesystem paths supplied by the model. File
    reads resolve through the lexical index, so only registered/indexed project
    content can be returned and existing egress metadata remains authoritative.
    """

    def __init__(self, project_dir: Path, config: ProjectConfig, indexer: IncrementalIndexer, graph: KnowledgeGraph):
        self.project_dir = project_dir
        self.config = config
        self.indexer = indexer
        self.graph = graph

    def execute(self, action: RetrievalAction, limit: int = 12) -> list[SearchHit]:
        target_repo, target_value = self._split_repo_target(action.target or action.query, action.repo)
        repos = {target_repo} if target_repo else None
        if action.tool == "search_project":
            return self.indexer.search(action.query or target_value, k=limit, repos=repos)
        if action.tool == "search_exact":
            return self.indexer.exact_search(action.query or target_value, k=limit, repos=repos)
        if action.tool in {"find_symbol", "find_references"}:
            target = target_value
            graph_hits = self.graph.search(target, limit=10)
            if action.tool == "find_symbol":
                locations = [(hit.source_path, hit.metadata.get("line")) for hit in graph_hits if hit.source_path]
            else:
                locations = self.graph.related_locations(graph_hits, limit=max(limit * 2, 20))
            hits = self.indexer.chunks_for_locations(locations, limit=limit)
            if repos:
                hits = [hit for hit in hits if hit.metadata.get("repo") in repos]
            return self._retag(hits, action.tool)
        if action.tool in {"read_file", "read_file_range"}:
            target = target_value
            candidates = self.indexer.lexical.exact(target, limit=20, repos=repos)
            source_paths: list[str] = []
            for candidate in candidates:
                rel = str(candidate.metadata.get("relative_path", ""))
                symbol = str(candidate.metadata.get("symbol", ""))
                if target.lower() in rel.lower() or target.lower() == symbol.lower():
                    path = str(candidate.metadata.get("source", ""))
                    if path and path not in source_paths:
                        source_paths.append(path)
            if not source_paths:
                return []
            if action.tool == "read_file_range" and action.start_line:
                locations = [(source, action.start_line) for source in source_paths[:3]]
                hits = self.indexer.chunks_for_locations(locations, limit=limit)
            else:
                hits = self.indexer.chunks_for_sources(source_paths[:3], limit=limit)
            return self._retag(hits, action.tool)
        return []

    def _split_repo_target(self, value: str, explicit_repo: str | None) -> tuple[str | None, str]:
        value = value.strip()
        if explicit_repo:
            return explicit_repo, value
        if ":" in value:
            prefix, remainder = value.split(":", 1)
            known = {source.name for source in self.config.resolved_sources(self.project_dir)} | {"project"}
            if prefix in known and remainder:
                return prefix, remainder
        return None, value

    @staticmethod
    def _retag(hits: Iterable[SearchHit], channel: str) -> list[SearchHit]:
        return [SearchHit(hit.text, hit.metadata, hit.score, tuple(dict.fromkeys((*hit.channels, channel)))) for hit in hits]


class RetrievalAgent:
    """One provider-safe retrieval-planning pass around local read-only tools.

    Initial retrieval remains deterministic. GPT then sees a compact source map
    and can request additional local searches before the final answer is produced.
    This gives us retrieve -> reason -> retrieve-more behaviour without relying on
    provider function/tool calling support.
    """

    def __init__(self, model: ChatModel, toolkit: RetrievalToolkit):
        self.model = model
        self.toolkit = toolkit

    def plan_and_retrieve(
        self,
        query: str,
        recent_conversation: str,
        initial_hits: list[SearchHit],
        routed_repos: Iterable[str] = (),
        max_actions: int = 6,
    ) -> RetrievalAgentResult:
        safe_hits = [hit for hit in initial_hits if outbound_metadata_allowed(hit.metadata)]
        source_map = self._source_map(safe_hits[:18])
        routed = ", ".join(routed_repos) or "not constrained"
        system = (
            "You are a retrieval planner for a local software-engineering knowledge base. "
            "Do not answer the user's technical question. Decide which additional READ-ONLY retrieval operations "
            "would materially improve the final answer. Return JSON only with shape "
            '{"actions":[{"tool":"search_project|search_exact|find_symbol|find_references|read_file|read_file_range",'
            '"query":"...","target":"...","repo":null,"start_line":null,"end_line":null}]}. '
            f"Return at most {max_actions} actions. Prefer exact/symbol/file reads when the existing source map points "
            "to a dependency or named artefact. Do not request files from the user. Do not request shell commands or writes."
        )
        user = (
            f"CURRENT REQUEST:\n{query}\n\n"
            f"RECENT CONVERSATION:\n{recent_conversation[-7000:]}\n\n"
            f"ROUTED REPOSITORIES (boosts, not hard limits):\n{routed}\n\n"
            f"INITIAL RETRIEVAL SOURCE MAP:\n{source_map}\n\n"
            "Request only additional retrieval that is likely to add missing implementation/configuration/dependency context. "
            "If the initial retrieval is sufficient, return {\"actions\":[]}."
        )
        raw = self.model.complete(system, user)
        actions = self._parse_actions(raw, max_actions=max_actions)
        hits: list[SearchHit] = []
        semantic_searches = 0
        executed: list[RetrievalAction] = []
        for action in actions:
            # Keep planner-driven remote vector queries bounded; exact/symbol/file
            # operations are local and may use the remaining action budget.
            if action.tool == "search_project":
                if semantic_searches >= 2:
                    continue
                semantic_searches += 1
            try:
                hits.extend(self.toolkit.execute(action, limit=12))
                executed.append(action)
            except Exception:
                # Retrieval assistance should improve answers, never make chat fail.
                continue
        actions = executed
        return RetrievalAgentResult(tuple(self._dedupe(hits)), tuple(actions), raw)

    @staticmethod
    def _source_map(hits: list[SearchHit]) -> str:
        if not hits:
            return "No indexed source was retrieved yet."
        lines: list[str] = []
        for hit in hits:
            repo = hit.metadata.get("repo", "source")
            rel = hit.metadata.get("relative_path", "unknown")
            start = hit.metadata.get("start_line")
            end = hit.metadata.get("end_line")
            symbol = hit.metadata.get("symbol")
            loc = f":{start}-{end or start}" if start else ""
            sym = f" symbol={symbol}" if symbol else ""
            snippet = re.sub(r"\s+", " ", hit.text.strip())[:240]
            lines.append(f"- {repo}:{rel}{loc}{sym} :: {snippet}")
        return "\n".join(lines)

    @staticmethod
    def _parse_actions(raw: str, max_actions: int = 6) -> list[RetrievalAction]:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except Exception:
            return []
        raw_actions = payload.get("actions", []) if isinstance(payload, dict) else []
        if not isinstance(raw_actions, list):
            return []
        result: list[RetrievalAction] = []
        for item in raw_actions[:max_actions]:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if tool not in ALLOWED_TOOLS:
                continue
            query = str(item.get("query") or "").strip()
            target = str(item.get("target") or "").strip()
            if not query and not target:
                continue
            repo = str(item.get("repo") or "").strip() or None
            try:
                start_line = int(item["start_line"]) if item.get("start_line") is not None else None
                end_line = int(item["end_line"]) if item.get("end_line") is not None else None
            except (TypeError, ValueError):
                start_line = end_line = None
            result.append(RetrievalAction(tool, query, target, repo, start_line, end_line))
        return result

    @staticmethod
    def _dedupe(hits: Iterable[SearchHit]) -> list[SearchHit]:
        result: list[SearchHit] = []
        seen: set[str] = set()
        for hit in hits:
            key = str(hit.metadata.get("id") or f"{hit.metadata.get('source')}:{hit.metadata.get('start_line')}:{hit.metadata.get('symbol')}")
            if key in seen:
                continue
            seen.add(key)
            result.append(hit)
        return result
