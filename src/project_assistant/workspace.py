from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG_NAME, ProjectConfig, SourceRoot, init_project
from .security import private_dir, private_file, validate_source_root


@dataclass(frozen=True)
class RegisteredProject:
    id: str
    name: str
    path: str
    kind: str  # managed | imported


class WorkspaceRegistry:
    """Discover and persist local Project Assistant workspaces.

    Managed projects live beneath PROJECT_ASSISTANT_PROJECTS_ROOT and are
    discovered from disk on every startup. Imported projects stay where they
    already live; only their paths are persisted in imports.json.

    v0.4 registry.json entries are migrated automatically.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        projects_root: Path | None = None,
        home: Path | None = None,
    ):
        default_home = Path(os.getenv("PROJECT_ASSISTANT_HOME", "~/.project-assistant")).expanduser()
        self.home = (home or default_home).resolve()
        self.projects_root = (
            projects_root
            or Path(os.getenv("PROJECT_ASSISTANT_PROJECTS_ROOT", str(self.home / "projects"))).expanduser()
        ).resolve()
        # `path` remains injectable for tests/backwards compatibility, but now
        # represents the imported-project catalogue rather than the source of
        # truth for managed projects.
        self.imports_path = (path or self.home / "imports.json").resolve()
        self.legacy_registry_path = (self.home / "registry.json").resolve()
        private_dir(self.home)
        private_dir(self.projects_root)
        self._migrate_legacy_registry()

    @staticmethod
    def project_id(path: Path) -> str:
        canonical = str(path.expanduser().resolve())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def slug(name: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-._").lower()
        return value or "project"

    def list(self) -> list[RegisteredProject]:
        projects: dict[str, RegisteredProject] = {}

        # Managed projects are self-discovering from the projects root.
        if self.projects_root.exists():
            for child in self.projects_root.iterdir():
                if not child.is_dir():
                    continue
                project = self._load_registered(child, "managed")
                if project:
                    projects[project.id] = project

        # Imported repos can live anywhere, so only their path pointers need a
        # tiny persistent catalogue.
        imports = self._load_imports()
        valid_imports: list[dict[str, str]] = []
        for item in imports.get("projects", []):
            raw_path = item.get("path")
            if not raw_path:
                continue
            path = Path(raw_path).expanduser().resolve()
            project = self._load_registered(path, "imported")
            if not project:
                continue
            # A managed project never needs a duplicate import entry.
            try:
                path.relative_to(self.projects_root)
                kind = "managed"
            except ValueError:
                kind = "imported"
            project = RegisteredProject(project.id, project.name, project.path, kind)
            projects[project.id] = project
            if kind == "imported":
                valid_imports.append({"path": project.path})

        if valid_imports != imports.get("projects", []):
            self._save_imports({"projects": valid_imports})

        return sorted(projects.values(), key=lambda p: p.name.lower())

    def create(self, name: str) -> RegisteredProject:
        """Create a new managed local Git repository for a project."""
        base = self.slug(name)
        target = self.projects_root / base
        counter = 2
        while target.exists():
            # Reuse an empty directory, otherwise choose a deterministic suffix.
            if target.is_dir() and not any(target.iterdir()):
                break
            target = self.projects_root / f"{base}-{counter}"
            counter += 1

        private_dir(target)
        self._git_init(target)
        init_project(target, name, internal_metadata=False)
        project = self._load_registered(target, "managed")
        if project is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Failed to initialise project at {target}")
        return project

    def import_repo(self, path: Path, name: str | None = None) -> RegisteredProject:
        """Import an existing Git repository without copying or moving it."""
        path = validate_source_root(path)
        repo_root = self._git_root(path)
        if repo_root != path.resolve():
            path = repo_root
        validate_source_root(path)

        config_path = path / CONFIG_NAME
        if config_path.exists():
            config = ProjectConfig.load(path)
            if name and name.strip() and config.name != name.strip():
                config.name = name.strip()
                config.save(path)
        else:
            # Keep all assistant metadata inside .assistant for imported code
            # repos so importing does not create tracked-looking root files.
            init_project(path, name.strip() if name and name.strip() else path.name, internal_metadata=True)

        project = self._load_registered(path, "imported")
        if project is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Failed to import repository: {path}")
        self._remember_import(path)
        return project

    # Backwards-compatible alias used by older callers.
    def register(self, path: Path) -> RegisteredProject:
        return self.import_repo(path)

    def get(self, project_id: str) -> RegisteredProject:
        for project in self.list():
            if project.id == project_id:
                return project
        raise KeyError(f"Unknown project: {project_id}")


    def rename(self, project_id: str, name: str) -> RegisteredProject:
        """Rename a project display name without moving its repository."""
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty")
        project = self.get(project_id)
        path = Path(project.path)
        config = ProjectConfig.load(path)
        config.name = name
        config.save(path)
        updated = self._load_registered(path, project.kind)
        if updated is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Failed to reload project after rename: {path}")
        return updated

    def convert_imported_to_source(
        self,
        project_id: str,
        target_project_id: str,
        source_name: str | None = None,
    ) -> tuple[RegisteredProject, SourceRoot]:
        """Turn a mistakenly imported project into a source of another project.

        This is metadata-only: the imported repository is not copied, moved or
        deleted. Existing .assistant metadata is deliberately left in place so
        correction never destroys conversation/project history.
        """
        project = self.get(project_id)
        if project.kind != "imported":
            raise ValueError("Only imported projects can be converted to source repositories")
        target = self.get(target_project_id)
        if target.id == project.id:
            raise ValueError("Choose a different target project")

        source_path = validate_source_root(Path(project.path))
        target_path = Path(target.path)
        config = ProjectConfig.load(target_path)
        resolved = config.resolved_sources(target_path)
        if any(Path(item.path).resolve() == source_path.resolve() for item in resolved):
            raise ValueError(f"Repository is already a source of {target.name}")

        name = (source_name or project.name or source_path.name).strip()
        if not name:
            name = source_path.name
        if any(item.name == name for item in config.source_roots):
            raise ValueError(f"Source name already exists in {target.name}: {name}")

        source = SourceRoot(name=name, path=str(source_path))
        config.source_roots.append(source)
        config.save(target_path)
        self.remove(project.id)
        updated_target = self.get(target.id)
        return updated_target, source

    def remove(self, project_id: str) -> None:
        """Forget an imported project. Managed repos are never deleted here."""
        project = self.get(project_id)
        if project.kind == "managed":
            raise ValueError(
                "Managed projects are discovered from disk and cannot be unregistered. "
                "Move/delete the repository explicitly if you want it removed."
            )
        imports = self._load_imports()
        imports["projects"] = [
            item
            for item in imports.get("projects", [])
            if str(Path(item.get("path", "")).expanduser().resolve()) != project.path
        ]
        self._save_imports(imports)

    def _load_registered(self, path: Path, kind: str) -> RegisteredProject | None:
        path = path.expanduser().resolve()
        if not (path / CONFIG_NAME).exists():
            return None
        try:
            config = ProjectConfig.load(path)
        except Exception:
            return None
        return RegisteredProject(self.project_id(path), config.name, str(path), kind)

    def _remember_import(self, path: Path) -> None:
        imports = self._load_imports()
        canonical = str(path.expanduser().resolve())
        if not any(str(Path(item.get("path", "")).expanduser().resolve()) == canonical for item in imports.get("projects", [])):
            imports.setdefault("projects", []).append({"path": canonical})
            self._save_imports(imports)

    def _load_imports(self) -> dict:
        if not self.imports_path.exists():
            return {"projects": []}
        try:
            value = json.loads(self.imports_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"projects": []}
        if not isinstance(value, dict):
            return {"projects": []}
        value.setdefault("projects", [])
        return value

    def _save_imports(self, value: dict) -> None:
        private_dir(self.imports_path.parent)
        self.imports_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        private_file(self.imports_path)

    def _migrate_legacy_registry(self) -> None:
        if not self.legacy_registry_path.exists() or self.legacy_registry_path == self.imports_path:
            return
        try:
            raw = json.loads(self.legacy_registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in raw.get("projects", []) if isinstance(raw, dict) else []:
            raw_path = item.get("path") if isinstance(item, dict) else None
            if not raw_path:
                continue
            path = Path(raw_path).expanduser().resolve()
            if not (path / CONFIG_NAME).exists():
                continue
            try:
                path.relative_to(self.projects_root)
            except ValueError:
                self._remember_import(path)

    @staticmethod
    def _git_init(path: Path) -> None:
        try:
            subprocess.run(["git", "-C", str(path), "init", "-q"], check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("Git is required to create Project Assistant projects") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr.strip() or f"git init failed for {path}") from exc

    @staticmethod
    def _git_root(path: Path) -> Path:
        try:
            raw = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except FileNotFoundError as exc:
            raise RuntimeError("Git is required to import a repository") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Not a Git repository: {path}") from exc
        return Path(raw).expanduser().resolve()
