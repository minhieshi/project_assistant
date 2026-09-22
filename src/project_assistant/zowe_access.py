from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import ProjectConfig, ZoweSystem
from .retrieval_types import SearchHit
from .security import EgressPolicy, SecurityError


MAX_ZOWE_OUTPUT_CHARS = 120_000
SAFE_PROFILE = re.compile(r"^[A-Za-z0-9._@#$-]{1,128}$")
SAFE_DATASET_PATTERN = re.compile(r"^[A-Za-z0-9@#$.*%_-]{1,160}$")
SAFE_DATASET = re.compile(r"^[A-Za-z0-9@#$._-]{1,160}(?:\([A-Za-z0-9@#$]{1,8}\))?$")
SAFE_JOB_FILTER = re.compile(r"^[A-Za-z0-9@#$*%._-]{0,64}$")
SAFE_JOB_ID = re.compile(r"^[A-Za-z][A-Za-z0-9@#$]{1,31}$")
SAFE_LOG_RANGE = re.compile(r"^[1-9][0-9]{0,2}[smh]$")

CAPABILITIES = {"datasets", "jobs", "logs"}


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    payload: Any
    retrieved_at: str


class ZoweAccess:
    """Read-only live z/OS access through a locally configured Zowe CLI.

    The model never receives arbitrary shell access. Every public method maps to a
    fixed Zowe list/view command, target values are validated, subprocesses are
    executed without a shell, and credentials remain in the user's Zowe profile.
    """

    DEFAULT_TTLS = {
        "dataset-list": 60,
        "member-list": 60,
        "dataset-read": 30,
        "job-search": 10,
        "job-status": 5,
        "job-output": 120,
        "system-logs": 5,
    }

    def __init__(
        self,
        config: ProjectConfig,
        policy: EgressPolicy | None = None,
        *,
        executable: str | None = None,
    ):
        self.config = config
        self.policy = policy or EgressPolicy()
        self.executable = executable or os.getenv("ZOWE_CLI_PATH", "zowe")
        self.timeout = max(5, min(int(os.getenv("ZOWE_CLI_TIMEOUT_SECONDS", "30")), 120))
        self._cache: dict[tuple, _CacheEntry] = {}

    def systems(self) -> tuple[str, ...]:
        return tuple(system.name for system in self.config.zowe_systems if system.enabled)

    def describe_systems(self) -> str:
        systems = [system for system in self.config.zowe_systems if system.enabled]
        if not systems:
            return "No live Zowe systems configured."
        lines = []
        for system in systems:
            allowed = ",".join(self._capabilities(system)) or "none"
            lines.append(f"- {system.name}: read-only capabilities={allowed}")
        return "\n".join(lines)

    def list_datasets(self, system: str, pattern: str, *, limit: int = 100) -> list[SearchHit]:
        self._validate(pattern, SAFE_DATASET_PATTERN, "data set pattern")
        max_items = max(1, min(int(limit), 1000))
        payload, retrieved_at, cached = self._run(
            system,
            "datasets",
            ["zos-files", "list", "data-set", pattern, "--max-length", str(max_items)],
            "dataset-list",
            (system, pattern, max_items),
        )
        return [self._hit(system, "dataset-list", pattern, payload, retrieved_at, cached, self.DEFAULT_TTLS["dataset-list"])]

    def list_members(self, system: str, dataset: str, pattern: str = "*", *, limit: int = 200) -> list[SearchHit]:
        self._validate(dataset, SAFE_DATASET, "data set name")
        if "(" in dataset:
            raise SecurityError("zos_list_members expects a partitioned data set name, not a member")
        self._validate(pattern or "*", SAFE_JOB_FILTER, "member pattern")
        max_items = max(1, min(int(limit), 1000))
        command = ["zos-files", "list", "all-members", dataset, "--max-length", str(max_items)]
        if pattern and pattern != "*":
            command += ["--pattern", pattern]
        payload, retrieved_at, cached = self._run(
            system,
            "datasets",
            command,
            "member-list",
            (system, dataset, pattern, max_items),
        )
        return [self._hit(system, "member-list", f"{dataset}:{pattern}", payload, retrieved_at, cached, self.DEFAULT_TTLS["member-list"])]

    def read_dataset(
        self,
        system: str,
        dataset: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> list[SearchHit]:
        self._validate(dataset, SAFE_DATASET, "data set/member name")
        command = ["zos-files", "view", "data-set", dataset]
        line_label = ""
        if start_line is not None:
            start = max(1, int(start_line))
            end = max(start, int(end_line or min(start + 399, start + 399)))
            end = min(end, start + 399)
            # Zowe data-set --range is zero-based; Project Assistant line ranges are one-based.
            command += ["--range", f"{start - 1}-{end - 1}"]
            line_label = f":{start}-{end}"
        payload, retrieved_at, cached = self._run(
            system,
            "datasets",
            command,
            "dataset-read",
            (system, dataset, start_line, end_line),
            prefer_stdout=True,
        )
        return [self._hit(system, "dataset", f"{dataset}{line_label}", payload, retrieved_at, cached, self.DEFAULT_TTLS["dataset-read"], start_line=start_line, end_line=end_line)]

    def search_jobs(self, system: str, prefix: str = "", owner: str = "") -> list[SearchHit]:
        self._validate(prefix, SAFE_JOB_FILTER, "job prefix", allow_empty=True)
        self._validate(owner, SAFE_JOB_FILTER, "job owner", allow_empty=True)
        command = ["zos-jobs", "list", "jobs"]
        if owner:
            command += ["--owner", owner]
        if prefix:
            command += ["--prefix", prefix]
        payload, retrieved_at, cached = self._run(
            system,
            "jobs",
            command,
            "job-search",
            (system, prefix, owner),
        )
        identity = f"prefix={prefix or '*'} owner={owner or '(profile default)'}"
        return [self._hit(system, "job-search", identity, payload, retrieved_at, cached, self.DEFAULT_TTLS["job-search"])]

    def job_status(self, system: str, jobid: str) -> list[SearchHit]:
        self._validate(jobid, SAFE_JOB_ID, "job id")
        payload, retrieved_at, cached = self._run(
            system,
            "jobs",
            ["zos-jobs", "view", "job-status-by-jobid", jobid],
            "job-status",
            (system, jobid.upper()),
        )
        return [self._hit(system, "job-status", jobid.upper(), payload, retrieved_at, cached, self.DEFAULT_TTLS["job-status"])]

    def job_output(self, system: str, jobid: str, spool_id: int | None = None) -> list[SearchHit]:
        self._validate(jobid, SAFE_JOB_ID, "job id")
        if spool_id is None:
            command = ["zos-jobs", "view", "all-spool-content", jobid]
            identity = jobid.upper()
        else:
            sid = int(spool_id)
            if sid < 1 or sid > 999_999:
                raise SecurityError("Spool file id is outside the accepted range")
            command = ["zos-jobs", "view", "spool-file-by-id", jobid, str(sid)]
            identity = f"{jobid.upper()}:{sid}"
        payload, retrieved_at, cached = self._run(
            system,
            "jobs",
            command,
            "job-output",
            (system, jobid.upper(), spool_id),
            prefer_stdout=True,
        )
        return [self._hit(system, "job-output", identity, payload, retrieved_at, cached, self.DEFAULT_TTLS["job-output"])]

    def system_logs(self, system: str, time_range: str = "10m", *, query: str = "") -> list[SearchHit]:
        self._validate(time_range, SAFE_LOG_RANGE, "log time range")
        query = (query or "").strip()[:240]
        payload, retrieved_at, cached = self._run(
            system,
            "logs",
            ["zos-logs", "list", "logs", "--range", time_range, "--direction", "backward"],
            "system-logs",
            (system, time_range),
        )
        if query:
            payload = self._filter_payload(payload, query)
        identity = f"range={time_range}" + (f" filter={query}" if query else "")
        return [self._hit(system, "system-logs", identity, payload, retrieved_at, cached, self.DEFAULT_TTLS["system-logs"])]

    def _run(
        self,
        system_name: str,
        capability: str,
        command: list[str],
        cache_kind: str,
        cache_key: tuple,
        *,
        prefer_stdout: bool = False,
    ) -> tuple[Any, str, bool]:
        system = self._system(system_name)
        self._require_capability(system, capability)
        ttl = self.DEFAULT_TTLS[cache_kind]
        key = (cache_kind, *cache_key)
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry and entry.expires_at > now:
            return entry.payload, entry.retrieved_at, True

        executable = self._resolved_executable()
        args = [executable, *command, *self._profile_args(system), "--response-format-json"]
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Zowe CLI executable was not found. Install Zowe CLI or set ZOWE_CLI_PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Read-only Zowe operation timed out after {self.timeout}s") from exc

        if result.returncode != 0:
            raise RuntimeError(self._safe_cli_error(result.stderr or result.stdout))

        try:
            payload = self._decode_payload(result.stdout, prefer_stdout=prefer_stdout)
        except RuntimeError as exc:
            raise RuntimeError(self._safe_cli_error(str(exc))) from exc
        rendered = self._render_payload(payload)
        self.policy.assert_text_safe(rendered, label=f"live Zowe {cache_kind} output")
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._cache[key] = _CacheEntry(now + ttl, payload, retrieved_at)
        return payload, retrieved_at, False

    def _system(self, name: str) -> ZoweSystem:
        for system in self.config.zowe_systems:
            if system.enabled and system.name == name:
                return system
        raise SecurityError(f"Unknown or disabled Zowe system: {name}")

    @staticmethod
    def _capabilities(system: ZoweSystem) -> tuple[str, ...]:
        return tuple(item for item in system.allowed_tools if item in CAPABILITIES)

    def _require_capability(self, system: ZoweSystem, capability: str) -> None:
        if capability not in self._capabilities(system):
            raise SecurityError(f"Zowe capability {capability!r} is not enabled for system {system.name!r}")

    def _profile_args(self, system: ZoweSystem) -> list[str]:
        args: list[str] = []
        if system.base_profile:
            self._validate(system.base_profile, SAFE_PROFILE, "Zowe base profile")
            args += ["--base-profile", system.base_profile]
        if system.zosmf_profile:
            self._validate(system.zosmf_profile, SAFE_PROFILE, "Zowe z/OSMF profile")
            args += ["--zosmf-profile", system.zosmf_profile]
        if not args:
            raise SecurityError(f"Zowe system {system.name!r} has no configured base/zosmf profile")
        return args

    def _resolved_executable(self) -> str:
        candidate = self.executable.strip()
        if not candidate:
            raise RuntimeError("ZOWE_CLI_PATH is empty")
        if os.path.sep in candidate:
            if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
                raise RuntimeError(f"Configured Zowe CLI is not executable: {candidate}")
            return candidate
        resolved = shutil.which(candidate)
        if not resolved:
            raise RuntimeError("Zowe CLI executable was not found. Install Zowe CLI or set ZOWE_CLI_PATH.")
        return resolved

    @staticmethod
    def _validate(value: str, pattern: re.Pattern[str], label: str, *, allow_empty: bool = False) -> None:
        if not value and allow_empty:
            return
        if not value or value.startswith("-") or not pattern.fullmatch(value):
            raise SecurityError(f"Unsafe or invalid {label}: {value!r}")

    @staticmethod
    def _decode_payload(stdout: str, *, prefer_stdout: bool = False) -> Any:
        text = (stdout or "").strip()
        if not text:
            return "(no output)"
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return text
        if not isinstance(decoded, dict):
            return decoded
        if decoded.get("success") is False:
            message = decoded.get("message") or decoded.get("stderr") or "Zowe command reported failure"
            raise RuntimeError(str(message))
        data = decoded.get("data")
        out = decoded.get("stdout")
        # Zowe view commands commonly put the human-readable resource body in
        # `stdout`, while list/status commands expose richer structured `data`.
        # Prefer the body for data set/spool reads so useful content is not lost
        # when the envelope also contains metadata.
        if prefer_stdout and out not in (None, ""):
            return out
        if data not in (None, "", [], {}):
            return data
        if out not in (None, ""):
            return out
        return {k: v for k, v in decoded.items() if k not in {"success", "exitCode", "stderr"}}

    @staticmethod
    def _filter_payload(payload: Any, query: str) -> Any:
        needle = query.casefold()
        if isinstance(payload, str):
            lines = [line for line in payload.splitlines() if needle in line.casefold()]
            return "\n".join(lines) if lines else f"(no log lines matched {query!r})"
        if isinstance(payload, list):
            matches = [item for item in payload if needle in json.dumps(item, ensure_ascii=False).casefold()]
            return matches
        if isinstance(payload, dict):
            for key in ("items", "logs", "records"):
                value = payload.get(key)
                if isinstance(value, list):
                    copy = dict(payload)
                    copy[key] = [item for item in value if needle in json.dumps(item, ensure_ascii=False).casefold()]
                    return copy
            if needle not in json.dumps(payload, ensure_ascii=False).casefold():
                return {"message": f"no log records matched {query!r}"}
        return payload

    @staticmethod
    def _render_payload(payload: Any) -> str:
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(text) <= MAX_ZOWE_OUTPUT_CHARS:
            return text
        head = MAX_ZOWE_OUTPUT_CHARS * 2 // 3
        tail = MAX_ZOWE_OUTPUT_CHARS - head
        return text[:head] + "\n... [live Zowe output truncated] ...\n" + text[-tail:]

    def _hit(
        self,
        system: str,
        resource_type: str,
        identity: str,
        payload: Any,
        retrieved_at: str,
        cached: bool,
        ttl: int,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> SearchHit:
        text = self._render_payload(payload)
        # Cached content was checked before it entered the cache; filtered log output
        # is checked again because the rendered payload may have changed locally.
        self.policy.assert_text_safe(text, label=f"live Zowe {resource_type} output")
        metadata = {
            "id": f"zowe:{system}:{resource_type}:{identity}",
            "source": f"zowe://{system}/{resource_type}",
            "repo": f"zowe:{system}",
            "relative_path": identity,
            "system": system,
            "resource_type": resource_type,
            "retrieved_at": retrieved_at,
            "live_mainframe": True,
            "source_type": "zowe",
            "cache_ttl_seconds": ttl,
            "cached": cached,
            "egress_allowed": True,
        }
        if start_line is not None:
            metadata["start_line"] = int(start_line)
        if end_line is not None:
            metadata["end_line"] = int(end_line)
        return SearchHit(text, metadata, 1.0, (f"zowe-{resource_type}", "live-mainframe"))

    def _safe_cli_error(self, text: str) -> str:
        cleaned = re.sub(r"<[^>]+>", " ", text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if self.policy.hard_findings(cleaned):
            return "Zowe command failed; sensitive error details were suppressed locally"
        return cleaned[:500] or "Zowe command failed"
