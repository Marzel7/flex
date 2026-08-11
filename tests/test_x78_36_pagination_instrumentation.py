import asyncio
import inspect

from src.extractors import realtime_creator_funding_extractor as funding


def test_pagination_ledger_is_task_local_and_bounded():
    first = {"creator": "A"}
    second = {"creator": "B"}
    first_token = funding._ACTIVE_PHASE_LEDGER.set(first)
    try:
        first_pagination = funding._pagination_ledger()
        first_pagination["pages"].append({"page": 1})
    finally:
        funding._ACTIVE_PHASE_LEDGER.reset(first_token)

    second_token = funding._ACTIVE_PHASE_LEDGER.set(second)
    try:
        second_pagination = funding._pagination_ledger()
        assert second_pagination["pages"] == []
        assert second_pagination is not first_pagination
        assert second_pagination["schema_version"] == "x78.36-v1"
    finally:
        funding._ACTIVE_PHASE_LEDGER.reset(second_token)


def test_no_ledger_is_created_outside_an_extraction_context():
    assert funding._pagination_ledger() is None


def test_instrumentation_does_not_change_frozen_limits_or_rpc_ceiling():
    assert funding.MAX_CONCURRENT_RPC == 8
    assert funding.MAX_PAGES == 8
    assert funding.FAST_FIRST_TX_PAGE_CAP == 3


def test_observation_records_explicit_incomplete_and_legacy_stop_reasons():
    source = inspect.getsource(funding.RealTimeCreatorFundingExtractor.extract_for_creator)
    assert "provider_timeout_incomplete" in source
    assert "provider_or_parser_error_incomplete" in source
    assert "legacy_first_tx_three_page_cap" in source
    assert "legacy_eight_page_cap" in source
    assert "legacy_thirty_day_cutoff" in source
    assert 'pagination["history_complete"] = True' in source
    assert "if page_funding_relevant_transfers > 0:" in source


def test_no_new_early_termination_or_coverage_reuse_was_added():
    source = inspect.getsource(funding.RealTimeCreatorFundingExtractor.extract_for_creator)
    assert "creator_history_coverage" not in source
    assert "x78_36_early_stop" not in source
    assert "deterministic_reuse" not in source
