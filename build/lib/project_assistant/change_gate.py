from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .conversations import ConversationStore
from .security import private_dir, private_file, resolve_within


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ChangeProposal:
    id: str
    conversation_id: str
    request: str
    plan: str
    status: str
    created_at: str
    plan_approved_at: str | None = None
    repo_path: str | None = None
    patch_file: str | None = None
    patch_sha256: str | None = None
    base_commit: str | None = None
    patch_staged_at: str | None = None
    patch_approved_at: str | None = None
    applied_at: str | None = None


class ChangeGate:
    """Two-stage plan/diff approval boundary around source mutation.

    A plan approval permits preparation of a candidate diff only. The exact staged
    diff is copied into .assistant, hashed and bound to a repo + HEAD commit. A
    second explicit approval is required before that immutable diff can be applied.
    """

    MAX_PATCH_BYTES = 5 * 1024 * 1024

    def __init__(
        self,
        project_dir: Path,
        conversations: ConversationStore,
        allowed_repo_roots: list[Path] | None = None,
    ):
        self.project_dir = project_dir.resolve()
        roots = allowed_repo_roots or [self.project_dir]
        self.allowed_repo_roots = {p.resolve() for p in roots}
        self.directory = private_dir(self.project_dir / ".assistant/proposals")
        self.patch_dir = private_dir(self.project_dir / ".assistant/patches")
        self.conversations = conversations

    def create(self, conversation_id: str, request: str, plan: str) -> ChangeProposal:
        proposal = ChangeProposal(
            id=uuid.uuid4().hex[:12],
            conversation_id=conversation_id,
            request=request,
            plan=plan,
            status="pending",
            created_at=_now(),
        )
        self.conversations.append_event(
            conversation_id,
            f"Change proposal {proposal.id} — PENDING PLAN APPROVAL",
            f"**Requested change**\n\n{request}\n\n"
            f"**Proposed implementation**\n\n{plan}\n\n"
            "No source files have been changed. Approving this plan only permits preparation of a candidate diff; the exact diff requires a second approval before application.",
        )
        self._save(proposal)
        return proposal

    def approve_plan(self, proposal_id: str) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status != "pending":
            raise PermissionError(f"Plan approval is not permitted while proposal is {proposal.status}")
        proposal.status = "plan_approved"
        proposal.plan_approved_at = _now()
        self._save(proposal)
        self.conversations.append_event(
            proposal.conversation_id,
            f"Change proposal {proposal.id} — PLAN APPROVED",
            "Plan approval recorded. A candidate diff may now be prepared, but source mutation is still forbidden until the exact staged diff is separately approved.",
        )
        return proposal

    # Backwards-compatible method name for CLI/internal callers from v0.3.
    def approve(self, proposal_id: str) -> ChangeProposal:
        return self.approve_plan(proposal_id)

    def reject(self, proposal_id: str) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status == "applied":
            raise PermissionError("An applied proposal cannot be rejected")
        proposal.status = "rejected"
        self._save(proposal)
        self.conversations.append_event(
            proposal.conversation_id,
            f"Change proposal {proposal.id} — REJECTED",
            "Proposal rejected. No further mutation is permitted for this proposal.",
        )
        return proposal

    def stage_patch(self, proposal_id: str, patch_path: Path, repo_dir: Path) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status not in {"plan_approved", "patch_pending"}:
            raise PermissionError(
                f"A patch can only be staged after plan approval and before diff approval; proposal is {proposal.status}"
            )
        repo_dir = self._allowed_repo(repo_dir)
        patch_path = patch_path.expanduser().resolve()
        if not patch_path.exists() or not patch_path.is_file():
            raise FileNotFoundError(f"Patch file does not exist: {patch_path}")
        data = patch_path.read_bytes()
        if not data:
            raise ValueError("Patch is empty")
        if len(data) > self.MAX_PATCH_BYTES:
            raise ValueError(f"Patch exceeds {self.MAX_PATCH_BYTES // (1024 * 1024)} MiB safety limit")

        base_commit = self._head(repo_dir)
        subprocess.run(
            ["git", "-C", str(repo_dir), "apply", "--check", str(patch_path)],
            check=True,
            capture_output=True,
            text=True,
        )

        digest = hashlib.sha256(data).hexdigest()
        stored = self.patch_dir / f"{proposal.id}-{digest[:16]}.diff"
        stored.write_bytes(data)
        private_file(stored)

        proposal.repo_path = str(repo_dir)
        proposal.patch_file = stored.name
        proposal.patch_sha256 = digest
        proposal.base_commit = base_commit
        proposal.patch_staged_at = _now()
        proposal.patch_approved_at = None
        proposal.status = "patch_pending"
        self._save(proposal)

        diff_text = data.decode("utf-8", errors="replace")
        self.conversations.append_event(
            proposal.conversation_id,
            f"Change proposal {proposal.id} — DIFF PENDING APPROVAL",
            f"**Repository**: `{repo_dir.name}`\n\n"
            f"**Base commit**: `{base_commit}`\n\n"
            f"**Patch SHA-256**: `{digest}`\n\n"
            "The exact candidate diff is recorded below. It has been validated with `git apply --check` but has **not** been applied.\n\n"
            f"```diff\n{diff_text.rstrip()}\n```",
        )
        return proposal

    def approve_patch(self, proposal_id: str) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status != "patch_pending":
            raise PermissionError(f"Diff approval is not permitted while proposal is {proposal.status}")
        self._verify_bound_patch(proposal)
        proposal.status = "patch_approved"
        proposal.patch_approved_at = _now()
        self._save(proposal)
        self.conversations.append_event(
            proposal.conversation_id,
            f"Change proposal {proposal.id} — DIFF APPROVED",
            f"Approved exact patch `{proposal.patch_sha256}` against base commit `{proposal.base_commit}`. Only this stored diff may now be applied.",
        )
        return proposal

    def apply_patch(self, proposal_id: str) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status != "patch_approved":
            raise PermissionError(
                f"Proposal {proposal_id} is {proposal.status}; the exact staged diff must be approved before source mutation"
            )
        repo_dir, patch_path = self._verify_bound_patch(proposal)
        subprocess.run(
            ["git", "-C", str(repo_dir), "apply", "--check", str(patch_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "apply", str(patch_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        proposal.status = "applied"
        proposal.applied_at = _now()
        self._save(proposal)
        self.conversations.append_event(
            proposal.conversation_id,
            f"Change proposal {proposal.id} — APPLIED",
            f"Applied the previously approved patch `{proposal.patch_sha256}` to `{repo_dir.name}` after re-validating the hash, repository, base commit and `git apply --check`.",
        )
        return proposal

    def get(self, proposal_id: str) -> ChangeProposal:
        path = self.directory / f"{proposal_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Proposal not found: {proposal_id}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        # v0.3 compatibility: an old "approved" proposal maps only to plan
        # approval. It does not gain permission to apply an arbitrary patch.
        if raw.get("status") == "approved":
            raw["status"] = "plan_approved"
        if "approved_at" in raw and "plan_approved_at" not in raw:
            raw["plan_approved_at"] = raw.pop("approved_at")
        allowed = ChangeProposal.__dataclass_fields__
        return ChangeProposal(**{key: value for key, value in raw.items() if key in allowed})

    def list(self, conversation_id: str | None = None) -> list[ChangeProposal]:
        proposals: list[ChangeProposal] = []
        for path in self.directory.glob("*.json"):
            try:
                proposal = self.get(path.stem)
            except Exception:
                continue
            if conversation_id and proposal.conversation_id != conversation_id:
                continue
            proposals.append(proposal)
        proposals.sort(key=lambda item: item.created_at, reverse=True)
        return proposals

    def payload(self, proposal: ChangeProposal) -> dict:
        data = asdict(proposal)
        data["patch_text"] = self.patch_text(proposal) if proposal.patch_file else None
        return data

    def patch_text(self, proposal: ChangeProposal) -> str:
        if not proposal.patch_file:
            return ""
        path = resolve_within(self.patch_dir, proposal.patch_file)
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def _verify_bound_patch(self, proposal: ChangeProposal) -> tuple[Path, Path]:
        if not all((proposal.repo_path, proposal.patch_file, proposal.patch_sha256, proposal.base_commit)):
            raise PermissionError("Proposal does not have a complete staged patch binding")
        repo_dir = self._allowed_repo(Path(proposal.repo_path))
        patch_path = resolve_within(self.patch_dir, proposal.patch_file)
        if not patch_path.exists() or not patch_path.is_file():
            raise FileNotFoundError("The staged patch is missing")
        digest = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        if digest != proposal.patch_sha256:
            raise PermissionError("Staged patch hash no longer matches the approved proposal")
        current_head = self._head(repo_dir)
        if current_head != proposal.base_commit:
            raise PermissionError(
                f"Repository HEAD changed after the patch was staged ({proposal.base_commit} -> {current_head}); restage and re-approve the diff"
            )
        return repo_dir, patch_path

    def _allowed_repo(self, repo_dir: Path) -> Path:
        resolved = repo_dir.expanduser().resolve()
        if resolved not in self.allowed_repo_roots:
            raise PermissionError(f"Repo is not registered as a writable project source: {resolved}")
        return resolved

    @staticmethod
    def _head(repo_dir: Path) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise PermissionError(f"Registered write target is not a usable Git repository: {repo_dir}") from exc

    def _save(self, proposal: ChangeProposal) -> None:
        path = self.directory / f"{proposal.id}.json"
        path.write_text(json.dumps(asdict(proposal), indent=2) + "\n", encoding="utf-8")
        private_file(path)
