from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .security import private_dir, private_file


HEADER_RE = re.compile(r"^## (?P<title>.+?) · (?P<timestamp>[^\n]+)$", re.MULTILINE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return (value[:48] or "conversation")


@dataclass
class Conversation:
    id: str
    path: Path
    title: str
    updated_at: str | None = None


@dataclass(frozen=True)
class ConversationEntry:
    title: str
    timestamp: str
    body: str
    role: str

    def to_dict(self) -> dict:
        return {"title": self.title, "timestamp": self.timestamp, "body": self.body, "role": self.role}


class ConversationStore:
    def __init__(self, directory: Path, on_update: Callable[[Path], None] | None = None):
        self.directory = directory.resolve()
        private_dir(self.directory)
        self.on_update = on_update

    def create(self, title: str) -> Conversation:
        cid = uuid.uuid4().hex[:10]
        name = f"{datetime.now().strftime('%Y-%m-%d')}-{_slug(title)}-{cid}.md"
        path = self.directory / name
        path.write_text(
            "---\n"
            f"conversation_id: {cid}\n"
            f"created_at: {utc_now()}\n"
            "---\n\n"
            f"# {title}\n\n",
            encoding="utf-8",
        )
        private_file(path)
        self._updated(path)
        return Conversation(cid, path, title, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"))

    def list(self) -> list[Conversation]:
        conversations: list[Conversation] = []
        for path in self.directory.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                cid_match = re.search(r"^conversation_id:\s*(\S+)", text[:1200], re.MULTILINE)
                title_match = re.search(r"^# (.+)$", text[:2400], re.MULTILINE)
                if not cid_match:
                    continue
                conversations.append(
                    Conversation(
                        id=cid_match.group(1),
                        path=path,
                        title=title_match.group(1).strip() if title_match else path.stem,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    )
                )
            except OSError:
                continue
        conversations.sort(key=lambda item: item.path.stat().st_mtime, reverse=True)
        return conversations

    def find(self, conversation_id: str) -> Conversation:
        for conversation in self.list():
            if conversation.id == conversation_id:
                return conversation
        raise FileNotFoundError(f"Conversation not found: {conversation_id}")

    def append(self, conversation_id: str, role: str, text: str) -> Path:
        conv = self.find(conversation_id)
        role_title = {"user": "User", "assistant": "Assistant", "system": "System"}.get(role, role.title())
        with conv.path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {role_title} · {utc_now()}\n\n{text.rstrip()}\n")
        private_file(conv.path)
        self._updated(conv.path)
        return conv.path

    def append_event(self, conversation_id: str, title: str, text: str) -> Path:
        conv = self.find(conversation_id)
        with conv.path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {title} · {utc_now()}\n\n{text.rstrip()}\n")
        private_file(conv.path)
        self._updated(conv.path)
        return conv.path

    def entries(self, conversation_id: str) -> list[ConversationEntry]:
        text = self.find(conversation_id).path.read_text(encoding="utf-8", errors="replace")
        matches = list(HEADER_RE.finditer(text))
        entries: list[ConversationEntry] = []
        for index, match in enumerate(matches):
            body_start = match.end()
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            title = match.group("title").strip()
            role = title.lower() if title in {"User", "Assistant", "System"} else "event"
            entries.append(
                ConversationEntry(
                    title=title,
                    timestamp=match.group("timestamp").strip(),
                    body=text[body_start:body_end].strip(),
                    role=role,
                )
            )
        return entries

    def recent_text(self, conversation_id: str, max_chars: int = 14000) -> str:
        text = self.find(conversation_id).path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= max_chars:
            return text
        return "[earlier conversation omitted]\n" + text[-max_chars:]

    def _updated(self, path: Path) -> None:
        if self.on_update:
            self.on_update(path)
