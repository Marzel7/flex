import pytest

from src.ops.treasury_cascade_coverage import (
    COMPLETE, DECODED, FAILED, HANDOFF_DEFERRED, HANDOFF_EMITTED, INCOMPLETE,
    CompactFundingFact, SelectedPage, emit_nonblocking_handoff, report_address_coverage,
    load_selected_page, open_isolated_coverage_store, persist_selected_page,
    TreasurySubscriptionCandidate, reconciliation_priority, select_subscription_pool,
)


def test_29_of_83_selection_is_explicit_per_address_and_not_silently_complete():
    confirmed = [f"treasury-{i:03d}" for i in range(83)]
    subscribed = confirmed[:29]
    rows = report_address_coverage(confirmed_treasuries=confirmed, subscribed=subscribed,
                                   cursors={wallet: f"sig-{wallet}" for wallet in confirmed}, activity={})
    assert len(rows) == 83
    assert sum(row.subscription_status == "SUBSCRIBED" for row in rows) == 29
    assert sum(row.subscription_status == "RECONCILIATION_ONLY" for row in rows) == 54
    assert all(row.cursor_signature for row in rows)


def test_unknown_subscription_address_rejects_coverage_report():
    with pytest.raises(ValueError, match="SUBSCRIPTION_OUTSIDE_CONFIRMED_SET"):
        report_address_coverage(confirmed_treasuries=["t"], subscribed=["not-confirmed"], cursors={}, activity={})


def test_incomplete_or_unsupported_page_cannot_advance_and_replays_prior_cursor():
    page = SelectedPage("t", "prior", ("a", "b"), {})
    page.record("a", DECODED)
    page.record("b", FAILED)
    assert page.coverage_status == INCOMPLETE
    assert page.compact_record()["prior_cursor"] == "prior"
    with pytest.raises(ValueError, match="CURSOR_ADVANCE_REQUIRES_COMPLETE_PAGE"):
        page.advance_cursor("new")
    replay = SelectedPage("t", page.prior_cursor, page.signatures, {})
    replay.record("a", DECODED)
    replay.record("b", DECODED)
    assert replay.coverage_status == COMPLETE
    assert replay.advance_cursor("new") == "new"


def test_incomplete_page_persists_across_restart_only_in_isolated_store(tmp_path):
    path = f"/private/tmp/dev019-cascade-coverage-{tmp_path.name}.sqlite"
    page = SelectedPage("t", "prior", ("a",), {})
    page.record("a", FAILED)
    first = open_isolated_coverage_store(path)
    persist_selected_page(first, page)
    first.close()
    second = open_isolated_coverage_store(path)
    restored = load_selected_page(second, treasury="t", prior_cursor="prior")
    second.close()
    assert restored and restored.coverage_status == INCOMPLETE
    with pytest.raises(ValueError, match="COVERAGE_STORE_MUST_BE_ISOLATED"):
        open_isolated_coverage_store("/private/var/folders/not-allowed.sqlite")


def test_duplicate_signature_and_outcome_outside_page_fail_closed():
    with pytest.raises(ValueError, match="DUPLICATE_SELECTED_SIGNATURE"):
        SelectedPage("t", None, ("a", "a"), {})
    page = SelectedPage("t", None, ("a",), {})
    with pytest.raises(ValueError, match="INVALID_DECODE_OUTCOME"):
        page.record("other", DECODED)


def _fact(**changes):
    base = dict(sender="treasury", receiver="funding", signature="sig", slot=7,
                transaction_index=None, instruction_index=2, lamports=10,
                sender_balance_delta=-10, receiver_balance_delta=10,
                provenance="WS_CASCADE_RPC_VERIFIED", coverage_status=COMPLETE)
    base.update(changes)
    return CompactFundingFact(**base)


def test_compact_handoff_requires_verified_complete_direct_fact_and_is_idempotent_keyed():
    fact = _fact()
    seen = []
    assert emit_nonblocking_handoff(fact=fact, consumer=seen.append)["status"] == HANDOFF_EMITTED
    assert seen[0]["transaction_index"] is None
    assert fact.idempotency_key == "sig:2:treasury:funding"
    with pytest.raises(ValueError, match="INVALID_COMPACT_FUNDING_FACT"):
        _fact(coverage_status=INCOMPLETE).compact_record()


def test_consumer_failure_isolated_and_introduces_no_provider_call():
    result = emit_nonblocking_handoff(fact=_fact(), consumer=lambda _: (_ for _ in ()).throw(RuntimeError("down")))
    assert result["status"] == HANDOFF_DEFERRED
    assert result["cascade_blocked"] is False
    assert result["provider_calls"] == 0


def test_exactly_eight_prioritizes_hot_and_active_verified_material_funding():
    candidates = [TreasurySubscriptionCandidate(f"hot-{i}", "HOT", 200 - i) for i in range(2)]
    candidates += [TreasurySubscriptionCandidate(f"active-{i}", "ACTIVE", 100 - i) for i in range(10)]
    result = select_subscription_pool(candidates=candidates, now=1_000)
    assert len(result["selected"]) == 8
    assert result["selected"][:2] == ["hot-0", "hot-1"]
    assert result["identity_mutations"] == 0


def test_fewer_than_eight_when_evidence_is_insufficient_and_transaction_only_is_not_eligible():
    candidates = [TreasurySubscriptionCandidate("material", "DORMANT", 5)]
    candidates += [TreasurySubscriptionCandidate(f"tx-only-{i}", "HOT", None) for i in range(82)]
    result = select_subscription_pool(candidates=candidates, now=1_000)
    assert result["selected"] == ["material"]
    assert len(result["reconciliation_only"]) == 82


def test_reactivation_can_displace_expired_lower_priority_slot_but_not_protected_dwell_slot():
    old = TreasurySubscriptionCandidate("old", "DORMANT", 1, subscribed_at=0)
    reactivated = TreasurySubscriptionCandidate("new", "DORMANT", None, reactivated_at=100)
    assert select_subscription_pool(candidates=[old, reactivated], now=10_000, slot_count=1)["selected"] == ["new"]
    protected_old = TreasurySubscriptionCandidate("old", "DORMANT", 1, subscribed_at=9_500)
    assert select_subscription_pool(candidates=[protected_old, reactivated], now=10_000, slot_count=1)["selected"] == ["old"]


def test_all_confirmed_remain_reconciliation_eligible_and_launch_backward_overrides_activity():
    dormant = TreasurySubscriptionCandidate("d", "RETIRED_CANDIDATE", None)
    active = TreasurySubscriptionCandidate("a", "ACTIVE", 1)
    assert reconciliation_priority(dormant, launch_backward_evidence=False) == "DAILY_RECONCILIATION"
    assert reconciliation_priority(active, launch_backward_evidence=False) == "HIGH_PRIORITY_RECONCILIATION"
    assert reconciliation_priority(dormant, launch_backward_evidence=True) == "TARGETED_RECONCILIATION"
