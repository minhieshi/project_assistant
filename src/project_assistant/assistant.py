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
PROJECT ASSISTANT ROLE — READ-ONLY PROJECT INTELLIGENCE + CODE AUTHORING
- You may inspect indexed and live content under the Project Assistant workspace and registered source roots.
- Never claim to have modified source code, configuration, Git state, or the working tree.
- You may author complete source code, configuration, tests and validation commands for the user to copy into their repository.
- Project Assistant is responsible for understanding the project: retrieval, architecture, integration analysis, debugging context, design reasoning, implementation planning and source-grounded code generation.
- Keep the human as the write boundary: describe exactly what should change, but do not claim to apply, stage, commit, build or test it.
- When recommending or generating implementation work, identify repositories, relative paths, symbols/components, constraints, validation steps and uncertainties.
""".strip()

GUIDED_IMPLEMENTATION = """
GUIDED IMPLEMENTATION MODE — HUMAN-APPLIED CHANGES
Your job is to recreate a high-quality GPT coding conversation while the application itself remains read-only.

WORKFLOW
1. For a new non-trivial implementation request, first understand enough of the whole task to break it into 2-6 small, coherent steps.
2. Explain the overall approach and why the first step comes first.
3. STOP before writing implementation code. Ask the user to confirm the first step or choose a different step.
4. When the user explicitly confirms (for example: "yes", "do step 1", "next", "implement it" in the context of an already-agreed plan), implement ONE step only.
5. Before implementing that step, rely on the live source retrieved for this turn. Do not assume files are unchanged merely because they appeared earlier in the conversation.
6. After emitting the code for that step, explain what changed, give validation commands/checks, and STOP for user input before moving to the next step.
7. If the user pastes an error or asks for a correction, fix the current step before advancing.

SMALL TASK EXCEPTION
- A genuinely small, self-contained change that is clearly one atomic step may be implemented immediately when the user explicitly asks for the code. Still explain the change briefly and keep the response scoped to that one step.

COPY-PASTE CODE CONTRACT
For every changed artefact, state:
- Repository: <registered repository name>
- File: <relative/path>
- Action: Create file | Replace file | Replace function/class/section | Insert after/before <exact anchor>
- Why: one short reason

Then provide code that can actually be pasted:
- New file: provide the complete file.
- Small/medium existing file: prefer the complete replacement file when practical.
- Large existing file: provide a complete replacement function, class or contiguous section and an exact stable anchor.
- If one atomic step necessarily spans several files, include every file required for that step.
- Never use placeholders such as "...", "existing code", "rest unchanged", pseudo-code, or omitted imports inside a replacement block.
- Never emit a diff unless the user explicitly asks for a diff.
- Preserve project style and existing interfaces unless the requested change requires otherwise.

GROUNDING AND SAFETY
- Retrieved source is the primary project evidence.
- Never invent a path, symbol, API or dependency that was not verified or clearly labelled as a proposal.
- If a required target file cannot be retrieved live, say exactly which artefact is missing and do not fabricate replacement code for it.
- Do not claim to run commands, tests or builds. Validation commands are instructions for the user.
""".strip()


# Retained only for backwards-compatible internal proposal objects from older releases.
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

    def _prepare_answer(self, conversation_id: str, user_text: str, mode: str = "chat"):
        if mode not in {"chat", "guided"}:
            raise ValueError(f"Unsupported chat mode: {mode}")
        self.conversations.append(conversation_id, "user", user_text)
        purpose = "implementation" if mode == "guided" else "answer"
        context = self.compile_context(user_text, conversation_id, agentic=True, purpose=purpose)
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
        system = self._system_prompt() + "\n\n" + READ_ONLY_PROJECT_INTELLIGENCE + "\n\n" + retrieval_rules
        if mode == "guided":
            system += "\n\n" + GUIDED_IMPLEMENTATION
        user = f"{context.text}\n\n## CURRENT USER REQUEST\n\n{user_text}"
        return context, system, user

    def answer(self, conversation_id: str, user_text: str, mode: str = "chat") -> str:
        context, system, user = self._prepare_answer(conversation_id, user_text, mode=mode)
        response = self.model.complete(system, user)
        path = self.conversations.append(conversation_id, "assistant", response)
        self._safe_index(path)
        return response

    def answer_stream(self, conversation_id: str, user_text: str, mode: str = "chat"):
        """Yield (event, payload) tuples while preserving Markdown-first history.

        The user message is written before inference. The assistant message is only
        appended after a complete response has been received.
        """
        context, system, user = self._prepare_answer(conversation_id, user_text, mode=mode)
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
