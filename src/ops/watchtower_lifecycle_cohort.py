"""Frozen, provider-free preparation for bounded Watchtower lifecycle requests.

This module deliberately has no transport, credential, scheduler, or journal
writer.  It composes the qualified one-request identity contract into an
immutable cohort authorization for a future explicitly authorized dispatcher.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from src.ops.watchtower_lifecycle_probe import request_identity, validate_authorization


POPULATION = "WATCHTOWER_FORENSIC_POPULATION_V2_20261009"
SCHEMA = "dev014.watchtower-lifecycle-cohort-authorization.v1"
INTERVAL = "15m"
WINDOW_SECONDS = 10 * 24 * 60 * 60
MAX_SESSION = 50
MAX_COHORT_BYTES = 100 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024**3


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def attempted_lifecycle_identities(journals: Iterable[str | Path]) -> tuple[set[str], set[str]]:
    """Read compact terminal/uncertain journals as no-repeat exclusions only."""
    identities: set[str] = set()
    mints: set[str] = set()
    for raw in journals:
        path = Path(raw)
        if not path.is_file() or path.is_symlink():
            raise ValueError("LIFECYCLE_JOURNAL_UNAVAILABLE")
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("LIFECYCLE_JOURNAL_UNREADABLE") from exc
        if record.get("state") != "NEW":
            identity, mint = record.get("request_identity"), record.get("mint")
            if not isinstance(identity, str) or not identity or not isinstance(mint, str) or not mint:
                raise ValueError("LIFECYCLE_JOURNAL_INVALID")
            identities.add(identity)
            mints.add(mint)
    return identities, mints


def build_cohort_authorization(records: list[dict[str, Any]], *, chronology_hash: str, authority_id: str) -> dict[str, Any]:
    """Freeze one bounded 15m request for each independently eligible launch."""
    if not isinstance(authority_id, str) or not authority_id or not isinstance(chronology_hash, str) or not chronology_hash:
        raise ValueError("LIFECYCLE_COHORT_AUTHORITY_REQUIRED")
    normalized: list[dict[str, Any]] = []
    ranks: set[int] = set()
    mints: set[str] = set()
    identities: set[str] = set()
    for record in sorted(records, key=lambda item: item["rank"]):
        auth = {key: record[key] for key in ("rank", "mint", "creation_timestamp", "time_from", "time_to", "interval", "content_hash")}
        validate_authorization(auth)
        identity = request_identity(auth)
        if (auth["rank"] in ranks or auth["mint"] in mints or identity in identities
                or auth["interval"] != INTERVAL or auth["time_from"] != auth["creation_timestamp"]
                or auth["time_to"] != auth["time_from"] + WINDOW_SECONDS):
            raise ValueError("LIFECYCLE_COHORT_MEMBERSHIP_INVALID")
        ranks.add(auth["rank"]); mints.add(auth["mint"]); identities.add(identity)
        normalized.append({**auth, "request_identity": identity})
    if not normalized:
        raise ValueError("LIFECYCLE_COHORT_EMPTY")
    value = {
        "schema": SCHEMA,
        "population_identity": POPULATION,
        "chronology_hash": chronology_hash,
        "authority_id": authority_id,
        "records": normalized,
        "max_total_paid_requests": len(normalized),
        "max_requests_per_session": MAX_SESSION,
        "historical_limit_per_60s": 10,
        "session_cooldown_seconds": 60,
        "max_cohort_bytes": MAX_COHORT_BYTES,
        "min_free_bytes": MIN_FREE_BYTES,
        "concurrency": 1,
        "retry": "NONE_AFTER_ATTEMPT",
        "pagination": "FORBIDDEN",
        "fallback": "FORBIDDEN",
    }
    value["content_hash"] = digest(value)
    return value


def eligible_authorization_records(opening_records: Iterable[dict[str, Any]], *, blocked_identities: set[str], blocked_mints: set[str]) -> list[dict[str, Any]]:
    """Use qualified opening evidence only; exclusions are journal-derived."""
    result: list[dict[str, Any]] = []
    for source in opening_records:
        if source.get("acquisition_status") != "COMPLETED_QUALIFIED_OBSERVATION":
            continue
        rank, mint, created = source.get("rank"), source.get("mint"), source.get("creation_timestamp")
        if not isinstance(rank, int) or not isinstance(mint, str) or not mint or not isinstance(created, int) or created <= 0:
            raise ValueError("LIFECYCLE_OPENING_RECORD_INVALID")
        auth = {"rank": rank, "mint": mint, "creation_timestamp": created, "time_from": created,
                "time_to": created + WINDOW_SECONDS, "interval": INTERVAL}
        auth["content_hash"] = request_identity(auth)
        if auth["content_hash"] in blocked_identities or mint in blocked_mints:
            continue
        result.append(auth)
    return result
