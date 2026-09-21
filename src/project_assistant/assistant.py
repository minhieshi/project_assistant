from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .change_gate import ChangeGate, ChangeProposal
from .config import PortkeySettings, ProjectConfig
from .context_compiler import ContextCompiler
from .conversations import ConversationStore
from .indexing import IncrementalIndexer
from .knowledge_graph import KnowledgeGraph
from .portkey import ChatModel, PortkeyChatModel, get_embedding_function
from .retrieval_agent import RetrievalAgent, RetrievalToolkit
from .security import private_file


CHANGE_CONTROL = """
CHANGE CONTROL — NON-NEGOTIABLE
- During normal chat and proposal stages, you have no permission to modify source files.
- If the user's request would change code/configuration, explain the intended change first: goal, files likely affected, implementation steps, validation, and material risks/trade-offs.
- Do not claim a code change has been made when it has not.
- Do not output a patch or full replacement source file during the proposal stage unless the user explicitly asks to inspect a draft; even then it remains unapplied.
- Plan approval permits preparation of a candidate diff only.
- Source mutation may happen only after the exact staged diff has its own explicit DIFF APPROVED state.
- The applied diff must match the stored SHA-256, registered repo and Git HEAD recorded when it was staged.
""".strip()


@dataclass
class ProjectAssistant:
    project_dir: Path
    config: ProjectConfig
    conversations: ConversationStore
    indexer: IncrementalIndexer
    compiler: ContextCompiler
    gate: ChangeGate
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
        allowed_roots = [Path(s.path) for s in config.resolved_sources(project_dir)] or [project_dir]
        gate = ChangeGate(project_dir, conversations, allowed_repo_roots=allowed_roots)
        chat_model = model or PortkeyChatModel(settings)
        toolkit = RetrievalToolkit(project_dir, config, indexer, graph)
        retrieval_agent = RetrievalAgent(chat_model, toolkit)
        return cls(project_dir, config, conversations, indexer, compiler, gate, chat_model, retrieval_agent)

    def compile_context(self, query: str, conversation_id: str | None = None, *, agentic: bool = True):
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
            "- Retrieval may include live filesystem reads, grep/file discovery, symbol/reference lookup and read-only Git inspection.\n"
            "- Treat retrieved source as the primary project evidence.\n"
            "- Do not ask the user to paste a file/playbook that is in a registered source root merely because it was not in the initial RAG snippets.\n"
            "- If a required artefact still was not surfaced after the bounded retrieval rounds, identify the exact missing artefact/search rather than pretending the project has no access to it.\n"
            "- Never claim you read a file unless it appears in retrieved context."
        )
        system = self._system_prompt() + "\n\n" + CHANGE_CONTROL + "\n\n" + retrieval_rules
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

    def propose_change(self, conversation_id: str, request: str) -> ChangeProposal:
        self.conversations.append(conversation_id, "user", request)
        context = self.compile_context(request, conversation_id, agentic=True)
        self.compiler.write_debug_snapshot(context)
        system = self._system_prompt() + "\n\n" + CHANGE_CONTROL + "\n\nReturn a concise implementation proposal only."
        user = (
            f"{context.text}\n\n## CHANGE REQUEST\n\n{request}\n\n"
            "Produce: goal; files/components likely affected; implementation steps; validation; risks/trade-offs. "
            "Do not generate or apply a patch."
        )
        plan = self.model.complete(system, user)
        proposal = self.gate.create(conversation_id, request, plan)
        self._safe_index(self.conversations.find(conversation_id).path)
        return proposal

    def approve(self, proposal_id: str):
        proposal = self.gate.approve(proposal_id)
        self._safe_index(self.conversations.find(proposal.conversation_id).path)
        return proposal

    def reject(self, proposal_id: str):
        proposal = self.gate.reject(proposal_id)
        self._safe_index(self.conversations.find(proposal.conversation_id).path)
        return proposal

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
