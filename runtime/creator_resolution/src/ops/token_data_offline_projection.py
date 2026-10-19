"""Deterministic, provider-free projection of retained token-data facts.

The caller supplies already-canonical rows and explicitly declares which keys
each overlay is allowed to contribute.  This keeps operation-specific source
adapters out of the merger and prevents a later overlay from silently changing
an unrelated qualified fact.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter


class OfflineProjectionError(ValueError):
    """A retained-input integrity or precedence violation."""


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def project_rows(*, manifest: list[dict], retained_rows: dict[str, dict], base_rows: dict[str, dict],
                 overlays: dict[str, tuple[dict[str, dict], frozenset[str]]]) -> tuple[list[dict], dict]:
    """Return ordered final rows and consumption accounting without I/O.

    A manifest member must identify one row via ``mint`` and ``sample_role``;
    retained rows take precedence for non-expanded members.  Every supplied
    overlay row is required to match exactly one expanded member and can only
    fill its explicitly whitelisted keys.
    """
    mints = [member["mint"] for member in manifest]
    duplicates = sorted(mint for mint, count in Counter(mints).items() if count > 1)
    if duplicates:
        raise OfflineProjectionError(f"duplicate manifest mints: {duplicates}")
    expanded = {m["mint"] for m in manifest if m["sample_role"] == "NEW_ACQUISITION"}
    rows = []
    used = {name: 0 for name in overlays}
    for member in manifest:
        mint = member["mint"]
        source = base_rows if mint in expanded else retained_rows
        if mint not in source:
            raise OfflineProjectionError(f"missing authoritative row: {mint}")
        row = copy.deepcopy(source[mint])
        if row.get("mint") != mint:
            raise OfflineProjectionError(f"row identity mismatch: {mint}")
        for name, (records, allowed) in overlays.items():
            record = records.get(mint)
            if record is None:
                continue
            if mint not in expanded:
                raise OfflineProjectionError(f"overlay targets retained row: {mint}")
            used[name] += 1
            for key, value in record.items():
                if key == "mint":
                    continue
                if key not in allowed:
                    raise OfflineProjectionError(f"{name} attempted unrelated field: {key}")
                old = row.get(key)
                # A scoped overlay may replace a terminal non-qualified state
                # (for example a credential-replayed provider block), but it
                # must never replace a retained qualified fact.
                if old is not None and old != value:
                    if key.endswith("_fact_state") and old != "FACT_QUALIFIED":
                        pass
                    else:
                        raise OfflineProjectionError(f"{name} conflicts at {mint}.{key}")
                row[key] = copy.deepcopy(value)
        rows.append(row)
    for name, (records, _allowed) in overlays.items():
        unexpected = sorted(set(records) - expanded)
        if unexpected:
            raise OfflineProjectionError(f"{name} has unexpected mints: {unexpected}")
        if used[name] != len(records):
            raise OfflineProjectionError(f"{name} consumption mismatch")
    return rows, {"manifest_count": len(manifest), "expanded_count": len(expanded), "overlay_consumed": used,
                  "output_digest": canonical_digest(rows)}
