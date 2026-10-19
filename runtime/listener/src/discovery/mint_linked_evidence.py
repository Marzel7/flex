"""Read-only mint-linked evidence adapters for OPS-DISCOVERY-P3."""
from __future__ import annotations

import sqlite3


def ep3_for_mint(connection: sqlite3.Connection, mint: str) -> dict:
    """Return observed transaction-first-lineage topology, never an identity claim."""
    launches = connection.execute("SELECT mint,creator,creation_signature,creation_time FROM tf_launches WHERE mint=?", (mint,)).fetchone()
    edges = connection.execute("SELECT sender,recipient,signature,block_time,relationship_type,mechanism,hop_depth,evidence_source FROM tf_edges WHERE launch_context=? ORDER BY block_time,edge_id", (mint,)).fetchall()
    return {"mint": mint, "state": "COMPLETE" if launches and edges else "NOT_AVAILABLE", "launch": launches, "edges": edges,
            "lineage": "DERIVED_FROM_WT_WALKBACK_AND_RETAINED_TRANSACTION_CACHE", "authority": "NON_AUTHORITATIVE"}


def migration_signer_from_record(record: dict) -> dict:
    """Fail closed unless a retained parsed migration transaction has one signer."""
    signers = tuple(record.get("signers") or ())
    if not record.get("migration_signature"):
        return {"state": "LOCAL_MIGRATION_TRANSACTION_ABSENT", "migration_signer": None}
    if len(signers) != 1:
        return {"state": "AMBIGUOUS_OR_UNAVAILABLE_SIGNER", "migration_signer": None, "signer_count": len(signers)}
    return {"state": "COMPLETE", "migration_signer": signers[0], "signer_count": 1,
            "migration_signature": record["migration_signature"]}
