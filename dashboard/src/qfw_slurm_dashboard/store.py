"""Durable dashboard operation, experiment, event, and audit state."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .models import Experiment, Operation, utc_now
from .redaction import redact, redact_payload


class DashboardStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()

    def _write(self, relative: str, payload: dict[str, Any]) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".new")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _read_all(self, directory: str) -> list[dict[str, Any]]:
        path = self.root / directory
        if not path.exists():
            return []
        values = []
        for item in sorted(path.glob("*.json")):
            try:
                value = json.loads(item.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                values.append(value)
        return values

    def save_operation(self, operation: Operation) -> None:
        with self._lock:
            operation.output = [redact(line)[-4000:] for line in operation.output[-1000:]]
            self._write(f"operations/{operation.operation_id}.json", operation.payload())

    def operations(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_all("operations")

    def save_experiment(self, experiment: Experiment) -> None:
        with self._lock:
            experiment.modified_at = utc_now()
            payload = redact_payload(experiment.payload())
            self._write(
                f"experiments/{experiment.experiment_id}.json", payload
            )

    def experiments(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_all("experiments")

    def append_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            path = self.root / "events.jsonl"
            payload = {"schema": "qfw-dashboard-event-v1", "timestamp": utc_now(), **event}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(redact(json.dumps(payload, sort_keys=True)) + "\n")
            os.chmod(path, 0o600)

    def events(
        self, *, cursor: int = 0, limit: int = 500, identity: str = "root"
    ) -> dict[str, Any]:
        path = self.root / "events.jsonl"
        if not path.exists():
            return {"events": [], "cursor": 0, "gap": False}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, int(cursor))
        gap = start < max(0, len(lines) - 10000)
        if gap:
            start = max(0, len(lines) - 10000)
        selected = lines[start : start + max(1, min(limit, 1000))]
        events = []
        for line in selected:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_identity = str(event.get("identity", ""))
            if identity == "root" or not event_identity or event_identity == identity:
                events.append(event)
        return {"events": events, "cursor": start + len(selected), "gap": gap}

    def audit(self, event: dict[str, Any]) -> None:
        self.append_event({"kind": "audit", **event})

    def clear_dashboard_state(self) -> dict[str, int]:
        """Remove Dashboard-owned history without touching external artifacts."""
        with self._lock:
            counts = {"operations": 0, "experiments": 0, "events": 0}
            for category in ("operations", "experiments"):
                directory = self.root / category
                if not directory.exists():
                    continue
                for item in directory.glob("*.json"):
                    item.unlink(missing_ok=True)
                    counts[category] += 1
            events = self.root / "events.jsonl"
            if events.exists():
                with events.open(encoding="utf-8", errors="replace") as stream:
                    counts["events"] = sum(1 for _ in stream)
                events.unlink()
            return counts
