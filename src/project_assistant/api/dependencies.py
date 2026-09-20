from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from ..workspace import WorkspaceRegistry

if TYPE_CHECKING:
    from ..assistant import ProjectAssistant


registry = WorkspaceRegistry()
_assistants: dict[str, "ProjectAssistant"] = {}
_lock = Lock()


def project_path(project_id: str) -> Path:
    return Path(registry.get(project_id).path)


def assistant_for(project_id: str, *, refresh: bool = False) -> "ProjectAssistant":
    # Delay the Chroma/LangChain import until an endpoint actually needs RAG or
    # inference. Project registration/conversation browsing can start without it.
    from ..assistant import ProjectAssistant

    path = project_path(project_id)
    key = str(path)
    with _lock:
        if refresh or key not in _assistants:
            _assistants[key] = ProjectAssistant.build(path)
        return _assistants[key]


def invalidate(project_id: str) -> None:
    path = project_path(project_id)
    with _lock:
        _assistants.pop(str(path), None)
