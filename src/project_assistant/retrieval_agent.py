from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .config import ProjectConfig
from .knowledge_graph import KnowledgeGraph
from .portkey import ChatModel
from .retrieval_types import SearchHit
from .security import outbound_metadata_allowed
from .source_access import RegisteredSourceAccess

if TYPE_CHECKING:
    from .indexing import IncrementalIndexer


ALLOWED_TOOLS = {
    "search_project",
    "search_exact",
    "find_symbol",
    "find_references",
    "list_files",
    "find_files",
    "grep_project",
    "file_metadata",
    "read_file",
    "read_file_range",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
}

NO_TARGET_TOOLS = {"list_files", "git_status", "git_diff", "git_log"}


@dataclass(frozen=True)
class RetrievalAction:
    tool: str
    query: str = ""
    target: str = ""
    repo: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    round_no: int = 0

    def label(self) -> str:
        subject = self.query or self.target
        suffix = f" repo={self.repo}" if self.repo else ""
        if self.start_line:
            suffix += f" lines={self.start_line}-{self.end_line or self.start_line}"
        prefix = f"round {self.round_no}: " if self.round_no else ""
        return f"{prefix}{self.tool}({subject!r}{suffix})"

    def key(self) -> tuple:
        return (self.tool, self.query, self.target, self.repo, self.start_line, self.end_line)


@dataclass(frozen=True)
class RetrievalAgentResult:
    hits: tuple[SearchHit, ...]
    actions: tuple[RetrievalAction, ...]
    planner_raw: str = ""
    warnings: tuple[str, ...] = ()
    rounds: int = 0


class RetrievalToolkit:
    """Read-only tools over indexed knowledge and registered source roots.

    Indexed search remains useful for RAG, but file/list/grep/Git tools operate on
    the live filesystem under the Project Assistant repo and every registered
    source root. There is no arbitrary shell command execution.
    """

    def __init__(self, project_dir: Path, config: ProjectConfig, indexer: IncrementalIndexer, graph: KnowledgeGraph):
        self.project_dir = project_dir
        self.config = config
        self.indexer = indexer
        self.graph = graph
        self.sources = RegisteredSourceAccess(project_dir, config)

    def source_names(self) -> tuple[str, ...]:
        return self.sources.source_names()

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

        # Live filesystem tools require a concrete source root for direct reads.
        if action.tool == "list_files":
            return self.sources.list_files(target_repo, target_value, limit=max(40, limit * 8))
        if action.tool == "find_files":
            return self.sources.find_files(target_repo, action.query or target_value, action.target if action.query else "", limit=max(40, limit * 8))
        if action.tool == "grep_project":
            return self.sources.grep_project(target_repo, action.query or target_value, action.target if action.query else "", limit=max(40, limit * 8))

        repo = target_repo
        if action.tool in {"file_metadata", "read_file", "read_file_range", "git_status", "git_diff", "git_log", "git_show"} and not repo:
            # Allow repo:path shorthand; otherwise direct file/Git operations must
            # identify the registered source root explicitly.
            return []
        assert repo is not None

        if action.tool == "file_metadata":
            return self.sources.file_metadata(repo, target_value)
        if action.tool == "read_file":
            return self.sources.read_file(repo, target_value)
        if action.tool == "read_file_range":
            if not action.start_line:
                return []
            return self.sources.read_file_range(repo, target_value, action.start_line, action.end_line)
        if action.tool == "git_status":
            return self.sources.git_status(repo)
        if action.tool == "git_diff":
            return self.sources.git_diff(repo)
        if action.tool == "git_log":
            return self.sources.git_log(repo, action.target or "", limit=max(10, limit))
        if action.tool == "git_show":
            if not action.target:
                return []
            return self.sources.git_show(repo, action.query or "HEAD", action.target)
        return []

    def _split_repo_target(self, value: str, explicit_repo: str | None) -> tuple[str | None, str]:
        value = value.strip()
        if explicit_repo:
            return explicit_repo, value
        if ":" in value:
            prefix, remainder = value.split(":", 1)
            if prefix in set(self.source_names()) and remainder:
                return prefix, remainder
        return None, value

    @staticmethod
    def _retag(hits: Iterable[SearchHit], channel: str) -> list[SearchHit]:
        return [SearchHit(hit.text, hit.metadata, hit.score, tuple(dict.fromkeys((*hit.channels, channel)))) for hit in hits]


