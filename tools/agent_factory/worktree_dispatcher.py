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
SHA_40_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")
GIT_EXE = shutil.which("git") or "git"


class WorktreeDispatchError(Exception):
    """Base error for worktree dispatch failures."""


class InvalidBranchNameError(WorktreeDispatchError):
    """Raised when an illegal branch name (e.g., main or invalid pattern) is requested."""


class DirtyWorktreeError(WorktreeDispatchError):
    """Raised when attempting to clean up a worktree that contains uncommitted changes."""


class OwnershipCollisionError(WorktreeDispatchError):
    """Raised when parallel tasks claim overlapping file paths."""


class HumanGateRequiredError(WorktreeDispatchError):
    """Raised when dispatch is attempted on a task requiring explicit human authorization."""


class ScopeViolationError(WorktreeDispatchError):
    """Raised when changes in a worktree violate the allowed/forbidden path contract."""


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
        self._active_contracts: dict[str, tuple[TaskContract, WorktreeInfo]] = {}
        self._reserved_tasks: dict[str, TaskContract] = {}

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
        """Determine safe cross-platform worktree base directory strictly outside repo_root."""
        if provided is not None:
            target = Path(provided).resolve()
            if target == self.repo_root or self.repo_root in target.parents:
                raise WorktreeDispatchError(
                    f"worktrees_dir '{target}' cannot be repo_root or inside repo_root."
                )
            target.mkdir(parents=True, exist_ok=True)
            return target

        env_override = os.environ.get("TAPJOB_WORKTREES_DIR")
        if env_override:
            target = Path(env_override).resolve()
            if target == self.repo_root or self.repo_root in target.parents:
                raise WorktreeDispatchError(
                    f"worktrees_dir '{target}' cannot be repo_root or inside repo_root."
                )
            target.mkdir(parents=True, exist_ok=True)
            return target

        # Default: sibling directory to repo root (e.g. C:\Projetos\tapjob-worktrees)
        default_dir = (self.repo_root.parent / f"{self.repo_root.name}-worktrees").resolve()
        if default_dir == self.repo_root or self.repo_root in default_dir.parents:
            raise WorktreeDispatchError(
                f"Default worktrees directory '{default_dir}' cannot be repo_root "
                "or inside repo_root."
            )
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
        """Verify that base_sha is an exact 40-character hex SHA pointing to an existing commit."""
        if not isinstance(base_sha, str):
            raise WorktreeDispatchError(
                f"Base commit SHA must be a string, got {type(base_sha).__name__}."
            )
        clean = base_sha.strip()
        if not SHA_40_REGEX.match(clean):
            raise WorktreeDispatchError(
                f"Base SHA '{clean}' must be an exact 40-character hexadecimal string."
            )
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
    ) -> None:
        """Safely remove a worktree, refusing if dirty, unmanaged, or outside worktrees_dir."""
        target = Path(worktree_path).resolve()
        if not target.exists():
            return

        if target == self.repo_root or self.repo_root in target.parents:
            raise WorktreeDispatchError(
                f"Cannot remove primary repository root or path inside repository root: '{target}'."
            )

        if not target.is_relative_to(self.worktrees_dir):
            raise WorktreeDispatchError(
                f"Target '{target}' is outside managed worktrees directory '{self.worktrees_dir}'."
            )

        # Worktree must be a registered git worktree belonging to this repository
        registered = self.list_worktrees()
        if not any(w.worktree_path.resolve() == target for w in registered):
            raise WorktreeDispatchError(
                f"Target '{target}' is not a registered git worktree belonging to this repository."
            )

        # Check if dirty
        is_clean = self.is_worktree_clean(target)
        if not is_clean:
            raise DirtyWorktreeError(
                f"Worktree '{target}' contains uncommitted changes. Safe cleanup refused."
            )

        cmd = [GIT_EXE, "worktree", "remove", str(target)]
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

    def dispatch_task(
        self,
        contract: TaskContract,
        active_contracts: Sequence[TaskContract] | None = None,
    ) -> WorktreeInfo:
        """Canonical dispatch: validate contract, SHA, branch, human-gate, and concurrency."""
        # 1. contract.validate()
        contract.validate()

        # 2 & 3. 40-char SHA validation and commit verification
        resolved_base = self.validate_base_sha(contract.canonical_base_sha)

        # 4. Reject main/master branch
        target_branch = (
            contract.target_branch or f"feat/{contract.task_id.lower().replace('_', '-')}"
        )
        valid_branch = self.validate_branch_name(target_branch)

        # 5. Human gate check
        if contract.human_gate_required:
            raise HumanGateRequiredError(
                f"Task '{contract.task_id}' requires explicit human gate authorization; "
                "autonomous dispatch refused."
            )

        # 6, 7, 8. Active tasks and parallel safety check
        existing_active = list(self._reserved_tasks.values())
        if active_contracts:
            existing_active.extend(active_contracts)
        peer_tasks = [t for t in existing_active if t.task_id != contract.task_id]

        if peer_tasks:
            if not contract.parallel_safe:
                raise WorktreeDispatchError(
                    f"Task '{contract.task_id}' is marked parallel_safe=False and cannot "
                    "be dispatched concurrently."
                )
            if not contract.contracts_frozen:
                raise WorktreeDispatchError(
                    "Parallel task dispatch requires contracts_frozen=True for task "
                    f"'{contract.task_id}'."
                )
            for peer in peer_tasks:
                if peer.canonical_base_sha != contract.canonical_base_sha:
                    raise WorktreeDispatchError(
                        f"Base SHA mismatch between concurrent tasks: '{contract.task_id}' "
                        f"({contract.canonical_base_sha}) vs '{peer.task_id}' "
                        f"({peer.canonical_base_sha})."
                    )
                if not peer.parallel_safe:
                    raise WorktreeDispatchError(
                        f"Concurrent active task '{peer.task_id}' is marked parallel_safe=False."
                    )
                if not peer.contracts_frozen:
                    raise WorktreeDispatchError(
                        f"Concurrent active task '{peer.task_id}' has contracts_frozen=False."
                    )
                conflicts = contract.check_ownership_conflict(peer)
                if conflicts:
                    raise OwnershipCollisionError(
                        f"Ownership collision between '{contract.task_id}' and '{peer.task_id}': "
                        f"{', '.join(conflicts)}"
                    )

        # 9. Reserve ownership before creating worktree
        self._reserved_tasks[contract.task_id] = contract

        # 10 & 11. Create worktree exactly from canonical_base_sha and associate
        try:
            folder_id = contract.task_id.lower().replace("_", "-")
            info = self.create_worktree(
                branch_name=valid_branch,
                base_sha=resolved_base,
                worktree_id=folder_id,
            )
            self._active_contracts[contract.task_id] = (contract, info)
            return info
        except Exception:
            self._reserved_tasks.pop(contract.task_id, None)
            raise

    def release_task(self, task_id: str) -> None:
        """Safely clean up worktree and release ownership reservation."""
        if task_id in self._active_contracts:
            _, info = self._active_contracts[task_id]
            self.remove_worktree(info.worktree_path)
            del self._active_contracts[task_id]
        self._reserved_tasks.pop(task_id, None)

    def validate_worktree_scope(
        self,
        contract: TaskContract,
        worktree_path: Path | str,
    ) -> list[str]:
        """Verify altered files in worktree strictly adhere to allowed_paths and forbidden_paths."""
        target = Path(worktree_path).resolve()
        if not target.exists():
            return [f"Worktree path '{target}' does not exist."]

        altered_files: set[str] = set()

        # 1. Check working directory status
        st_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "status", "--porcelain"],
            cwd=str(target),
            capture_output=True,
            text=True,
            check=False,
        )
        if st_proc.returncode != 0:
            return [f"Failed to check git status in '{target}': {st_proc.stderr.strip()}"]

        for line in st_proc.stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split(maxsplit=1)
            if len(parts) > 1:
                path_part = parts[1]
                if " -> " in path_part:
                    path_part = path_part.split(" -> ")[1]
                altered_files.add(path_part.strip().strip('"').replace("\\", "/"))

        # 2. Check committed diff against base_sha
        diff_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "diff", "--name-only", f"{contract.canonical_base_sha}..HEAD"],
            cwd=str(target),
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_proc.returncode == 0:
            for line in diff_proc.stdout.splitlines():
                clean_line = line.strip().strip('"').replace("\\", "/")
                if clean_line:
                    altered_files.add(clean_line)

        violations: list[str] = []
        for file_path in sorted(altered_files):
            # Guard against path traversal, empty paths, or absolute paths
            if (
                not file_path
                or ".." in file_path
                or file_path.startswith("/")
                or file_path.startswith("\\")
            ):
                violations.append(
                    f"Invalid path traversal or absolute path detected: '{file_path}'"
                )
                continue

            # Check forbidden_paths
            norm_file = TaskContract._normalize_path(file_path)
            is_forbidden = False
            for fb in contract.forbidden_paths:
                norm_fb = TaskContract._normalize_path(fb)
                if norm_file == norm_fb or norm_file.startswith(norm_fb + "/"):
                    violations.append(
                        f"Altered file '{file_path}' is covered by forbidden_path '{fb}'"
                    )
                    is_forbidden = True
                    break

            if is_forbidden:
                continue

            # Check allowed_paths
            is_allowed = False
            for al in contract.allowed_paths:
                norm_al = TaskContract._normalize_path(al)
                if norm_file == norm_al or norm_file.startswith(norm_al + "/"):
                    is_allowed = True
                    break

            if not is_allowed:
                violations.append(
                    f"Altered file '{file_path}' is not covered by any allowed_path in "
                    f"contract '{contract.task_id}'"
                )

        return violations

    def enforce_worktree_scope(
        self,
        contract: TaskContract,
        worktree_path: Path | str,
    ) -> None:
        """Enforce worktree scope, raising ScopeViolationError if altered files violate policy."""
        violations = self.validate_worktree_scope(contract, worktree_path)
        if violations:
            raise ScopeViolationError(
                f"Scope violation in worktree for task '{contract.task_id}': "
                + "; ".join(violations)
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
