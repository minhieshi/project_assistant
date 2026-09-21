from __future__ import annotations

import fnmatch
import hmac
import os
import re
import secrets
from pathlib import Path


class SecurityError(RuntimeError):
    pass


class EgressBlockedError(SecurityError):
    pass


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

SENSITIVE_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "credentials*",
    "secrets*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "kubeconfig",
    "auth.json",
)

SENSITIVE_PATH_PARTS = {".ssh", ".aws", ".gnupg", "keychains"}

# Patterns are intentionally conservative. They look for actual credential-like
# values, not merely words such as "password" in source code.
HARD_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)

# These matches are useful for visibility but are intentionally not blockers.
# Enterprise code frequently contains secret *references* and configuration names
# that look credential-like without containing credential material.
ADVISORY_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credential-reference",
        re.compile(
            r"(?im)\b(?:password|passwd|api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|private[_-]?key|secret)\b"
            r"\s*[:=]\s*[\"']?([^\s\"'#,;]{12,}|[^\"'\n]{16,})[\"']?"
        ),
    ),
)

PLACEHOLDER_MARKERS = (
    "${",
    "{{",
    "}}",
    "<redacted>",
    "redacted",
    "example",
    "changeme",
    "replace-me",
    "replace_me",
    "dummy",
    "placeholder",
    "os.getenv",
    "process.env",
    "getenv(",
)


def is_loopback_host(host: str) -> bool:
    return host.strip().lower() in LOOPBACK_HOSTS


def default_home() -> Path:
    return Path(os.getenv("PROJECT_ASSISTANT_HOME", "~/.project-assistant")).expanduser().resolve()


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def private_file(path: Path) -> Path:
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def token_path() -> Path:
    configured = os.getenv("PROJECT_ASSISTANT_TOKEN_FILE")
    return Path(configured).expanduser().resolve() if configured else default_home() / "api-token"


def load_or_create_api_token() -> str:
    explicit = os.getenv("PROJECT_ASSISTANT_API_TOKEN")
    if explicit:
        return explicit.strip()

    path = token_path()
    private_dir(path.parent)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise SecurityError(f"Local API token file is invalid: {path}")
        private_file(path)
        return token

    token = secrets.token_urlsafe(48)
    # O_EXCL avoids accidentally replacing an existing token in a race.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise
    return token


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def resolve_within(root: Path, candidate: Path | str) -> Path:
    root = root.expanduser().resolve()
    candidate_path = Path(candidate).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = root / candidate_path
    resolved = candidate_path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SecurityError(f"Path escapes allowed root: {candidate}") from exc
    return resolved


def path_is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def outbound_metadata_allowed(metadata: dict) -> bool:
    """Return False only for chunks explicitly marked as non-egress."""
    return metadata.get("egress_allowed", True) is not False


def validate_source_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {resolved}")
    home = Path.home().resolve()
    if resolved in {Path(resolved.anchor), home}:
        raise SecurityError("Refusing to register the filesystem root or the entire home directory as a source")
    lower_parts = {part.lower() for part in resolved.parts}
    if lower_parts & SENSITIVE_PATH_PARTS:
        raise SecurityError(f"Refusing to register a credential-sensitive directory as a source: {resolved}")
    return resolved


class EgressPolicy:
    """Prevents obvious credential material from being sent to remote model APIs."""

    def path_allowed(self, path: Path) -> bool:
        name = path.name.lower()
        if any(fnmatch.fnmatch(name, pattern.lower()) for pattern in SENSITIVE_FILE_PATTERNS):
            return False
        parts = {part.lower() for part in path.parts}
        if parts & SENSITIVE_PATH_PARTS:
            return False
        return True

    @staticmethod
    def _scan(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[str]:
        findings: list[str] = []
        for label, pattern in patterns:
            for match in pattern.finditer(text):
                candidate = match.group(1) if match.lastindex else match.group(0)
                candidate_lower = candidate.lower()
                if any(marker in candidate_lower for marker in PLACEHOLDER_MARKERS):
                    continue
                findings.append(label)
                break
        return sorted(set(findings))

    def hard_findings(self, text: str) -> list[str]:
        """High-confidence credential material that must not leave the machine."""
        return self._scan(text, HARD_SECRET_PATTERNS)

    def advisory_findings(self, text: str) -> list[str]:
        """Credential-looking references that should not block normal source/chat."""
        return self._scan(text, ADVISORY_SECRET_PATTERNS)

    def findings(self, text: str) -> list[str]:
        # Backwards-compatible meaning: only findings strong enough to block egress.
        return self.hard_findings(text)

    def assert_text_safe(self, text: str, *, label: str = "outbound content") -> None:
        found = self.hard_findings(text)
        if found:
            raise EgressBlockedError(
                f"Blocked {label} from leaving the machine because it appears to contain: {', '.join(found)}"
            )

    def assert_path_safe(self, path: Path) -> None:
        if not self.path_allowed(path):
            raise EgressBlockedError(f"Blocked sensitive file from indexing/egress: {path.name}")
