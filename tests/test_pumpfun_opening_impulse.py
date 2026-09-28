import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.ops.pumpfun_opening_impulse import reconstruct_opening_impulse


ROOT = Path(__file__).resolve().parents[1]


def _byzantine():
    stream = json.loads((ROOT / "docs/audits/pumpfun_first_slot_complete_replay/first_slot_state_stream.v1.json").read_text())
    target = stream["target"]
    return reconstruct_opening_impulse(
        mint=target["mint"], creation_signature="retained-creation-signature",
        creation_slot=target["creation_slot"], creation_time=1788250333,
        creator="J3yZQYJECzT3PD9BpzQzSM4FKENrDeVSCtmxdFSNThMy", bonding_curve=target["curve"],
        associated_bonding_curve=target["associated_curve"], supply_raw=1_000_000_000_000_000,
        decimals=6, opening_slot=target["slot"], actions=stream["successful_state_actions"],
        archived_prebuy_state={"source": "retained Alchemy archive"},
    )


def _watchtower():
    stream = json.loads((ROOT / "docs/audits/watchtower_buy3_opening_mc/first_slot_decoded_stream.v1.json").read_text())
    return reconstruct_opening_impulse(
        mint=stream["mint"], creation_signature=stream["actions"][0]["signature"],
        creation_slot=stream["slot"], creation_time=stream["block_time"],
        creator="ErakdZBCte7528cWXCeQddxu8ehHsk2Snp6A2Qkb6dmm", bonding_curve="776PqgJmvBpVUmcEpqyztGsxBjCubbkwfNinwDjtVwUX",
        associated_bonding_curve="3VAxJ8vMVU41uRnR1Y1dvZMgo7Sd6bBg2PWgCuRUUDF3", supply_raw=1_000_000_000_000_000,
        decimals=6, opening_slot=stream["slot"], actions=stream["actions"],
    )


def test_byzantine_retained_reference_parity():
    result = _byzantine()
    assert result.first_independent_buy_mc_sol.quantize(Decimal(".000000001")) == Decimal("157.774174034")
    assert result.opening_slot_peak_mc_sol.quantize(Decimal(".000000001")) == Decimal("162.171622209")
    assert result.opening_slot_end_mc_sol.quantize(Decimal(".000000001")) == Decimal("161.005185916")


def test_watchtower_retained_reference_parity_and_creator_exclusion():
    result = _watchtower()
    assert result.first_buy_mc_sol.quantize(Decimal(".00000000000001")) == Decimal("31.76204916505928")
    assert result.opening_slot_peak_mc_sol.quantize(Decimal(".0000000000001")) == Decimal("410.8801681207574")
    assert result.opening_slot_end_mc_sol.quantize(Decimal(".0000000000001")) == Decimal("410.8801681207574")
    assert result.first_independent_buy_buyer == "DzVRD5tcci4An7YQ3Pacj1VRrDUcpdnEs84KJku6gQVy"


def test_ordering_sell_inclusion_and_missing_evidence():
    result = _byzantine()
    assert [a.action_type for a in result.ordered_actions] == ["BUY", "BUY", "BUY", "BUY", "BUY", "SELL", "BUY", "BUY"]
    assert [(a.slot, a.transaction_index, a.action_index) for a in result.ordered_actions] == sorted(
        (a.slot, a.transaction_index, a.action_index) for a in result.ordered_actions
    )
    with pytest.raises(ValueError, match="opening action evidence"):
        reconstruct_opening_impulse(mint="m", creation_signature="s", creation_slot=1, creation_time=None,
            creator="c", bonding_curve="b", associated_bonding_curve=None, supply_raw=1, decimals=0,
            opening_slot=1, actions=[])
