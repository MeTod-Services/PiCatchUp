"""Small, process-independent device status snapshots.

The JSON file is intentionally ephemeral: it describes what the device is doing
now and can always be recreated.  Writers replace it atomically so a status
command never observes half-written JSON.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)


class Status(StrEnum):
    BOOTING = "booting"
    READY = "ready"
    RECORDING = "recording"
    SAVING = "saving"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    SENDING = "sending"
    SUCCESS = "success"
    ERROR = "error"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class DeviceStatus:
    """One status update shared by the status file, LED and display."""

    status: Status
    detail: str | None = None
    elapsed_seconds: int | None = None
    battery_percent: int | None = None
    updated_at: str = field(default_factory=_timestamp)

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "status": self.status.value,
            "updated_at": self.updated_at,
        }
        if self.detail is not None:
            value["detail"] = self.detail
        if self.elapsed_seconds is not None:
            value["elapsed_seconds"] = max(0, self.elapsed_seconds)
        if self.battery_percent is not None:
            value["battery_percent"] = min(100, max(0, self.battery_percent))
        return value

    @classmethod
    def from_dict(cls, value: object) -> DeviceStatus:
        if not isinstance(value, dict):
            raise ValueError("status JSON must be an object")
        raw_status = value.get("status")
        if not isinstance(raw_status, str):
            raise ValueError("status JSON has no status")
        detail = value.get("detail")
        elapsed = value.get("elapsed_seconds")
        battery = value.get("battery_percent")
        updated = value.get("updated_at")
        if detail is not None and not isinstance(detail, str):
            raise ValueError("status detail must be text")
        if elapsed is not None and (not isinstance(elapsed, int) or isinstance(elapsed, bool)):
            raise ValueError("elapsed_seconds must be a whole number")
        if battery is not None and (not isinstance(battery, int) or isinstance(battery, bool)):
            raise ValueError("battery_percent must be a whole number")
        if updated is not None and not isinstance(updated, str):
            raise ValueError("updated_at must be text")
        return cls(
            status=Status(raw_status),
            detail=detail,
            elapsed_seconds=elapsed,
            battery_percent=battery,
            updated_at=updated or _timestamp(),
        )


class StatusFile:
    """Publish and read the latest status through one atomic JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def publish(self, snapshot: DeviceStatus | Status, *, detail: str | None = None) -> None:
        if isinstance(snapshot, Status):
            snapshot = DeviceStatus(snapshot, detail=detail)
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(snapshot.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, self.path)
        except OSError as error:
            log.warning("could not publish device status to %s: %s", self.path, error)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def read(self) -> DeviceStatus | None:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
            return DeviceStatus.from_dict(raw)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, json.JSONDecodeError) as error:
            log.warning("could not read device status from %s: %s", self.path, error)
            return None


def publish_status(path: Path, snapshot: DeviceStatus | Status) -> None:
    """Convenience wrapper for one-off publishers."""
    StatusFile(path).publish(snapshot)


def read_status(path: Path) -> DeviceStatus | None:
    """Convenience wrapper for status/doctor commands."""
    return StatusFile(path).read()
