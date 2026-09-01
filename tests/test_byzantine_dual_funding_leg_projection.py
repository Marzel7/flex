"""Read-only Byzantine economic-leg presentation contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.ops import operator_reader
from src.ops.operation_summary import build_operation_summary


ROOT = Path(__file__).resolve().parents[1]
BYZ = "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY"


def _digest(mints: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(mints), separators=(",", ":")).encode()).hexdigest()


def test_frozen_artifacts_project_the_expected_independent_leg_counts():
    legs = operator_reader._byzantine_dual_leg_evidence()
    assert len(legs) == 182
    upstream = {mint for mint, row in legs.items() if row["upstream_funding"].get("state") == "PROVEN"}
    creator = {mint for mint, row in legs.items() if row["creator_provisioning"].get("state") == "PROVEN"}
    assert (len(upstream), len(creator), len(upstream & creator)) == (105, 168, 91)
    assert len(upstream - creator) == 14
    assert len(creator - upstream) == 77
    assert len(set(legs) - upstream - creator) == 0
    assert "3dgGSHC49cbcmdjuZD6y1WDAtsP91v7j8Z1hTey9pump" in upstream & creator


def test_upstream_only_mint_has_no_inferred_creator_leg():
    row = operator_reader._byzantine_dual_leg_evidence()["2GAd4qECH8cN4oQWwhiaBMADNupKCv8h9m9x3a4mpump"]
    assert row["upstream_funding"]["state"] == "PROVEN"
    assert row["creator_provisioning"]["state"] != "PROVEN"


def test_creator_only_wsol_mint_has_no_inferred_upstream_leg():
    row = operator_reader._byzantine_dual_leg_evidence()["2Br5iZyB5w7W2LhXhKVo6NFUFmoyjWmuDyQR1Edspump"]
    assert row["upstream_funding"]["state"] != "PROVEN"
    assert row["creator_provisioning"]["mechanism"] == "WSOL_WRAP_CLOSE"


def test_reference_mint_exposes_both_economic_roles():
    row = operator_reader._byzantine_dual_leg_evidence()["3dgGSHC49cbcmdjuZD6y1WDAtsP91v7j8Z1hTey9pump"]
    assert row["upstream_funding"]["state"] == "PROVEN"
    assert row["creator_provisioning"]["state"] == "PROVEN"


def test_neither_leg_is_explicitly_unavailable():
    rows = [{"mint": "not-retained"}]
    operator_reader._enrich_byzantine_dual_legs(rows)
    assert rows[0]["upstream_funding"]["state"] == "NOT_RETAINED"
    assert rows[0]["creator_provisioning"]["state"] == "NOT_RETAINED"


def test_selected_walkback_trace_is_separate_and_subprov_never_proves_a_leg(monkeypatch):
    monkeypatch.setattr(operator_reader, "_byzantine_dual_leg_evidence", lambda: {})
    rows = [{"mint": "legacy-only", "subprov_wallet": BYZ, "wrap_close_signature": "SELECTED_SIG"}]
    operator_reader._enrich_byzantine_dual_legs(rows)
    assert rows[0]["selected_walkback_tx"] == "SELECTED_SIG"
    assert rows[0]["upstream_funding"]["state"] == "NOT_RETAINED"
    assert rows[0]["creator_provisioning"]["state"] == "NOT_RETAINED"


def test_summary_preserves_dual_legs_and_relabels_selected_trace_without_membership_change():
    mint = "3dgGSHC49cbcmdjuZD6y1WDAtsP91v7j8Z1hTey9pump"
    row = {"mint": mint, "creator_wallet": "creator", "subprov_wallet": BYZ,
           "wrap_close_signature": "SELECTED", "create_time": 10}
    operator_reader._enrich_byzantine_dual_legs([row])
    summary = build_operation_summary({"recent_launches": [row]}, [])
    launch = summary["all_launches"][0]
    assert launch["selected_walkback_tx"] == "SELECTED"
    assert launch["upstream_funding"]["state"] == "PROVEN"
    assert launch["creator_provisioning"]["state"] == "PROVEN"
    assert launch["intermediary"] == BYZ


def test_shared_historical_signature_remains_creator_level_evidence():
    results = json.loads((ROOT / "docs/audits/byzantine_96_exact_signature_rpc/verification_results.v1.json").read_text())["results"]
    shared = next(row for row in results if row["MINT_LINKAGE_SUPPORTED"] == "CREATOR_LEVEL_SUPPORTED")
    assert shared["BYZANTINE_VERIFICATION_CLASS"] == "CONFIRMED_BYZ_CREATOR_WSOL"
    assert shared["PROVISION_SOURCE"] == BYZ


def test_frozen_membership_and_ui_labels_remain_explicit():
    baseline = json.loads((ROOT / "docs/audits/byzantine_surfaced_mint_cohort_compatibility_freeze.v1.json").read_text())
    legs = operator_reader._byzantine_dual_leg_evidence()
    assert _digest(list(legs)) == baseline["sorted_mint_membership_digest"]
    template = (ROOT / "templates/operator_intelligence.html").read_text()
    assert "leg('Upstream'" in template
    assert "leg('Creator'" in template
    assert "Selected Walkback Tx" in template
    assert "Funding Tx '+esc(short(selected))" not in template


def test_selected_walkback_trace_is_never_hidden_when_it_matches_an_economic_leg():
    template = (ROOT / "templates/operator_intelligence.html").read_text()
    # The technical trace is intentionally independent of the economic role.
    # These cover upstream-only, creator-only, shared-leg, and no-leg rows:
    # once `selected` is present, the template must retain its link.
    assert "Selected Walkback Tx '+esc(short(selected))+' ↗" in template
    assert "if (legs && selected &&" not in template
    assert ".signature===selected)) tx=''" not in template


def test_reader_projection_has_no_rpc_or_activity_metric_mutation_path():
    source = (ROOT / "src/ops/operator_reader.py").read_text()
    dual_leg_source = source[source.index("def _byzantine_dual_leg_evidence"):source.index("def _enrich_byzantine_dual_legs")]
    assert "getTransaction" not in dual_leg_source
    assert "conn.execute" not in dual_leg_source
    assert "DISTINCT funder_block_time" in source
