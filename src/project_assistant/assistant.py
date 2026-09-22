from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import PortkeySettings, ProjectConfig
from .context_compiler import ContextCompiler
from .conversations import ConversationStore
from .indexing import IncrementalIndexer
from .knowledge_graph import KnowledgeGraph
from .portkey import ChatModel, PortkeyChatModel, get_embedding_function
from .retrieval_agent import RetrievalAgent, RetrievalToolkit
from .security import private_file
from .source_access import RegisteredSourceAccess


READ_ONLY_PROJECT_INTELLIGENCE = """
PROJECT ASSISTANT ROLE — READ-ONLY PROJECT INTELLIGENCE
- You may inspect indexed and live content under the Project Assistant workspace and registered source roots, plus explicitly configured read-only Zowe systems.
- Never claim to have modified source code, configuration, Git state, or the working tree.
- Never claim to have submitted/cancelled jobs, changed data sets, issued console commands, changed USS files, or otherwise mutated z/OS state.
- Project Assistant is responsible for understanding the project: retrieval, architecture, integration analysis, debugging context, design reasoning, and implementation planning.
- A separate coding agent (OpenCode) is responsible for edits, shell commands, builds, tests, commits, and other source mutations.
- When recommending implementation work, identify repositories, relative paths, symbols/components, constraints, validation steps, and uncertainties so the work can be handed off cleanly.
""".strip()

# Retained only for backwards-compatible internal proposal objects from older
# releases. The v0.8.0 API/UI no longer exposes source mutation.
CHANGE_CONTROL = READ_ONLY_PROJECT_INTELLIGENCE


