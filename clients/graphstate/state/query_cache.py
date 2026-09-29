"""
Query cache — prevents redundant tool calls across agent invocations.

One of the key token-efficiency mechanisms:
If Agent A already called `kubectl get pods -n payment` and Agent B would
call the exact same thing within its freshness window, reuse the cached
result rather than incurring another tool latency + token overhead.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class QueryCache:
    """In-memory cache for deterministic read-only tool results."""

    def __init__(self, default_ttl_seconds: float = 60.0):
        self._default_ttl = default_ttl_seconds
        self._cache: dict[str, dict[str, Any]] = {}
        self._hits: int = 0
        self._misses: int = 0

    @staticmethod
    def _compute_key(tool_name: str, params: dict[str, Any]) -> str:
        serialized = json.dumps(
            {"tool": tool_name, "params": params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, tool_name: str, params: dict[str, Any]) -> Any | None:
        key = self._compute_key(tool_name, params)
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        now = datetime.now(timezone.utc).timestamp()
        if now - entry["cached_at"] > entry["ttl"]:
            # Expired
            del self._cache[key]
            self._misses += 1
            return None

        self._hits += 1
        return entry["result"]

    def set(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        ttl_seconds: float | None = None,
    ) -> None:
        key = self._compute_key(tool_name, params)
        self._cache[key] = {
            "result": result,
            "cached_at": datetime.now(timezone.utc).timestamp(),
            "ttl": ttl_seconds if ttl_seconds is not None else self._default_ttl,
            "tool_name": tool_name,
        }

    def invalidate(self, tool_name: str | None = None) -> int:
        if tool_name is None:
            count = len(self._cache)
            self._cache.clear()
            return count

        to_delete = [
            k for k, v in self._cache.items() if v.get("tool_name") == tool_name
        ]
        for k in to_delete:
            del self._cache[k]
        return len(to_delete)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": (
                self._hits / (self._hits + self._misses)
                if (self._hits + self._misses) > 0
                else 0.0
            ),
        }
