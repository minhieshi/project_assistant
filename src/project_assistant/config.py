from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .security import private_dir, private_file, resolve_within, validate_source_root


CONFIG_NAME = ".assistant/project.json"

DEFAULT_SYSTEM_PROMPT = """You are a senior mainframe platform engineer specialising in z/os, zCX, USS and ansible automation. Your chatgpt browser in your enterprise has been downgraded to instant only and you need to build a tool you can run locally which has the most important features of brower GPT - conversation history written to project file, context builder, RAG, and knowledge graph so you can continue to produce the same output as you normaly would. GPT 5.6 is only available in other modes via Port key now.

You use australian english, only give 3 recommendations when you're confident about the answer. You refactor as you go and produce very little AI slop.
"""


@dataclass
class SourceRoot:
    name: str
    path: str


@dataclass
class ProjectConfig:
    name: str
    source_roots: list[SourceRoot] = field(default_factory=list)
    system_prompt_path: str = "assistant_system.md"
    project_memory_path: str = "PROJECT.md"
    conversation_dir: str = ".assistant/conversations"
    generated_dir: str = ".assistant/generated"
    chunk_size: int = 1600
    chunk_overlap: int = 240
    vector_top_k: int = 24
    lexical_top_k: int = 36
    rag_top_n: int = 12
    graph_top_n: int = 12
    graph_expansion_top_n: int = 8
    repo_route_top_n: int = 3

    @classmethod
    def load(cls, project_dir: Path) -> "ProjectConfig":
        project_dir = project_dir.expanduser().resolve()
        path = project_dir / CONFIG_NAME
        if not path.exists():
            raise FileNotFoundError(f"Project is not initialised: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["source_roots"] = [SourceRoot(**item) for item in raw.get("source_roots", [])]
        config = cls(**raw)
        # A modified project.json must not redirect internal reads/writes outside
        # the registered project directory.
        for value in (
            config.system_prompt_path,
            config.project_memory_path,
            config.conversation_dir,
            config.generated_dir,
        ):
            config.project_path(project_dir, value)
        return config

    def save(self, project_dir: Path) -> Path:
        project_dir = project_dir.expanduser().resolve()
        path = project_dir / CONFIG_NAME
        private_dir(path.parent)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        private_file(path)
        return path

    def project_path(self, project_dir: Path, configured_path: str) -> Path:
        return resolve_within(project_dir, configured_path)

    def resolved_sources(self, project_dir: Path) -> list[SourceRoot]:
        result: list[SourceRoot] = []
        for source in self.source_roots:
            p = Path(source.path).expanduser()
            if not p.is_absolute():
                p = (project_dir / p).resolve()
            else:
                p = p.resolve()
            if p.exists():
                p = validate_source_root(p)
            result.append(SourceRoot(source.name, str(p)))
        return result


@dataclass(frozen=True)
class PortkeySettings:
    base_url: str
    api_key: str
    chat_model: str
    embedding_model: str
    chat_virtual_key: str | None = None
    embedding_virtual_key: str | None = None
    chat_config_id: str | None = None
    embedding_config_id: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    api_mode: str = "chat_completions"
    reasoning_effort: str = "high"

    @classmethod
    def from_env(cls) -> "PortkeySettings":
        extra_raw = os.getenv("PORTKEY_EXTRA_HEADERS_JSON", "{}")
        try:
            extra = json.loads(extra_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("PORTKEY_EXTRA_HEADERS_JSON must be valid JSON") from exc
        if not isinstance(extra, dict):
            raise ValueError("PORTKEY_EXTRA_HEADERS_JSON must be a JSON object")
        settings = cls(
            base_url=os.getenv("PORTKEY_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("PORTKEY_API_KEY", ""),
            chat_model=os.getenv("PORTKEY_CHAT_MODEL", "gpt-5.6"),
            embedding_model=os.getenv("PORTKEY_EMBEDDING_MODEL", ""),
            chat_virtual_key=os.getenv("PORTKEY_CHAT_VIRTUAL_KEY") or None,
            embedding_virtual_key=os.getenv("PORTKEY_EMBEDDING_VIRTUAL_KEY") or None,
            chat_config_id=os.getenv("PORTKEY_CHAT_CONFIG_ID") or None,
            embedding_config_id=os.getenv("PORTKEY_EMBEDDING_CONFIG_ID") or None,
            extra_headers={str(k): str(v) for k, v in extra.items()},
            api_mode=os.getenv("PORTKEY_API_MODE", "chat_completions").strip().lower(),
            reasoning_effort=(os.getenv("PORTKEY_REASONING_EFFORT") or "high").strip().lower(),
        )
        if settings.api_mode not in {"chat_completions", "responses"}:
            raise ValueError("PORTKEY_API_MODE must be 'chat_completions' or 'responses'")
        if settings.reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("PORTKEY_REASONING_EFFORT must be low, medium, or high")
        return settings

    def _headers(self, virtual_key: str | None, config_id: str | None) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.api_key:
            headers["x-portkey-api-key"] = self.api_key
        if virtual_key:
            headers["x-portkey-virtual-key"] = virtual_key
        if config_id:
            headers["x-portkey-config"] = config_id
        return headers

    def chat_headers(self) -> dict[str, str]:
        return self._headers(self.chat_virtual_key, self.chat_config_id)

    def embedding_headers(self) -> dict[str, str]:
        return self._headers(self.embedding_virtual_key, self.embedding_config_id)


def _exclude_assistant_state_from_git(project_dir: Path) -> None:
    git_dir = project_dir / ".git"
    if not git_dir.is_dir():
        return
    exclude = git_dir / "info/exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8", errors="replace") if exclude.exists() else ""
    if ".assistant/" in {line.strip() for line in existing.splitlines()}:
        return
    with exclude.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(".assistant/\n")


def init_project(project_dir: Path, name: str, *, internal_metadata: bool = False) -> ProjectConfig:
    project_dir = project_dir.expanduser().resolve()
    private_dir(project_dir / ".assistant")
    private_dir(project_dir / ".assistant/conversations")
    private_dir(project_dir / ".assistant/generated")
    private_dir(project_dir / ".assistant/proposals")
    private_dir(project_dir / ".assistant/patches")
    private_dir(project_dir / ".assistant/debug")

    config = ProjectConfig(name=name)
    if internal_metadata:
        config.system_prompt_path = ".assistant/assistant_system.md"
        config.project_memory_path = ".assistant/PROJECT.md"
    config.save(project_dir)

    prompt = config.project_path(project_dir, config.system_prompt_path)
    if not prompt.exists():
        prompt.write_text(DEFAULT_SYSTEM_PROMPT, encoding="utf-8")
    if internal_metadata:
        private_file(prompt)

    memory = config.project_path(project_dir, config.project_memory_path)
    if not memory.exists():
        memory.write_text(
            f"# {name}\n\n"
            "## Purpose\n\n"
            "## Architecture\n\n"
            "## Decisions\n\n"
            "## Current work\n\n"
            "## Known issues\n\n",
            encoding="utf-8",
        )
    if internal_metadata:
        private_file(memory)

    _exclude_assistant_state_from_git(project_dir)
    return config
