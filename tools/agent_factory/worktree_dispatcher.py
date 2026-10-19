"""TapJob Agent Factory — Automated, isolated Git worktree dispatch and lifecycle management."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.agent_factory.task_contract import TaskContract
from tools.agent_factory.team import ALL_ROLES, load_profile

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


class DuplicateTaskIdError(WorktreeDispatchError):
    """Raised when attempting to dispatch a task whose task_id is already registered."""


class CorruptRegistryError(WorktreeDispatchError):
    """Raised when the persistent task registry is malformed, unparseable, or invalid."""


class RolePermissionError(WorktreeDispatchError):
    """Raised when a task with write permissions is dispatched to a read-only role."""


class RoleOwnershipViolationError(WorktreeDispatchError):
    """Raised when a task claims allowed_paths outside the role's authorized ownership_paths."""


class ScopeVerificationRequiredError(WorktreeDispatchError):
    """Raised when release_task is invoked before verify_task_scope has succeeded."""


class LockTimeoutError(WorktreeDispatchError):
    """Raised when acquiring the persistent registry lock times out."""


class TaskNotFoundError(WorktreeDispatchError):
    """Raised when attempting an operation on a task_id that does not exist in the registry."""


@dataclass(frozen=True)
class WorktreeInfo:
    """Descriptor for an active Git worktree."""

    worktree_path: Path
    branch_name: str
    base_sha: str
    head_sha: str
    is_clean: bool


def _is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is alive across Windows and POSIX."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            process_query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
            synchronize = 0x00100000  # SYNCHRONIZE
            handle = ctypes.windll.kernel32.OpenProcess(process_query | synchronize, False, pid)
            if handle == 0:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    still_active = 259
                    return exit_code.value == still_active
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True  # Fail-closed: assume alive if inspection fails
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError as err:
            import errno

            return err.errno != errno.ESRCH


