import sqlite3

from src.ops.operation_confirmation import (
    OperationConfirmationService, apply_confirmation_overlay, readiness_for,
)


def family(mints=("M1", "M2")):
    return {
        "family_id": "family:b48", "family_name": "B48k / Dv34 Family",
        "family_anchor": "B48", "stage": "EMERGING", "lifecycle_state": "EMERGING",
        "status": "Emerging", "review_label": "Emerging", "review_status": "MONITORING",
        "promotion_status": "REVIEWABLE", "state_changed_at": 100, "previous_stage": "CANDIDATE",
        "launch_list": list(mints), "observed_launches": len(mints), "launches": len(mints),
        "member_wallets": ["B48", "Dv34"], "treasuries": ["T1"],
        "funding_mechanisms": ["PLAIN_XFER"], "dominant_topology": "Treasury → client → creator",
        "contradictions": [], "exclusion_evidence": [],
        "cohesion": {"conflicts": []}, "blocking_reasons": [],
        "evidence_completeness": {"score": 80}, "operational_maturity": {"score": 70},
        "discovery_timeline": [], "evidence_timeline": [], "growth_timeline": [],
    }


class Registry:
    def __init__(self, item):
        self.item = item
        self._cached_families = [item]

    def get(self, family_id):
        return self.item if family_id == self.item["family_id"] else None


def test_readiness_distinguishes_evidence_from_confirmation():
    result = readiness_for(family())
    assert result["ready"] is True
    assert result["evidence_coverage"] == 80
    assert result["analyst_decision_required"] is True
    assert "never confirms" in result["explanation"]


def test_confirmation_persists_metadata_and_audit_without_token_rows(tmp_path):
    path = str(tmp_path / "ops.db")
    sqlite3.connect(path).close()
    item = family()
    registry = Registry(item)
    service = OperationConfirmationService(path, registry)
    # Return the projected confirmed shape after the write.
    def get(family_id):
        projected = family()
        apply_confirmation_overlay([projected], path)
        return projected
    registry.get = get
    result = service.confirm("family:b48", analyst="analyst", reason="Evidence reviewed", notes="Reference case")
    assert result["lifecycle_state"] == "CONFIRMED"
    assert result["family_id"] == "family:b48"
    assert result["launch_list"] == ["M1", "M2"]
    assert result["confirmation"]["confirmed_by"] == "analyst"
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM wt_operation_family_confirmations").fetchone()[0] == 1
    assert conn.execute("SELECT action FROM wt_operation_family_confirmation_audit").fetchone()[0] == "CONFIRMED"
    assert not any("mint" in row[1].lower() for row in conn.execute("PRAGMA table_info(wt_operation_family_confirmations)"))
    conn.close()
    reversed_family = service.reverse("family:b48", analyst="reviewer", reason="Explicit correction")
    assert reversed_family["lifecycle_state"] == "EMERGING"
    assert reversed_family["confirmation"]["confirmed"] is False
    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM wt_operation_family_confirmation_audit").fetchone()[0] == 2
    check.close()


def test_future_launch_inherits_confirmation_by_family_id(tmp_path):
    path = str(tmp_path / "ops.db")
    conn = sqlite3.connect(path)
    from src.ops.operation_confirmation import SCHEMA
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO wt_operation_family_confirmations "
        "(family_id,confirmed,confirmed_at,confirmed_by,confirmation_reason,previous_lifecycle,updated_at) "
        "VALUES ('family:b48',1,200,'analyst','reviewed','EMERGING',200)"
    )
    conn.commit(); conn.close()
    rebuilt = family(("M1", "M2", "FUTURE_MINT"))
    apply_confirmation_overlay([rebuilt], path)
    assert rebuilt["lifecycle_state"] == "CONFIRMED"
    assert "FUTURE_MINT" in rebuilt["launch_list"]
    assert len([x for x in rebuilt["growth_timeline"] if x["event_type"] == "OPERATION_CONFIRMED"]) == 1


def test_profile_contains_confirmation_workflow():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "templates" / "operation_profile.html").read_text()
    assert "Promotion" in source
    assert "Advanced Evidence" in source
    assert "Confirm Operation" in source
    assert "pres.confirmation_permitted&&disp==='OPERATOR_CANDIDATE'" in source
    assert "/api/operators/promotions/" in source
    assert "OPERATOR CANDIDATE" in source


def test_cached_operational_intelligence_refreshes_only_attribution(monkeypatch):
    from src.ops import operation_attribution, operational_intelligence
    class Resolver:
        def __init__(self, *_): pass
        def resolve_many(self, mints):
            return {mint: {
                "operation_id": "family:b48", "family_id": "family:b48",
                "operation_name": "B48k / Dv34 Family", "lifecycle": "CONFIRMED",
                "state": "CONFIRMED_OPERATION", "confidence": "CONFIRMED",
                "evidence_source": "operation_registry", "registry_version": "operation-registry-v1",
                "profile_href": "/profile", "timeline_href": "/timeline", "evidence_href": "/evidence",
            } for mint in mints}
    monkeypatch.setattr(operation_attribution, "OperationAttributionService", Resolver)
    intelligence = {"records": {"M1": {"topology": "FAN_OUT", "performance": 123}}}
    operational_intelligence.refresh_registry_attribution(intelligence, "ops", "core")
    assert intelligence["records"]["M1"]["operation_state"] == "CONFIRMED_OPERATION"
    assert intelligence["records"]["M1"]["topology"] == "FAN_OUT"
    assert intelligence["records"]["M1"]["performance"] == 123
