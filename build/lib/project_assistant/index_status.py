from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .security import private_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RepoIndexStats:
    scanned: int = 0
    eligible: int = 0
    indexed: int = 0
    unchanged: int = 0
    skipped: int = 0
    local_only: int = 0
    failed: int = 0
    chunks: int = 0


@dataclass
class IndexStatus:
    state: str = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    current_repo: str | None = None
    current_file: str | None = None
    scanned: int = 0
    eligible: int = 0
    indexed: int = 0
    unchanged: int = 0
    skipped: int = 0
    local_only: int = 0
    failed: int = 0
    added: int = 0
    changed: int = 0
    deleted: int = 0
    chunks: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    local_only_reasons: dict[str, int] = field(default_factory=dict)
    repos: dict[str, RepoIndexStats] = field(default_factory=dict)
    recent_skips: list[dict[str, str]] = field(default_factory=list)
    recent_local_only: list[dict[str, str]] = field(default_factory=list)
    last_error: str | None = None

    def to_dict(self) -> dict:
        raw = asdict(self)
        return raw


class IndexStatusStore:
    def __init__(self, path: Path):
        self.path = path
        self._last_write = 0.0
        self.status = IndexStatus()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.status = IndexStatus(
                    **{k: v for k, v in raw.items() if k != "repos"},
                    repos={name: RepoIndexStats(**stats) for name, stats in raw.get("repos", {}).items()},
                )
            except Exception:
                self.status = IndexStatus()

    def start(self) -> None:
        self.status = IndexStatus(state="running", started_at=utc_now())
        self.save(force=True)

    def repo(self, name: str) -> RepoIndexStats:
        return self.status.repos.setdefault(name, RepoIndexStats())

    def current(self, repo: str | None, relative_path: str | None) -> None:
        self.status.current_repo = repo
        self.status.current_file = relative_path
        self.save()

    def skipped(self, repo: str, relative_path: str, reason: str) -> None:
        self.status.skipped += 1
        self.repo(repo).skipped += 1
        self.status.skip_reasons[reason] = self.status.skip_reasons.get(reason, 0) + 1
        self.status.recent_skips.append({"repo": repo, "path": relative_path, "reason": reason})
        self.status.recent_skips = self.status.recent_skips[-50:]
        self.save()


    def local_only(self, repo: str, relative_path: str, reason: str) -> None:
        self.status.local_only += 1
        self.repo(repo).local_only += 1
        self.status.local_only_reasons[reason] = self.status.local_only_reasons.get(reason, 0) + 1
        self.status.recent_local_only.append({"repo": repo, "path": relative_path, "reason": reason})
        self.status.recent_local_only = self.status.recent_local_only[-50:]
        self.save()

    def finish(self) -> None:
        self.status.state = "completed"
        self.status.finished_at = utc_now()
        self.status.current_repo = None
        self.status.current_file = None
        self.save(force=True)

    def fail(self, message: str) -> None:
        self.status.state = "failed"
        self.status.finished_at = utc_now()
        self.status.last_error = message
        self.status.current_repo = None
        self.status.current_file = None
        self.save(force=True)

    def save(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.path.exists() and now - self._last_write < 0.15:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.status.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        private_file(self.path)
        self._last_write = now
