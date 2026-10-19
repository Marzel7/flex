"""X29.4 — Known Infrastructure Spam registry.

A wallet whose primary observed behaviour is indiscriminate dust-spraying
across many recipients, rather than genuine operational funding. Receiving
unsolicited SOL from a spam wallet conveys NO information about the
recipient's identity, operation, behaviour, or intent -- a wallet has no
control over who sends it SOL. This registry therefore describes only the
SENDER, never the recipient (see wallet_quality.py for recipient-side
annotation).

This is a MANUALLY MAINTAINED registry, not an automatic classifier:
  - No automatic spam discovery is introduced by this module.
  - No heuristic classification is introduced.
  - No machine-learning or behavioural promotion is introduced.
  - Only wallets explicitly added here (after investigation + manual
    review) are ever treated as Infrastructure Spam.

Distinct from Treasury/Subprovider/Creator/Relay/Bridge/Operator (src/
utils/infra_mapping.py's INFRASTRUCTURE_ACCOUNTS/CEX_ACCOUNTS registries)
-- those represent real ecosystem infrastructure or exchanges; this
registry represents confirmed NOISE that must never become an
outcome_type, an operational classification, an attribution result, or an
operator signal (X29.4 brief).

Migrated from X29.1.3's src/utils/spam_infrastructure_registry.py, whose
INFRASTRUCTURE_SPAM outcome-type constant is REMOVED by this sprint per
the brief's explicit migration instruction -- spam is never an
outcome_type here or anywhere downstream.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

DDL = """
CREATE TABLE IF NOT EXISTS wt_known_spam_wallets (
    wallet          TEXT PRIMARY KEY,
    name            TEXT,
    classification  TEXT,
    reason          TEXT,
    evidence        TEXT,
    first_seen      INTEGER,
    last_seen       INTEGER,
    added_by        TEXT,
    added_at        INTEGER NOT NULL,
    notes           TEXT
);
"""

# Confirmed seed (X29.1.3 investigation, carried forward unchanged):
# GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc -- observed as a
# SUBPROV_TO_CREATOR funder to 110 distinct wallets in wt_provisioning_edges
# (a single wallet fanning out to that many distinct recipients, with none
# of them producing a second-tier relationship of their own, is the
# dust-spray signature, not a genuine sub-provisioner) and referenced in
# 227 wt_attribution_outcomes rows, every one of them UNKNOWN_INFRASTRUCTURE.
_SEED_WALLETS: dict[str, dict[str, Any]] = {
    "GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc": {
        "name": "Spam Dust Marker (seed)",
        "classification": "spam_dust",
        "reason": "Indiscriminate dust-spray fan-out, not genuine provisioning",
        "evidence": (
            "Observed as SUBPROV_TO_CREATOR funder to 110 distinct wallets "
            "(wt_provisioning_edges) with no confirmed downstream launches "
            "attributable to genuine provisioning; referenced in 227 "
            "wt_attribution_outcomes rows, all UNKNOWN_INFRASTRUCTURE — "
            "indiscriminate dust-spray pattern, not operational funding."
        ),
        "added_by": "X29.1.3 investigation, migrated by X29.4",
        "notes": "Migrated from src/utils/spam_infrastructure_registry.py (X29.1.3)",
    },
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def seed_known_spam_wallets(conn: sqlite3.Connection) -> int:
    """Idempotent: inserts the confirmed seed set if not already present.
    Never overwrites an existing manually-curated row (INSERT OR IGNORE)."""
    ensure_schema(conn)
    now = int(time.time())
    written = 0
    for wallet, info in _SEED_WALLETS.items():
        cur = conn.execute(
            """INSERT OR IGNORE INTO wt_known_spam_wallets
               (wallet, name, classification, reason, evidence, first_seen, last_seen, added_by, added_at, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (wallet, info["name"], info["classification"], info["reason"], info["evidence"],
             now, now, info["added_by"], now, info.get("notes")),
        )
        written += cur.rowcount
    conn.commit()
    return written


def is_known_spam_wallet(conn: Optional[sqlite3.Connection], address: Optional[str]) -> bool:
    """True only if `address` is present in the manually-maintained
    wt_known_spam_wallets registry. Never infers spam status from
    behaviour -- membership in this table is the ONLY signal."""
    if not address or conn is None:
        return False
    if not _table_exists(conn, "wt_known_spam_wallets"):
        return False
    return bool(conn.execute(
        "SELECT 1 FROM wt_known_spam_wallets WHERE wallet=?", (address,)
    ).fetchone())


def get_spam_wallet_info(conn: sqlite3.Connection, address: Optional[str]) -> Optional[dict[str, Any]]:
    if not address or not _table_exists(conn, "wt_known_spam_wallets"):
        return None
    row = conn.execute(
        "SELECT * FROM wt_known_spam_wallets WHERE wallet=?", (address,)
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM wt_known_spam_wallets LIMIT 0").description]
    return dict(zip(cols, row))


def confirmed_spam_addresses(conn: sqlite3.Connection) -> list[str]:
    """All confirmed Infrastructure Spam addresses, for analytics/lookups
    that need the full set rather than a single-address check."""
    if not _table_exists(conn, "wt_known_spam_wallets"):
        return []
    return [r[0] for r in conn.execute("SELECT wallet FROM wt_known_spam_wallets").fetchall()]
