"""Finite, non-textual startup evidence. Never collect stdout, stderr or exception text."""

import os
import stat
from pathlib import Path

STAGES = (
    "image_staging",
    "sandbox_launch",
    "initdb",
    "pg_start",
    "alembic",
    "python_imports",
    "fixture_seed",
    "python_start",
    "readiness_checks",
    "gateway_ready",
)
CATEGORIES = ("entered", "completed", "command_failed", "import_error", "os_error", "python_error")
MAX_BYTES = 128
SANDBOX_PATH = "/tmp/startup.status"  # noqa: S108  # Server-only file bind.


def parse_record(data: bytes) -> dict[str, str | int]:
    """Reject the entire record, including unknown fields; never return rejected input."""
    fields = data.decode("ascii").split(" ")
    if len(data) > MAX_BYTES or len(fields) != 5 or fields[0] != "v1":
        raise ValueError("invalid diagnosis")
    _, stage, last, category, number = fields
    if stage not in STAGES or last not in ("none", *STAGES) or category not in CATEGORIES:
        raise ValueError("invalid diagnosis")
    if not number.endswith("\n") or not number[:-1].isascii() or not number[:-1].isdecimal():
        raise ValueError("invalid diagnosis")
    code = int(number[:-1])
    if not 0 <= code <= 255 or number != f"{code}\n":
        raise ValueError("invalid diagnosis")
    position = STAGES.index(stage)
    expected = stage if category == "completed" else STAGES[position - 1] if position else "none"
    if last != expected or (category != "command_failed" and code != 0):
        raise ValueError("invalid diagnosis")
    return {"stage_entered": stage, "last_completed": last, "category": category, "code": code}


def write_record(stage: str, category: str = "entered", code: int = 0) -> None:
    """The trusted server writes one bounded snapshot, never appends a log."""
    position = STAGES.index(stage)
    last = stage if category == "completed" else STAGES[position - 1] if position else "none"
    data = f"v1 {stage} {last} {category} {code}\n".encode("ascii")
    parse_record(data)
    descriptor = os.open(SANDBOX_PATH, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("invalid diagnosis file")
        os.ftruncate(descriptor, 0)
        if os.write(descriptor, data) != len(data):
            raise OSError("incomplete diagnosis")
    finally:
        os.close(descriptor)


class StartupDiagnostics:
    """Pin an owner-only directory and precreated file across the child's lifetime."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "startup.status"
        self.report: dict[str, object] = {}
        self._directory = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._file = -1
        try:
            info = os.fstat(self._directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("invalid diagnosis directory")
            self._file = os.open(
                "startup.status",
                os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=self._directory,
            )
            self._identity = os.fstat(self._file)
        except BaseException:
            self.close()
            raise

    def mark(self, stage: str, category: str = "entered") -> None:
        """Host instrumentation writes through the pinned descriptor, never a reopened path."""
        position = STAGES.index(stage)
        last = stage if category == "completed" else STAGES[position - 1] if position else "none"
        data = f"v1 {stage} {last} {category} 0\n".encode("ascii")
        parse_record(data)
        os.ftruncate(self._file, 0)
        if os.pwrite(self._file, data, 0) != len(data):
            raise OSError("incomplete diagnosis")

    def read(self) -> dict[str, object]:
        """Only fixed tokens escape this boundary, even on corrupt or swapped files."""
        result: dict[str, object] = {
            "diagnosis": "missing",
            "stage_entered": "sandbox_launch",
            "last_completed": "none",
            "category": "missing_diagnosis",
            "code": None,
        }
        try:
            current = os.stat("startup.status", dir_fd=self._directory, follow_symlinks=False)
            info = os.fstat(self._file)
            if (
                (current.st_dev, current.st_ino) != (self._identity.st_dev, self._identity.st_ino)
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or info.st_size > MAX_BYTES
            ):
                raise ValueError("invalid diagnosis")
            data = os.pread(self._file, MAX_BYTES + 1, 0)
            if not data:
                return result
            record = parse_record(data)
            after = os.fstat(self._file)
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("unstable diagnosis")
            return {"diagnosis": "available", **record}
        except (OSError, ValueError, UnicodeError):
            return {**result, "diagnosis": "invalid", "category": "invalid_diagnosis"}

    def capture(self, returncode: int | None, outcome: str, settled: bool) -> None:
        record = self.read()
        self.report = {
            **record,
            "server_diagnosis": (
                "missing"
                if record["diagnosis"] != "available"
                or record["stage_entered"] == "image_staging"
                or (record["stage_entered"] == "sandbox_launch" and record["category"] == "entered")
                else "available"
            ),
            "lifecycle": outcome,
            "timed_out": outcome in ("readiness_timeout", "run_timeout", "staging_timeout"),
            "exit_status": (
                returncode if returncode is not None and 0 <= returncode <= 255 else None
            ),
            "signal": -returncode if returncode is not None and -64 <= returncode < 0 else None,
            "settled": settled,
        }

    def close(self) -> None:
        if self._file >= 0:
            os.close(self._file)
            self._file = -1
        if self._directory >= 0:
            os.close(self._directory)
            self._directory = -1
