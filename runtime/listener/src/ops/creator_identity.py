"""X64.8 creator identity and disposable-score intelligence.

Read-only and RPC-free. Classification is launch-specific but uses the
creator's persisted launch history across token_analysis.

X67.38 -- Creator Identity answers exactly one question: has this creator
launched before THIS launch? Two mutually exclusive, collectively
exhaustive categories:

  FRESH_CREATOR    previous_launch_count == 0 (no launch strictly earlier
                    than this one is known for this creator)
  EXISTING_CREATOR previous_launch_count >= 1

Removed: SINGLE_USE_CREATOR, REPEAT_CREATOR, RETURNING_CREATOR,
DORMANT_REACTIVATED, UNKNOWN_CREATOR_IDENTITY. These mixed identity with
historical activity pattern (how often/recently a creator has launched)
and evidence quality (whether history could be computed at all) into a
single classification. That information is not deleted -- launch_count,
prior_launch_gap_seconds, and creator_age_seconds remain on every record
exactly as before, so a SEPARATE additive behavioural classification
(e.g. "Reactivated", "Burst Launcher", "Serial Creator" -- out of this
task's scope) can still be built from the same facts. A creator can be
Identity=EXISTING_CREATOR and Behaviour=Reactivated simultaneously,
rather than "Dormant/Reactivated" being forced to stand in for both.

Every launch always resolves to exactly one of the two categories --
there is no longer an UNKNOWN identity. A creator whose history could not
be computed (missing creator, or the HISTORY_ROW_CAP guard below) is a
DATA-QUALITY condition, not an identity: it is recorded via
creator_identity_skip_reason (already-existing field, unchanged) while
creator_identity itself still resolves to FRESH_CREATOR or
EXISTING_CREATOR using whatever launch_count is actually known (0 in the
skip case, since no history was fetched -- this is the same "treat
unknown as no confirmed prior launches" convention classify_creator_
identity already used for a missing creator before this change).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from src.ops.operational_behaviour_tags import _parse_token_analysis_timestamp

FRESH_CREATOR = "FRESH_CREATOR"
EXISTING_CREATOR = "EXISTING_CREATOR"

IDENTITY_ORDER = (FRESH_CREATOR, EXISTING_CREATOR)
IDENTITY_LABELS = {
    FRESH_CREATOR: "Fresh Creator",
    EXISTING_CREATOR: "Existing Creator",
}

FRESH_MAX_AGE_SECONDS = 86400
MINIMAL_HISTORY_MAX_TRANSACTIONS = 3

# A small number of "creator" wallets in this project's own live data are
# not real launch creators at all but documented self-funding-scheme
# addresses (see docs/CLAUDE.md's "Self-Funding Detection" section) that
# happen to end up as pf_ws_creator/earliest_tx_creator on tens of
# thousands of token_analysis rows -- legitimate creators top out under
# ~4,000 launches; the pathological ones run 17,000+. Fetching every launch
# row for such a wallet just to compute a launch count and a prior-launch
# gap made one 7-day Discovery window load take 50-75s (measured live),
# almost all of it spent re-reading a single wallet's ~17.5k rows.
#
# HISTORY_ROW_CAP is a generic, rule-based guard, not a wallet denylist: any
# creator whose token_analysis row count exceeds this threshold is skipped
# before the expensive full fetch. Its previous_launch_count is then
# unknowable and defaults to 0 (X67.38: treated the same as "no confirmed
# prior launches," resolving to FRESH_CREATOR), with reason
# HISTORY_ROW_CAP_EXCEEDED recorded via creator_identity_skip_reason as the
# observable data-quality signal -- never surfaced as a distinct identity.
# This changes the displayed identity for wallets already far outside "a
# creator with a normal launch history" -- it does not change
# classification for any ordinary creator below the threshold.
HISTORY_ROW_CAP = 2000
HISTORY_ROW_CAP_EXCEEDED = "HISTORY_ROW_CAP_EXCEEDED"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _chunks(values: list[str], size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _evidence(name: str, weight: int, available: bool, passed: bool | None,
              source: str) -> dict[str, Any]:
    return {
        "signal": name, "weight": weight, "available": available,
        "passed": passed if available else None, "source": source,
    }


def classify_creator_identity(*, previous_launch_count: int) -> str:
    """Return exactly one identity category: has this creator launched
    before THIS launch? previous_launch_count must count only launches
    strictly earlier than the current one -- never the current launch
    itself. Always resolves to FRESH_CREATOR or EXISTING_CREATOR; there is
    no third value. A missing/unknown creator or an uncomputed history
    (HISTORY_ROW_CAP guard) is handled by the caller passing
    previous_launch_count=0 -- the data-quality condition is recorded
    separately via creator_identity_skip_reason, not as a distinct
    identity."""
    return FRESH_CREATOR if previous_launch_count < 1 else EXISTING_CREATOR


def disposable_creator_score(*, creator_age_seconds: int | None, launch_count: int,
                             tx_count: int | None, first_tx_signature: str | None,
                             create_signature: str | None, last_tx_at: int | None,
                             migration_at: int | None) -> dict[str, Any]:
    """Evidence-backed 0-100 score. Missing facts contribute no points."""
    facts = [
        _evidence("Fresh wallet at launch", 20, creator_age_seconds is not None,
                  creator_age_seconds is not None and 0 <= creator_age_seconds <= FRESH_MAX_AGE_SECONDS,
                  "creator birth evidence + launch timestamp"),
        _evidence("Single launch", 25, launch_count > 0, launch_count == 1,
                  "token_analysis creator launch count"),
        _evidence("Minimal transaction history", 20, tx_count is not None,
                  tx_count is not None and tx_count <= MINIMAL_HISTORY_MAX_TRANSACTIONS,
                  "creator_tx_ledger transaction count"),
        _evidence("No activity after migration", 20,
                  last_tx_at is not None and migration_at is not None,
                  last_tx_at is not None and migration_at is not None and last_tx_at <= migration_at,
                  "creator_tx_ledger last transaction + migration timestamp"),
        _evidence("First transaction is CREATE", 15,
                  bool(first_tx_signature and create_signature),
                  bool(first_tx_signature and create_signature and first_tx_signature == create_signature),
                  "creator_tx_ledger earliest signature + token_analysis create signature"),
    ]
    score = sum(f["weight"] for f in facts if f["available"] and f["passed"])
    available_weight = sum(f["weight"] for f in facts if f["available"])
    return {
        "score": score,
        "evidence_coverage_pct": round(available_weight, 1),
        "evidence": facts,
        "unavailable_evidence": [
            "Creator balance after migration", "Previous SPL activity",
        ],
    }


def _creators_over_history_row_cap(conn: sqlite3.Connection, creators: list[str]) -> set[str]:
    """Cheap pre-probe: returns the subset of `creators` whose token_analysis
    row count exceeds HISTORY_ROW_CAP. Uses the SAME batched `IN (...)` +
    GROUP BY pattern as the rest of this module (not one query per
    creator -- measured live: a per-creator LIMIT-capped subquery loop over
    ~2,000 creators cost 38s despite each individual query being fast,
    purely from per-call overhead at that volume; a single batched `GROUP
    BY ... HAVING COUNT(*) > N` over the same creators costs ~1.6s). This is
    a detection pass only -- it still visits every row belonging to a
    creator to produce that creator's count, same as the original
    full-fetch code's own cost profile, but it returns ONLY the creator
    identifiers over the cap, never materializes their launch rows, so the
    (much more expensive) full-row fetch below can exclude them entirely."""
    over_cap: set[str] = set()
    for chunk in _chunks(creators):
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT pf_ws_creator FROM token_analysis WHERE pf_ws_creator IN ({marks}) "
            f"GROUP BY pf_ws_creator HAVING COUNT(*) > ?", (*chunk, HISTORY_ROW_CAP),
        ):
            over_cap.add(row[0])
        for row in conn.execute(
            f"SELECT earliest_tx_creator FROM token_analysis "
            f"WHERE (pf_ws_creator IS NULL OR pf_ws_creator='') AND earliest_tx_creator IN ({marks}) "
            f"GROUP BY earliest_tx_creator HAVING COUNT(*) > ?", (*chunk, HISTORY_ROW_CAP),
        ):
            over_cap.add(row[0])
    return over_cap


def enrich_creator_identity(core_db_path: str, records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Attach identity and score to flat Discovery records using batched SQL.

    Creators whose token_analysis row count exceeds HISTORY_ROW_CAP are
    detected up front via a cheap capped probe and excluded from the
    expensive full-history fetch entirely -- see HISTORY_ROW_CAP's
    docstring above for why (a handful of pathological wallets in this
    project's own data would otherwise make every Discovery load re-fetch
    tens of thousands of rows for a single wallet). Every other creator's
    classification and scoring is completely unaffected by this guard.
    """
    creators = sorted({r.get("creator") for r in records.values() if r.get("creator")})
    launch_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tx_metrics: dict[str, dict[str, Any]] = {}
    over_cap_creators: set[str] = set()
    if not creators:
        creators = []

    conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        if creators and _table_exists(conn, "token_analysis"):
            over_cap_creators = _creators_over_history_row_cap(conn, creators)
            fetch_creators = [c for c in creators if c not in over_cap_creators]
            seen_mints: set[str] = set()
            for chunk in _chunks(fetch_creators):
                marks = ",".join("?" for _ in chunk)
                queries = (
                    (f"SELECT mint,pf_ws_creator creator,created_at,migrated_at,create_tx_signature "
                     f"FROM token_analysis WHERE pf_ws_creator IN ({marks})", chunk),
                    (f"SELECT mint,earliest_tx_creator creator,created_at,migrated_at,create_tx_signature "
                     f"FROM token_analysis WHERE (pf_ws_creator IS NULL OR pf_ws_creator='') "
                     f"AND earliest_tx_creator IN ({marks})", chunk),
                )
                for sql, params in queries:
                    for row in conn.execute(sql, params):
                        if row["mint"] in seen_mints:
                            continue
                        seen_mints.add(row["mint"])
                        launch_history[row["creator"]].append({
                            "mint": row["mint"],
                            "created_at": _parse_token_analysis_timestamp(row["created_at"]),
                            "migrated_at": _parse_token_analysis_timestamp(row["migrated_at"]),
                            "create_signature": row["create_tx_signature"],
                        })
        if creators and _table_exists(conn, "creator_tx_ledger"):
            for chunk in _chunks(creators):
                marks = ",".join("?" for _ in chunk)
                for row in conn.execute(
                    f"SELECT creator_pubkey,COUNT(*) tx_count,MIN(blockTime) first_tx_at,"
                    f"MAX(blockTime) last_tx_at FROM creator_tx_ledger "
                    f"WHERE creator_pubkey IN ({marks}) GROUP BY creator_pubkey", chunk,
                ):
                    tx_metrics[row["creator_pubkey"]] = dict(row)
                for row in conn.execute(
                    f"SELECT l.creator_pubkey,l.signature FROM creator_tx_ledger l JOIN ("
                    f"SELECT creator_pubkey,MIN(blockTime) bt FROM creator_tx_ledger "
                    f"WHERE creator_pubkey IN ({marks}) GROUP BY creator_pubkey) first "
                    f"ON first.creator_pubkey=l.creator_pubkey AND first.bt=l.blockTime", chunk,
                ):
                    tx_metrics.setdefault(row["creator_pubkey"], {})["first_tx_signature"] = row["signature"]
    finally:
        conn.close()

    counts = {identity: 0 for identity in IDENTITY_ORDER}
    scores: list[int] = []
    over_cap_count = 0
    for mint, record in records.items():
        creator = record.get("creator")
        if creator and creator in over_cap_creators:
            # Skipped entirely -- no history fetch was performed for this
            # creator, so no launch_count/prior_gap/current are available.
            # This is a DATA-QUALITY condition (recorded via
            # creator_identity_skip_reason), never an identity of its own:
            # with previous_launch_count unknowable, it defaults to 0 (the
            # same "treat unknown as no confirmed prior launches" convention
            # applied when the creator itself is missing), which resolves
            # to FRESH_CREATOR under the new two-value model -- not a third
            # "Unknown" identity.
            over_cap_count += 1
            disposable = disposable_creator_score(
                creator_age_seconds=None, launch_count=0, tx_count=None,
                first_tx_signature=None, create_signature=None,
                last_tx_at=None, migration_at=None,
            )
            record.update({
                "creator_identity": FRESH_CREATOR,
                "creator_identity_label": IDENTITY_LABELS[FRESH_CREATOR],
                "creator_identity_skip_reason": HISTORY_ROW_CAP_EXCEEDED,
                "creator_launch_count": None,
                "prior_launch_gap_seconds": None,
                "disposable_creator_score": disposable,
            })
            counts[FRESH_CREATOR] += 1
            scores.append(disposable["score"])
            continue

        history = sorted(
            launch_history.get(creator, []),
            key=lambda row: (row["created_at"] is None, row["created_at"] or 0, row["mint"]),
        )
        launch_count = len(history)
        current = next((row for row in history if row["mint"] == mint), None)
        prior_gap = None
        previous_launch_count = 0
        if current and current["created_at"] is not None:
            # X67.38 -- strictly earlier than the current launch; the
            # current launch itself must never be counted as "previous."
            prior_created_ats = [row["created_at"] for row in history
                                  if row["created_at"] is not None and row["created_at"] < current["created_at"]]
            previous_launch_count = len(prior_created_ats)
            if prior_created_ats:
                prior_gap = int(current["created_at"] - max(prior_created_ats))
        identity = classify_creator_identity(previous_launch_count=previous_launch_count)
        metrics = tx_metrics.get(creator, {})
        disposable = disposable_creator_score(
            creator_age_seconds=record.get("creator_age_seconds"),
            launch_count=launch_count,
            tx_count=metrics.get("tx_count"),
            first_tx_signature=metrics.get("first_tx_signature"),
            create_signature=current.get("create_signature") if current else None,
            last_tx_at=metrics.get("last_tx_at"),
            migration_at=int(current["migrated_at"]) if current and current["migrated_at"] is not None else None,
        )
        record.update({
            "creator_identity": identity,
            "creator_identity_label": IDENTITY_LABELS[identity],
            "creator_identity_skip_reason": None,
            "creator_launch_count": launch_count,
            "prior_launch_gap_seconds": prior_gap,
            "disposable_creator_score": disposable,
        })
        counts[identity] += 1
        scores.append(disposable["score"])

    total = len(records)
    # X67.38 -- every launch always resolves to FRESH_CREATOR or
    # EXISTING_CREATOR; there is no "unknown" identity bucket anymore.
    # "classified" therefore always equals the full population.
    # history_row_cap_exceeded_count remains as the DIAGNOSTIC signal for
    # the data-quality condition (creator history could not be fetched),
    # surfaced separately from identity, per this task's explicit
    # requirement not to expose it as an operational creator identity.
    assert sum(counts.values()) == total, (
        f"creator identity counts ({sum(counts.values())}) must equal total population ({total})"
    )
    return {
        "identities": [{
            "identity": identity, "label": IDENTITY_LABELS[identity],
            "count": counts[identity],
            "coverage_pct": round(counts[identity] / total * 100, 1) if total else 0.0,
        } for identity in IDENTITY_ORDER],
        "classified": total,
        "unknown": 0,
        "history_row_cap_exceeded_count": over_cap_count,
        "score_distribution": {
            "0_24": sum(score < 25 for score in scores),
            "25_49": sum(25 <= score < 50 for score in scores),
            "50_74": sum(50 <= score < 75 for score in scores),
            "75_100": sum(score >= 75 for score in scores),
        },
    }
