from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import ProjectConfig, init_project
from .security import private_dir, private_file


@dataclass(frozen=True)
class RegisteredProject:
    id: str
    name: str
    path: str


class WorkspaceRegistry:
    """Local registry of project directories.

    The registry stores paths only. Project state remains in each project's
    Markdown/.assistant files, so CLI and web clients share the same source of truth.
    """

    def __init__(self, path: Path | None = None):
        default_home = Path(os.getenv("PROJECT_ASSISTANT_HOME", "~/.project-assistant")).expanduser()
        self.path = (path or default_home / "registry.json").resolve()
        private_dir(self.path.parent)

    @staticmethod
    def project_id(path: Path) -> str:
        canonical = str(path.expanduser().resolve())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def list(self) -> list[RegisteredProject]:
        raw = self._load()
        projects: list[RegisteredProject] = []
        dirty = False
        for item in raw.get("projects", []):
            path = Path(item["path"]).expanduser().resolve()
            config_path = path / ".assistant/project.json"
            if not config_path.exists():
                dirty = True
                continue
            try:
                config = ProjectConfig.load(path)
            except Exception:
                dirty = True
                continue
            projects.append(RegisteredProject(self.project_id(path), config.name, str(path)))
        projects.sort(key=lambda p: p.name.lower())
        if dirty:
            self._save({"projects": [{"path": p.path} for p in projects]})
        return projects

    def register(self, path: Path) -> RegisteredProject:
        path = path.expanduser().resolve()
        config = ProjectConfig.load(path)
        raw = self._load()
        items = raw.setdefault("projects", [])
        canonical = str(path)
        if not any(str(Path(item["path"]).expanduser().resolve()) == canonical for item in items):
            items.append({"path": canonical})
            self._save(raw)
        return RegisteredProject(self.project_id(path), config.name, canonical)

    def create(self, path: Path, name: str) -> RegisteredProject:
        path = path.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        init_project(path, name)
        return self.register(path)

    def get(self, project_id: str) -> RegisteredProject:
        for project in self.list():
            if project.id == project_id:
                return project
        raise KeyError(f"Unknown project: {project_id}")

    def remove(self, project_id: str) -> None:
        project = self.get(project_id)
        raw = self._load()
        raw["projects"] = [
            item
            for item in raw.get("projects", [])
            if str(Path(item["path"]).expanduser().resolve()) != project.path
        ]
        self._save(raw)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"projects": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            value = {"projects": []}
        if not isinstance(value, dict):
            return {"projects": []}
        value.setdefault("projects", [])
        return value

    def _save(self, value: dict) -> None:
        self.path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        private_file(self.path)
