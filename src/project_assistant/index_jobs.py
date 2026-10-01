from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable


class IndexJobCoordinator:
    """Own long-running full-index jobs independently of HTTP request lifetimes.

    Only one full index is admitted per project. Different projects may index in
    parallel. The indexer's own project-wide lock remains the authority for shared
    manifest/Chroma writes, including smaller background conversation re-index jobs.
    """

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="project-assistant-full-index")
        self._lock = Lock()
        self._jobs: dict[str, Future] = {}

    def active(self, project_id: str) -> bool:
        with self._lock:
            future = self._jobs.get(project_id)
            return bool(future is not None and not future.done())

    def submit(self, project_id: str, work: Callable[[], object], *, on_queued: Callable[[], object] | None = None) -> bool:
        with self._lock:
            current = self._jobs.get(project_id)
            if current is not None and not current.done():
                return False
            if on_queued is not None:
                on_queued()
            future = self._executor.submit(work)
            self._jobs[project_id] = future
            future.add_done_callback(lambda completed, pid=project_id: self._clear(pid, completed))
            return True

    def _clear(self, project_id: str, completed: Future) -> None:
        with self._lock:
            if self._jobs.get(project_id) is completed:
                self._jobs.pop(project_id, None)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
