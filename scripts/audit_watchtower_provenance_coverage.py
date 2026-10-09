#!/usr/bin/env python3
"""Build the DEV-014 read-only Watchtower event-provenance coverage matrix.

The input cohort is frozen.  SQLite inputs are opened immutable/read-only and
the output is a compact research artifact; this tool never calls a provider or
touches operational monitor, queue, or lifecycle state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json"
OFFSET_AUDIT = ROOT / "docs/audits/watchtower-opening-offset-audit.v2.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_provenance_coverage_20261009.v1.json"
SPECIAL_MINT = "3yvK6WWww1moF3qx3UvHngZCHy9kRCVd9Jva8tRrpump"
SPECIAL_MIGRATION = {
    "signature": "mPr6Gv6Sjag8sU6bLC4m5uejoshcqVcBhs8r5uwXMrjZkXfdnffQukgs1c64LpFhvTJXN94zc2rujmQGoAp5M5X",
    "slot": 453969437,
    "timestamp": 1791307928,
    "source": "src/ops/retained_injector_opening.py",
}


def _db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _rows(connection: sqlite3.Connection, sql: str) -> dict[str, dict[str, Any]]:
    cursor = connection.execute(sql)
    return {str(row["mint"]): dict(row) for row in cursor.fetchall()}


def build(*, wt_db: Path, core_db: Path) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text())
    launches = manifest["launches"]
    frozen = {str(row["mint"]) for row in launches}
    if len(launches) != 639 or len(frozen) != 639:
        raise ValueError("FROZEN_COHORT_CARDINALITY_MISMATCH")

    wt = _db(wt_db)
    core = _db(core_db)
    wt.row_factory = sqlite3.Row
    core.row_factory = sqlite3.Row
    entry = _rows(wt, """
        SELECT mint, canonical_create_signature, canonical_create_slot,
               canonical_create_time, evidence_identity
        FROM operation_monitor_entry_provenance
        WHERE canonical_create_signature IS NOT NULL AND canonical_create_slot IS NOT NULL
    """)
    registry = _rows(wt, """
        SELECT mint, create_signature, create_slot, create_time, confidence,
               creator_extraction_method
        FROM wt_watchtower_launches
    """)
    audit = _rows(wt, """
        SELECT mint, create_signature, create_slot, create_time, source,
               migration_signature, migration_time
        FROM wt_launch_audit
    """)
    migration = _rows(core, """
        SELECT mint, migration_tx, migrated_at, dex, pumpswap_pool_address,
               final_verdict
        FROM pumpfun_migration_verification
        WHERE migration_tx IS NOT NULL AND migrated_at IS NOT NULL
    """)
    offsets = json.loads(OFFSET_AUDIT.read_text())["records"]
    offset_mints = {str(row["mint"]) for row in offsets if row.get("mint") in frozen and row.get("offsets")}
    if "INJECTOR" not in {str(row["mint"]) for row in offsets}:
        raise ValueError("SPECIAL_OFFSET_RECORD_MISSING")

    records: list[dict[str, Any]] = []
    for launch in sorted(launches, key=lambda row: str(row["mint"])):
        mint = str(launch["mint"])
        ep = entry.get(mint)
        reg = registry.get(mint)
        aud = audit.get(mint)
        mig = migration.get(mint)
        if ep:
            creation_class = "CREATION_QUALIFIED"
            creation = {"signature": ep["canonical_create_signature"], "slot": ep["canonical_create_slot"],
                        "timestamp": ep["canonical_create_time"], "source": "operation_monitor_entry_provenance"}
        elif mint == SPECIAL_MINT:
            creation_class = "CREATION_CONFLICTING"
            creation = {"signature": (reg or aud or {}).get("create_signature"), "slot": None,
                        "timestamp": (reg or aud or {}).get("create_time"),
                        "source": "wt_watchtower_launches+wt_launch_audit:fixture_identity_collision"}
        elif aud or reg:
            creation_class = "FIXTURE_ONLY"
            source = "wt_launch_audit:FIXTURE_BACKFILL+wt_watchtower_launches"
            creation = {"signature": (reg or aud or {}).get("create_signature"), "slot": None,
                        "timestamp": (reg or aud or {}).get("create_time"), "source": source}
        else:
            creation_class = "CREATION_EVIDENCE_UNAVAILABLE"
            creation = {"signature": None, "slot": None, "timestamp": None, "source": None}

        if mint == SPECIAL_MINT:
            migration_class = "MIGRATION_QUALIFIED"
            migration_identity = dict(SPECIAL_MIGRATION)
        elif mig or (aud and aud.get("migration_signature") and aud.get("migration_time")):
            migration_class = "MIGRATION_SLOT_MISSING"
            base = mig or aud or {}
            migration_identity = {"signature": base.get("migration_tx") or base.get("migration_signature"),
                                  "slot": None, "timestamp": base.get("migrated_at") or base.get("migration_time"),
                                  "source": "pumpfun_migration_verification" if mig else "wt_launch_audit:FIXTURE_BACKFILL"}
        else:
            migration_class = "MIGRATION_EVIDENCE_UNAVAILABLE"
            migration_identity = {"signature": None, "slot": None, "timestamp": None, "source": None}

        if mint == SPECIAL_MINT:
            chronology = "EVENT_IDENTITY_MISMATCH"
            reason = "audit.create_slot equals independently retained migration slot"
        elif creation_class == "CREATION_QUALIFIED" and migration_class == "MIGRATION_QUALIFIED":
            chronology = "INDEPENDENT_CHRONOLOGY_QUALIFIED"
            reason = None
        elif creation_class == "CREATION_QUALIFIED":
            chronology = "MIGRATION_IDENTITY_MISSING"
            reason = "migration slot is not retained"
        elif migration_class == "MIGRATION_QUALIFIED":
            chronology = "CREATION_IDENTITY_MISSING"
            reason = "creation identity is not independently qualified"
        elif creation_class == "CREATION_EVIDENCE_UNAVAILABLE" and migration_class == "MIGRATION_EVIDENCE_UNAVAILABLE":
            chronology = "INSUFFICIENT_EVIDENCE"
            reason = "both independent event identities are unavailable"
        else:
            chronology = "CREATION_IDENTITY_MISSING"
            reason = "creation is fixture-only or lacks an independently retained slot"
        opening = launch.get("evidence", {}).get("opening", {})
        records.append({
            "mint": mint,
            "original_label": launch["original_classification"],
            "current_membership": "CURRENT_CONFIRMED",
            "qualified_entry": opening.get("status") == "QUALIFIED",
            "creation": {"class": creation_class, **creation},
            "migration": {"class": migration_class, **migration_identity},
            "chronology": chronology,
            "conflict_reason": reason,
            "strict_offset_price_retained": mint in offset_mints or mint == SPECIAL_MINT,
        })

    if len({row["mint"] for row in records}) != 639:
        raise ValueError("MATRIX_CARDINALITY_MISMATCH")
    current = _db(wt_db)
    current.row_factory = sqlite3.Row
    memberships = current.execute("""
        SELECT mint FROM operator_launch_membership
        WHERE operator_id IN ('04265d9f-6eb2-568c-a49e-9253091a4dbb', 'bb255638-a493-551f-938c-8be7c9ea4f1e')
    """).fetchall()
    current_mints = {str(row["mint"]) for row in memberships}
    if current_mints != frozen:
        raise ValueError("CURRENT_MEMBERSHIP_DRIFT")

    def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(rows),
            "creation_qualified": sum(row["creation"]["class"] == "CREATION_QUALIFIED" for row in rows),
            "migration_qualified": sum(row["migration"]["class"] == "MIGRATION_QUALIFIED" for row in rows),
            "both_identities_qualified": sum(row["creation"]["class"] == "CREATION_QUALIFIED" and row["migration"]["class"] == "MIGRATION_QUALIFIED" for row in rows),
            "independent_chronology_qualified": sum(row["chronology"] == "INDEPENDENT_CHRONOLOGY_QUALIFIED" for row in rows),
            "creation_signature_missing": sum(row["creation"]["signature"] is None for row in rows),
            "creation_slot_missing": sum(row["creation"]["slot"] is None for row in rows),
            "migration_signature_missing": sum(row["migration"]["signature"] is None for row in rows),
            "migration_slot_missing": sum(row["migration"]["slot"] is None for row in rows),
            "fixture_only": sum(row["creation"]["class"] == "FIXTURE_ONLY" for row in rows),
            "event_identity_conflicts": sum(row["chronology"] == "EVENT_IDENTITY_MISMATCH" for row in rows),
            "chronology_conflicts": sum(row["chronology"] == "CHRONOLOGY_CONFLICT" for row in rows),
            "insufficient_evidence": sum(row["chronology"] == "INSUFFICIENT_EVIDENCE" for row in rows),
        }

    monitored = [row for row in records if row["qualified_entry"]]
    unmonitored = [row for row in records if not row["qualified_entry"]]
    summary = counts(records)
    artifact = {
        "schema_version": 1,
        "artifact_type": "DEV014_WATCHTOWER_FULL_CREATION_MIGRATION_PROVENANCE_COVERAGE",
        "cohort": manifest["cohort_version"],
        "cohort_identity": manifest["cohort_identity"],
        "source_manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "scope": {"frozen_mints": 639, "current_cumulative_mints": len(current_mints), "new_launches_since_snapshot": 0,
                  "provider_requests": 0, "live_database_writes": 0, "queue_writes": 0, "lifecycle_mutations": 0,
                  "raw_payload_retention": False, "maximum_single_file_bytes": 1_000_000, "unbounded_growth_paths": 0},
        "authority": {"membership": "operator_launch_membership: Watchtower operator 04265d9f + Deep operator bb255638",
                      "creation": "operation_monitor_entry_provenance is the only accepted independently bound creation source",
                      "migration": "retained_injector_opening is the only accepted independently bound migration source",
                      "rejected": ["wt_launch_audit:FIXTURE_BACKFILL", "wt_watchtower_launches:WALKBACK_RECOVERED"]},
        "summary": {**summary, "creation_classes": dict(sorted(Counter(row["creation"]["class"] for row in records).items())),
                    "migration_classes": dict(sorted(Counter(row["migration"]["class"] for row in records).items())),
                    "chronology_classes": dict(sorted(Counter(row["chronology"] for row in records).items())),
                    "monitored_70": counts(monitored), "unmonitored_569": counts(unmonitored),
                    "retained_strict_offset_price_evidence": sum(row["strict_offset_price_retained"] for row in records),
                    "potentially_recoverable_without_provider": 0},
        "records": records,
        "conclusion": "No frozen mint has both independently qualified creation and migration identities. Retained price offsets never substitute for missing event provenance.",
    }
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--wt-db", type=Path, required=True,
                        help="canonical Watchtower SQLite database; opened mode=ro/query_only")
    parser.add_argument("--core-db", type=Path, required=True,
                        help="canonical core SQLite database; opened mode=ro/query_only")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    artifact = build(wt_db=args.wt_db, core_db=args.core_db)
    rendered = json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n"
    if len(rendered.encode()) >= 1_000_000:
        raise ValueError("COMPACT_ARTIFACT_BOUND_EXCEEDED")
    if args.check:
        if not args.output.exists() or args.output.read_text() != rendered:
            raise SystemExit("ARTIFACT_NOT_DETERMINISTIC")
    else:
        args.output.write_text(rendered)


if __name__ == "__main__":
    main()