def validate_registry_entry(tid: str, entry: Any) -> TaskContract:
    """Strictly validate a registry entry structurally and semantically.

    Fails closed with CorruptRegistryError on any discrepancy.
    """
    if not isinstance(tid, str) or not tid.strip():
        raise CorruptRegistryError(f"Registry key must be a non-empty string, got {tid!r}")
    if not isinstance(entry, dict):
        raise CorruptRegistryError(
            f"Registry entry '{tid}' must be a dict, got {type(entry).__name__}"
        )

    task_id = entry.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid or missing 'task_id': {task_id!r}"
        )
    if tid != task_id:
        raise CorruptRegistryError(
            f"Registry key mismatch: key '{tid}' != entry.task_id '{task_id}'"
        )

    owner_role = entry.get("owner_role")
    if not isinstance(owner_role, str) or owner_role not in ALL_ROLES:
        raise CorruptRegistryError(f"Registry entry '{tid}' has invalid owner_role: {owner_role!r}")

    base_sha = entry.get("canonical_base_sha")
    if not isinstance(base_sha, str) or not bool(re.match(r"^[0-9a-fA-F]{40}$", base_sha)):
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid canonical_base_sha: {base_sha!r}"
        )

    target_branch = entry.get("target_branch")
    if (
        not isinstance(target_branch, str)
        or not target_branch.strip()
        or target_branch in ("main", "master")
    ):
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid target_branch: {target_branch!r}"
        )

    worktree_path = entry.get("worktree_path")
    if not isinstance(worktree_path, str):
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid worktree_path: {worktree_path!r}"
        )

    allowed_paths = entry.get("allowed_paths")
    if not isinstance(allowed_paths, list) or not all(isinstance(p, str) for p in allowed_paths):
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid allowed_paths: {allowed_paths!r}"
        )

    forbidden_paths = entry.get("forbidden_paths")
    if not isinstance(forbidden_paths, list) or not all(
        isinstance(p, str) for p in forbidden_paths
    ):
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid forbidden_paths: {forbidden_paths!r}"
        )

    parallel_safe = entry.get("parallel_safe")
    if type(parallel_safe) is not bool:
        tname = type(parallel_safe).__name__
        raise CorruptRegistryError(
            f"Registry entry '{tid}' parallel_safe must be strict bool, got {tname}"
        )

    contracts_frozen = entry.get("contracts_frozen")
    if type(contracts_frozen) is not bool:
        tname = type(contracts_frozen).__name__
        raise CorruptRegistryError(
            f"Registry entry '{tid}' contracts_frozen must be strict bool, got {tname}"
        )

    scope_verified = entry.get("scope_verified")
    if type(scope_verified) is not bool:
        tname = type(scope_verified).__name__
        raise CorruptRegistryError(
            f"Registry entry '{tid}' scope_verified must be strict bool, got {tname}"
        )

    status = entry.get("status")
    valid_statuses = {"reserved", "active", "scope_verified", "activation_failed"}
    if status not in valid_statuses:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid status: {status!r}. "
            f"Expected one of {sorted(valid_statuses)}"
        )

    if scope_verified:
        verified_head = entry.get("scope_verified_head_sha")
        if not isinstance(verified_head, str) or not bool(
            re.match(r"^[0-9a-fA-F]{40}$", verified_head)
        ):
            raise CorruptRegistryError(
                f"Registry entry '{tid}' is scope_verified but has invalid "
                f"scope_verified_head_sha: {verified_head!r}"
            )

    nested_contract_dict = entry.get("contract")
    if not isinstance(nested_contract_dict, dict) or not nested_contract_dict:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' missing or empty nested 'contract' dict"
        )

    try:
        contract = TaskContract.from_dict(nested_contract_dict)
        contract.validate()
    except Exception as exc:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' has invalid nested TaskContract: {exc}"
        ) from exc

    # Cross-field consistency checks
    if contract.task_id != task_id:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' task_id '{task_id}' does not match "
            f"nested contract.task_id '{contract.task_id}'"
        )
    if contract.owner_role != owner_role:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' owner_role '{owner_role}' does not match "
            f"nested contract.owner_role '{contract.owner_role}'"
        )
    if contract.canonical_base_sha != base_sha:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' canonical_base_sha does not match nested contract"
        )
    if contract.allowed_paths != allowed_paths:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' allowed_paths does not match nested contract.allowed_paths"
        )
    if contract.forbidden_paths != forbidden_paths:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' forbidden_paths does not match nested contract.forbidden_paths"
        )
    if contract.parallel_safe is not parallel_safe:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' parallel_safe does not match nested contract.parallel_safe"
        )
    if contract.contracts_frozen is not contracts_frozen:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' contracts_frozen does not match nested "
            "contract.contracts_frozen"
        )
    if contract.target_branch and contract.target_branch != target_branch:
        raise CorruptRegistryError(
            f"Registry entry '{tid}' target_branch '{target_branch}' does not match "
            f"nested contract.target_branch '{contract.target_branch}'"
        )

    return contract


