"""X76.1 — Operator Identity Projection Integrity.

Permanent regression coverage: creating an identity, expanding an
identity, adding a treasury (via the LIVE production write path --
watchtower_alignment.reconcile_confirmed_treasury), merging, splitting,
reactivation, and retirement must all leave operator_entities and
operator_identity_assets in a mutually consistent state -- never one
populated while the other silently stays empty.
"""
from __future__ import annotations

import os
import sqlite3
import time

import pytest

from src.ops.operator_identity_governance import (
    OperatorIdentityGovernanceService,
    _ENTITY_TYPE_TO_ASSET_TYPE,
    project_entity_to_asset,
)
from src.ops.operator_writer import OperatorWriter

WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present")


def seed_operator(path, operator_id, name, status="CONFIRMED"):
    now = int(time.time())
    OperatorWriter(str(path)).transaction(
        f"seed-{operator_id}",
        lambda conn: conn.execute(
            "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,"
            "review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operator_id, status, "CERTAIN", now, now, name, "REVIEWED", name, now, now),
        ),
    )


@pytest.fixture
def governed(tmp_path):
    path = tmp_path / "operators.db"
    OperatorWriter(str(path)).initialize_schema()
    seed_operator(path, "op-a", "OperatorA")
    seed_operator(path, "op-b", "OperatorB")
    service = OperatorIdentityGovernanceService(str(path))
    service.bootstrap_confirmed()
    return path, service


def metadata(reason="Validated evidence"):
    return {"analyst": "analyst-1", "evidence_revision": "evidence-r7", "reason": reason}


def _counts(path):
    conn = sqlite3.connect(path)
    entities = conn.execute("SELECT COUNT(*) FROM operator_entities").fetchone()[0]
    assets = conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]
    conn.close()
    return entities, assets


class TestExpandProjectsBothTables:
    def test_expand_creates_entity_and_asset(self, governed):
        path, service = governed
        before_entities, before_assets = _counts(path)
        service.expand("op-a", {**metadata(), "asset_type": "TREASURY", "asset_value": "wallet-1"})
        after_entities, after_assets = _counts(path)
        assert after_entities == before_entities + 1
        assert after_assets == before_assets + 1

    def test_expand_is_idempotent(self, governed):
        path, service = governed
        service.expand("op-a", {**metadata(), "asset_type": "TREASURY", "asset_value": "wallet-1"})
        e1, a1 = _counts(path)
        service.expand("op-a", {**metadata(), "asset_type": "TREASURY", "asset_value": "wallet-1"})
        e2, a2 = _counts(path)
        assert (e1, a1) == (e2, a2)


class TestProjectEntityToAssetDirectly:
    """The new X76.1 primitive, exercised directly."""

    def test_project_creates_asset_matching_entity(self, governed):
        path, service = governed
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        asset_id = project_entity_to_asset(conn, "op-a", "TREASURY", "wallet-direct")
        conn.commit()
        assert asset_id is not None
        row = conn.execute(
            "SELECT * FROM operator_identity_assets WHERE asset_id=?", (asset_id,)
        ).fetchone()
        assert row is not None
        assert row["operator_id"] == "op-a"
        assert row["asset_type"] == "TREASURY"
        assert row["asset_value"] == "wallet-direct"
        conn.close()

    def test_project_is_idempotent(self, governed):
        path, service = governed
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        id1 = project_entity_to_asset(conn, "op-a", "TREASURY", "wallet-direct")
        conn.commit()
        id2 = project_entity_to_asset(conn, "op-a", "TREASURY", "wallet-direct")
        conn.commit()
        assert id1 == id2
        count = conn.execute(
            "SELECT COUNT(*) FROM operator_identity_assets WHERE asset_value=?", ("wallet-direct",)
        ).fetchone()[0]
        assert count == 1
        conn.close()

    def test_unmapped_entity_type_returns_none_without_error(self, governed):
        path, service = governed
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        result = project_entity_to_asset(conn, "op-a", "CLIENT", "wallet-client")
        conn.commit()
        assert result is None
        count = conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]
        assert count == 0
        conn.close()

    def test_unknown_operator_returns_none_without_error(self, governed):
        path, service = governed
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        result = project_entity_to_asset(conn, "nonexistent-operator", "TREASURY", "wallet-x")
        conn.commit()
        assert result is None
        conn.close()


