from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ZoweReadError(RuntimeError):
    pass


_DATASET_RE = re.compile(r"^[A-Za-z0-9@$#.*%_-]+(?:\([A-Za-z0-9@$#_-]{1,8}\))?$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9@$#_-]{1,16}$")


def _bounded_int(value: int, *, minimum: int, maximum: int, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ZoweReadError(f"{name} must be an integer") from exc
    if number < minimum or number > maximum:
        raise ZoweReadError(f"{name} must be between {minimum} and {maximum}")
    return number


def _dataset(value: str, *, allow_pattern: bool = False) -> str:
    text = str(value).strip()
    if not text or len(text) > 128 or not _DATASET_RE.fullmatch(text):
        raise ZoweReadError("Invalid z/OS data set name or pattern")
    if not allow_pattern and any(ch in text for ch in "*%"):
        raise ZoweReadError("Wildcards are only allowed for data set searches")
    return text


def _job_id(value: str) -> str:
    text = str(value).strip()
    if not _JOB_ID_RE.fullmatch(text):
        raise ZoweReadError("Invalid z/OS job ID")
    return text


def _redact_error(text: str) -> str:
    # Defensive only: fixed commands never place credentials on argv. Still avoid
    # returning obvious credential values if Zowe itself renders them in an error.
    text = re.sub(r"(?i)(--(?:password|pass|pw|token-value|tv)\s+)(\S+)", r"\1<redacted>", text)
    text = re.sub(r"(?i)(password|access[_ -]?token|refresh[_ -]?token|token[-_ ]?value)\s*[:=]\s*\S+", r"\1=<redacted>", text)
    return text


@dataclass(frozen=True)
class ZoweRunner:
    executable: str | None = None
    timeout_seconds: int | None = None
    max_output_chars: int | None = None

    def _exe(self) -> str:
        configured = self.executable or os.getenv("ZOWE_MCP_CLI_PATH")
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return str(path.resolve())
            resolved = shutil.which(configured)
            if resolved:
                return resolved
            raise ZoweReadError(f"Zowe CLI executable not found: {configured}")
        resolved = shutil.which("zowe")
        if not resolved:
            raise ZoweReadError("Zowe CLI was not found on PATH. Set ZOWE_MCP_CLI_PATH if needed.")
        return resolved

    def _timeout(self) -> int:
        value = self.timeout_seconds if self.timeout_seconds is not None else int(os.getenv("ZOWE_MCP_TIMEOUT_SECONDS", "60"))
        return _bounded_int(value, minimum=5, maximum=300, name="ZOWE_MCP_TIMEOUT_SECONDS")

    def _limit(self) -> int:
        value = self.max_output_chars if self.max_output_chars is not None else int(os.getenv("ZOWE_MCP_MAX_OUTPUT_CHARS", "60000"))
        return _bounded_int(value, minimum=1000, maximum=500000, name="ZOWE_MCP_MAX_OUTPUT_CHARS")

    def run(self, *args: str) -> str:
        command = [self._exe(), *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout(),
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ZoweReadError(f"Zowe CLI timed out after {self._timeout()} seconds") from exc
        except OSError as exc:
            raise ZoweReadError(f"Unable to execute Zowe CLI: {exc}") from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            detail = _redact_error((stderr or stdout).strip())
            if len(detail) > 4000:
                detail = detail[:4000] + "\n...[error truncated]"
            raise ZoweReadError(f"Zowe CLI exited with status {completed.returncode}: {detail or 'no error text returned'}")

        output = stdout.strip()
        limit = self._limit()
        if len(output) > limit:
            output = output[:limit] + f"\n...[output truncated at {limit} characters]"
        return output or "(Zowe returned no output)"

    def info(self) -> str:
        return self.run("--version")

    def list_datasets(self, pattern: str, max_results: int = 50, attributes: bool = False) -> str:
        pattern = _dataset(pattern, allow_pattern=True)
        maximum = _bounded_int(max_results, minimum=1, maximum=1000, name="max_results")
        args = ["zos-files", "list", "data-set", pattern, "--max", str(maximum)]
        if attributes:
            args.append("--attributes")
        return self.run(*args)

    def list_dataset_members(self, dataset: str, max_results: int = 100, pattern: str | None = None, attributes: bool = False) -> str:
        dataset = _dataset(dataset)
        maximum = _bounded_int(max_results, minimum=1, maximum=1000, name="max_results")
        args = ["zos-files", "list", "all-members", dataset, "--max", str(maximum)]
        if pattern:
            member_pattern = str(pattern).strip()
            if not re.fullmatch(r"[A-Za-z0-9@$#*%_-]{1,16}", member_pattern):
                raise ZoweReadError("Invalid member pattern")
            args.extend(["--pattern", member_pattern])
        if attributes:
            args.append("--attributes")
        return self.run(*args)

    def read_dataset(self, dataset: str) -> str:
        dataset = _dataset(dataset)
        return self.run("zos-files", "view", "data-set", dataset)

    def get_job_status(self, job_id: str) -> str:
        return self.run("zos-jobs", "view", "job-status-by-jobid", _job_id(job_id))

    def get_job_spool(self, job_id: str) -> str:
        return self.run("zos-jobs", "view", "all-spool-content", _job_id(job_id))
