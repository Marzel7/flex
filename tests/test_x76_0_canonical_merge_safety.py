"""X76.0 — Canonical Merge Safety & Identity Boundary Protection.

Permanent regression coverage for the merge contract in
src/ops/canonical_merge_contract.py. Ensures a merge (of the automatic
EmergingOperatorService absorption kind) can never be triggered by a
single shared wallet, a shared treasury alone, shared infrastructure
alone, or a projection/storage-field coincidence -- only by independent
identity evidence meeting the documented threshold.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from src.ops.canonical_merge_contract import evaluate_merge

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present")


class TestSingleSharedWalletNeverMerges:
    def test_single_shared_wallet_no_other_evidence(self):
        canonical = {"family_id": "canonical:op", "member_wallets": ["shared"]}
        candidate = {"family_id": "family:cand", "member_wallets": ["shared", "unrelated"]}
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False

    def test_single_shared_wallet_via_treasury_field_no_other_evidence(self):
        """The exact field-naming asymmetry X75.3A found: a shared wallet
        recorded in 'treasuries' on both sides, with zero other evidence,
        must still not merge."""
        canonical = {"family_id": "canonical:op", "treasuries": ["shared_treasury"]}
        candidate = {"family_id": "family:cand", "treasuries": ["shared_treasury"]}
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False
        names = {c.name for c in decision.unsatisfied_criteria}
        assert "identity_signal_threshold" in names


class TestSharedTreasuryAloneNeverMerges:
    def test_shared_treasury_no_mechanism_no_topology_no_depth(self):
        canonical = {"family_id": "canonical:op", "treasuries": ["t1", "t2"]}
        candidate = {"family_id": "family:cand", "treasuries": ["t1"]}
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False


class TestSharedInfrastructureAloneNeverMerges:
    def test_shared_client_wallet_no_identity_signals(self):
        canonical = {"family_id": "canonical:op", "client_wallets": ["c1"]}
        candidate = {"family_id": "family:cand", "client_wallets": ["c1"], "provisioning_clients": ["c1"]}
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False


class TestConfirmedIdentityExpansionAloneDoesNotForceMerge:
    """A wallet being CONFIRMED elsewhere (e.g. in wt_confirmed_treasuries)
    is not itself passed to evaluate_merge as a signal -- only funding
    mechanism, topology, and structural depth are. This test documents
    that confirmation status alone (absence from rejected_wallets) is
    necessary but not sufficient -- it only removes the hard stop, it does
    not supply a positive identity signal."""

    def test_not_rejected_is_not_sufficient_alone(self):
        canonical = {"family_id": "canonical:op", "member_wallets": ["w1"]}
        candidate = {"family_id": "family:cand", "member_wallets": ["w1"]}
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False
        assert any(c.name == "no_rejected_review" and c.satisfied for c in decision.satisfied_criteria)
        assert decision.allowed is False


class TestIndependentControllerEvidenceAllowsMerge:
    def test_two_identity_signals_allow_merge(self):
        canonical = {
            "family_id": "canonical:op", "member_wallets": ["w1"],
            "funding_mechanisms": ["WSOL_WRAP_CLOSE"], "dominant_topology": "treasury -> subprov -> creator",
        }
        candidate = {
            "family_id": "family:cand", "member_wallets": ["w1"],
            "funding_mechanisms": ["WSOL_WRAP_CLOSE"], "dominant_topology": "treasury -> subprov -> creator",
            "walkback_descendant_count": 12,
        }
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is True
        assert len(decision.satisfied_criteria) >= 4  # overlap, no-rejection, + >=2 identity signals

    def test_only_one_identity_signal_still_blocks(self):
        canonical = {
            "family_id": "canonical:op", "member_wallets": ["w1"],
            "funding_mechanisms": ["WSOL_WRAP_CLOSE"],
        }
        candidate = {
            "family_id": "family:cand", "member_wallets": ["w1"],
            "funding_mechanisms": ["WSOL_WRAP_CLOSE"],
        }
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False, "one identity signal alone must not be sufficient"


class TestRejectedReviewIsAHardStop:
    def test_rejected_overlap_blocks_even_with_strong_evidence(self):
        canonical = {
            "family_id": "canonical:op", "member_wallets": ["w1"],
            "funding_mechanisms": ["WSOL_WRAP_CLOSE"], "dominant_topology": "x",
        }
        candidate = {
            "family_id": "family:cand", "member_wallets": ["w1"],
            "funding_mechanisms": ["WSOL_WRAP_CLOSE"], "dominant_topology": "x",
            "walkback_descendant_count": 99,
        }
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset({"w1"}))
        assert decision.allowed is False, "a REJECTED wallet must block absorption even with strong evidence"
        assert decision.unsatisfied_criteria[0].name == "no_rejected_review"


class TestNoOverlapNeverMerges:
    def test_no_wallet_overlap_at_all(self):
        canonical = {"family_id": "canonical:op", "member_wallets": ["w1"]}
        candidate = {"family_id": "family:cand", "member_wallets": ["w2"]}
        decision = evaluate_merge(canonical, candidate, rejected_wallets=frozenset())
        assert decision.allowed is False


class TestNamedControlsAgainstLiveData:
    """PHASE 5/9 -- named validation against the live database."""

    @pytest.fixture(scope="class")
    def live_conn(self):
        _skip_if_no_live_db()
        conn = sqlite3.connect(f"file:{_LIVE_DB}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield conn
        conn.close()

    @pytest.fixture(scope="class")
    def rejected_wallets(self, live_conn):
        return frozenset(
            r["treasury"] for r in live_conn.execute(
                "SELECT treasury FROM wt_treasury_review WHERE status='REJECTED'"
            ).fetchall()
        )

    def test_watchtower_does_not_absorb_b48k_dv34(self, rejected_wallets):
        try:
            from src.ops.emerging_operator_service import EmergingOperatorService
            from src.core.db import OPS_DB_PATH, DB_PATH
        except Exception:
            pytest.skip("EmergingOperatorService unavailable in this environment")
        svc = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
        families = svc._compose()
        watchtower = next((f for f in families if f.get("family_name") == "WATCHTOWER"), None)
        b48k_dv34 = next((f for f in families if "B48k" in str(f.get("family_name", ""))), None)
        if watchtower is None or b48k_dv34 is None:
            pytest.skip("WATCHTOWER or B48k/Dv34 not present in this database snapshot")
        absorbed_ids = watchtower.get("absorbed_family_ids") or []
        assert not any("0a1cc08d9cdc33b1" in str(fid) for fid in absorbed_ids), (
            "B48k/Dv34 must never be absorbed into WATCHTOWER without satisfying "
            "the full merge contract"
        )
        assert b48k_dv34.get("promoted_to_operation_id") is None

    def test_merge_evaluation_is_explainable(self):
        try:
            from src.ops.emerging_operator_service import EmergingOperatorService
            from src.core.db import OPS_DB_PATH, DB_PATH
        except Exception:
            pytest.skip("EmergingOperatorService unavailable in this environment")
        svc = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
        families = svc._compose()
        b48k_dv34 = next((f for f in families if "B48k" in str(f.get("family_name", ""))), None)
        if b48k_dv34 is None:
            pytest.skip("B48k/Dv34 not present in this database snapshot")
        evaluations = b48k_dv34.get("merge_evaluations") or []
        assert evaluations, "expected at least one merge evaluation to be recorded for explainability"
        watchtower_eval = next(
            (e for e in evaluations if "04265d9f" in e.get("canonical_id", "")), None
        )
        assert watchtower_eval is not None
        assert watchtower_eval["allowed"] is False
        assert watchtower_eval["satisfied_criteria"]
        assert watchtower_eval["unsatisfied_criteria"]
        assert "identity_signal_threshold" in {c["name"] for c in watchtower_eval["unsatisfied_criteria"]}


class TestDiscoveryNeverMerges:
    """PHASE 7 -- Discovery must never merge identities; only
    OperatorIdentityGovernanceService.merge() (a deliberate, human-invoked
    action reached solely through its own HTTP endpoint) may."""

    def test_no_write_statements_in_discovery_modules(self):
        import glob
        discovery_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "discovery"))
        offenders = []
        for path in glob.glob(os.path.join(discovery_dir, "*.py")):
            with open(path) as f:
                content = f.read()
            for keyword in ("INSERT INTO", "UPDATE ", "DELETE FROM", ".commit("):
                if keyword in content:
                    offenders.append((path, keyword))
        assert not offenders, f"Discovery modules must never write: found {offenders}"

    def test_no_merge_calls_in_discovery_modules(self):
        import glob
        discovery_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "discovery"))
        offenders = []
        for path in glob.glob(os.path.join(discovery_dir, "*.py")):
            with open(path) as f:
                content = f.read()
            if ".merge(" in content:
                offenders.append(path)
        assert not offenders, f"Discovery modules must never call .merge(): found {offenders}"
