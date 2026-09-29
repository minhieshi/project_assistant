from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .security import private_dir, private_file


HEADER_RE = re.compile(r"^## (?P<title>.+?) · (?P<timestamp>[^\n]+)$", re.MULTILINE)
FILENAME_ID_RE = re.compile(r"-(?P<conversation_id>[0-9a-f]{10})\.md$", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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

    @staticmethod
    def _filename_id(path: Path) -> str | None:
        match = FILENAME_ID_RE.search(path.name)
        return match.group("conversation_id") if match else None

    @staticmethod
    def _fallback_title(path: Path, conversation_id: str) -> str:
        stem = path.stem
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
        suffix = f"-{conversation_id}"
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
        return (stem.replace("-", " ").strip() or "Conversation").title()

    def _read_conversation(self, path: Path) -> Conversation | None:
        """Parse a conversation while tolerating damaged front matter.

        Conversation IDs are deliberately also encoded in generated filenames. If
        a failed/partial write damages the YAML front matter, the file therefore
        remains discoverable instead of becoming an orphan that makes the UI 404.
        """
        text = path.read_text(encoding="utf-8", errors="replace")
        cid_match = re.search(r"^conversation_id:\s*(\S+)", text[:1200], re.MULTILINE)
        conversation_id = cid_match.group(1) if cid_match else self._filename_id(path)
        if not conversation_id:
            return None
        title_match = re.search(r"^# (.+)$", text[:2400], re.MULTILINE)
        title = title_match.group(1).strip() if title_match else self._fallback_title(path, conversation_id)
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        return Conversation(conversation_id, path, title, updated_at)

    def create(self, title: str) -> Conversation:
        cid = uuid.uuid4().hex[:10]
        name = f"{datetime.now().strftime('%Y-%m-%d')}-{_slug(title)}-{cid}.md"
        path = self.directory / name
        temp = self.directory / f".{name}.{uuid.uuid4().hex}.tmp"
        content = (
            "---\n"
            f"conversation_id: {cid}\n"
            f"created_at: {utc_now()}\n"
            "---\n\n"
            f"# {title}\n\n"
        )
        try:
            temp.write_text(content, encoding="utf-8")
            private_file(temp)
            # The conversation header is committed atomically. A crash cannot leave
            # a half-written Markdown file that later disappears from discovery.
            os.replace(temp, path)
            private_file(path)
        finally:
            temp.unlink(missing_ok=True)
        self._updated(path)
        return Conversation(cid, path, title, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"))

    def list(self) -> list[Conversation]:
        conversations: list[Conversation] = []
        for path in self.directory.glob("*.md"):
            try:
                conversation = self._read_conversation(path)
                if conversation is not None:
                    conversations.append(conversation)
            except OSError:
                continue
        # updated_at is captured while the file is known to exist. Avoid a second
        # stat() during sorting, which otherwise creates a race with external file
        # moves/deletes.
        conversations.sort(key=lambda item: item.updated_at or "", reverse=True)
        return conversations

    def find(self, conversation_id: str) -> Conversation:
        for conversation in self.list():
            if conversation.id == conversation_id:
                return conversation
        raise FileNotFoundError(f"Conversation not found: {conversation_id}")

    @staticmethod
    def _entry_text(text: object) -> str:
        if text is None:
            raise ValueError("Conversation entry content was None; refusing to write an invalid entry")
        return str(text)

    def append(self, conversation_id: str, role: str, text: str) -> Path:
        conv = self.find(conversation_id)
        rendered = self._entry_text(text)
        role_title = {"user": "User", "assistant": "Assistant", "system": "System"}.get(role, role.title())
        with conv.path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {role_title} · {utc_now()}\n\n{rendered.rstrip()}\n")
            fh.flush()
            os.fsync(fh.fileno())
        private_file(conv.path)
        self._updated(conv.path)
        return conv.path

    def append_event(self, conversation_id: str, title: str, text: str) -> Path:
        conv = self.find(conversation_id)
        rendered = self._entry_text(text)
        with conv.path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {title} · {utc_now()}\n\n{rendered.rstrip()}\n")
            fh.flush()
            os.fsync(fh.fileno())
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