class TestMergeCarriesAssetsForward:
    def test_merge_preserves_source_assets_on_destination(self, governed):
        path, service = governed
        service.expand("op-b", {**metadata(), "asset_type": "TREASURY", "asset_value": "wallet-src"})
        service.merge("op-a", ["op-b"], metadata("Confirmed same controller"))
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM operator_identity_assets WHERE operator_id='op-a' AND asset_value='wallet-src'"
        ).fetchone()
        assert row is not None, "merge must carry the source operator's assets forward to the destination"
        conn.close()


class TestActivityTransitionsDoNotOrphanAssets:
    """Reactivation/retirement/review transitions must never touch
    operator_identity_assets rows -- they change activity_status only."""

    def test_retire_does_not_remove_assets(self, governed):
        path, service = governed
        service.expand("op-a", {**metadata(), "asset_type": "TREASURY", "asset_value": "wallet-1"})
        before_entities, before_assets = _counts(path)
        service.retire("op-a", metadata("No longer active"))
        after_entities, after_assets = _counts(path)
        assert (after_entities, after_assets) == (before_entities, before_assets)

    def test_set_activity_does_not_remove_assets(self, governed):
        path, service = governed
        service.expand("op-a", {**metadata(), "asset_type": "TREASURY", "asset_value": "wallet-1"})
        before_entities, before_assets = _counts(path)
        service.set_activity("op-a", "DORMANT", metadata("Inactivity observed"))
        after_entities, after_assets = _counts(path)
        assert (after_entities, after_assets) == (before_entities, before_assets)


class TestLiveProductionWritePath:
    """The actual defect: watchtower_alignment.reconcile_confirmed_treasury(),
    the live writer responsible for 69 of 70 current operator_entities rows,
    must now ALSO project into operator_identity_assets, on the same
    connection/transaction."""

    @pytest.fixture
    def live_copy_conn(self, tmp_path):
        _skip_if_no_live_db()
        import shutil
        copy_path = tmp_path / "wt_ops_v2_copy.db"
        shutil.copy2(_LIVE_DB, copy_path)
        conn = sqlite3.connect(copy_path)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()

    def test_reconcile_confirmed_treasury_projects_asset(self, live_copy_conn):
        from src.ops.watchtower_alignment import reconcile_confirmed_treasury
        conn = live_copy_conn
        row = conn.execute("SELECT treasury FROM wt_confirmed_treasuries LIMIT 1").fetchone()
        if not row:
            pytest.skip("no confirmed treasuries present in this database snapshot")
        treasury = row["treasury"]

        before_asset = conn.execute(
            "SELECT 1 FROM operator_identity_assets WHERE operator_id=? AND asset_type='TREASURY' AND asset_value=?",
            (WATCHTOWER_OPERATOR_ID, treasury),
        ).fetchone()

        reconcile_confirmed_treasury(conn, treasury)
        conn.commit()

        after_asset = conn.execute(
            "SELECT 1 FROM operator_identity_assets WHERE operator_id=? AND asset_type='TREASURY' AND asset_value=?",
            (WATCHTOWER_OPERATOR_ID, treasury),
        ).fetchone()
        assert after_asset is not None, (
            "reconcile_confirmed_treasury must project into operator_identity_assets "
            "on the same call that writes operator_entities"
        )

    def test_reconcile_confirmed_treasury_is_idempotent_for_assets(self, live_copy_conn):
        from src.ops.watchtower_alignment import reconcile_confirmed_treasury
        conn = live_copy_conn
        row = conn.execute("SELECT treasury FROM wt_confirmed_treasuries LIMIT 1").fetchone()
        if not row:
            pytest.skip("no confirmed treasuries present in this database snapshot")
        treasury = row["treasury"]

        reconcile_confirmed_treasury(conn, treasury)
        conn.commit()
        count1 = conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]

        reconcile_confirmed_treasury(conn, treasury)
        conn.commit()
        count2 = conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]

        assert count1 == count2, "re-reconciling the same treasury must not duplicate its asset row"

    def test_reconcile_does_not_break_when_asset_tables_missing(self):
        """isolated legacy/test database with no operator_identity_assets
        table at all -- the projection must fail silently, never blocking
        the authoritative operator_entities write."""
        from src.ops.watchtower_alignment import reconcile_confirmed_treasury, ensure_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE operators (operator_id TEXT PRIMARY KEY, display_name TEXT, status TEXT, confidence TEXT)"
        )
        conn.execute(
            "INSERT INTO operators VALUES (?,?,?,?)", (WATCHTOWER_OPERATOR_ID, "WATCHTOWER", "CONFIRMED", "CERTAIN")
        )
        conn.execute(
            "CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT, entity_type TEXT, "
            "confidence TEXT, evidence_count INTEGER, first_seen INTEGER, last_seen INTEGER, added_at INTEGER, "
            "UNIQUE(operator_id, entity_address))"
        )
        conn.execute(
            "CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, provenance TEXT, confirmed_at INTEGER, "
            "method TEXT, confidence TEXT, out_sol REAL, transfer_pct INTEGER, recipients INTEGER, micro_pings INTEGER)"
        )
        conn.execute(
            "INSERT INTO wt_confirmed_treasuries (treasury, provenance, confirmed_at) VALUES (?,?,?)",
            ("wallet-isolated", "CONFIRMED_SEED", int(time.time())),
        )
        ensure_schema(conn)
        result = reconcile_confirmed_treasury(conn, "wallet-isolated")
        conn.commit()
        assert result["status"] in ("RECONCILED", "ALIGNED")
        entity = conn.execute(
            "SELECT 1 FROM operator_entities WHERE entity_address='wallet-isolated'"
        ).fetchone()
        assert entity is not None, "the authoritative operator_entities write must succeed regardless of asset-table availability"


