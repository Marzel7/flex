"""X75.3A — Data-integrity test matrix for the X75.3 audit findings.

These tests run against a COPY of the live wt_ops_v2.db (never the live
file itself) and assert the exact facts X75.3's read-only audit
established about Dv34, EFKV, and the B48k / Dv34 Investigation
Population. They exist to codify those findings as a durable regression
guard, not to re-run the audit -- if any of these ever fail, the
underlying data or the classifier's read logic has changed in a way that
needs investigation before any UI built on top of it can be trusted.

Skipped automatically if the live database or the specific wallets are not
present (e.g. a fresh/CI environment without production data) -- these are
data-integrity assertions about THIS platform's live data, not portable
fixture tests.
"""
from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

from src.discovery.relationship_classification import (
    build_entity_context,
    dedupe_populations_by_family_id,
    find_canonical_identity,
    find_direct_edges,
    find_review_decision,
    relationship_between,
)

DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
EFKV = "EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5"
WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present -- these are live-data integrity checks, not portable fixtures")


@pytest.fixture(scope="module")
def live_conn(tmp_path_factory):
    _skip_if_no_live_db()
    tmp_dir = tmp_path_factory.mktemp("x75_3a")
    copy_path = tmp_dir / "wt_ops_v2_copy.db"
    shutil.copy2(_LIVE_DB, copy_path)
    conn = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def populations(live_conn):
    """Deduped population list from EmergingOperatorService, computed once
    per test module run (expensive)."""
    try:
        from src.ops.operator_routes import _get_emerging_service
        d = _get_emerging_service().list(limit=200, debug=False)
    except Exception:
        pytest.skip("EmergingOperatorService unavailable in this environment")
    return dedupe_populations_by_family_id(
        d.get("confirmed_operations_reconciled", []),
        d.get("active_investigations_reconciled", []),
        d.get("operator_candidates_reconciled", []),
        d.get("review_cases_reconciled", []),
        d.get("infrastructure_alerts_reconciled", []),
    )


def _has_dv34_data(live_conn):
    row = live_conn.execute(
        "SELECT 1 FROM wt_treasury_review WHERE treasury=?", (DV34,)
    ).fetchone()
    return bool(row)


class TestNoFalseDirectEdge:
    """PART 2.1 -- No false direct edge."""

    def test_no_direct_edge_dv34_efkv(self, live_conn):
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        result = find_direct_edges(live_conn, DV34, EFKV)
        assert result.exists is False, (
            f"Expected zero direct edges between Dv34 and EFKV, found {len(result.edges)}: {result.edges}"
        )

    def test_no_direct_edge_reverse_direction(self, live_conn):
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        result = find_direct_edges(live_conn, EFKV, DV34)
        assert result.exists is False


class TestStructuralCoMembershipOnly:
    """PART 2.2 -- Structural co-membership only: both wallets may share a
    population while direct-edge count remains zero."""

    def test_both_wallets_appear_in_same_population(self, populations):
        dv34_pop_ids = {
            p["family_id"] for p in populations
            if DV34 in ((p.get("member_wallets") or []) + (p.get("client_wallets") or []) + (p.get("provisioning_clients") or []))
        }
        efkv_pop_ids = {
            p["family_id"] for p in populations
            if EFKV in ((p.get("treasuries") or []) + (p.get("member_treasuries") or []))
        }
        shared = dv34_pop_ids & efkv_pop_ids
        assert shared, "Expected Dv34 and EFKV to share at least one Investigation Population"

    def test_relationship_between_reports_structural_not_direct(self, live_conn, populations):
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        rel = relationship_between(live_conn, DV34, EFKV, populations)
        assert rel["direct_relationship"]["observed"] is False
        assert rel["structural_membership"]["observed"] is True
        family_ids = {p["family_id"] for p in rel["structural_membership"]["populations"]}
        assert any("0a1cc08d9cdc33b1" in fid for fid in family_ids), (
            f"Expected the B48k/Dv34 family among structural matches, got {family_ids}"
        )


