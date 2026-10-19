"""X29.4 — Wallet Quality: environmental annotations, orthogonal to identity.

Core principle (from the brief): receiving SOL from a spam wallet is not
evidence -- it is environmental noise. A wallet has no control over who
sends it SOL. Therefore Wallet Quality annotations:
  - never become an outcome_type
  - never become an operational classification
  - never become an attribution result
  - never become an operator signal
  - never influence Operational Attribution, Funding Boundary, Funding
    Topology, Operational Behaviour, or Funding Mechanism

This table is orthogonal to every other intelligence dimension. It is
consulted by walkback (to skip known-spam funders) and by the UI (to show
a purely informational "Wallet Quality" section), and by nothing else.

spam_sender/spam_recipient distinction:
  spam_sender    -- this wallet is ITSELF a confirmed entry in
                     wt_known_spam_wallets (the sender side).
  spam_recipient -- this wallet has RECEIVED a transfer from a confirmed
                     spam sender. This is purely descriptive: it says
                     nothing about the recipient's own identity, operation,
                     or intent (a WATCHTOWER treasury or a Coinbase wallet
                     can both be a spam_recipient without that changing
                     what they ARE).

Zero automatic promotion: unknown wallets sending unsolicited SOL are
never classified as spam by this module -- only wt_known_spam_wallets
membership (a manual process) ever sets spam_sender=true.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

from src.ops.known_spam_wallets import is_known_spam_wallet

DDL = """
CREATE TABLE IF NOT EXISTS wt_wallet_quality (
    wallet                      TEXT PRIMARY KEY,
    spam_sender                 INTEGER NOT NULL DEFAULT 0 CHECK(spam_sender IN (0,1)),
    spam_recipient               INTEGER NOT NULL DEFAULT 0 CHECK(spam_recipient IN (0,1)),
    dust_marker                 INTEGER NOT NULL DEFAULT 0 CHECK(dust_marker IN (0,1)),
    dust_recipient              INTEGER NOT NULL DEFAULT 0 CHECK(dust_recipient IN (0,1)),
    high_unsolicited_inbound    INTEGER NOT NULL DEFAULT 0 CHECK(high_unsolicited_inbound IN (0,1)),
    confidence                  REAL,
    first_seen                  INTEGER,
    last_seen                   INTEGER,
    metadata                    TEXT,
    created_at                  INTEGER NOT NULL,
    updated_at                  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wallet_quality_spam_sender ON wt_wallet_quality(spam_sender);
CREATE INDEX IF NOT EXISTS ix_wallet_quality_spam_recipient ON wt_wallet_quality(spam_recipient);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _upsert_flag(conn: sqlite3.Connection, wallet: str, field: str, seen_at: Optional[int] = None) -> None:
    """Idempotent flag-set: creates the row if absent, sets `field`=1,
    updates first_seen/last_seen. Never clears a flag once set (annotations
    only accumulate evidence of past behaviour, never retract it)."""
    now = int(time.time())
    seen_at = seen_at if seen_at is not None else now
    assert field in {
        "spam_sender", "spam_recipient", "dust_marker",
        "dust_recipient", "high_unsolicited_inbound",
    }
    existing = conn.execute(
        "SELECT wallet, first_seen, last_seen FROM wt_wallet_quality WHERE wallet=?", (wallet,)
    ).fetchone()
    if existing:
        first_seen = existing[1] if existing[1] is not None else seen_at
        last_seen = max(existing[2] or seen_at, seen_at)
        conn.execute(
            f"UPDATE wt_wallet_quality SET {field}=1, first_seen=?, last_seen=?, updated_at=? WHERE wallet=?",
            (first_seen, last_seen, now, wallet),
        )
    else:
        conn.execute(
            f"""INSERT INTO wt_wallet_quality
                (wallet, {field}, first_seen, last_seen, created_at, updated_at)
                VALUES (?,1,?,?,?,?)""",
            (wallet, seen_at, seen_at, now, now),
        )


def mark_spam_sender(conn: sqlite3.Connection, wallet: str, *, seen_at: Optional[int] = None) -> None:
    """Only called for wallets already confirmed in wt_known_spam_wallets --
    this function does not itself decide spam status, it only records the
    annotation for a wallet the registry already confirmed."""
    ensure_schema(conn)
    _upsert_flag(conn, wallet, "spam_sender", seen_at)
    conn.commit()


def mark_spam_recipient(conn: sqlite3.Connection, wallet: str, *, seen_at: Optional[int] = None) -> None:
    """Records that `wallet` received a transfer from a confirmed spam
    sender. Does NOT classify, attribute, or create any operational
    relationship -- purely descriptive of an observed inbound transfer."""
    ensure_schema(conn)
    _upsert_flag(conn, wallet, "spam_recipient", seen_at)
    conn.commit()


def record_spam_transfer(conn: sqlite3.Connection, sender: str, recipient: str, *, seen_at: Optional[int] = None) -> None:
    """Convenience: given a transfer already known to originate from a
    confirmed spam sender, annotate BOTH sides — sender as spam_sender
    (reaffirming registry membership) and recipient as spam_recipient.
    Never creates attribution, operator identity, or funding-boundary
    evidence for either wallet."""
    mark_spam_sender(conn, sender, seen_at=seen_at)
    mark_spam_recipient(conn, recipient, seen_at=seen_at)


def get_wallet_quality(conn: sqlite3.Connection, wallet: str) -> Optional[dict[str, Any]]:
    """Read-only lookup — the UI/analytics consumption path."""
    if not wallet or not _table_exists(conn, "wt_wallet_quality"):
        return None
    row = conn.execute("SELECT * FROM wt_wallet_quality WHERE wallet=?", (wallet,)).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM wt_wallet_quality LIMIT 0").description]
    return dict(zip(cols, row))


def serialize_wallet_quality(record: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """API/UI shape — purely informational booleans, never an identity or
    attribution field."""
    if not record:
        return None
    return {
        "spam_sender": bool(record.get("spam_sender")),
        "spam_recipient": bool(record.get("spam_recipient")),
        "dust_marker": bool(record.get("dust_marker")),
        "dust_recipient": bool(record.get("dust_recipient")),
        "high_unsolicited_inbound": bool(record.get("high_unsolicited_inbound")),
        "confidence": record.get("confidence"),
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
    }


def is_environmental_noise_only(record: Optional[dict[str, Any]]) -> bool:
    """True if the ONLY signal on this wallet is spam-adjacency (sender or
    recipient) with no other quality flags — a structural guard used by
    tests to confirm a wallet's identity fields (attribution/topology/
    behaviour) remain untouched regardless of this annotation."""
    if not record:
        return False
    return bool(record.get("spam_sender")) or bool(record.get("spam_recipient"))
