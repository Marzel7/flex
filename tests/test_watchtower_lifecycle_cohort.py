import pytest

from src.ops.watchtower_lifecycle_cohort import (
    build_cohort_authorization, eligible_authorization_records, request_identity,
)


def _record(rank, mint, created=100):
    return {"rank": rank, "mint": mint, "creation_timestamp": created,
            "acquisition_status": "COMPLETED_QUALIFIED_OBSERVATION"}


def test_cohort_authorization_is_exact_and_excludes_prior_lifecycle_identity():
    opening = [_record(1, "mint-one"), _record(2, "mint-two"), {"rank": 3, "acquisition_status": "UNACQUIRED_NO_QUALIFIED_ANCHOR"}]
    excluded = {request_identity({"rank": 1, "mint": "mint-one", "creation_timestamp": 100, "time_from": 100, "time_to": 864100, "interval": "15m"})}
    records = eligible_authorization_records(opening, blocked_identities=excluded, blocked_mints=set())
    authorization = build_cohort_authorization(records, chronology_hash="chronology", authority_id="test")
    assert [item["rank"] for item in authorization["records"]] == [2]
    assert authorization["max_total_paid_requests"] == 1
    assert authorization["records"][0]["time_to"] == 864100
    assert authorization["historical_limit_per_60s"] == 10


def test_cohort_authorization_rejects_duplicate_or_non_15m_membership():
    record = {"rank": 1, "mint": "mint", "creation_timestamp": 1, "time_from": 1, "time_to": 864001, "interval": "15m"}
    record["content_hash"] = request_identity(record)
    with pytest.raises(ValueError, match="MEMBERSHIP"):
        build_cohort_authorization([record, dict(record)], chronology_hash="c", authority_id="a")
