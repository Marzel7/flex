"""X24.9 — Subscription Target Validation & Watchlist Integrity.

Audits every persistent table that feeds a websocket-subscription source
(treasury, session subprov, promoted subprov / WS_PROMOTE_DISCOVERED, dust
marker, CDC) for malformed pubkeys, duplicates, and disabled/orphaned rows.

Read-only. Never mutates production data — see recommend_remediation() for
suggested (not applied) fixes, per the sprint's explicit no-auto-mutation
constraint.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from typing import TypedDict

from src.utils.pubkey_validation import is_valid_pubkey, invalid_reason


class SourceAudit(TypedDict):
    source: str
    table: str
    total_rows: int
    valid: int
    invalid: int
    invalid_wallets: list[dict]
    duplicates: int
    disabled: int


# One entry per subscription source (Phase 2's inventory). Each maps a source name
# to the table + query needed to enumerate every wallet that table could feed into
# SubscriptionManager.subscribe(), plus how to detect a "disabled" row for that
# source (None if the source has no disable concept).
_SOURCES = {
    "treasury": {
        "table": "wt_confirmed_treasuries",
        "wallet_col": "treasury",
        "disabled_expr": None,
    },
    "session_subprov": {
        "table": "wt_active_subprov_sessions",
        "wallet_col": "subprov_wallet",
        "disabled_expr": "state != 'ACTIVE'",
    },
    "promoted_subprov": {
        "table": "wt_discovered_subprovs",
        "wallet_col": "subprov",
        "disabled_expr": "treasury_known != 1",
    },
    "dust": {
        "table": "wt_dust_markers",
        "wallet_col": "wallet",
        "disabled_expr": "active = 0",
    },
    "cdc": {
        "table": "wt_capital_distributor_candidates",
        "wallet_col": "wallet",
        "disabled_expr": "observation_state != 'SUBSCRIBED'",
    },
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def audit_source(conn: sqlite3.Connection, source: str) -> SourceAudit:
    """Audit one subscription source. Raises KeyError for an unknown source name."""
    spec = _SOURCES[source]
    table, wallet_col, disabled_expr = spec["table"], spec["wallet_col"], spec["disabled_expr"]

    if not _table_exists(conn, table):
        return SourceAudit(source=source, table=table, total_rows=0, valid=0,
                            invalid=0, invalid_wallets=[], duplicates=0, disabled=0)

    rows = conn.execute(f"SELECT {wallet_col} FROM {table}").fetchall()
    wallets = [r[0] for r in rows]
    total_rows = len(wallets)

    counts = Counter(wallets)
    duplicates = sum(c - 1 for c in counts.values() if c > 1)

    invalid_wallets = []
    valid = 0
    for w in set(wallets):
        if is_valid_pubkey(w):
            valid += 1
        else:
            invalid_wallets.append({
                "wallet": w,
                "reason": invalid_reason(w),
                "occurrences": counts[w],
            })
    invalid = len(invalid_wallets)

    disabled = 0
    if disabled_expr is not None:
        disabled = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {disabled_expr}").fetchone()[0]

    return SourceAudit(
        source=source, table=table, total_rows=total_rows,
        valid=valid, invalid=invalid, invalid_wallets=invalid_wallets,
        duplicates=duplicates, disabled=disabled,
    )


def audit_all_sources(conn: sqlite3.Connection) -> dict[str, SourceAudit]:
    """Phase 2/3/6 inventory: every known subscription source, read-only."""
    return {source: audit_source(conn, source) for source in _SOURCES}


def startup_validation_summary(conn: sqlite3.Connection) -> dict:
    """Phase 3 — the totals surfaced through health metrics at process startup.
    Never silently drops a failure: every invalid/duplicate/disabled row is
    counted and attributable back to its source."""
    per_source = audit_all_sources(conn)
    return {
        "total_valid": sum(a["valid"] for a in per_source.values()),
        "total_invalid": sum(a["invalid"] for a in per_source.values()),
        "total_duplicates": sum(a["duplicates"] for a in per_source.values()),
        "total_disabled": sum(a["disabled"] for a in per_source.values()),
        "invalid_by_source": {s: a["invalid"] for s, a in per_source.items()},
        "per_source": per_source,
    }


def recommend_remediation(conn: sqlite3.Connection) -> list[dict]:
    """Phase 7 — explicit, human-readable recommendations. Read-only: produces
    suggestions only, never applies them (no UPDATE/DELETE anywhere in this module)."""
    recs = []
    for source, audit in audit_all_sources(conn).items():
        for iw in audit["invalid_wallets"]:
            recs.append({
                "source": source,
                "table": audit["table"],
                "wallet": iw["wallet"],
                "reason": iw["reason"],
                "occurrences": iw["occurrences"],
                "recommendation": (
                    "Disable row (set active=0 / equivalent) or correct the address; "
                    "malformed pubkeys can never receive a websocket acknowledgement "
                    "and will retry/exhaust indefinitely if left active."
                ),
            })
    return recs
