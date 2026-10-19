"""
Knowledge Layer — address file loader.

Loads the version-controlled YAML knowledge files once at startup and caches
them in memory. No runtime editing. No database. No CRUD.

The loader is the only component that reads the knowledge/ directory.
Every other part of the Knowledge Layer calls get_address_table() and
get_all_known_addresses().

Design:
  - Tolerates empty files (entries: []) without error.
  - Tolerates missing files without error (returns empty set).
  - Validates YAML structure; logs warnings on bad entries but does not crash.
  - Thread-safe: double-checked lock on the module-level cache.
"""

from __future__ import annotations

import os
import threading
import time
from typing import NamedTuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]  — handled at load time

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_KNOWLEDGE_DIR = os.environ.get(
    "KNOWLEDGE_DIR",
    os.path.join(_REPO, "knowledge"),
)

_ADDRESS_FILES = {
    "cex":      "addresses/cex.yaml",
    "relay":    "addresses/relay.yaml",
    "jito":     "addresses/jito.yaml",
    "bundlers": "addresses/bundlers.yaml",
}


# ── Address entry ─────────────────────────────────────────────────────────────

class AddressEntry(NamedTuple):
    address:    str
    label:      str
    confidence: str
    source:     str
    family:     str       # which file this came from (cex, relay, jito, bundlers)
    file_path:  str       # full path — used as provenance string


# ── Module-level cache ────────────────────────────────────────────────────────

_cache_lock   = threading.Lock()
_cache: dict[str, list[AddressEntry]] | None = None   # family → entries
_cache_index: dict[str, AddressEntry]        | None = None   # address → entry
_loaded_at:   float | None = None


def _load_file(family: str, rel_path: str) -> list[AddressEntry]:
    """Load one address YAML file. Returns empty list on any problem."""
    full_path = os.path.join(_KNOWLEDGE_DIR, rel_path)

    if not os.path.exists(full_path):
        return []

    if yaml is None:
        raise ImportError(
            "PyYAML is required for the Knowledge Layer. "
            "Install it with: pip install pyyaml"
        )

    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        print(f"[KNOWLEDGE] WARNING: could not parse {full_path}: {exc}")
        return []

    if data is None:
        return []  # empty file

    raw_entries = data.get("entries") or []
    if not isinstance(raw_entries, list):
        print(f"[KNOWLEDGE] WARNING: {full_path}: 'entries' must be a list")
        return []

    results: list[AddressEntry] = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            print(f"[KNOWLEDGE] WARNING: {full_path} entry[{i}] is not a mapping — skipped")
            continue
        address    = raw.get("address", "").strip()
        label      = raw.get("label", "").strip()
        confidence = raw.get("confidence", "UNKNOWN").strip().upper()
        source     = raw.get("source", "").strip()

        if not address:
            print(f"[KNOWLEDGE] WARNING: {full_path} entry[{i}] missing 'address' — skipped")
            continue

        results.append(AddressEntry(
            address=address,
            label=label,
            confidence=confidence,
            source=source,
            family=family,
            file_path=full_path,
        ))

    return results


def _build_cache() -> tuple[dict[str, list[AddressEntry]], dict[str, AddressEntry]]:
    by_family: dict[str, list[AddressEntry]] = {}
    index:     dict[str, AddressEntry]       = {}

    for family, rel_path in _ADDRESS_FILES.items():
        entries = _load_file(family, rel_path)
        by_family[family] = entries
        for entry in entries:
            # Later families do not overwrite earlier ones for the same address.
            if entry.address not in index:
                index[entry.address] = entry

    return by_family, index


def _ensure_loaded() -> None:
    global _cache, _cache_index, _loaded_at
    if _cache is not None:
        return
    with _cache_lock:
        if _cache is not None:
            return
        by_family, index = _build_cache()
        _cache       = by_family
        _cache_index = index
        _loaded_at   = time.time()
        total = sum(len(v) for v in by_family.values())
        print(f"[KNOWLEDGE] Loaded {total} address entries from {_KNOWLEDGE_DIR}")


# ── Public API ────────────────────────────────────────────────────────────────

def get_address_table(family: str) -> list[AddressEntry]:
    """Return all entries for a specific family (cex, relay, jito, bundlers)."""
    _ensure_loaded()
    return list(_cache.get(family, []))  # type: ignore[union-attr]


def lookup_address(address: str) -> AddressEntry | None:
    """Look up a single address across all families. O(1) dict lookup."""
    _ensure_loaded()
    return _cache_index.get(address)  # type: ignore[union-attr]


def get_all_known_addresses() -> dict[str, AddressEntry]:
    """Return the full address → entry index."""
    _ensure_loaded()
    return dict(_cache_index)  # type: ignore[union-attr]


def loaded_at() -> float | None:
    """Return the unix timestamp when the cache was built, or None if not yet loaded."""
    return _loaded_at


def invalidate_cache() -> None:
    """Force cache reload on next access. For tests only."""
    global _cache, _cache_index, _loaded_at
    with _cache_lock:
        _cache       = None
        _cache_index = None
        _loaded_at   = None
