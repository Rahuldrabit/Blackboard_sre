"""Append-only agent artifacts, durable even if the harness kills the process."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


class ArtifactLog:
    def __init__(self, directory: Path, model: str):
        self.directory = directory
        self.model = model
        directory.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict):
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "model": self.model, **event}
        payload = (json.dumps(event, default=json_default, ensure_ascii=False) + "\n").encode()
        paths = [self.directory / "events.jsonl"]
        kind = event.get("type", "other")
        role = re.sub(r"[^a-zA-Z0-9_-]", "_", str(event.get("role", "driver")))
        if kind.startswith("model"):
            paths.append(self.directory / "agents" / f"{role}.jsonl")
        elif kind.startswith("tool"):
            paths.append(self.directory / "tools.jsonl")
        elif kind.startswith("submission") or kind == "status":
            paths.append(self.directory / "submissions.jsonl")
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

    def snapshot(self, state):
        target = self.directory / "state.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, default=json_default, indent=2))
        temporary.replace(target)