class RetrievalAgent:
    """Bounded retrieve -> inspect -> retrieve-again loop over read-only tools.

    The agent can explore the project repo and registered source roots for several
    rounds before the final answer/proposal is generated. It never receives shell
    access or write access, and remote semantic searches are capped across the
    entire loop.
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
        max_rounds: int = 3,
    ) -> RetrievalAgentResult:
        accumulated = self._dedupe([hit for hit in initial_hits if outbound_metadata_allowed(hit.metadata)])
        routed = ", ".join(routed_repos) or "not constrained"
        source_names_fn = getattr(self.toolkit, "source_names", None)
        source_roots = ", ".join(source_names_fn()) if callable(source_names_fn) else "project and registered sources"
        all_hits: list[SearchHit] = []
        executed: list[RetrievalAction] = []
        raw_rounds: list[str] = []
        warnings: list[str] = []
        semantic_searches = 0
        seen_actions: set[tuple] = set()
        rounds_used = 0

        for round_no in range(1, max(1, max_rounds) + 1):
            rounds_used = round_no
            source_map = self._source_map(accumulated[-32:])
            prior_actions = "\n".join(f"- {action.label()}" for action in executed[-12:]) or "- none"
            system = self._planner_system(max_actions)
            user = (
                f"CURRENT REQUEST:\n{query}\n\n"
                f"RECENT CONVERSATION:\n{recent_conversation[-7000:]}\n\n"
                f"REGISTERED READ-ONLY SOURCE ROOTS:\n{source_roots}\n\n"
                f"ROUTED REPOSITORIES (boosts, not hard limits):\n{routed}\n\n"
                f"RETRIEVAL ROUND: {round_no} of {max_rounds}\n\n"
                f"ALREADY EXECUTED ACTIONS:\n{prior_actions}\n\n"
                f"CURRENT SOURCE MAP:\n{source_map}\n\n"
                "Assess whether you have enough implementation/configuration/test context to answer or propose the change accurately. "
                "If not, request the next read-only operations. Follow dependencies across registered source roots. "
                "Do not ask the user to paste a file that can be found/read from a registered source root."
            )
            try:
                raw = self.model.complete(system, user)
            except Exception as exc:
                warnings.append(f"Retrieval planning stopped after round {round_no - 1}: {self._safe_error(exc)}")
                break
            raw_rounds.append(raw)
            sufficient, actions = self._parse_plan(raw, max_actions=max_actions)
            if sufficient or not actions:
                break

            round_executed = 0
            for parsed in actions:
                action = RetrievalAction(
                    parsed.tool,
                    parsed.query,
                    parsed.target,
                    parsed.repo,
                    parsed.start_line,
                    parsed.end_line,
                    round_no,
                )
                if action.key() in seen_actions:
                    continue
                seen_actions.add(action.key())
                if action.tool == "search_project":
                    if semantic_searches >= 2:
                        continue
                    semantic_searches += 1
                try:
                    hits = self.toolkit.execute(action, limit=12)
                except Exception as exc:
                    warnings.append(f"{action.label()} failed: {self._safe_error(exc)}")
                    continue
                executed.append(action)
                round_executed += 1
                if hits:
                    all_hits.extend(hits)
                    accumulated = self._dedupe(accumulated + [hit for hit in hits if outbound_metadata_allowed(hit.metadata)])

            # If the planner produced only duplicate/blocked/invalid actions, do
            # not waste additional reasoning calls in a loop with no new evidence.
            if round_executed == 0:
                break

        return RetrievalAgentResult(
            tuple(self._dedupe(all_hits)),
            tuple(executed),
            "\n\n--- retrieval round ---\n\n".join(raw_rounds),
            tuple(dict.fromkeys(warnings)),
            rounds_used,
        )

    @staticmethod
    def _planner_system(max_actions: int) -> str:
        return (
            "You are a retrieval planner for a local software-engineering project. Do not answer the user's technical question. "
            "You have standing READ-ONLY permission across the Project Assistant repo and all registered source repos/folders. "
            "You may continue retrieving until the available evidence is sufficient, but you must never request writes, arbitrary shell commands, or files outside registered roots. "
            "Return JSON only with shape "
            '{"sufficient":false,"actions":[{"tool":"search_project|search_exact|find_symbol|find_references|list_files|find_files|grep_project|file_metadata|read_file|read_file_range|git_status|git_diff|git_log|git_show",'
            '"query":"...","target":"...","repo":null,"start_line":null,"end_line":null}]}. '
            f"Return at most {max_actions} actions per round. If enough context is already available, return "
            '{"sufficient":true,"actions":[]}. '
            "Tool guidance: repo is the registered source name. read_file/read_file_range/file_metadata require repo + relative target path. "
            "list_files uses target as a relative directory. find_files uses query as filename/glob and optional target directory. "
            "grep_project uses query as a case-insensitive literal and optional target directory. git_status/git_diff require only repo. "
            "git_log uses optional target path. git_show uses query as ref (usually HEAD) and target as relative file path. "
            "Prefer live read_file/read_file_range when an indexed snippet is incomplete or could be stale."
        )

    @staticmethod
    def _source_map(hits: list[SearchHit]) -> str:
        if not hits:
            return "No source has been retrieved yet. Use registered-root tools to locate and read what is needed."
        lines: list[str] = []
        for hit in hits:
            repo = hit.metadata.get("repo", "source")
            rel = hit.metadata.get("relative_path", "unknown")
            start = hit.metadata.get("start_line")
            end = hit.metadata.get("end_line")
            symbol = hit.metadata.get("symbol")
            loc = f":{start}-{end or start}" if start else ""
            sym = f" symbol={symbol}" if symbol else ""
            channels = ",".join(hit.channels) if hit.channels else "retrieval"
            snippet = re.sub(r"\s+", " ", hit.text.strip())[:320]
            lines.append(f"- {repo}:{rel}{loc}{sym} via={channels} :: {snippet}")
        return "\n".join(lines)

    @classmethod
    def _parse_plan(cls, raw: str, max_actions: int = 6) -> tuple[bool, list[RetrievalAction]]:
        payload = cls._json_payload(raw)
        sufficient = bool(payload.get("sufficient", False)) if isinstance(payload, dict) else False
        return sufficient, cls._actions_from_payload(payload, max_actions)

    @classmethod
    def _parse_actions(cls, raw: str, max_actions: int = 6) -> list[RetrievalAction]:
        """Backwards-compatible parser used by tests and older callers."""
        payload = cls._json_payload(raw)
        return cls._actions_from_payload(payload, max_actions)

    @staticmethod
    def _json_payload(raw: str) -> dict:
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
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _actions_from_payload(payload: dict, max_actions: int) -> list[RetrievalAction]:
        raw_actions = payload.get("actions", [])
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
            repo = str(item.get("repo") or "").strip() or None
            if not query and not target and tool not in NO_TARGET_TOOLS:
                continue
            if tool in {"git_status", "git_diff"} and not repo:
                continue
            try:
                start_line = int(item["start_line"]) if item.get("start_line") is not None else None
                end_line = int(item["end_line"]) if item.get("end_line") is not None else None
            except (TypeError, ValueError):
                start_line = end_line = None
            result.append(RetrievalAction(tool, query, target, repo, start_line, end_line))
        return result

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = re.sub(r"<[^>]+>", " ", str(exc))
        text = re.sub(r"\s+", " ", text).strip()
        return text[:300] or type(exc).__name__

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
