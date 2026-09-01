from src.ops.operation_summary import build_operation_summary


def test_initial_fee_payer_anchor_is_evidence_derived_and_recent_launches_are_bounded():
    rows = [{"mint": "M%s" % i, "creator_wallet": "C%s" % i, "parent_first_funder_fee_payer": "FUND" if i < 4 else "OLD", "parent_first_funder_signature": "S%s" % i, "parent_first_funder_timestamp": "2026-08-%02dT00:00:00+00:00" % (i + 1), "parent_first_funder_intermediate_source": "rpc", "mechanism": "WSOL_WRAP_CLOSE"} for i in range(5)]
    summary = build_operation_summary({"activity_snapshot": {"activity_state": "ACTIVE", "metrics": {"launches_last_7d": 5}}, "behavioural_profile": {"profile_version": 1, "provenance": {"atomic_sequence": ["create", "close"]}}}, rows)
    assert summary["primary_anchor"]["address"] == "FUND"
    assert summary["primary_anchor"]["qualification"] == "CONFIRMED_ANCHOR"
    assert len(summary["recent_launches"]) == 3
    assert summary["fingerprint"]["mechanism"] == "WSOL_WRAP_CLOSE"


def test_watchtower_treasury_uses_the_same_anchor_contract():
    summary = build_operation_summary({"entities": [{"entity_type": "TREASURY", "entity_address": "TREASURY", "evidence_count": 4}], "recent_launches": [{"mint": "M", "treasury_wallet": "TREASURY", "subprov_wallet": "SUB", "creator_wallet": "CREATOR", "funding_mechanism": "WSOL_WRAP_CLOSE"}]}, [])
    assert summary["primary_anchor"]["role"] == "TREASURY"
    assert summary["primary_anchor"]["address"] == "TREASURY"
    assert summary["fingerprint"]["route"] == "Treasury → sub-provider → creator"
    assert summary["fingerprint"]["mechanism"] == "WSOL_WRAP_CLOSE"


def test_non_p3r_retained_launches_use_the_same_bounded_summary_list():
    launches = [{"mint": "W%s" % i, "creator_wallet": "C%s" % i, "create_time": i, "treasury_wallet": "T"} for i in range(4)]
    summary = build_operation_summary({"recent_launches": launches}, [])
    assert [row["mint"] for row in summary["recent_launches"]] == ["W0", "W1", "W2"]
    assert len(summary["all_launches"]) == 4


def test_manual_profile_members_render_without_inventing_a_funding_path():
    summary = build_operation_summary({"behavioural_profile": {"member_mints": ["M1", "M2"]}}, [])
    assert [row["mint"] for row in summary["recent_launches"]] == ["M1", "M2"]
    assert summary["recent_launches"][0]["anchor"] is None
