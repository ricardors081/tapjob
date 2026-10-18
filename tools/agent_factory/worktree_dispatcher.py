"""TapJob Agent Factory — Automated, isolated Git worktree dispatch and lifecycle management."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.agent_factory.task_contract import TaskContract

BRANCH_NAME_REGEX = re.compile(r"^(chore|feat|fix|docs|test|security|ops)/[a-zA-Z0-9_\-]+$")
BLOCKED_BRANCHES = {"main", "master", "origin/main", "origin/master"}
GIT_EXE = shutil.which("git") or "git"


class WorktreeDispatchError(Exception):
    """Base error for worktree dispatch failures."""


class InvalidBranchNameError(WorktreeDispatchError):
    """Raised when an illegal branch name (e.g., main or invalid pattern) is requested."""


class DirtyWorktreeError(WorktreeDispatchError):
    """Raised when attempting to clean up a worktree that contains uncommitted changes."""


class OwnershipCollisionError(WorktreeDispatchError):
    """Raised when parallel tasks claim overlapping file paths."""


@dataclass(frozen=True)
class WorktreeInfo:
    """Descriptor for an active Git worktree."""

    worktree_path: Path
    branch_name: str
    base_sha: str
    head_sha: str
    is_clean: bool


class WorktreeDispatcher:
    """Manages creation, inspection, and safe cleanup of isolated Git worktrees."""

    def __init__(
        self,
        repo_root: Path | str | None = None,
        worktrees_dir: Path | str | None = None,
    ) -> None:
        self.repo_root = self._resolve_repo_root(repo_root)
        self.worktrees_dir = self._resolve_worktrees_dir(worktrees_dir)

    def _resolve_repo_root(self, provided: Path | str | None) -> Path:
        """Resolve canonical repository root safely."""
        if provided is not None:
            candidate = Path(provided).resolve()
            if (candidate / ".git").exists():
                return candidate
            raise WorktreeDispatchError(
                f"Provided path '{candidate}' is not a valid git repository."
            )

        # Try git rev-parse --show-toplevel
        try:
            res = subprocess.run(  # noqa: S603
                [GIT_EXE, "rev-parse", "--show-toplevel"],
                cwd=str(Path(__file__).resolve().parent),
                capture_output=True,
                text=True,
                check=True,
            )
            top = Path(res.stdout.strip()).resolve()
            if (top / ".git").exists():
                return top
        except Exception:  # noqa: S110
            pass

        # Fallback to navigating up from current file
        fallback = Path(__file__).resolve().parent.parent.parent
        if (fallback / ".git").exists():
            return fallback

        raise WorktreeDispatchError("Could not resolve canonical git repository root.")

    def _resolve_worktrees_dir(self, provided: Path | str | None) -> Path:
        """Determine safe cross-platform worktree base directory outside the primary checkout."""
        if provided is not None:
            target = Path(provided).resolve()
            target.mkdir(parents=True, exist_ok=True)
            return target

        env_override = os.environ.get("TAPJOB_WORKTREES_DIR")
        if env_override:
            target = Path(env_override).resolve()
            target.mkdir(parents=True, exist_ok=True)
            return target

        # Default: sibling directory to repo root (e.g. C:\Projetos\tapjob-worktrees)
        default_dir = (self.repo_root.parent / f"{self.repo_root.name}-worktrees").resolve()
        default_dir.mkdir(parents=True, exist_ok=True)
        return default_dir

    def validate_branch_name(self, branch_name: str) -> str:
        """Validate that branch_name is legal and strictly prohibits main/master."""
        clean = branch_name.strip()
        normalized = clean.lower().removeprefix("refs/heads/")

        if normalized in BLOCKED_BRANCHES:
            raise InvalidBranchNameError(
                f"Branch '{branch_name}' is protected. "
                "Creating worktrees on main or master is strictly prohibited."
            )

        if ".." in clean or clean.startswith("/") or clean.startswith("\\"):
            raise InvalidBranchNameError(
                f"Branch name '{branch_name}' contains illegal path traversal."
            )

        if not BRANCH_NAME_REGEX.match(clean):
            raise InvalidBranchNameError(
                f"Branch name '{clean}' does not match required convention: "
                "^(chore|feat|fix|docs|test|security|ops)/[a-zA-Z0-9_-]+$"
            )

        return clean

    def validate_base_sha(self, base_sha: str) -> str:
        """Verify that base_sha points to an existing commit in the repository."""
        clean = base_sha.strip()
        try:
            res = subprocess.run(  # noqa: S603
                [GIT_EXE, "rev-parse", "--verify", f"{clean}^{{commit}}"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except subprocess.CalledProcessError as exc:
            raise WorktreeDispatchError(
                f"Base commit '{clean}' does not resolve to a valid commit: {exc.stderr.strip()}"
            ) from exc

    def create_worktree(
        self,
        branch_name: str,
        base_sha: str,
        worktree_id: str | None = None,
    ) -> WorktreeInfo:
        """Create an isolated git worktree checked out to a new branch at base_sha."""
        valid_branch = self.validate_branch_name(branch_name)
        resolved_base = self.validate_base_sha(base_sha)

        # Sanitize folder name from branch or worktree_id
        folder_name = worktree_id or valid_branch.replace("/", "-")
        worktree_path = (self.worktrees_dir / folder_name).resolve()

        # Prevent path traversal
        try:
            worktree_path.relative_to(self.worktrees_dir)
        except ValueError as exc:
            raise WorktreeDispatchError(
                f"Path traversal detected: '{worktree_path}' is outside '{self.worktrees_dir}'"
            ) from exc

        if worktree_path.exists():
            raise WorktreeDispatchError(
                f"Worktree destination path '{worktree_path}' already exists."
            )

        # Run git worktree add -b <branch> <path> <base_sha>
        cmd = [GIT_EXE, "worktree", "add", "-b", valid_branch, str(worktree_path), resolved_base]
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip()
            raise WorktreeDispatchError(
                f"Failed to create git worktree '{worktree_path}' on branch '{valid_branch}': {err}"
            )

        # Verify HEAD commit in created worktree
        head_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "HEAD"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            check=True,
        )
        head_sha = head_proc.stdout.strip()

        return WorktreeInfo(
            worktree_path=worktree_path,
            branch_name=valid_branch,
            base_sha=resolved_base,
            head_sha=head_sha,
            is_clean=True,
        )

    def is_worktree_clean(self, worktree_path: Path | str) -> bool:
        """Check if worktree has zero uncommitted modifications."""
        target = Path(worktree_path).resolve()
        if not target.exists():
            return True

        proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "status", "--porcelain"],
            cwd=str(target),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise WorktreeDispatchError(
                f"Failed to query git status in '{target}': {proc.stderr.strip()}"
            )

        return len(proc.stdout.strip()) == 0

    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all active Git worktrees."""
        proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "worktree", "list", "--porcelain"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=True,
        )

        worktrees: list[WorktreeInfo] = []
        current_path: Path | None = None
        current_head: str = ""
        current_branch: str = ""

        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree ").strip()).resolve()
            elif line.startswith("HEAD "):
                current_head = line.removeprefix("HEAD ").strip()
            elif line.startswith("branch "):
                current_branch = line.removeprefix("branch ").strip().removeprefix("refs/heads/")
            elif not line and current_path is not None:
                is_clean = self.is_worktree_clean(current_path)
                worktrees.append(
                    WorktreeInfo(
                        worktree_path=current_path,
                        branch_name=current_branch or "DETACHED",
                        base_sha=current_head,
                        head_sha=current_head,
                        is_clean=is_clean,
                    )
                )
                current_path = None
                current_head = ""
                current_branch = ""

        if current_path is not None:
            is_clean = self.is_worktree_clean(current_path)
            worktrees.append(
                WorktreeInfo(
                    worktree_path=current_path,
                    branch_name=current_branch or "DETACHED",
                    base_sha=current_head,
                    head_sha=current_head,
                    is_clean=is_clean,
                )
            )

        return worktrees

    def remove_worktree(
        self,
        worktree_path: Path | str,
        force: bool = False,
    ) -> None:
        """Safely remove a worktree, strictly refusing if uncommitted changes exist."""
        target = Path(worktree_path).resolve()
        if not target.exists():
            return

        if target == self.repo_root:
            raise WorktreeDispatchError("Cannot remove the primary repository root as a worktree.")

        # Check if dirty
        is_clean = self.is_worktree_clean(target)
        if not is_clean and not force:
            raise DirtyWorktreeError(
                f"Worktree '{target}' contains uncommitted changes. Safe cleanup refused."
            )

        cmd = [GIT_EXE, "worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(target))

        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise WorktreeDispatchError(
                f"Failed to remove git worktree '{target}': {proc.stderr.strip()}"
            )

    def validate_parallel_dispatch(self, tasks: Sequence[TaskContract]) -> list[str]:
        """Validate whether tasks can be dispatched concurrently in parallel worktrees."""
        errors: list[str] = []

        if len(tasks) < 2:
            return errors

        # 1. Every task must declare parallel_safe=True
        for t in tasks:
            if not t.parallel_safe:
                errors.append(f"Task '{t.task_id}' is marked parallel_safe=False.")

        # 2. Check for frozen contracts when interdependencies exist
        all_ids = {t.task_id for t in tasks}
        for t in tasks:
            has_peer_dep = any(dep in all_ids for dep in t.dependencies)
            if has_peer_dep and not t.contracts_frozen:
                errors.append(
                    f"Task '{t.task_id}' has concurrent peer dependencies, "
                    "but contracts_frozen is False."
                )

        # 3. Check pairwise ownership collisions
        for i in range(len(tasks)):
            for j in range(i + 1, len(tasks)):
                t1 = tasks[i]
                t2 = tasks[j]
                conflicts = t1.check_ownership_conflict(t2)
                for c in conflicts:
                    errors.append(
                        f"Ownership collision between '{t1.task_id}' and '{t2.task_id}': {c}"
                    )

        return errors