@dataclass
class ProjectAssistant:
    project_dir: Path
    config: ProjectConfig
    conversations: ConversationStore
    indexer: IncrementalIndexer
    compiler: ContextCompiler
    model: ChatModel
    retrieval_agent: RetrievalAgent

    @classmethod
    def build(cls, project_dir: Path, model: ChatModel | None = None, embedding_function=None) -> "ProjectAssistant":
        project_dir = project_dir.resolve()
        config = ProjectConfig.load(project_dir)
        settings = PortkeySettings.from_env()
        embeddings = embedding_function or get_embedding_function(settings)
        graph = KnowledgeGraph(project_dir / ".assistant/knowledge_graph.sqlite3")
        indexer = IncrementalIndexer(project_dir, config, embeddings)
        # Conversation Markdown is always written first. We reindex once per
        # completed turn/proposal rather than making two embedding calls for the
        # user and assistant halves of the same turn.
        conversations = ConversationStore(config.project_path(project_dir, config.conversation_dir))
        compiler = ContextCompiler(
            project_dir,
            config,
            indexer,
            conversations,
            graph,
            max_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "48000")),
        )
        chat_model = model or PortkeyChatModel(settings)
        toolkit = RetrievalToolkit(project_dir, config, indexer, graph)
        retrieval_agent = RetrievalAgent(chat_model, toolkit)
        return cls(project_dir, config, conversations, indexer, compiler, chat_model, retrieval_agent)

    def compile_context(self, query: str, conversation_id: str | None = None, *, agentic: bool = True, purpose: str = "answer"):
        seed = self.compiler.initial_retrieval(query, conversation_id)
        extra_hits = []
        actions: tuple[str, ...] = ()
        agent_warnings: tuple[str, ...] = ()
        enabled = os.getenv("RETRIEVAL_AGENT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
        if agentic and enabled:
            recent = self.conversations.recent_text(conversation_id, max_chars=18000) if conversation_id else ""
            try:
                result = self.retrieval_agent.plan_and_retrieve(
                    query,
                    recent,
                    list(seed.initial_hits),
                    routed_repos=[route.name for route in seed.routes],
                    max_actions=int(os.getenv("RETRIEVAL_AGENT_MAX_ACTIONS", "6")),
                    max_rounds=int(os.getenv("RETRIEVAL_AGENT_MAX_ROUNDS", "3")),
                    purpose=purpose,
                )
                extra_hits = list(result.hits)
                actions = tuple(action.label() for action in result.actions)
                agent_warnings = result.warnings
            except Exception:
                # The planner is an optional retrieval enhancement. If the remote
                # chat route is temporarily unavailable, answer from deterministic
                # local retrieval rather than failing before the final answer path.
                extra_hits = []
                actions = ()
                agent_warnings = ()
        return self.compiler.compile(
            query,
            conversation_id,
            seed=seed,
            supplemental_hits=extra_hits,
            retrieval_actions=actions,
            retrieval_warnings=agent_warnings,
        )

    def _prepare_answer(self, conversation_id: str, user_text: str):
        self.conversations.append(conversation_id, "user", user_text)
        context = self.compile_context(user_text, conversation_id, agentic=True)
        self.compiler.write_debug_snapshot(context)
        retrieval_rules = (
            "PROJECT RETRIEVAL — IMPORTANT\n"
            "- Project Assistant has already performed conversation-aware RAG plus bounded multi-round read-only exploration across the project repo and all registered source roots.\n"
            "- Retrieval may include live filesystem reads, grep/file discovery, symbol/reference lookup, read-only Git inspection, and explicitly configured live Zowe reads for data sets, jobs/spool and z/OS operation logs.\n"
            "- Treat retrieved source as the primary project evidence.\n"
            "- LIVE MAINFRAME CONTEXT is timestamped operational evidence. When it conflicts with older indexed material about current z/OS state, prefer the live Zowe evidence for current state and respect its retrieved_at timestamp.\n"
            "- Do not treat live runtime state as proof of design intent; use project source/documentation for intent and architecture.\n"
            "- Do not ask the user to paste a file/playbook that is in a registered source root merely because it was not in the initial RAG snippets.\n"
            "- If a required artefact still was not surfaced after the bounded retrieval rounds, identify the exact missing artefact/search rather than pretending the project has no access to it.\n"
            "- Never claim you read a file unless it appears in retrieved context."
        )
        system = self._system_prompt() + "\n\n" + READ_ONLY_PROJECT_INTELLIGENCE + "\n\n" + retrieval_rules
        user = f"{context.text}\n\n## CURRENT USER REQUEST\n\n{user_text}"
        return context, system, user

    def answer(self, conversation_id: str, user_text: str) -> str:
        context, system, user = self._prepare_answer(conversation_id, user_text)
        response = self.model.complete(system, user)
        path = self.conversations.append(conversation_id, "assistant", response)
        self._safe_index(path)
        return response

    def answer_stream(self, conversation_id: str, user_text: str):
        """Yield (event, payload) tuples while preserving Markdown-first history.

        The user message is written before inference. The assistant message is only
        appended after a complete response has been received.
        """
        context, system, user = self._prepare_answer(conversation_id, user_text)
        yield ("context", context)
        chunks: list[str] = []
        streamer = getattr(self.model, "stream", None)
        if callable(streamer):
            for delta in streamer(system, user):
                chunks.append(delta)
                yield ("delta", delta)
            response = "".join(chunks)
        else:
            response = self.model.complete(system, user)
            chunks.append(response)
            yield ("delta", response)
        path = self.conversations.append(conversation_id, "assistant", response)
        self._safe_index(path)
        yield ("done", response)


    def prepare_implementation_brief(self, conversation_id: str, focus: str = ""):
        """Create a source-grounded handoff for a separate coding agent.

        This is deliberately read-only. It may retrieve indexed content and live
        files/Git metadata, but it never stages, hashes, applies, or writes source
        changes. The resulting brief is persisted in the conversation so project
        design decisions remain part of the knowledge base.
        """
        recent = self.conversations.recent_text(conversation_id, max_chars=18000)
        entries = self.conversations.entries(conversation_id)
        recent_user = [entry.body for entry in entries if entry.role == "user"][-5:]
        retrieval_focus = focus.strip() or "\n\n".join(recent_user).strip()
        if not retrieval_focus:
            retrieval_focus = "Prepare an implementation handoff for the issue discussed in this conversation."
        query = (
            "Prepare a coding-agent implementation handoff for the project issue below. "
            "Find the concrete repositories, files, symbols, integration boundaries, configuration and tests needed to implement it safely.\n\n"
            + retrieval_focus[-12000:]
        )
        context = self.compile_context(query, conversation_id, agentic=True, purpose="handoff")
        debug_path = self.compiler.write_debug_snapshot(context)
        live_state = self._live_git_state_all_sources()
        system = (
            self._system_prompt()
            + "\n\n"
            + READ_ONLY_PROJECT_INTELLIGENCE
            + "\n\n"
            + "You are preparing a concise, source-grounded implementation brief for OpenCode, a separate coding agent. "
              "Do not generate a patch and do not claim any file was changed. Prefer repository + relative path + symbol references over copying large source blocks. "
              "Separate verified project evidence from inference and explicitly list unresolved questions when the retrieved material is insufficient."
        )
        user = f"""{context.text}

## RECENT PROJECT CONVERSATION

{recent}

## OPTIONAL HANDOFF FOCUS

{focus.strip() or '(use the current conversation as the focus)'}

## LIVE REGISTERED SOURCE STATE

{live_state}

## OUTPUT FORMAT

Return Markdown with these sections, omitting only sections that truly do not apply:

# OpenCode implementation brief
## Problem / desired outcome
## Current understanding / root cause
## Integration path
## Relevant repositories and files
For each relevant file: repository, relative path, important symbol/section, and why it matters.
## Recommended implementation direction
Give ordered implementation steps, but no patch.
## Constraints and behaviours to preserve
## Validation / tests
## Uncertainties or checks for OpenCode
## Retrieval evidence
List the most useful source paths/symbols that grounded the brief.

End with: "OpenCode: re-open the referenced live files and verify the working tree before editing."
"""
        brief = self.model.complete(system, user)
        if focus.strip():
            self.conversations.append_event(conversation_id, "Handoff Focus", focus.strip())
        path = self.conversations.append_event(conversation_id, "OpenCode Implementation Brief", brief)
        self._safe_index(path)
        return brief, context, debug_path

    def _live_git_state_all_sources(self) -> str:
        """Return authoritative live state for every registered source root.

        This deliberately does not depend on RAG/retrieval hits. A change proposal
        must always know current branch/HEAD/working-tree state for all Git-backed
        registered roots, even when retrieval initially matched the wrong repo.
        """
        access = RegisteredSourceAccess(self.project_dir, self.config)
        states = access.repository_states()
        if not states:
            return "No registered source roots."

        lines: list[str] = []
        for state in states:
            repo = str(state["repo"])
            if not state["is_git"]:
                lines.append(f"- {repo}: Git not applicable (registered source is not an independent Git repository).")
                continue
            branch = state.get("branch") or "(detached/no branch)"
            head = state.get("head") or "unavailable"
            working_tree = state.get("working_tree") or "unknown"
            lines.append(f"- {repo}: branch={branch}; HEAD={head}; working_tree={working_tree}")
        return "\n".join(lines)

    # Backwards-compatible alias for tests/callers from v0.7.7. Context is ignored
    # intentionally because live Git verification must not depend on retrieval.
    def _live_git_state_for_context(self, context=None) -> str:
        return self._live_git_state_all_sources()

    def _safe_index(self, path: Path) -> None:
        # Conversation durability must not depend on the embedding endpoint being
        # available. If reindexing fails, preserve the Markdown and record a local
        # retryable error instead of losing the turn.
        try:
            self.indexer.index_specific_file(path)
        except Exception as exc:
            error_path = self.project_dir / ".assistant/index_errors.log"
            error_path.parent.mkdir(parents=True, exist_ok=True)
            with error_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{path.name}: {type(exc).__name__}: {exc}\n")
            private_file(error_path)

    def _system_prompt(self) -> str:
        path = self.config.project_path(self.project_dir, self.config.system_prompt_path)
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else "You are a senior software engineer."
