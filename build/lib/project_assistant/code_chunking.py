from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CodeChunk:
    text: str
    start_line: int
    end_line: int
    symbol: str | None = None
    symbol_kind: str | None = None
    language: str = "text"


LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rexx": "rexx",
    ".rex": "rexx",
    ".jcl": "jcl",
    ".proc": "jcl",
    ".cob": "cobol",
    ".cbl": "cobol",
    ".cobol": "cobol",
    ".pli": "pli",
    ".pl1": "pli",
}


class CodeChunker:
    """Dependency-free structural chunker for code and project text.

    It favours semantic units (functions, tasks, steps and paragraphs) and falls
    back to bounded line windows. It intentionally avoids requiring Tree-sitter
    so the assistant remains easy to install in restricted enterprise environments.
    """

    def __init__(self, max_chars: int = 6000, overlap_lines: int = 8):
        self.max_chars = max(1200, max_chars)
        self.overlap_lines = max(0, overlap_lines)

    def chunk_file(self, path: Path, text: str) -> list[CodeChunk]:
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "text")
        if not text.strip():
            return []
        if language == "python":
            chunks = self._python(text)
        elif language == "markdown":
            chunks = self._sections(text, r"^(#{1,6})\s+(.+?)\s*$", "section", language)
        elif language == "yaml":
            chunks = self._ansible_yaml(text)
        elif language == "jcl":
            chunks = self._jcl(text)
        elif language == "cobol":
            chunks = self._cobol(text)
        elif language == "rexx":
            chunks = self._rexx(text)
        elif language == "pli":
            chunks = self._pli(text)
        elif language in {"javascript", "typescript", "java", "kotlin", "go", "rust", "c", "cpp"}:
            chunks = self._brace_language(text, language)
        else:
            chunks = []
        if not chunks:
            chunks = self._window(text, language=language)
        return self._bound(chunks)

    def _python(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        chunks: list[CodeChunk] = []
        first_definition = min(
            (n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
            default=len(lines) + 1,
        )
        if first_definition > 1:
            preamble = "".join(lines[: first_definition - 1]).strip()
            if preamble:
                chunks.append(CodeChunk(preamble, 1, first_definition - 1, "<module>", "module", "python"))

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(self._ast_chunk(lines, node, node.name, "function"))
            elif isinstance(node, ast.ClassDef):
                class_chunk = self._ast_chunk(lines, node, node.name, "class")
                if len(class_chunk.text) <= self.max_chars:
                    chunks.append(class_chunk)
                    continue
                header = lines[node.lineno - 1].rstrip("\n")
                method_nodes = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if not method_nodes:
                    chunks.append(class_chunk)
                    continue
                # Preserve class identity on each method so retrieval does not lose scope.
                for method in method_nodes:
                    body = "".join(lines[method.lineno - 1 : method.end_lineno]).rstrip()
                    text_with_scope = f"{header}\n    # ...\n{body}"
                    chunks.append(
                        CodeChunk(
                            text_with_scope,
                            method.lineno,
                            method.end_lineno or method.lineno,
                            f"{node.name}.{method.name}",
                            "method",
                            "python",
                        )
                    )
        return chunks

    @staticmethod
    def _ast_chunk(lines: list[str], node, symbol: str, kind: str) -> CodeChunk:
        end = getattr(node, "end_lineno", None) or node.lineno
        return CodeChunk("".join(lines[node.lineno - 1 : end]).rstrip(), node.lineno, end, symbol, kind, "python")

    def _ansible_yaml(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts: list[tuple[int, str]] = []
        for i, line in enumerate(lines, start=1):
            match = re.match(r"^\s*-\s+name:\s*[\"']?(.+?)[\"']?\s*$", line)
            if match:
                starts.append((i, match.group(1)))
        if not starts:
            return []
        return self._chunks_from_starts(lines, [(line, name, "ansible_task") for line, name in starts], "yaml")

    def _jcl(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts = []
        for i, line in enumerate(lines, start=1):
            match = re.match(r"^//([A-Z0-9@$#]+)\s+(JOB|EXEC)\b", line, re.IGNORECASE)
            if match:
                kind = "jcl_job" if match.group(2).upper() == "JOB" else "jcl_step"
                starts.append((i, match.group(1), kind))
        return self._chunks_from_starts(lines, starts, "jcl")

    def _cobol(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts: list[tuple[int, str, str]] = []
        for i, line in enumerate(lines, start=1):
            program = re.search(r"\bPROGRAM-ID\.\s+([A-Z0-9-]+)", line, re.IGNORECASE)
            if program:
                starts.append((i, program.group(1), "cobol_program"))
            paragraph = re.match(r"^\s{0,8}([A-Z0-9-]+)\.\s*$", line, re.IGNORECASE)
            if paragraph:
                starts.append((i, paragraph.group(1), "cobol_paragraph"))
        return self._chunks_from_starts(lines, self._dedupe_starts(starts), "cobol")

    def _rexx(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts = []
        for i, line in enumerate(lines, start=1):
            match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(?:/\*.*\*/)?$", line)
            if match:
                starts.append((i, match.group(1), "rexx_label"))
        return self._chunks_from_starts(lines, starts, "rexx")

    def _pli(self, text: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts = []
        for i, line in enumerate(lines, start=1):
            match = re.match(r"^\s*([A-Za-z_$#@][A-Za-z0-9_$#@]*)\s*:\s*(?:PROC|PROCEDURE)\b", line, re.IGNORECASE)
            if match:
                starts.append((i, match.group(1), "pli_procedure"))
        return self._chunks_from_starts(lines, starts, "pli")

    def _sections(self, text: str, pattern: str, kind: str, language: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts = []
        rx = re.compile(pattern)
        for i, line in enumerate(lines, start=1):
            match = rx.match(line)
            if match:
                starts.append((i, match.group(match.lastindex or 1), kind))
        return self._chunks_from_starts(lines, starts, language)

    def _brace_language(self, text: str, language: str) -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        starts: list[tuple[int, str, str]] = []
        patterns = [
            (r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+|export\s+|async\s+)*class\s+([A-Za-z_$][\w$]*)", "class"),
            (r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", "function"),
            (r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=.*=>\s*\{", "function"),
            (r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(", "function"),
            (r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][\w]*)\s*\(", "function"),
            (r"^\s*(?:[\w<>\[\],.?]+\s+)+([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:throws\s+[\w., ]+)?\s*\{", "function"),
        ]
        for i, line in enumerate(lines, start=1):
            for pattern, kind in patterns:
                match = re.match(pattern, line)
                if match:
                    starts.append((i, match.group(1), kind))
                    break
        if not starts:
            return []
        chunks: list[CodeChunk] = []
        for start, symbol, kind in self._dedupe_starts(starts):
            end = self._brace_block_end(lines, start)
            if end < start:
                continue
            chunks.append(CodeChunk("".join(lines[start - 1 : end]).rstrip(), start, end, symbol, kind, language))
        return chunks

    @staticmethod
    def _brace_block_end(lines: list[str], start_line: int) -> int:
        depth = 0
        seen_open = False
        for idx in range(start_line - 1, len(lines)):
            line = re.sub(r"//.*$", "", lines[idx])
            for ch in line:
                if ch == "{":
                    depth += 1
                    seen_open = True
                elif ch == "}" and seen_open:
                    depth -= 1
                    if depth <= 0:
                        return idx + 1
        return len(lines) if seen_open else start_line

    def _chunks_from_starts(self, lines: list[str], starts: list[tuple[int, str, str]], language: str) -> list[CodeChunk]:
        if not starts:
            return []
        starts = sorted(starts, key=lambda x: x[0])
        chunks: list[CodeChunk] = []
        if starts[0][0] > 1:
            preamble = "".join(lines[: starts[0][0] - 1]).strip()
            if preamble:
                chunks.append(CodeChunk(preamble, 1, starts[0][0] - 1, "<preamble>", "preamble", language))
        for i, (start, symbol, kind) in enumerate(starts):
            end = (starts[i + 1][0] - 1) if i + 1 < len(starts) else len(lines)
            body = "".join(lines[start - 1 : end]).rstrip()
            if body:
                chunks.append(CodeChunk(body, start, end, symbol, kind, language))
        return chunks

    def _window(self, text: str, language: str = "text") -> list[CodeChunk]:
        lines = text.splitlines(keepends=True)
        chunks: list[CodeChunk] = []
        start = 0
        while start < len(lines):
            # A single minified/generated line must never bypass max_chars.
            # Split it directly while preserving the source line number.
            if len(lines[start]) > self.max_chars:
                raw = lines[start].rstrip("\n")
                for offset in range(0, len(raw), self.max_chars):
                    part = raw[offset:offset + self.max_chars]
                    if part:
                        chunks.append(CodeChunk(part, start + 1, start + 1, None, None, language))
                start += 1
                continue

            chars = 0
            end = start
            while end < len(lines) and chars + len(lines[end]) <= self.max_chars:
                chars += len(lines[end])
                end += 1
            if end == start:
                end = start + 1
            chunks.append(CodeChunk("".join(lines[start:end]).rstrip(), start + 1, end, None, None, language))
            if end >= len(lines):
                break
            start = max(start + 1, end - self.overlap_lines)
        return [c for c in chunks if c.text]

    def _bound(self, chunks: Iterable[CodeChunk]) -> list[CodeChunk]:
        bounded: list[CodeChunk] = []
        for chunk in chunks:
            if len(chunk.text) <= self.max_chars:
                bounded.append(chunk)
                continue
            sub = self._window(chunk.text, language=chunk.language)
            for i, part in enumerate(sub, start=1):
                offset = chunk.start_line - 1
                bounded.append(
                    CodeChunk(
                        part.text,
                        part.start_line + offset,
                        part.end_line + offset,
                        f"{chunk.symbol}#{i}" if chunk.symbol else None,
                        chunk.symbol_kind,
                        chunk.language,
                    )
                )
        return bounded

    @staticmethod
    def _dedupe_starts(starts: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
        seen: set[int] = set()
        result = []
        for item in starts:
            if item[0] not in seen:
                seen.add(item[0])
                result.append(item)
        return result
