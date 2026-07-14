"""
Operations OS — Registry Cache.

Loads the Operations Registry once at application startup and holds it
for the process lifetime. All shell routes call get_registry() — they
never call load_registry() directly.

Rules:
  - Thread-safe initialisation via a module-level lock.
  - No Flask imports.
  - No database connections.
  - Cache is invalidated only by process restart (YAML edits require restart).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from src.ops.registry_loader import load_registry, OperationDefinition

_lock   = threading.Lock()
_cache: dict[str, OperationDefinition] | None = None
_loaded_at: float | None = None


def get_registry(registry_dir: Path | None = None) -> dict[str, OperationDefinition]:
    """Return the loaded registry, initialising it on first call.

    Thread-safe. Subsequent calls return the cached result without
    re-parsing YAML files.
    """
    global _cache, _loaded_at
    if _cache is not None:
        return _cache

    with _lock:
        # Double-check after acquiring lock (another thread may have loaded).
        if _cache is not None:
            return _cache
        _cache = load_registry(registry_dir)
        _loaded_at = time.time()

    return _cache


def cache_loaded_at() -> float | None:
    """Timestamp when the cache was last populated, or None if not yet loaded."""
    return _loaded_at


def invalidate_cache() -> None:
    """Force the next get_registry() call to reload from disk.

    Intended for tests only. Production code should restart the process
    to pick up YAML changes.
    """
    global _cache, _loaded_at
    with _lock:
        _cache = None
        _loaded_at = None
