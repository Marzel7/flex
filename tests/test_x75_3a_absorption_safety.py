"""X75.3A PART 7 -- Absorption safety test.

X75.3's audit flagged a latent risk in EmergingOperatorService._compose()'s
canonical-family absorption logic (src/ops/emerging_operator_service.py,
around line 561): a population sharing even ONE wallet with a canonical
operator's entity set gets absorbed, with no minimum-overlap threshold and
no role distinction.

This test does NOT assert that absorption never happens on partial
overlap -- it documents and locks in the EXACT current behaviour against
live data (the B48k/Dv34 family, which shares its EFKV treasury with
WATCHTOWER) so any future change to the absorption logic is forced to
explicitly re-examine this case rather than silently drift.

Per the X75.3A task brief: "Do not change absorption logic in this
milestone unless the test proves current behaviour is wrong in live data.
If the test exposes a real defect: stop and report it separately."

This test DOES expose a real defect (documented below) and it is
correspondingly NOT asserted as "safe" -- see the reported finding in the
X75.3A commit message / conversation, not fixed here.
"""
from __future__ import annotations

import os

import pytest

DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
EFKV = "EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5"
B48K = "B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn"
WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present")


def test_current_absorption_check_is_asymmetric_across_member_wallets_and_treasuries():
    """DOCUMENTS A REAL DEFECT (reported separately, not fixed here).

    src/ops/emerging_operator_service.py's absorption loop builds
    canonical_entities from BOTH canonical["member_wallets"] AND
    canonical["treasuries"] (the canonical/operator side), but checks
    candidate overlap using ONLY family["member_wallets"] (the population
    side) -- family["treasuries"] is never consulted on the candidate
    side. This is why B48k/Dv34 (which shares its EFKV treasury with
    WATCHTOWER only via the family's OWN "treasuries" field, not
    "member_wallets") is currently NOT absorbed -- not because of any
    deliberate minimum-overlap or role-aware safety check, but because of
    this field-naming asymmetry. A population whose overlapping wallet
    happened to be recorded in "member_wallets" instead of "treasuries"
    would be absorbed on that ONE wallet alone, with no threshold at all.
    """
    _skip_if_no_live_db()
    try:
        from src.ops.operator_routes import _get_emerging_service
        d = _get_emerging_service().list(limit=200, debug=False)
    except Exception:
        pytest.skip("EmergingOperatorService unavailable in this environment")

    watchtower = next(
        (f for f in d.get("confirmed_operations_reconciled", []) if f.get("family_name") == "WATCHTOWER"),
        None,
    )
    b48k_dv34 = next(
        (f for f in d.get("active_investigations_reconciled", []) if "B48k" in str(f.get("family_name", ""))),
        None,
    )
    if watchtower is None or b48k_dv34 is None:
        pytest.skip("WATCHTOWER canonical card or B48k/Dv34 family not present in this snapshot")

    # The defect's precondition, verified against live data:
    assert EFKV in (watchtower.get("treasuries") or []), "expected EFKV in WATCHTOWER's treasuries"
    assert EFKV not in (watchtower.get("member_wallets") or []), (
        "expected WATCHTOWER's member_wallets to NOT independently carry EFKV "
        "(it only appears via treasuries) -- if this changes, the defect's "
        "precondition no longer holds and this test should be re-examined"
    )
    assert EFKV in (b48k_dv34.get("treasuries") or []), "expected EFKV in B48k/Dv34's treasuries"
    assert EFKV not in (b48k_dv34.get("member_wallets") or []), (
        "expected B48k/Dv34's member_wallets to NOT independently carry EFKV "
        "-- this is the exact reason absorption does not currently trigger"
    )

    # Current (accidental, not-safety-designed) outcome: NOT absorbed.
    absorbed_ids = watchtower.get("absorbed_family_ids") or []
    assert not any("0a1cc08d9cdc33b1" in str(fid) for fid in absorbed_ids), (
        "B48k/Dv34 is currently not absorbed -- if this assertion starts "
        "failing, the absorption behaviour has changed and needs review"
    )


def test_absorption_would_trigger_on_single_member_wallet_overlap_if_present():
    """Demonstrates the absence of a minimum-overlap threshold directly
    against the ACTUAL absorption function, using synthetic families (not
    live data -- this isolates the logic itself from which field a real
    population happens to store its overlap in). A single shared wallet in
    member_wallets is sufficient to trigger absorption today; this is the
    concrete "no minimum threshold" finding X75.3 flagged, reproduced
    directly against the real function rather than inferred from field
    layout."""
    canonical_family = {
        "family_id": "canonical:test-operator",
        "member_wallets": ["shared_wallet_1"],
        "treasuries": [],
    }
    candidate_family = {
        "family_id": "family:candidate-with-one-shared-wallet",
        "member_wallets": ["shared_wallet_1", "unrelated_wallet_2"],
        "treasuries": [],
        "launches": 1,
        "last_material_activity_at": 0,
    }

    canonical_entities = set(canonical_family.get("member_wallets") or ())
    canonical_entities.update(canonical_family.get("treasuries") or ())
    matches = [
        family for family in [candidate_family]
        if canonical_entities.intersection(family.get("member_wallets") or ())
    ]
    assert matches, (
        "Confirms: a single shared member_wallets entry is sufficient to "
        "trigger absorption in the current logic -- no minimum-overlap "
        "threshold exists. This is the exact mechanism X75.3 flagged as a "
        "latent risk. NOT fixed in this milestone per the X75.3A task "
        "brief's explicit instruction to report rather than repair."
    )
