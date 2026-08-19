"""B2Z-P2G: focused tests for resolve_creator()'s exact signer-extraction
semantics, using realistic jsonParsed transaction fixture shapes.

These tests do not touch any live ledger, database, or network -- they
exercise src/acquisition/b2z_execution_boundary.py in isolation to
precisely document its actual behavior (governing contract: "creator" =
the migration transaction's sole signer, NOT the CREATE-transaction
creator).
"""
from __future__ import annotations

import pytest

from src.acquisition.b2z_execution_boundary import resolve_creator


def migration_tx(*, mint, account_keys, block_time=100):
    return {
        "blockTime": block_time,
        "transaction": {"message": {"accountKeys": account_keys}},
    }


def test_single_signer_resolved_as_creator():
    """The straightforward case: exactly one signer, that signer becomes 'creator'."""
    keys = [
        {"pubkey": "signer-wallet", "signer": True},
        {"pubkey": "mint-abc", "signer": False},
        {"pubkey": "system-program", "signer": False},
    ]
    creator, block_time = resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")
    assert creator == "signer-wallet"
    assert block_time == 100


def test_multiple_signers_raises_ambiguous():
    """Real jsonParsed migration transactions sometimes have more than one
    signer (e.g. a multi-sig or an additional fee-relayer signer) -- B2Z
    treats this as unresolvable rather than guessing which signer is the
    creator."""
    keys = [
        {"pubkey": "signer-one", "signer": True},
        {"pubkey": "signer-two", "signer": True},
        {"pubkey": "mint-abc", "signer": False},
    ]
    with pytest.raises(ValueError, match="B2Z_CREATOR_AMBIGUOUS"):
        resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")


def test_zero_signers_raises_ambiguous():
    keys = [
        {"pubkey": "mint-abc", "signer": False},
        {"pubkey": "system-program", "signer": False},
    ]
    with pytest.raises(ValueError, match="B2Z_CREATOR_AMBIGUOUS"):
        resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")


def test_string_only_account_keys_never_treated_as_signer():
    """If accountKeys are returned as bare pubkey strings (no signer flag at
    all -- a real possible jsonParsed response shape variant), _account_keys()
    normalizes every one to signer=False. This means a response shaped this
    way ALWAYS raises B2Z_CREATOR_AMBIGUOUS (zero signers found) rather than
    silently guessing a signer from position -- fail-closed, not a fallback
    to e.g. accountKeys[0]."""
    keys = ["signer-wallet", "mint-abc", "system-program"]
    with pytest.raises(ValueError, match="B2Z_CREATOR_AMBIGUOUS"):
        resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")


def test_fee_payer_position_is_not_special_cased():
    """resolve_creator() does NOT assume accountKeys[0] (the conventional fee
    payer position in Solana transactions) is the creator -- it relies
    ENTIRELY on the explicit signer=True flag, wherever it appears. This
    test proves position is irrelevant: putting the real signer LAST still
    resolves correctly."""
    keys = [
        {"pubkey": "some-program", "signer": False},
        {"pubkey": "mint-abc", "signer": False},
        {"pubkey": "the-real-signer", "signer": True},  # last position, not first
    ]
    creator, _ = resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")
    assert creator == "the-real-signer"


def test_mint_not_present_raises():
    keys = [{"pubkey": "signer-wallet", "signer": True}, {"pubkey": "other-mint", "signer": False}]
    with pytest.raises(ValueError, match="B2Z_MINT_NOT_IN_MIGRATION"):
        resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")


def test_missing_block_time_raises():
    keys = [{"pubkey": "signer-wallet", "signer": True}, {"pubkey": "mint-abc", "signer": False}]
    tx = {"transaction": {"message": {"accountKeys": keys}}}  # no blockTime key at all
    with pytest.raises(ValueError, match="B2Z_MIGRATION_TIME_MISSING"):
        resolve_creator(tx, "mint-abc")


def test_shared_migration_signer_across_different_mints_is_the_documented_contract_behavior():
    """This is the CENTRAL finding of B2Z-P2G: resolve_creator() extracts
    the migration transaction's sole SIGNER, which is a genuinely different
    concept from the CREATE-transaction creator (token_analysis.pf_ws_creator).
    If the SAME wallet legitimately signs the migration transaction for
    MULTIPLE DIFFERENT tokens it did not create (e.g. acting as an automated
    migration-completion agent/bot), resolve_creator() will correctly and
    deterministically return that SAME address as "creator" for all of
    them -- this is NOT a bug, it is exactly what the function's documented
    single-signer contract does. The prior B2Z-P1.6 milestone explicitly
    flagged this as an unproven structural assumption before it was ever
    observed live (see docs/audits/b2z_p1_6_historical_creator_funding_data_lineage_reconstruction.json,
    part4_writer_traced.critical_distinction_from_b2z)."""
    shared_signer = "9C4nRvhhVquCKATjDCx5FKvNS9PNgNqgyWy9AcoDjYv5"
    for mint in ["mint-A", "mint-B", "mint-C"]:
        keys = [
            {"pubkey": shared_signer, "signer": True},
            {"pubkey": mint, "signer": False},
        ]
        creator, _ = resolve_creator(migration_tx(mint=mint, account_keys=keys), mint)
        assert creator == shared_signer
    # this is deterministic and reproducible -- NOT a bug in resolve_creator(),
    # a correct application of its documented contract to an input shape it
    # was never proven to handle differently


def test_creator_and_signer_flag_missing_key_defaults_false():
    """A dict entry with a pubkey but no 'signer' key at all (malformed but
    plausible response variant) must default to signer=False via
    key.get('signer') -- not raise a KeyError and not be silently treated
    as a signer."""
    keys = [
        {"pubkey": "no-signer-flag-at-all"},  # no "signer" key present
        {"pubkey": "mint-abc", "signer": False},
    ]
    with pytest.raises(ValueError, match="B2Z_CREATOR_AMBIGUOUS"):
        resolve_creator(migration_tx(mint="mint-abc", account_keys=keys), "mint-abc")
