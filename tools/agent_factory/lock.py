"""TapJob Agent Factory — Hardened, ABA-safe directory-based cross-process lock."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is alive across Windows and POSIX."""
    if pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            import ctypes

            process_query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
            synchronize = 0x00100000  # SYNCHRONIZE
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query | synchronize, False, pid
            )
            if handle == 0:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
                    still_active = 259
                    return exit_code.value == still_active
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DirectoryLockError(Exception):
    """Base exception for directory-based locking failures."""


class LockTimeoutError(DirectoryLockError):
    """Timed out attempting to acquire directory lock."""


class CorruptLockMetadataError(DirectoryLockError):
    """Lock directory contains malformed or unparseable owner metadata."""


class ForeignLockError(DirectoryLockError):
    """Attempted to modify or release a lock owned by another process or token."""


class DirectoryLock:
    """Hardened, cross-process directory lock preventing ABA races during stale recovery.

    Invariants:
    1. Single-writer mutual exclusion via atomic mkdir.
    2. Owner metadata (PID, UUID token, timestamp) recorded in owner.json.
    3. Stale lock recovery renames canonical lock directory to a unique tombstone
       atomically (os.replace) before cleanup, eliminating ABA delete races.
    4. Release renames canonical lock directory to a unique tombstone atomically
       before deletion, guaranteeing foreign locks are never unlinked.
    5. Malformed or orphan owner metadata fails closed without blind deletion.
    6. Zero contextlib.suppress in safety-critical reclaim and release logic.
    """

    def __init__(
        self,
        lock_path: Path | str,
        timeout_seconds: float = 10.0,
        poll_interval: float = 0.05,
        timeout_error_cls: type[Exception] = LockTimeoutError,
        corrupt_error_cls: type[Exception] = CorruptLockMetadataError,
    ) -> None:
        self.lock_path = Path(lock_path).resolve()
        self.parent_dir = self.lock_path.parent
        self.owner_file = self.lock_path / "owner.json"
        self.default_timeout = timeout_seconds
        self.poll_interval = poll_interval
        self.timeout_error_cls = timeout_error_cls
        self.corrupt_error_cls = corrupt_error_cls
        self._token: str | None = None
        self._pid: int | None = None

    @property
    def is_locked(self) -> bool:
        """Return True if this lock instance currently holds the lock."""
        return self._token is not None and self._pid is not None

    def acquire_lock(self, timeout_seconds: float | None = None) -> None:
        """Acquire the directory lock, blocking up to timeout_seconds."""
        timeout = self.default_timeout if timeout_seconds is None else timeout_seconds
        token = uuid.uuid4().hex
        my_pid = os.getpid()
        start_time = time.time()
        self.parent_dir.mkdir(parents=True, exist_ok=True)

        while time.time() - start_time < timeout:
            try:
                self.lock_path.mkdir(parents=False, exist_ok=False)
                # Exclusively created lock directory. Write owner metadata.
                owner_data: dict[str, Any] = {
                    "pid": my_pid,
                    "token": token,
                    "created_at": time.time(),
                }
                self.owner_file.write_text(json.dumps(owner_data, indent=2), encoding="utf-8")
                self._token = token
                self._pid = my_pid
                return
            except FileExistsError:
                # Lock directory exists. Inspect ownership.
                self._handle_existing_lock(start_time, timeout)

        msg = f"Timed out acquiring directory lock at '{self.lock_path}' after {timeout}s."
        raise self.timeout_error_cls(msg)

    def _handle_existing_lock(self, start_time: float, timeout: float) -> None:
        """Inspect existing lock directory, validate owner metadata, and claim if stale."""
        if not self.lock_path.exists():
            return

        if not self.owner_file.exists():
            # Grace period for owner to finish writing metadata
            time.sleep(self.poll_interval)
            if self.lock_path.exists() and not self.owner_file.exists():
                try:
                    dir_age = time.time() - self.lock_path.stat().st_ctime
                except OSError:
                    return
                if dir_age > 1.0:
                    msg = (
                        f"Orphan lock directory at '{self.lock_path}' "
                        f"lacks owner metadata after {dir_age:.2f}s."
                    )
                    raise self.corrupt_error_cls(msg)
            return

        try:
            owner_raw = self.owner_file.read_text(encoding="utf-8")
            owner_data = json.loads(owner_raw)
        except Exception as exc:
            msg = f"Corrupt lock owner metadata at '{self.owner_file}': {exc}"
            raise self.corrupt_error_cls(msg) from exc

        if type(owner_data) is not dict:
            msg = f"Lock owner metadata at '{self.owner_file}' must be a JSON object."
            raise self.corrupt_error_cls(msg)

        owner_pid = owner_data.get("pid")
        owner_token = owner_data.get("token")
        if (
            type(owner_pid) is not int
            or owner_pid <= 0
            or type(owner_token) is not str
            or not owner_token
        ):
            msg = (
                f"Malformed lock owner metadata at '{self.owner_file}': "
                f"pid={owner_pid!r}, token={owner_token!r}"
            )
            raise self.corrupt_error_cls(msg)

        # Check owner process liveness
        if _is_pid_alive(owner_pid):
            # Live owner: lock is actively held. Poll until free or timeout.
            time.sleep(self.poll_interval)
            return

        # Owner process is confirmed dead! Atomic stale lock reclamation via tombstone
        self._reclaim_stale_lock(owner_pid, owner_token)

    def _reclaim_stale_lock(self, expected_pid: int, expected_token: str) -> None:
        """Atomically claim and purge a dead-process lock directory without ABA race."""
        tombstone_name = f".tombstone_{expected_pid}_{expected_token}_{uuid.uuid4().hex}"
        tombstone_path = self.parent_dir / tombstone_name

        try:
            self.lock_path.replace(tombstone_path)
        except (FileNotFoundError, OSError):
            # Another contender claimed it or lock was freed/re-created. Back off.
            time.sleep(self.poll_interval)
            return

        # Atomically claimed the stale instance. Revalidate inside tombstone:
        tombstone_owner = tombstone_path / "owner.json"
        if tombstone_owner.exists():
            with contextlib.suppress(OSError):
                t_raw = tombstone_owner.read_text(encoding="utf-8")
                t_data = json.loads(t_raw)
                if (
                    isinstance(t_data, dict)
                    and t_data.get("pid") == expected_pid
                    and t_data.get("token") == expected_token
                ):
                    tombstone_owner.unlink()
                    tombstone_path.rmdir()
        else:
            with contextlib.suppress(OSError):
                tombstone_path.rmdir()

    def release(self) -> None:
        """Release the lock held by this instance. Fails closed on token/PID mismatch."""
        if not self._token or not self._pid:
            return

        held_token = self._token
        held_pid = self._pid
        self._token = None
        self._pid = None

        if not self.lock_path.exists():
            return

        if not self.owner_file.exists():
            msg = f"Cannot release lock at '{self.lock_path}': owner.json missing."
            raise self.corrupt_error_cls(msg)

        try:
            cur_raw = self.owner_file.read_text(encoding="utf-8")
            cur_data = json.loads(cur_raw)
        except Exception as exc:
            msg = f"Failed to read lock owner metadata on release at '{self.owner_file}': {exc}"
            raise self.corrupt_error_cls(msg) from exc

        if (
            not isinstance(cur_data, dict)
            or cur_data.get("token") != held_token
            or cur_data.get("pid") != held_pid
        ):
            msg = (
                f"Cannot release lock at '{self.lock_path}': foreign owner detected "
                f"(expected token={held_token[:8]}..., pid={held_pid}; "
                f"found token={cur_data.get('token')!r}, pid={cur_data.get('pid')!r})."
            )
            raise ForeignLockError(msg)

        # Token matches our exact lock. Atomically move canonical directory
        # to private release tombstone
        rel_name = f".release_{held_token}_{uuid.uuid4().hex}"
        rel_path = self.parent_dir / rel_name
        try:
            self.lock_path.replace(rel_path)
        except (FileNotFoundError, OSError) as exc:
            msg = f"Failed to atomically move lock for release: {exc}"
            raise DirectoryLockError(msg) from exc

        rel_owner = rel_path / "owner.json"
        if rel_owner.exists():
            rel_owner.unlink()
        rel_path.rmdir()

    @contextmanager
    def acquire(self, timeout_seconds: float | None = None) -> Iterator[DirectoryLock]:
        """Context manager yielding the acquired DirectoryLock instance."""
        self.acquire_lock(timeout_seconds=timeout_seconds)
        try:
            yield self
        finally:
            self.release()

    def __enter__(self) -> DirectoryLock:
        self.acquire_lock()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
