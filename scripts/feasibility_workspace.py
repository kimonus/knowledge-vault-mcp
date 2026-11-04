"""Owner-checked temporary workspace for the synthetic feasibility prototype."""

import fcntl
import os
import secrets
import shutil
import tempfile
from pathlib import Path


class WorkspaceError(Exception):
    """Workspace ownership or lifecycle could not be verified."""


class Workspace:
    """Keep an exclusive lock for the lifetime of an owned temporary workspace."""

    def __init__(self, root: Path, owner: str, descriptor: int) -> None:
        self.root = root
        self.owner = owner
        self._descriptor = descriptor

    @classmethod
    def create(cls, parent: Path) -> "Workspace":
        root = Path(tempfile.mkdtemp(prefix="kv-feasibility-", dir=parent)).resolve()
        owner = secrets.token_hex(16)
        try:
            marker = root / "owner"
            marker.write_text(owner, encoding="ascii")
            marker.chmod(0o600)
            descriptor = os.open(root / "lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            shutil.rmtree(root)
            raise
        return cls(root, owner, descriptor)

    def cleanup(self) -> None:
        if self._descriptor < 0:
            raise WorkspaceError("workspace is already closed")
        _validate(self.root, self.owner)
        _require_settled(self.root)
        try:
            shutil.rmtree(self.root)
        finally:
            os.close(self._descriptor)
            self._descriptor = -1

    def mark_launched(self) -> None:
        """Persist uncertainty before spawning any sandbox child."""
        if self._descriptor < 0:
            raise WorkspaceError("workspace is already closed")
        descriptor = os.open(
            self.root / "child-unsettled", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def mark_settled(self) -> None:
        """Call only after the runner has reaped all of its sandbox children."""
        if self._descriptor < 0:
            raise WorkspaceError("workspace is already closed")
        (self.root / "child-unsettled").unlink()


def _require_settled(root: Path) -> None:
    if (root / "child-unsettled").exists() or (root / "child-unsettled").is_symlink():
        raise WorkspaceError("sandbox child may still be active; manual recovery required")


def _validate(root: Path, owner: str) -> None:
    if len(owner) != 32 or any(c not in "0123456789abcdef" for c in owner):
        raise WorkspaceError("invalid owner")
    if root.is_symlink() or not root.is_dir() or not root.name.startswith("kv-feasibility-"):
        raise WorkspaceError("invalid workspace")
    info = root.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise WorkspaceError("workspace owner or permissions mismatch")
    marker = root / "owner"
    if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="ascii") != owner:
        raise WorkspaceError("workspace owner mismatch")


def recover(root: Path, owner: str) -> None:
    """Remove a verified abandoned workspace, never one whose lock is held."""
    _validate(root, owner)
    try:
        descriptor = os.open(root / "lock", os.O_RDWR | os.O_NOFOLLOW)
    except OSError as exc:
        raise WorkspaceError("workspace lock unavailable") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError("workspace is active") from exc
        _validate(root, owner)
        _require_settled(root)
        shutil.rmtree(root)
    finally:
        os.close(descriptor)
