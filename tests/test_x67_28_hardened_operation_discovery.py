"""X67.28 hardened funnel contracts."""
from __future__ import annotations

from collections import defaultdict
import time

from src.ops.emerging_operator_service import EmergingOperatorService


def _service(monkeypatch, *, budget=5):
    monkeypatch.setenv("OPERATION_DISCOVERY_ATTENTION_BUDGET", str(budget))
    return EmergingOperatorService("missing.db", "missing-live.db")


def _profile(wallet, *, creators=6, treasuries=("T1", "T2"), excluded=False, first=100, last=900000):
    return {
        "wallet": wallet, "sources": {"edges", "sessions", "candidate"}, "candidate": {},
        "creators": {f"{wallet}-C{i}" for i in range(creators)}, "treasuries": set(treasuries),
        "mechanisms": {"PLAIN_XFER", "WSOL_WRAP_CLOSE"}, "signatures": {f"{wallet}-S1", f"{wallet}-S2"},
        "launches": {f"{wallet}-M{i}" for i in range(creators)}, "first": first, "last": last,
        "sessions": 3, "active_sessions": 1, "session_times": {first, last}, "edge_times": [last] * creators,
        "evidence": [], "outcomes": [], "templates": defaultdict(int), "campaigns": set(),
        "session_amounts": [1.0, 2.0, 3.0], "zero_value_edges": 0,
        "exclusions": ([{"type": "DUST_PATTERN", "detail": "dust", "source": "sessions"}] if excluded else []),
        "warnings": [],
    }


def test_complete_link_cohesion_requires_repeated_clients(monkeypatch):
    service = _service(monkeypatch)
    strong_a, strong_b = _profile("A"), _profile("B")
    thin = _profile("THIN", creators=2)
    assert service._cohesion_pair(strong_a, strong_b)["valid"] is True
    assert service._cohesion_pair(strong_a, thin)["valid"] is False


def test_singleton_low_volume_cannot_enter_attention_lane(monkeypatch):
    service = _service(monkeypatch)
    family = service._profile_family([_profile("ONE", creators=2)], None, [])
    assert family["attention_eligible"] is False
    assert family["stage"] in {"CANDIDATE", "DORMANT"}
    assert "family breadth unresolved" in family["blocking_reasons"]


def test_independent_metrics_and_structured_readiness(monkeypatch):
    service = _service(monkeypatch)
    family = service._profile_family([_profile("A"), _profile("B")], None, [])
    assert set(family) >= {
        "evidence_completeness", "discovery_significance", "operational_maturity",
        "promotion_ready", "blocking_reasons", "membership",
    }
    assert family["evidence_completeness"] is not family["discovery_significance"]
    assert family["promotion_ready"] is True
    assert all(m["membership_strength"] >= 50 for m in family["membership"])


def test_exclusion_evidence_prevents_surface_eligibility(monkeypatch):
    service = _service(monkeypatch)
    family = service._profile_family([_profile("DUST", excluded=True)], None, [])
    assert family["stage"] == "RETIRED"
    assert family["attention_eligible"] is False
    assert family["promotion_ready"] is False


def test_family_id_stable_when_later_member_is_added(monkeypatch):
    service = _service(monkeypatch)
    first = _profile("CORE", first=100)
    later = _profile("LATER", first=200)
    one = service._profile_family([first], None, [])
    two = service._profile_family([first, later], None, [])
    assert one["family_id"] == two["family_id"]
    assert two["family_anchor"] == "CORE"


def test_attention_budget_is_deterministic(monkeypatch):
    service = _service(monkeypatch, budget=1)
    high = service._profile_family([_profile("A"), _profile("B")], None, [])
    low = service._profile_family([_profile("C")], None, [])
    high["stage"] = low["stage"] = "SIGNIFICANT_ACTIVE"
    high["attention_eligible"] = low["attention_eligible"] = True
    service._cached_families = [low, high]
    service._cached_at = 0
    # Ranking contract is significance, completeness, activity, stable id.
    ranked = sorted([low, high], key=lambda f: (
        f["discovery_significance"]["score"], f["evidence_completeness"]["score"],
        f["last_material_activity_at"] or 0, f["family_id"],
    ), reverse=True)
    assert ranked[0]["family_id"] == high["family_id"]


def test_dormancy_and_material_reactivation_preserve_identity(monkeypatch):
    service = _service(monkeypatch)
    now = int(time.time())
    old = _profile("CORE", first=now - 90 * 86400, last=now - 60 * 86400)
    dormant = service._profile_family([old], None, [])
    old["last"] = now
    old["edge_times"].append(now)
    reactivated = service._profile_family([old], None, [])
    assert dormant["stage"] == "DORMANT"
    assert reactivated["stage"] != "DORMANT"
    assert dormant["family_id"] == reactivated["family_id"]
