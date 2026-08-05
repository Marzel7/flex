"""X76.1 — Idempotent backfill: project every existing operator_entities
row into operator_identity_assets.

Root cause (see docs/audits/x76_1_projection_integrity_audit.md): the
live production writer of operator_entities (watchtower_alignment.
reconcile_confirmed_treasury) never called into
OperatorIdentityGovernanceService, so operator_identity_assets never
received a row despite operator_entities being populated normally. That
gap is closed going forward by src/ops/watchtower_alignment.py's now
wiring in project_entity_to_asset() on every future confirmation. This
script backfills the rows that predate that fix.

Idempotent: project_entity_to_asset() itself is INSERT OR IGNORE against a
deterministic uuid5 asset_id, so running this script twice (or against a
database some rows have already been projected into) produces the exact
same end state with zero duplicates. Safe on partially-populated
databases -- rows already present are left untouched.

Only entity_types with a defined reverse mapping (TREASURY,
SUB_PROVISIONER, CREATOR -- see ENTITY_ASSET_TYPES /
_ENTITY_TYPE_TO_ASSET_TYPE in operator_identity_governance.py) are
projected; any other entity_type (e.g. CLIENT) is reported as
intentionally skipped, not silently dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sqlite3

from src.core.db import OPS_DB_PATH
from src.ops.operator_identity_governance import project_entity_to_asset, _ENTITY_TYPE_TO_ASSET_TYPE


def backfill(conn: sqlite3.Connection) -> dict:
    before_assets = conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]
    before_entities = conn.execute("SELECT COUNT(*) FROM operator_entities").fetchone()[0]

    rows = conn.execute(
        "SELECT operator_id, entity_address, entity_type FROM operator_entities"
    ).fetchall()

    projected, skipped_unmapped, already_present = 0, 0, 0
    skipped_types: dict[str, int] = {}
    for operator_id, entity_address, entity_type in rows:
        asset_type = _ENTITY_TYPE_TO_ASSET_TYPE.get(str(entity_type or "").upper())
        if not asset_type:
            skipped_unmapped += 1
            skipped_types[entity_type] = skipped_types.get(entity_type, 0) + 1
            continue
        existing = conn.execute(
            "SELECT 1 FROM operator_identity_assets WHERE operator_id=? AND asset_type=? AND asset_value=?",
            (operator_id, asset_type, entity_address),
        ).fetchone()
        asset_id = project_entity_to_asset(
            conn, operator_id, entity_type, entity_address,
            evidence_revision="backfill:x76_1",
        )
        if existing:
            already_present += 1
        elif asset_id:
            projected += 1

    conn.commit()

    after_assets = conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]
    after_entities = conn.execute("SELECT COUNT(*) FROM operator_entities").fetchone()[0]

    return {
        "before_assets": before_assets,
        "before_entities": before_entities,
        "after_assets": after_assets,
        "after_entities": after_entities,
        "entity_rows_scanned": len(rows),
        "newly_projected": projected,
        "already_present": already_present,
        "skipped_unmapped_type": skipped_unmapped,
        "skipped_types_breakdown": skipped_types,
    }


def main() -> None:
    conn = sqlite3.connect(str(OPS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result = backfill(conn)
    finally:
        conn.close()
    print("[X76.1 backfill] before: operator_entities={before_entities} operator_identity_assets={before_assets}".format(**result))
    print("[X76.1 backfill] after:  operator_entities={after_entities} operator_identity_assets={after_assets}".format(**result))
    print(f"[X76.1 backfill] entity rows scanned: {result['entity_rows_scanned']}")
    print(f"[X76.1 backfill] newly projected: {result['newly_projected']}")
    print(f"[X76.1 backfill] already present (idempotent no-op): {result['already_present']}")
    print(f"[X76.1 backfill] skipped (no asset-type mapping): {result['skipped_unmapped_type']} {result['skipped_types_breakdown']}")
    assert result["after_entities"] == result["before_entities"], "backfill must never modify operator_entities"


if __name__ == "__main__":
    main()
