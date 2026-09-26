from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .conversations import ConversationStore
from .portkey import ChatModel
from .security import private_dir, private_file


@dataclass(frozen=True)
class ConsolidationResult:
    ran: bool
    entries: int
    consolidation_path: Path | None = None
    user_memory_path: Path | None = None
    reason: str = ""


class MemoryConsolidator:
    """Turn raw conversation history into curated long-term memory.

    Raw conversations remain the audit trail. Daily consolidations are the normal
    historical retrieval source, and user memory is derived only from those
    consolidations rather than from every individual utterance.
    """

    def __init__(
        self,
        project_dir: Path,
        conversations: ConversationStore,
        model: ChatModel,
        index_file,
        *,
        consolidation_dir: Path,
        user_memory_path: Path,
        state_path: Path,
        interval_hours: int = 24,
    ) -> None:
        self.project_dir = project_dir.resolve()
        self.conversations = conversations
        self.model = model
        self.index_file = index_file
        self.consolidation_dir = consolidation_dir.resolve()
        self.user_memory_path = user_memory_path.resolve()
        self.state_path = state_path.resolve()
        self.interval = timedelta(hours=max(1, interval_hours))
        private_dir(self.consolidation_dir)
        private_dir(self.state_path.parent)
        private_dir(self.user_memory_path.parent)

    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        state = self._load_state()
        last_attempt = self._parse_time(state.get("last_attempt_at") or state.get("last_run_at"))
        return last_attempt is None or now - last_attempt >= self.interval

    def consolidate(self, *, force: bool = False, now: datetime | None = None) -> ConsolidationResult:
        now = now or datetime.now(timezone.utc)
        state = self._load_state()
        if not force and not self.due(now):
            return ConsolidationResult(False, 0, reason="not-due")

        state["last_attempt_at"] = now.isoformat(timespec="seconds")
        self._save_state(state)

        processed_through = self._parse_time(state.get("processed_through"))
        items = self._conversation_entries_since(processed_through)
        if not items:
            state["last_run_at"] = now.isoformat(timespec="seconds")
            self._save_state(state)
            return ConsolidationResult(False, 0, reason="no-new-conversation-entries")

        transcript = self._render_entries(items)
        consolidation = self.model.complete(
            self._consolidation_system_prompt(),
            "## CONVERSATION HISTORY SINCE THE PREVIOUS CONSOLIDATION\n\n" + transcript,
        ).strip()
        if not consolidation:
            raise RuntimeError("Nightly consolidation returned no content")

        local_day = now.astimezone().date().isoformat()
        consolidation_path = self._unique_daily_path(local_day)
        first_timestamp = items[0][2]
        last_timestamp = items[-1][2]
        current_memory = self._read_user_memory()
        updated_memory = self.model.complete(
            self._user_memory_system_prompt(),
            "## CURRENT USER MEMORY\n\n"
            + (current_memory or "(none yet)")
            + "\n\n## NEW DAILY CONSOLIDATION\n\n"
            + consolidation,
        ).strip()
        if not updated_memory:
            raise RuntimeError("User-memory consolidation returned no content")

        consolidation_path.write_text(
            "---\n"
            "memory_kind: daily_consolidation\n"
            f"created_at: {now.isoformat(timespec='seconds')}\n"
            f"processed_from: {first_timestamp}\n"
            f"processed_through: {last_timestamp}\n"
            "---\n\n"
            f"# Daily Consolidation — {local_day}\n\n"
            + consolidation.rstrip()
            + "\n",
            encoding="utf-8",
        )
        private_file(consolidation_path)

        self.user_memory_path.write_text(
            "---\n"
            "memory_kind: user_memory\n"
            f"updated_at: {now.isoformat(timespec='seconds')}\n"
            "---\n\n# User Memory\n\n"
            + updated_memory.rstrip()
            + "\n",
            encoding="utf-8",
        )
        private_file(self.user_memory_path)

        state.update(
            {
                "last_run_at": now.isoformat(timespec="seconds"),
                "processed_through": last_timestamp,
                "last_consolidation": str(consolidation_path),
                "entries_processed": int(state.get("entries_processed", 0)) + len(items),
            }
        )
        self._save_state(state)
        self._safe_index(consolidation_path)
        self._safe_index(self.user_memory_path)
        return ConsolidationResult(True, len(items), consolidation_path, self.user_memory_path)

    def _safe_index(self, path: Path) -> None:
        try:
            self.index_file(path)
        except Exception as exc:
            error_path = self.project_dir / ".assistant/index_errors.log"
            error_path.parent.mkdir(parents=True, exist_ok=True)
            with error_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{path.name}: {type(exc).__name__}: {exc}\n")
            private_file(error_path)

    def read_user_memory(self) -> str:
        return self._read_user_memory()

    def _conversation_entries_since(self, cutoff: datetime | None) -> list[tuple[str, str, str, str, str]]:
        items: list[tuple[str, str, str, str, str]] = []
        for conversation in self.conversations.list():
            for entry in self.conversations.entries(conversation.id):
                timestamp = self._parse_time(entry.timestamp)
                if timestamp is None:
                    continue
                if cutoff is not None and timestamp <= cutoff:
                    continue
                items.append((conversation.id, conversation.title, entry.timestamp, entry.title, entry.body))
        items.sort(key=lambda item: item[2])
        return items

    @staticmethod
    def _render_entries(items: list[tuple[str, str, str, str, str]]) -> str:
        rendered: list[str] = []
        current: tuple[str, str] | None = None
        for conversation_id, title, timestamp, entry_title, body in items:
            key = (conversation_id, title)
            if key != current:
                rendered.append(f"\n### Conversation: {title} ({conversation_id})")
                current = key
            rendered.append(f"\n#### {entry_title} · {timestamp}\n\n{body.strip()}")
        return "\n".join(rendered).strip()

    def _unique_daily_path(self, day: str) -> Path:
        candidate = self.consolidation_dir / f"{day}.md"
        if not candidate.exists():
            return candidate
        counter = 2
        while True:
            candidate = self.consolidation_dir / f"{day}-{counter}.md"
            if not candidate.exists():
                return candidate
            counter += 1

    def _read_user_memory(self) -> str:
        if not self.user_memory_path.exists():
            return ""
        text = self.user_memory_path.read_text(encoding="utf-8", errors="replace")
        if "# User Memory" in text:
            return text.split("# User Memory", 1)[1].strip()
        return text.strip()

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        private_file(self.state_path)

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _consolidation_system_prompt() -> str:
        return """You consolidate one Project Assistant user's conversations into trusted long-term project memory.

Create concise Markdown containing only information worth carrying forward. Prefer current truth over chronology.

Required sections when relevant:
- What we accomplished
- Current decisions / accepted approach
- Debugging and lessons learned
- Superseded or rejected approaches
- Open work / unresolved questions
- User working preferences observed

Rules:
- Distinguish implemented/accepted outcomes from ideas that were merely discussed.
- Put failed, rejected, or abandoned approaches only under Superseded or rejected approaches and state why they were rejected when known.
- Do not promote speculation into fact.
- Preserve concrete technical details that would help future debugging or design work.
- Repeated user corrections are valuable evidence of working preferences; describe the underlying preference rather than quoting repetitive instructions.
- Prefer the simplest accurate statement. Do not add generic advice, filler, or recommendations.
- Do not include a heading for an empty section.
"""

    @staticmethod
    def _user_memory_system_prompt() -> str:
        return """Maintain a compact Markdown user-memory profile using ONLY the supplied current user memory and the new daily consolidation.

This profile is for adapting how a software/project assistant works with this user over time.

Keep only durable or repeatedly supported information such as:
- engineering and problem-solving preferences;
- communication preferences;
- recurring constraints;
- stable goals or workflow choices.

Rules:
- Learn from repeated corrections. For example, repeated requests to simplify can support a durable preference for the smallest viable implementation and incremental complexity.
- Do not store temporary task details that belong in project memory.
- Do not turn a single weak signal into a strong preference.
- If new evidence conflicts with an older preference, update or qualify it instead of keeping contradictory statements.
- Keep the profile short and actionable.
- Return the complete replacement Markdown body for User Memory, without a '# User Memory' heading or YAML frontmatter.
"""