class TestPhase8ConsistencyAudit:
    """No orphan entities, no orphan assets, no duplicates, no missing
    assets for entity_types that have a defined mapping."""

    @pytest.fixture
    def live_conn(self):
        _skip_if_no_live_db()
        conn = sqlite3.connect(f"file:{_LIVE_DB}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield conn
        conn.close()

    def test_no_missing_assets_for_mapped_entity_types(self, live_conn):
        mapped_types = tuple(_ENTITY_TYPE_TO_ASSET_TYPE.keys())
        placeholders = ",".join("?" for _ in mapped_types)
        missing = live_conn.execute(
            f"SELECT oe.operator_id, oe.entity_address, oe.entity_type FROM operator_entities oe "
            f"WHERE oe.entity_type IN ({placeholders}) "
            f"AND NOT EXISTS (SELECT 1 FROM operator_identity_assets oa "
            f"WHERE oa.operator_id=oe.operator_id AND oa.asset_value=oe.entity_address)",
            mapped_types,
        ).fetchall()
        assert not missing, f"found operator_entities rows with a mapped type but no projected asset: {[dict(r) for r in missing]}"

    def test_no_duplicate_assets(self, live_conn):
        dupes = live_conn.execute(
            "SELECT operator_id, asset_type, asset_value, COUNT(*) c FROM operator_identity_assets "
            "GROUP BY operator_id, asset_type, asset_value HAVING c > 1"
        ).fetchall()
        assert not dupes, f"found duplicate operator_identity_assets rows: {[dict(r) for r in dupes]}"

    def test_no_orphan_assets(self, live_conn):
        """Every asset must reference a real operator."""
        orphans = live_conn.execute(
            "SELECT oa.asset_id FROM operator_identity_assets oa "
            "WHERE NOT EXISTS (SELECT 1 FROM operators o WHERE o.operator_id=oa.operator_id)"
        ).fetchall()
        assert not orphans, f"found operator_identity_assets rows with no matching operator: {orphans}"

    def test_watchtower_treasury_entities_and_assets_match_exactly(self, live_conn):
        entity_wallets = {
            r["entity_address"] for r in live_conn.execute(
                "SELECT entity_address FROM operator_entities WHERE operator_id=? AND entity_type='TREASURY'",
                (WATCHTOWER_OPERATOR_ID,),
            ).fetchall()
        }
        asset_wallets = {
            r["asset_value"] for r in live_conn.execute(
                "SELECT asset_value FROM operator_identity_assets WHERE operator_id=? AND asset_type='TREASURY'",
                (WATCHTOWER_OPERATOR_ID,),
            ).fetchall()
        }
        assert entity_wallets == asset_wallets, (
            f"WATCHTOWER treasury entities and assets must match exactly. "
            f"entities-only: {entity_wallets - asset_wallets}, assets-only: {asset_wallets - entity_wallets}"
        )
