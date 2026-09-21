from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig
from .index_policy import ARCHIVE_EXTENSIONS, COMPILED_EXTENSIONS, should_skip_dir
from .retrieval_types import SearchHit
from .security import EgressPolicy, SecurityError, resolve_within


MAX_FULL_READ_BYTES = 384 * 1024
MAX_RANGE_CHARS = 120_000
MAX_GREP_FILE_BYTES = 4 * 1024 * 1024
MAX_TOOL_OUTPUT_CHARS = 120_000
MAX_DISCOVERY_FILES = 20_000
SAFE_GIT_REF = re.compile(r"^[A-Za-z0-9._/@{}~^:+-]{1,200}$")


@dataclass(frozen=True)
class SourceLocation:
    name: str
    root: Path


class RegisteredSourceAccess:
    """Read-only filesystem/Git access scoped to the project and registered sources.

    This is intentionally *not* shell access. Every operation is implemented as a
    fixed Python/Git operation, and every requested path must resolve inside the
    named source root. Symlink escapes and high-confidence secret material are
    blocked before content can enter outbound model context.
    """

    def __init__(self, project_dir: Path, config: ProjectConfig, policy: EgressPolicy | None = None):
        self.project_dir = project_dir.expanduser().resolve()
        self.config = config
        self.policy = policy or EgressPolicy()

    def roots(self) -> dict[str, Path]:
        roots: dict[str, Path] = {"project": self.project_dir}
        for source in self.config.resolved_sources(self.project_dir):
            if source.name == "project":
                continue
            roots[source.name] = Path(source.path).expanduser().resolve()
        return roots

    def source_names(self) -> tuple[str, ...]:
        return tuple(self.roots().keys())

    def describe_roots(self) -> str:
        return "\n".join(f"- {name}" for name in self.source_names())

    def read_file(self, repo: str, relative_path: str) -> list[SearchHit]:
        root, path = self._resolve_file(repo, relative_path)
        size = path.stat().st_size
        if size > MAX_FULL_READ_BYTES:
            lines = self._line_count(path)
            return [self._info_hit(
                repo,
                path,
                root,
                f"File is {size:,} bytes / {lines:,} lines. Use read_file_range for targeted sections rather than reading the whole file.",
                "file-metadata",
            )]
        text = self._safe_text(path)
        return [self._file_hit(repo, root, path, text, 1, max(1, text.count("\n") + 1), "live-read")]

    def read_file_range(self, repo: str, relative_path: str, start_line: int, end_line: int | None) -> list[SearchHit]:
        root, path = self._resolve_file(repo, relative_path)
        start = max(1, int(start_line or 1))
        end = max(start, int(end_line or min(start + 249, start + 399)))
        end = min(end, start + 399)
        selected: list[str] = []
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for number, line in enumerate(fh, start=1):
                if number < start:
                    continue
                if number > end:
                    break
                selected.append(line)
                if sum(len(item) for item in selected) >= MAX_RANGE_CHARS:
                    break
        text = "".join(selected)
        if not text:
            return [self._info_hit(repo, path, root, f"No content found in requested line range {start}-{end}.", "live-read-range")]
        self._assert_content_safe(text, path)
        actual_end = start + max(0, len(selected) - 1)
        return [self._file_hit(repo, root, path, text, start, actual_end, "live-read-range")]

    def file_metadata(self, repo: str, relative_path: str) -> list[SearchHit]:
        root, path = self._resolve_file(repo, relative_path)
        stat = path.stat()
        text = (
            f"repo={repo}\npath={path.relative_to(root).as_posix()}\n"
            f"size_bytes={stat.st_size}\nlines={self._line_count(path)}\n"
            f"modified_ns={stat.st_mtime_ns}\n"
        )
        return [self._info_hit(repo, path, root, text, "file-metadata")]

    def list_files(self, repo: str | None, relative_dir: str = "", limit: int = 200) -> list[SearchHit]:
        if not repo:
            text = "Registered read-only source roots:\n" + "\n".join(f"- {name}" for name in self.source_names())
            return [SearchHit(text, {"id": "live:source-roots", "repo": "project", "relative_path": ".", "egress_allowed": True}, 1.0, ("list-files",))]
        root = self._root(repo)
        directory = self._resolve_dir(root, relative_dir)
        entries: list[str] = []
        for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in {".git", ".assistant"} or should_skip_dir(child.name):
                continue
            try:
                resolved = child.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if not self.policy.path_allowed(resolved):
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(f"- {resolved.relative_to(root).as_posix()}{suffix}")
            if len(entries) >= limit:
                entries.append(f"- ... truncated after {limit} entries")
                break
        rel = directory.relative_to(root).as_posix() if directory != root else "."
        text = f"Directory listing for {repo}:{rel}\n" + ("\n".join(entries) if entries else "(empty)")
        return [SearchHit(text, {"id": f"live:list:{repo}:{rel}", "repo": repo, "relative_path": rel, "source": str(directory), "egress_allowed": True}, 1.0, ("list-files",))]

    def find_files(self, repo: str | None, pattern: str, relative_dir: str = "", limit: int = 100) -> list[SearchHit]:
        names = [repo] if repo else list(self.source_names())
        matches: list[str] = []
        needle = pattern.strip()
        if not needle:
            return []
        wildcard = any(ch in needle for ch in "*?[]")
        for name in names:
            root = self._root(name)
            base = self._resolve_dir(root, relative_dir)
            for path in self._iter_files(name, root, base):
                rel = path.relative_to(root).as_posix()
                if wildcard:
                    matched = fnmatch.fnmatch(rel, needle) or fnmatch.fnmatch(path.name, needle)
                else:
                    matched = needle.lower() in rel.lower()
                if matched:
                    matches.append(f"- {name}:{rel}")
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        text = f"Files matching {pattern!r}:\n" + ("\n".join(matches) if matches else "(none)")
        return [SearchHit(text, {"id": f"live:find:{repo or 'all'}:{pattern}", "repo": repo or "project", "relative_path": relative_dir or ".", "egress_allowed": True}, 1.0, ("find-files",))]

    def grep_project(self, repo: str | None, pattern: str, relative_dir: str = "", limit: int = 100) -> list[SearchHit]:
        names = [repo] if repo else list(self.source_names())
        query = pattern.strip()
        if not query:
            return []
        if len(query) > 240:
            query = query[:240]
        needle = query.casefold()
        results: list[str] = []
        for name in names:
            root = self._root(name)
            base = self._resolve_dir(root, relative_dir)
            for path in self._iter_files(name, root, base):
                if path.suffix.lower() in ARCHIVE_EXTENSIONS | COMPILED_EXTENSIONS:
                    continue
                try:
                    if path.stat().st_size > MAX_GREP_FILE_BYTES or not self.policy.path_allowed(path):
                        continue
                    with path.open("rb") as fh:
                        sample = fh.read(8192)
                    if b"\x00" in sample:
                        continue
                    with path.open("r", encoding="utf-8", errors="replace") as fh:
                        for line_no, line in enumerate(fh, start=1):
                            if needle not in line.casefold():
                                continue
                            if self.policy.hard_findings(line):
                                continue
                            clean = line.rstrip("\n")
                            if len(clean) > 800:
                                clean = clean[:797] + "..."
                            results.append(f"- {name}:{path.relative_to(root).as_posix()}:{line_no}: {clean}")
                            if len(results) >= limit:
                                break
                except OSError:
                    continue
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        text = f"Grep results for {pattern!r}:\n" + ("\n".join(results) if results else "(none)")
        return [SearchHit(text, {"id": f"live:grep:{repo or 'all'}:{pattern}", "repo": repo or "project", "relative_path": relative_dir or ".", "egress_allowed": True}, 1.0, ("grep-project",))]


    def repository_state(self, repo: str) -> dict[str, object]:
        """Return live Git state for a registered source root.

        Non-Git source folders are valid Project Assistant sources and are reported as
        not applicable rather than as an unresolved verification failure.
        """
        root = self._root(repo)
        try:
            git_root = self._git_root(repo)
        except SecurityError:
            return {
                "repo": repo,
                "is_git": False,
                "branch": None,
                "head": None,
                "working_tree": "not-applicable",
            }
        branch = self._run_git(git_root, ["branch", "--show-current"]).strip() or None
        head = self._run_git(git_root, ["rev-parse", "HEAD"]).strip() or None
        porcelain = self._run_git(git_root, ["status", "--porcelain"]).strip()
        return {
            "repo": repo,
            "is_git": True,
            "branch": branch,
            "head": head,
            "working_tree": "dirty" if porcelain else "clean",
        }

    def repository_states(self, repos: Iterable[str] | None = None) -> list[dict[str, object]]:
        names = list(dict.fromkeys(repos or self.source_names()))
        states: list[dict[str, object]] = []
        for repo in names:
            if repo not in self.roots():
                continue
            states.append(self.repository_state(repo))
        return states

    def git_status(self, repo: str) -> list[SearchHit]:
        root = self._git_root(repo)
        text = self._run_git(root, ["status", "--short", "--branch"])
        return [self._git_hit(repo, root, "status", text or "Working tree clean.")]

    def git_diff(self, repo: str) -> list[SearchHit]:
        root = self._git_root(repo)
        unstaged = self._run_git(root, ["diff", "--no-ext-diff", "--"])
        staged = self._run_git(root, ["diff", "--cached", "--no-ext-diff", "--"])
        text = "## Unstaged\n" + (unstaged or "(none)") + "\n\n## Staged\n" + (staged or "(none)")
        return [self._git_hit(repo, root, "diff", text)]

    def git_log(self, repo: str, relative_path: str = "", limit: int = 20) -> list[SearchHit]:
        root = self._git_root(repo)
        args = ["log", f"-n{max(1, min(limit, 50))}", "--date=short", "--pretty=format:%h %ad %s"]
        if relative_path:
            path = self._resolve_path(root, relative_path)
            args += ["--", path.relative_to(root).as_posix()]
        text = self._run_git(root, args)
        return [self._git_hit(repo, root, "log", text or "No commits found.")]

    def git_show(self, repo: str, ref: str, relative_path: str) -> list[SearchHit]:
        root = self._git_root(repo)
        ref = (ref or "HEAD").strip()
        if not SAFE_GIT_REF.fullmatch(ref) or ref.startswith("-"):
            raise SecurityError("Unsafe Git ref")
        path = self._resolve_path(root, relative_path)
        rel = path.relative_to(root).as_posix()
        text = self._run_git(root, ["show", f"{ref}:{rel}"])
        self._assert_content_safe(text, path)
        return [SearchHit(
            self._truncate_tool_output(text),
            {"id": f"live:git-show:{repo}:{ref}:{rel}", "repo": repo, "relative_path": rel, "source": str(path), "git_ref": ref, "egress_allowed": True},
            1.0,
            ("git-show",),
        )]

    def _root(self, repo: str) -> Path:
        roots = self.roots()
        if repo not in roots:
            raise SecurityError(f"Unknown registered source root: {repo}")
        return roots[repo]

    def _resolve_file(self, repo: str, relative_path: str) -> tuple[Path, Path]:
        root = self._root(repo)
        path = self._resolve_path(root, relative_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"File not found in registered source {repo}: {relative_path}")
        self.policy.assert_path_safe(path)
        if path.suffix.lower() in ARCHIVE_EXTENSIONS | COMPILED_EXTENSIONS:
            raise SecurityError(f"Refusing to read compiled/archive file: {relative_path}")
        return root, path

    def _resolve_dir(self, root: Path, relative_dir: str) -> Path:
        directory = self._resolve_path(root, relative_dir or ".")
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError(f"Directory not found: {relative_dir}")
        return directory

    @staticmethod
    def _resolve_path(root: Path, relative_path: str) -> Path:
        value = (relative_path or ".").strip()
        candidate = Path(value)
        if candidate.is_absolute():
            raise SecurityError("Absolute paths are not accepted by retrieval tools; use source name + relative path")
        return resolve_within(root, candidate)

    def _safe_text(self, path: Path) -> str:
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            raise SecurityError(f"Refusing to read binary file: {path.name}")
        text = data.decode("utf-8", errors="replace")
        self._assert_content_safe(text, path)
        return text

    def _assert_content_safe(self, text: str, path: Path) -> None:
        findings = self.policy.hard_findings(text)
        if findings:
            raise SecurityError(
                f"Read blocked from outbound context because {path.name} appears to contain high-confidence secret material: {', '.join(findings)}"
            )

    def _file_hit(self, repo: str, root: Path, path: Path, text: str, start: int, end: int, channel: str) -> SearchHit:
        rel = path.relative_to(root).as_posix()
        return SearchHit(
            self._truncate_tool_output(text),
            {
                "id": f"live:{repo}:{rel}:{start}:{end}",
                "repo": repo,
                "relative_path": rel,
                "source": str(path),
                "start_line": start,
                "end_line": end,
                "live_read": True,
                "egress_allowed": True,
            },
            1.0,
            (channel,),
        )

    def _info_hit(self, repo: str, path: Path, root: Path, text: str, channel: str) -> SearchHit:
        rel = path.relative_to(root).as_posix()
        return SearchHit(text, {"id": f"live:info:{repo}:{rel}:{channel}", "repo": repo, "relative_path": rel, "source": str(path), "egress_allowed": True}, 1.0, (channel,))

    def _git_hit(self, repo: str, root: Path, kind: str, text: str) -> SearchHit:
        self._assert_content_safe(text, root / ".git")
        return SearchHit(self._truncate_tool_output(text), {"id": f"live:git:{repo}:{kind}", "repo": repo, "relative_path": ".", "source": str(root), "egress_allowed": True}, 1.0, (f"git-{kind}",))

    def _git_root(self, repo: str) -> Path:
        root = self._root(repo)
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SecurityError(f"Registered source is not an independent Git repository: {repo}") from exc
        git_root = Path(result.stdout.strip()).resolve()
        if git_root != root:
            raise SecurityError(
                f"Git inspection is restricted to repos registered at their Git root; {repo} resolves to parent repo {git_root}"
            )
        return root

    @staticmethod
    def _run_git(root: Path, args: list[str]) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr.strip() or "Git read operation failed") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git read operation timed out") from exc
        return result.stdout

    def _iter_files(self, repo: str, root: Path, base: Path) -> Iterable[Path]:
        git_files = self._git_files(root)
        count = 0
        if git_files is not None:
            for path in git_files:
                try:
                    path.relative_to(base)
                except ValueError:
                    continue
                if not self.policy.path_allowed(path):
                    continue
                yield path
                count += 1
                if count >= MAX_DISCOVERY_FILES:
                    return
            return

        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != ".assistant" and not should_skip_dir(d)]
            for filename in files:
                path = Path(current) / filename
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root)
                except (OSError, ValueError):
                    continue
                if not resolved.is_file() or not self.policy.path_allowed(resolved):
                    continue
                yield resolved
                count += 1
                if count >= MAX_DISCOVERY_FILES:
                    return

    @staticmethod
    def _git_files(root: Path) -> list[Path] | None:
        try:
            top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True, timeout=5)
        except Exception:
            return None
        if Path(top.stdout.strip()).resolve() != root:
            return None
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                check=True, capture_output=True, timeout=10
            )
        except Exception:
            return None
        files: list[Path] = []
        for raw in result.stdout.split(b"\x00"):
            if not raw:
                continue
            rel = raw.decode("utf-8", errors="surrogateescape")
            path = (root / rel).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                files.append(path)
        return files

    @staticmethod
    def _line_count(path: Path) -> int:
        count = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for count, _ in enumerate(fh, start=1):
                pass
        return count

    @staticmethod
    def _truncate_tool_output(text: str) -> str:
        if len(text) <= MAX_TOOL_OUTPUT_CHARS:
            return text
        head = MAX_TOOL_OUTPUT_CHARS * 2 // 3
        tail = MAX_TOOL_OUTPUT_CHARS - head
        return text[:head] + "\n... [tool output truncated] ...\n" + text[-tail:]