class WorktreeDispatcher:
    """Manages creation, inspection, and safe cleanup of isolated Git worktrees."""

    def __init__(
        self,
        repo_root: Path | str | None = None,
        worktrees_dir: Path | str | None = None,
    ) -> None:
        self.repo_root = self._resolve_repo_root(repo_root)
        self.worktrees_dir = self._resolve_worktrees_dir(worktrees_dir)
        self.registry_dir = (self.worktrees_dir / ".tapjob-agent-factory").resolve()
        self.registry_file = self.registry_dir / "registry.json"
        self.lock_path = self.registry_dir / ".lock"
        self._active_contracts: dict[str, tuple[TaskContract, WorktreeInfo]] = {}
        self._reserved_tasks: dict[str, TaskContract] = {}

    validate_registry_entry = staticmethod(validate_registry_entry)

    @contextmanager
    def _registry_lock(self, timeout_seconds: float = 10.0) -> Iterator[None]:
        """Cross-platform directory-based file lock ensuring single-writer registry mutations."""
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        owner_file = self.lock_path / "owner.json"
        start_time = time.time()
        acquired = False
        while time.time() - start_time < timeout_seconds:
            try:
                self.lock_path.mkdir(parents=False, exist_ok=False)
                # Acquired directory lock. Write owner metadata.
                owner_data = {
                    "pid": os.getpid(),
                    "token": token,
                    "created_at": time.time(),
                }
                owner_file.write_text(json.dumps(owner_data), encoding="utf-8")
                acquired = True
                break
            except FileExistsError:
                # Lock directory exists. Check if owner process is dead.
                if owner_file.exists():
                    try:
                        owner_raw = owner_file.read_text(encoding="utf-8")
                        owner_data = json.loads(owner_raw)
                    except Exception as exc:
                        raise CorruptRegistryError(
                            f"Corrupt lock owner metadata at '{owner_file}': {exc}"
                        ) from exc

                    if type(owner_data) is not dict:
                        raise CorruptRegistryError(
                            f"Lock owner metadata at '{owner_file}' must be a JSON object."
                        ) from None

                    owner_pid = owner_data.get("pid")
                    if type(owner_pid) is not int or owner_pid <= 0:
                        raise CorruptRegistryError(
                            f"Malformed PID in lock owner metadata: {owner_pid!r}"
                        ) from None

                    if not _is_pid_alive(owner_pid):
                        # Deterministically recover stale lock from dead process
                        owner_file.unlink(missing_ok=True)
                        with contextlib.suppress(OSError):
                            self.lock_path.rmdir()
                        continue
                time.sleep(0.05)

        if not acquired:
            msg = (
                f"Timed out acquiring registry lock at '{self.lock_path}' after {timeout_seconds}s."
            )
            raise LockTimeoutError(msg)

        try:
            yield
        finally:
            if acquired and owner_file.exists():
                try:
                    cur_raw = owner_file.read_text(encoding="utf-8")
                    cur_data = json.loads(cur_raw)
                except Exception as exc:
                    raise WorktreeDispatchError(
                        f"Failed to read lock owner file on release: {exc}"
                    ) from exc
                if type(cur_data) is dict and cur_data.get("token") == token:
                    owner_file.unlink(missing_ok=True)
                    try:
                        self.lock_path.rmdir()
                    except OSError as exc:
                        raise WorktreeDispatchError(
                            f"Failed to remove lock dir '{self.lock_path}': {exc}"
                        ) from exc

    def _read_registry_file(self) -> dict[str, Any]:
        """Read and validate the persistent registry file. Fails closed on corruption."""
        if not self.registry_file.exists():
            return {}
        try:
            raw = self.registry_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            raise CorruptRegistryError(
                f"Failed to parse registry file '{self.registry_file}': {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise CorruptRegistryError(
                f"Registry at '{self.registry_file}' must be a JSON object, "
                f"got {type(data).__name__}"
            )

        for tid, entry in data.items():
            validate_registry_entry(tid, entry)

        return data

    def _write_registry_file(self, data: dict[str, Any]) -> None:
        """Atomically write the registry data to disk."""
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        tmp_name = f"registry.json.tmp.{os.getpid()}.{time.time_ns()}"
        tmp_file = self.registry_dir / tmp_name
        tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_file.replace(self.registry_file)

    def reconcile_registry(self) -> list[str]:
        """Prune stale entries from the persistent registry whose worktrees no longer exist."""
        with self._registry_lock():
            data = self._read_registry_file()
            existing_worktrees = {w.worktree_path.resolve() for w in self.list_worktrees()}
            stale: list[str] = []
            for task_id, entry in list(data.items()):
                wpath_str = entry.get("worktree_path", "")
                if not wpath_str:
                    continue
                wpath = Path(wpath_str).resolve()
                if not wpath.exists() and wpath not in existing_worktrees:
                    stale.append(task_id)
                    del data[task_id]
            if stale:
                self._write_registry_file(data)
            return stale

    def _validate_role_ownership(self, contract: TaskContract) -> None:
        """Validate that the contract conforms to the owner_role's permissions and ownership."""
        try:
            profile = load_profile(contract.owner_role)
        except Exception as exc:
            raise WorktreeDispatchError(
                f"Failed to load agent profile for owner_role '{contract.owner_role}': {exc}"
            ) from exc

        if not profile.enable_write_tools and contract.allowed_paths:
            raise RolePermissionError(
                f"Role '{contract.owner_role}' is read-only (enable_write_tools=False) "
                "and cannot receive write tasks with allowed_paths."
            )

        for path in contract.allowed_paths:
            norm_allowed = TaskContract._normalize_path(path).strip("/")
            covered = False
            for op in profile.ownership_paths:
                norm_op = TaskContract._normalize_path(op).strip("/")
                if norm_op in ("", "."):
                    covered = True
                    break
                if norm_allowed == norm_op or norm_allowed.startswith(norm_op + "/"):
                    covered = True
                    break
            if not covered:
                raise RoleOwnershipViolationError(
                    f"Role '{contract.owner_role}' is not authorized to modify path '{path}'. "
                    f"Authorized ownership paths: {profile.ownership_paths}"
                )

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
        """Canonical dispatch with persistent registry, role ownership, and duplicate guards."""
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

        # 6. Role ownership and write permission check
        self._validate_role_ownership(contract)

        # 7. Persistent registry check for duplicate task_id and concurrency checks
        reservation_created = False
        with self._registry_lock():
            registry_data = self._read_registry_file()

            # Fail closed on duplicate task_id
            if contract.task_id in registry_data:
                existing_status = registry_data[contract.task_id].get("status", "unknown")
                raise DuplicateTaskIdError(
                    f"Task '{contract.task_id}' is already registered with status "
                    f"'{existing_status}'. Duplicate dispatch refused."
                )

            # Reconstruct peer tasks from persistent registry
            peer_tasks: list[TaskContract] = []
            for _tid, entry in registry_data.items():
                peer_tasks.append(TaskContract.from_dict(entry["contract"]))

            if active_contracts:
                known_ids = {t.task_id for t in peer_tasks}
                for ac in active_contracts:
                    if ac.task_id != contract.task_id and ac.task_id not in known_ids:
                        peer_tasks.append(ac)

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
                            f"Concurrent active task '{peer.task_id}' is marked "
                            "parallel_safe=False."
                        )
                    if not peer.contracts_frozen:
                        raise WorktreeDispatchError(
                            f"Concurrent active task '{peer.task_id}' has contracts_frozen=False."
                        )
                    conflicts = contract.check_ownership_conflict(peer)
                    if conflicts:
                        raise OwnershipCollisionError(
                            f"Ownership collision between '{contract.task_id}' and "
                            f"'{peer.task_id}': {', '.join(conflicts)}"
                        )

            # Reserve ownership in persistent registry before creating worktree
            registry_data[contract.task_id] = {
                "task_id": contract.task_id,
                "owner_role": contract.owner_role,
                "canonical_base_sha": contract.canonical_base_sha,
                "target_branch": valid_branch,
                "worktree_path": "",
                "allowed_paths": contract.allowed_paths,
                "forbidden_paths": contract.forbidden_paths,
                "parallel_safe": contract.parallel_safe,
                "contracts_frozen": contract.contracts_frozen,
                "status": "reserved",
                "scope_verified": False,
                "scope_verified_head_sha": "",
                "contract": contract.to_dict(),
            }
            self._write_registry_file(registry_data)
            reservation_created = True

        # 8. Create worktree and promote status to active
        worktree_created = False
        worktree_info: WorktreeInfo | None = None
        try:
            folder_id = contract.task_id.lower().replace("_", "-")
            info = self.create_worktree(
                branch_name=valid_branch,
                base_sha=resolved_base,
                worktree_id=folder_id,
            )
            worktree_created = True
            worktree_info = info

            with self._registry_lock():
                registry_data = self._read_registry_file()
                if contract.task_id not in registry_data:
                    raise WorktreeDispatchError(
                        f"Task '{contract.task_id}' reservation disappeared before activation."
                    )
                registry_data[contract.task_id]["worktree_path"] = str(info.worktree_path.resolve())
                registry_data[contract.task_id]["status"] = "active"
                self._write_registry_file(registry_data)

            self._active_contracts[contract.task_id] = (contract, info)
            self._reserved_tasks[contract.task_id] = contract
            return info
        except Exception as exc:
            if worktree_created and worktree_info is not None:
                # Worktree created, but status promotion failed.
                # Preserve reservation in fail-closed state ("activation_failed")
                # with real worktree_path so future dispatches see ownership.
                try:
                    with self._registry_lock():
                        rdata = self._read_registry_file()
                        if contract.task_id in rdata:
                            rdata[contract.task_id]["worktree_path"] = str(
                                worktree_info.worktree_path.resolve()
                            )
                            rdata[contract.task_id]["status"] = "activation_failed"
                            self._write_registry_file(rdata)
                except Exception as recovery_exc:
                    raise WorktreeDispatchError(
                        f"Task '{contract.task_id}' activation failed ({exc}), and updating "
                        f"registry to 'activation_failed' failed ({recovery_exc}). "
                        f"Worktree remains at '{worktree_info.worktree_path}'."
                    ) from exc
                raise WorktreeDispatchError(
                    f"Task '{contract.task_id}' worktree was created at "
                    f"'{worktree_info.worktree_path}', but status promotion failed ({exc}). "
                    "Entry preserved with status='activation_failed'."
                ) from exc
            else:
                if reservation_created:
                    try:
                        with self._registry_lock():
                            rdata = self._read_registry_file()
                            rdata.pop(contract.task_id, None)
                            self._write_registry_file(rdata)
                    except Exception as cleanup_exc:
                        raise WorktreeDispatchError(
                            f"Failed to create worktree for '{contract.task_id}' ({exc}) "
                            f"and failed to clean up reservation: {cleanup_exc}"
                        ) from exc
                raise

    def get_active_task(self, task_id: str) -> tuple[TaskContract, WorktreeInfo] | None:
        """Inspect and return active TaskContract and live WorktreeInfo for given task_id."""
        with self._registry_lock():
            registry_data = self._read_registry_file()

        if task_id not in registry_data:
            return None

        entry = registry_data[task_id]
        status = entry.get("status")
        if status not in ("active", "scope_verified"):
            return None

        wpath_str = entry.get("worktree_path", "")
        if not wpath_str:
            return None

        wpath = Path(wpath_str).resolve()
        if not wpath.exists():
            return None

        head_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "HEAD"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        if head_proc.returncode != 0:
            return None
        head_sha = head_proc.stdout.strip()

        br_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        branch = (
            br_proc.stdout.strip() if br_proc.returncode == 0 else entry.get("target_branch", "")
        )

        contract = TaskContract.from_dict(entry["contract"])
        winfo = WorktreeInfo(
            worktree_path=wpath,
            branch_name=branch,
            base_sha=contract.canonical_base_sha,
            head_sha=head_sha,
            is_clean=self.is_worktree_clean(wpath),
        )
        return (contract, winfo)

    def validate_task_consolidation_evidence(
        self,
        contract: TaskContract,
        expected_head_sha: str,
    ) -> list[str]:
        """Validate canonical AF-1 evidence for consolidation readiness."""
        blockers: list[str] = []
        tid = contract.task_id

        if not expected_head_sha or not bool(re.match(r"^[0-9a-fA-F]{40}$", expected_head_sha)):
            blockers.append(
                f"Task '{tid}' lacks valid scope_verified_head_sha: {expected_head_sha!r}"
            )
            return blockers

        with self._registry_lock():
            reg_data = self._read_registry_file()

        if tid not in reg_data:
            blockers.append(f"Task '{tid}' has no persistent entry in WorktreeDispatcher registry.")
            return blockers

        entry = reg_data[tid]

        # 1. Dispatcher entry status must be scope_verified
        if entry.get("status") != "scope_verified":
            msg = (
                f"Task '{tid}' dispatcher registry status is '{entry.get('status')}', "
                "expected 'scope_verified'."
            )
            blockers.append(msg)

        # 2. scope_verified boolean must be strict True
        if entry.get("scope_verified") is not True:
            blockers.append(f"Task '{tid}' dispatcher registry scope_verified is not True.")

        # 3. scope_verified_head_sha must match expected
        disp_head = entry.get("scope_verified_head_sha")
        if disp_head != expected_head_sha:
            msg = (
                f"Task '{tid}' dispatcher verified HEAD mismatch: "
                f"expected '{expected_head_sha}', got '{disp_head}'."
            )
            blockers.append(msg)

        # 4. Consistency of owner_role and target_branch
        if entry.get("owner_role") != contract.owner_role:
            msg = (
                f"Task '{tid}' owner_role mismatch: "
                f"expected '{contract.owner_role}', got '{entry.get('owner_role')}'."
            )
            blockers.append(msg)
        if entry.get("target_branch") != contract.target_branch:
            msg = (
                f"Task '{tid}' target_branch mismatch: "
                f"expected '{contract.target_branch}', got '{entry.get('target_branch')}'."
            )
            blockers.append(msg)

        # 5. Nested TaskContract must match exactly
        nested_contract_dict = entry.get("contract")
        if not isinstance(nested_contract_dict, dict) or nested_contract_dict != contract.to_dict():
            blockers.append(
                f"Task '{tid}' nested TaskContract in dispatcher registry "
                "does not match mission contract."
            )

        # 6. Live worktree checks
        wpath_str = entry.get("worktree_path")
        if not wpath_str:
            blockers.append(f"Task '{tid}' has no worktree_path recorded.")
            return blockers

        wpath = Path(wpath_str).resolve()
        if not wpath.exists():
            blockers.append(f"Task '{tid}' worktree path '{wpath}' does not exist on disk.")
            return blockers

        registered_worktrees = {w.worktree_path.resolve() for w in self.list_worktrees()}
        if wpath not in registered_worktrees:
            blockers.append(f"Task '{tid}' worktree '{wpath}' is not a registered Git worktree.")

        # 7. Current branch must match contract.target_branch
        br_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        if br_proc.returncode != 0:
            blockers.append(f"Task '{tid}' failed to inspect branch in worktree.")
        else:
            cur_branch = br_proc.stdout.strip()
            if cur_branch != contract.target_branch:
                msg = (
                    f"Task '{tid}' current branch '{cur_branch}' does not match "
                    f"target_branch '{contract.target_branch}'."
                )
                blockers.append(msg)

        # 8. Worktree must be clean
        if not self.is_worktree_clean(wpath):
            blockers.append(f"Task '{tid}' worktree contains uncommitted/dirty changes.")

        # 9. Live HEAD must match expected_head_sha
        head_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "HEAD"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        if head_proc.returncode != 0:
            blockers.append(f"Task '{tid}' failed to inspect live HEAD in worktree.")
        else:
            live_head = head_proc.stdout.strip()
            if live_head != expected_head_sha:
                msg = (
                    f"Task '{tid}' HEAD changed after scope verification "
                    f"({live_head} != {expected_head_sha})."
                )
                blockers.append(msg)

        return blockers

    def verify_task_scope(self, task_id: str) -> None:
        """Canonical verification: validate contract, worktree binding, branch, and scope."""
        with self._registry_lock():
            registry_data = self._read_registry_file()

        if task_id not in registry_data:
            raise TaskNotFoundError(f"Task '{task_id}' not found in persistent registry.")

        entry = registry_data[task_id]
        wpath_str = entry.get("worktree_path", "")
        if not wpath_str:
            raise WorktreeDispatchError(
                f"Task '{task_id}' has no associated worktree_path in registry."
            )

        wpath = Path(wpath_str).resolve()
        if not wpath.exists():
            raise ScopeViolationError(
                f"Worktree path '{wpath}' for task '{task_id}' does not exist on disk."
            )

        registered_worktrees = {w.worktree_path.resolve() for w in self.list_worktrees()}
        if wpath not in registered_worktrees:
            raise ScopeViolationError(
                f"Path '{wpath}' is not a registered git worktree of this repository."
            )

        # Validate branch binding
        br_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        if br_proc.returncode != 0:
            raise ScopeViolationError(
                f"Failed to query branch in worktree '{wpath}': {br_proc.stderr.strip()}"
            )
        current_branch = br_proc.stdout.strip()
        expected_branch = entry.get("target_branch")
        if current_branch != expected_branch:
            raise ScopeViolationError(
                f"Branch mismatch for task '{task_id}': expected '{expected_branch}', "
                f"got '{current_branch}'."
            )

        # Reconstruct contract
        contract = TaskContract.from_dict(entry["contract"])
        contract.validate()

        # Verify base SHA exists in worktree git repo
        sha_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "cat-file", "-e", f"{contract.canonical_base_sha}^{{commit}}"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        if sha_proc.returncode != 0:
            raise ScopeViolationError(
                f"Base SHA '{contract.canonical_base_sha}' does not exist in worktree '{wpath}'."
            )

        # Enforce scope
        self.enforce_worktree_scope(contract, wpath)

        # Capture exact current worktree HEAD
        head_proc = subprocess.run(  # noqa: S603
            [GIT_EXE, "rev-parse", "HEAD"],
            cwd=str(wpath),
            capture_output=True,
            text=True,
            check=False,
        )
        if head_proc.returncode != 0:
            raise ScopeViolationError(
                f"Failed to resolve HEAD for worktree '{wpath}': {head_proc.stderr.strip()}"
            )
        verified_head_sha = head_proc.stdout.strip()
        if not bool(re.match(r"^[0-9a-fA-F]{40}$", verified_head_sha)):
            raise ScopeViolationError(
                f"Invalid HEAD SHA '{verified_head_sha}' resolved for worktree '{wpath}'."
            )

        # Mark scope_verified in persistent registry
        with self._registry_lock():
            registry_data = self._read_registry_file()
            if task_id in registry_data:
                registry_data[task_id]["status"] = "scope_verified"
                registry_data[task_id]["scope_verified"] = True
                registry_data[task_id]["scope_verified_head_sha"] = verified_head_sha
                registry_data[task_id]["scope_verified_at"] = datetime.now(UTC).isoformat()
                self._write_registry_file(registry_data)

    def release_task(self, task_id: str) -> None:
        """Safely clean up worktree and release ownership after mandatory scope verification."""
        with self._registry_lock():
            registry_data = self._read_registry_file()
            if task_id not in registry_data:
                raise TaskNotFoundError(f"Task '{task_id}' not found in persistent registry.")

            entry = registry_data[task_id]
            is_verified = (
                entry.get("scope_verified", False) is True
                and entry.get("status") == "scope_verified"
            )
            if not is_verified:
                raise ScopeVerificationRequiredError(
                    f"Task '{task_id}' cannot be released before successful scope verification. "
                    f"Call verify_task_scope('{task_id}') first."
                )

            verified_head = entry.get("scope_verified_head_sha", "")
            if not isinstance(verified_head, str) or not bool(
                re.match(r"^[0-9a-fA-F]{40}$", verified_head)
            ):
                raise ScopeVerificationRequiredError(
                    f"Task '{task_id}' has invalid scope_verified_head_sha: '{verified_head}'."
                )

            wpath_str = entry.get("worktree_path")
            if not wpath_str:
                raise WorktreeDispatchError(
                    f"Task '{task_id}' has no associated worktree_path in registry."
                )

            wpath = Path(wpath_str).resolve()
            if not wpath.exists():
                raise ScopeViolationError(
                    f"Worktree path '{wpath}' for task '{task_id}' does not exist on disk."
                )

            registered_worktrees = {w.worktree_path.resolve() for w in self.list_worktrees()}
            if wpath not in registered_worktrees:
                raise ScopeViolationError(
                    f"Path '{wpath}' is not a registered git worktree of this repository."
                )

            # Check branch matches target_branch
            br_proc = subprocess.run(  # noqa: S603
                [GIT_EXE, "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(wpath),
                capture_output=True,
                text=True,
                check=False,
            )
            if br_proc.returncode != 0:
                raise ScopeViolationError(
                    f"Failed to query branch in worktree '{wpath}': {br_proc.stderr.strip()}"
                )
            current_branch = br_proc.stdout.strip()
            if current_branch != entry.get("target_branch"):
                raise ScopeViolationError(
                    f"Branch mismatch during release of task '{task_id}': "
                    f"expected '{entry.get('target_branch')}', got '{current_branch}'."
                )

            # Verify worktree HEAD matches verified_head
            head_proc = subprocess.run(  # noqa: S603
                [GIT_EXE, "rev-parse", "HEAD"],
                cwd=str(wpath),
                capture_output=True,
                text=True,
                check=False,
            )
            if head_proc.returncode != 0:
                err_msg = head_proc.stderr.strip()
                raise ScopeViolationError(
                    f"Failed to resolve HEAD during release of task '{task_id}': {err_msg}"
                )
            current_head = head_proc.stdout.strip()
            if current_head != verified_head:
                raise ScopeVerificationRequiredError(
                    f"Worktree HEAD for task '{task_id}' has changed since scope verification "
                    f"({current_head} != {verified_head}). Re-run verify_task_scope."
                )

            # Re-enforce scope and clean worktree before removal
            contract = TaskContract.from_dict(entry["contract"])
            self.enforce_worktree_scope(contract, wpath)

            self.remove_worktree(wpath)

            del registry_data[task_id]
            self._write_registry_file(registry_data)

        self._active_contracts.pop(task_id, None)
        self._reserved_tasks.pop(task_id, None)

    def validate_worktree_scope(
        self,
        contract: TaskContract,
        worktree_path: Path | str,
    ) -> list[str]:
        """Verify altered files in worktree strictly adhere to allowed_paths and forbidden_paths."""
        target = Path(worktree_path).resolve()
        if not target.exists():
            raise ScopeViolationError(f"Worktree path '{target}' does not exist.")

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
            raise ScopeViolationError(
                f"Failed to check git status in '{target}': {st_proc.stderr.strip()}"
            )

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
        if diff_proc.returncode != 0:
            raise ScopeViolationError(
                f"Failed to check git diff in '{target}': {diff_proc.stderr.strip()}"
            )

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