class TestCanonicalRoleIntegrity:
    """PART 2.3 -- EFKV = WATCHTOWER treasury; Dv34 != WATCHTOWER treasury."""

    def test_efkv_is_watchtower_treasury(self, live_conn):
        identity = find_canonical_identity(live_conn, EFKV)
        assert identity is not None, "Expected EFKV to have a canonical identity"
        assert identity.operator_id == WATCHTOWER_OPERATOR_ID
        assert identity.entity_type == "TREASURY"

    def test_dv34_is_not_watchtower_treasury(self, live_conn):
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        identity = find_canonical_identity(live_conn, DV34)
        assert identity is None, f"Expected Dv34 to have no canonical operator identity, got {identity}"

    def test_efkv_no_direct_funding_edge_to_dv34(self, live_conn):
        result = find_direct_edges(live_conn, EFKV, DV34)
        assert result.exists is False

    def test_efkv_never_entered_treasury_review_queue(self, live_conn):
        """EFKV was auto-confirmed via CONFIRMED_SUBPROV_TRACE, bypassing
        the human-review path -- there should be no row for it at all."""
        decision = find_review_decision(live_conn, EFKV)
        assert decision is None, f"Expected EFKV to have no wt_treasury_review row, got {decision}"

    def test_efkv_has_three_watchtower_launches(self, live_conn):
        row = live_conn.execute(
            "SELECT COUNT(*) FROM wt_watchtower_launches WHERE treasury_wallet=?", (EFKV,)
        ).fetchone()
        assert row[0] == 3, f"Expected exactly 3 confirmed WATCHTOWER launches for EFKV, found {row[0]}"

    def test_efkv_in_confirmed_treasuries(self, live_conn):
        row = live_conn.execute(
            "SELECT provenance FROM wt_confirmed_treasuries WHERE treasury=?", (EFKV,)
        ).fetchone()
        assert row is not None, "Expected EFKV in wt_confirmed_treasuries"


class TestReviewIntegrity:
    """PART 2.4 -- Dv34 rejection remains active and prevents WATCHTOWER
    expansion matching."""

    def test_dv34_review_status_is_rejected(self, live_conn):
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        decision = find_review_decision(live_conn, DV34)
        assert decision is not None
        assert decision.status == "REJECTED"

    def test_dv34_not_in_confirmed_treasuries(self, live_conn):
        row = live_conn.execute(
            "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (DV34,)
        ).fetchone()
        assert row is None

    def test_dv34_not_in_watchtower_launches(self, live_conn):
        row = live_conn.execute(
            "SELECT COUNT(*) FROM wt_watchtower_launches WHERE "
            "treasury_wallet=? OR subprov_wallet=? OR creator_wallet=?",
            (DV34, DV34, DV34),
        ).fetchone()
        assert row[0] == 0

    def test_rejected_treasury_excludes_population_from_watchtower_expansion(self, live_conn, populations):
        """Mirrors src/discovery/operation_convergence.py's own exclusion
        check -- a population containing a REJECTED treasury must not be
        proposed as a Potential Expansion of any operator."""
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        from src.discovery.operation_convergence import build_convergence_view
        from src.ops.operator_routes import _get_emerging_service
        list_payload = _get_emerging_service().list(limit=200, debug=False)
        view = build_convergence_view(live_conn, list_payload)
        expansion_family_ids = {e["family_id"] for e in view["potential_expansions"]}
        assert "family:0a1cc08d9cdc33b1" not in expansion_family_ids, (
            "B48k/Dv34 family must not be proposed as a Potential Expansion "
            "-- it contains a REJECTED treasury (Dv34)"
        )


class TestIndependentOperationIntegrity:
    """PART 2.5 -- B48k / Dv34 remains independently addressable and is
    not silently absorbed into WATCHTOWER's canonical card."""

    def test_b48k_dv34_family_independently_addressable(self, populations):
        matches = [p for p in populations if "0a1cc08d9cdc33b1" in str(p.get("family_id"))]
        assert matches, "Expected the B48k/Dv34 family to be present as its own population"

    def test_b48k_dv34_not_absorbed_into_watchtower(self, populations):
        watchtower_card = next(
            (p for p in populations if p.get("family_name") == "WATCHTOWER" or p.get("operator_id") == WATCHTOWER_OPERATOR_ID),
            None,
        )
        if watchtower_card is None:
            pytest.skip("WATCHTOWER canonical card not present in this population set")
        absorbed = watchtower_card.get("absorbed_family_ids") or []
        assert not any("0a1cc08d9cdc33b1" in str(fid) for fid in absorbed), (
            f"B48k/Dv34 must not appear in WATCHTOWER's absorbed_family_ids, got {absorbed}"
        )


class TestEntityContextIntegrity:
    """Cross-check src/discovery/relationship_classification.py's
    build_entity_context() reports the same distinctions independently."""

    def test_dv34_context(self, live_conn, populations):
        if not _has_dv34_data(live_conn):
            pytest.skip("Dv34 not present in this database snapshot")
        ctx = build_entity_context(live_conn, DV34, populations)
        assert ctx.canonical_identity is None
        assert ctx.review_decision is not None
        assert ctx.review_decision.status == "REJECTED"
        assert any("0a1cc08d9cdc33b1" in p["family_id"] for p in ctx.structural_populations)

    def test_efkv_context(self, live_conn, populations):
        ctx = build_entity_context(live_conn, EFKV, populations)
        assert ctx.canonical_identity is not None
        assert ctx.canonical_identity.operator_id == WATCHTOWER_OPERATOR_ID
        assert ctx.review_decision is None
        assert any("0a1cc08d9cdc33b1" in p["family_id"] for p in ctx.structural_populations)
