"""Single-host generation admission with a durable uncertain-call fence.

A process lock alone cannot bound upstream work after SIGKILL. Nonempty state
survives its owner and requires a verified provider restart before reuse.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any


class SlotError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GenerationSlot:
    def __init__(self, path: Path, *, recovery: bool = False) -> None:
        self.path = path
        self.recovery = recovery
        self.descriptor: int | None = None

    def __enter__(self) -> GenerationSlot:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise SlotError("generation_slot_unsafe") from exc
        self.descriptor = fd
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                raise SlotError("generation_slot_unsafe")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SlotError("generation_slot_busy") from exc
            if os.fstat(fd).st_size and not self.recovery:
                raise SlotError("generation_reconciliation_required")
            return self
        except BaseException:
            os.close(fd)
            self.descriptor = None
            raise

    def mark_dispatch(self, provider: str) -> None:
        assert self.descriptor is not None
        state = json.dumps(
            {"version": 1, "provider": provider, "started_at": datetime.now(UTC).isoformat()}
        ).encode()
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        written = os.write(self.descriptor, state)
        if written != len(state):
            raise SlotError("generation_slot_write_failed")
        os.ftruncate(self.descriptor, len(state))
        os.fsync(self.descriptor)
        parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def state(self) -> dict[str, Any]:
        assert self.descriptor is not None
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        raw = os.read(self.descriptor, 4097)
        try:
            state = json.loads(raw)
            if not isinstance(state, dict) or len(raw) > 4096 or state.get("version") != 1:
                raise ValueError
            return state
        except (ValueError, UnicodeError) as exc:
            raise SlotError("generation_slot_state_invalid") from exc

    def complete(self) -> None:
        """Only after an upstream terminal response, or verified provider restart."""
        assert self.descriptor is not None
        os.ftruncate(self.descriptor, 0)
        os.fsync(self.descriptor)

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
