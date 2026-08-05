"""X74.1A — Remove validation-contamination rows from mutable identity state.

During X74.1's own validation, a governance-service call resolved to the
LIVE ops database before test isolation was corrected, writing 3 real
TREASURY_ADDED events to the immutable operator_identity_events log
(event_ids 3fbc01eb..., a5ad09cb..., f13edc19...). Those events cannot and
must not be deleted — the immutability triggers correctly prevent it, and
that is working as designed.

Two of those events (3fbc01eb, a5ad09cb) targeted the same treasury address
and left no surviving asset/entity row (cleaned up during X74.1 itself).
The third (f13edc19) left one surviving row in each of two MUTABLE tables
that do not carry immutability triggers: operator_identity_assets and
operator_entities. Those rows inflate WATCHTOWER's current treasury asset
count without representing any genuine analyst decision — the treasury in
question (3zhkoGJNPdtftnRG7dPTpfRecDE6YZ6fbb9hSF24gi2S) is still
PENDING_REVIEW in wt_treasury_review, was never added to
wt_confirmed_treasuries, and has zero references from launch membership,
wrap-close lineage, or attribution outcomes.

This script deletes exactly those 2 rows, identified by primary key, after
re-verifying their provenance and lack of real-world references at run
time. It touches only operator_identity_assets and operator_entities. It
never touches operator_identity_events (immutable, untouched) or any other
table (wt_confirmed_treasuries, wt_treasury_review, wt_watchtower_launches,
attribution/discovery/reconciliation tables, walkback tables).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.db import OPS_DB_PATH

WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"
CONTAMINATING_EVENT_ID = "f13edc19-50b5-495e-b925-cc8ab61a0f26"
CONTAMINATING_TREASURY = "3zhkoGJNPdtftnRG7dPTpfRecDE6YZ6fbb9hSF24gi2S"
KNOWN_TEST_EVENT_IDS = (
    "3fbc01eb-4590-47d1-a1ce-52d17173ad00",
    "a5ad09cb-cca9-46b4-b057-de5e60d22f95",
    CONTAMINATING_EVENT_ID,
)


def _die(msg: str) -> None:
    print(f"[X74.1A] ABORT — {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    conn = sqlite3.connect(OPS_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    # ── Re-verify provenance at run time — never trust a stale snapshot ──
    event = conn.execute(
        "SELECT event_id, operator_id, event_type, analyst, reason, evidence_revision, "
        "timestamp, payload_json FROM operator_identity_events WHERE event_id=?",
        (CONTAMINATING_EVENT_ID,),
    ).fetchone()
    if not event:
        _die(f"immutable event {CONTAMINATING_EVENT_ID} not found — nothing to reference, stopping")
    if event["analyst"] != "test-analyst" or event["event_type"] != "TREASURY_ADDED":
        _die(f"event {CONTAMINATING_EVENT_ID} does not match expected test-analyst/TREASURY_ADDED shape — stopping")
    if CONTAMINATING_TREASURY not in (event["payload_json"] or ""):
        _die(f"event {CONTAMINATING_EVENT_ID} payload does not reference {CONTAMINATING_TREASURY} — stopping")

    # confirm all 3 known test events are present and untouched (sanity, no write)
    present = {r["event_id"] for r in conn.execute(
        f"SELECT event_id FROM operator_identity_events WHERE event_id IN "
        f"({','.join('?' * len(KNOWN_TEST_EVENT_IDS))})", KNOWN_TEST_EVENT_IDS,
    ).fetchall()}
    if present != set(KNOWN_TEST_EVENT_IDS):
        _die(f"expected all 3 known test events present, found {present} — stopping")

    # the target rows, identified by the event_id foreign key
    asset = conn.execute(
        "SELECT asset_id, operator_id, asset_type, asset_value, status, evidence_revision, added_at, event_id "
        "FROM operator_identity_assets WHERE event_id=?",
        (CONTAMINATING_EVENT_ID,),
    ).fetchone()
    if not asset:
        _die(f"no operator_identity_assets row references event {CONTAMINATING_EVENT_ID} — nothing to remove")
    if asset["asset_value"] != CONTAMINATING_TREASURY or asset["operator_id"] != WATCHTOWER_OPERATOR_ID:
        _die("asset row does not match expected treasury/operator — stopping")

    entity = conn.execute(
        "SELECT operator_id, entity_address, entity_type, confidence, evidence_count, first_seen, last_seen, added_at "
        "FROM operator_entities WHERE entity_address=? AND operator_id=?",
        (CONTAMINATING_TREASURY, WATCHTOWER_OPERATOR_ID),
    ).fetchone()

    # ── Live reference checks — must all be negative before deleting ──
    if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (CONTAMINATING_TREASURY,)).fetchone():
        _die("treasury IS in wt_confirmed_treasuries — this would be a real confirmation, stopping")
    review = conn.execute(
        "SELECT status FROM wt_treasury_review WHERE treasury=?", (CONTAMINATING_TREASURY,)
    ).fetchone()
    if review and review["status"] not in ("PENDING_REVIEW",):
        _die(f"wt_treasury_review status is {review['status']!r}, not PENDING_REVIEW — a real decision may exist, stopping")
    if conn.execute(
        "SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE lineage_source_treasury=?", (CONTAMINATING_TREASURY,)
    ).fetchone()[0]:
        _die("wrap-close lineage references this treasury — stopping")
    if conn.execute(
        "SELECT COUNT(*) FROM wt_discovered_subprovs WHERE treasury=?", (CONTAMINATING_TREASURY,)
    ).fetchone()[0]:
        _die("discovered-subprov lineage references this treasury — stopping")
    if conn.execute(
        "SELECT COUNT(*) FROM wt_attribution_outcomes WHERE evidence_json LIKE ?",
        (f"%{CONTAMINATING_TREASURY}%",),
    ).fetchone()[0]:
        _die("attribution outcomes reference this treasury — stopping")
    if conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (WATCHTOWER_OPERATOR_ID,)
    ).fetchone()[0]:
        # not itself disqualifying, but surfaced for the audit record
        print("[X74.1A] note: WATCHTOWER has launch memberships (unrelated to this asset) — not blocking")

    before_count = conn.execute(
        "SELECT COUNT(*) FROM operator_identity_assets WHERE operator_id=? AND asset_type='TREASURY' AND status='ACTIVE'",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()[0]

    print("[X74.1A] Verified — deleting exactly 2 mutable rows:")
    print(f"  operator_identity_assets.asset_id = {asset['asset_id']}")
    if entity:
        print(f"  operator_entities (operator_id={WATCHTOWER_OPERATOR_ID}, entity_address={CONTAMINATING_TREASURY})")
    print(f"  Reason: mutable relationship rows originated from validation event {CONTAMINATING_EVENT_ID}")
    print(f"  Immutable audit preserved: {', '.join(KNOWN_TEST_EVENT_IDS)}")

    conn.execute("DELETE FROM operator_identity_assets WHERE asset_id=?", (asset["asset_id"],))
    deleted_entity = conn.execute(
        "DELETE FROM operator_entities WHERE entity_address=? AND operator_id=?",
        (CONTAMINATING_TREASURY, WATCHTOWER_OPERATOR_ID),
    ).rowcount
    conn.commit()

    after_count = conn.execute(
        "SELECT COUNT(*) FROM operator_identity_assets WHERE operator_id=? AND asset_type='TREASURY' AND status='ACTIVE'",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()[0]

    still_present = {r["event_id"] for r in conn.execute(
        f"SELECT event_id FROM operator_identity_events WHERE event_id IN "
        f"({','.join('?' * len(KNOWN_TEST_EVENT_IDS))})", KNOWN_TEST_EVENT_IDS,
    ).fetchall()}
    if still_present != set(KNOWN_TEST_EVENT_IDS):
        _die("POST-CHECK FAILED: an immutable event went missing after cleanup — this should be impossible")

    print(f"[X74.1A] operator_identity_assets rows deleted: 1")
    print(f"[X74.1A] operator_entities rows deleted: {deleted_entity}")
    print(f"[X74.1A] WATCHTOWER ACTIVE treasury asset count: before={before_count} after={after_count}")
    print("[X74.1A] Immutable operator_identity_events: unchanged, all 3 known test events still present.")
    conn.close()


if __name__ == "__main__":
    main()
