import pytest

from src.evidence.contracts.cross_stage_eligibility_adapters import CrossStageEligibilityAdapterError, adapt_eb0_1j, adapt_verified_bundle_summaries


D = "a" * 64
R = "a7d851abbe14bc43b2bef2df071f3c06bf29cf52"
H = {"bundle_digest": D}


def _inputs():
    run1={"bundle_schema_version":"eb0.1j.v1","census_schema_version":"c","run_id":"r1","high_water_migrated_at":1,"mint_limit":5000,"input_fingerprint":D,"result_digest":D,"source_schema_fingerprints":{"x":D}}
    agg={"selected_mint_count":2,"eligible_mint_count":2,"excluded_by_cohort_bound_count":0,"corpus_count":1,"mints_without_canonical_evidence_count":1,"observation_count":1,"excluded_observation_count":0,"ignored_explicit_record_count":0,"event_counts":{},"quality_counts":{},"completeness_counts":{},"missing_event_kind_counts":{},"conflicting_observation_count":0,"missing_valuation_count":1}
    run2={"bundle_schema_version":"eb0.2h.v1","extraction_schema_version":"e","run_id":"r2","engineering_revision":R,"input_fingerprint":D,"extraction_result_digest":D,"policies":[]}; acc2={"selected_mints":["a"],"qualified_mints":[],"excluded_mints":{"a":"missing"},"policy_count":1,"fact_count":0,"eligible_denominator_count":0,"unknown_count":0,"conflicting_fact_count":0}
    run3={"bundle_schema_version":"eb0.3g.v1","run_id":"r3","engineering_revision":R,"request_metadata":{"mint":"m","from_timestamp_ms":1,"to_timestamp_ms":2},"raw_envelope_digest":D,"manifest_digest":D}; man3={"manifest_digest":D,"observations":[{}],"quality_counts":{"OBSERVED":1},"completeness_counts":{"COMPLETE":1}}
    run4={"bundle_schema_version":"eb0.4h.v1","extraction_schema_version":"e","run_id":"r4","engineering_revision":R,"input_fingerprint":D,"extraction_result_digest":D}; acc4={"selected_operation_ids":["a","b"],"qualified_operation_ids":["a","b"],"excluded_operations":{},"candidate_group_count":1,"fact_count":2,"nomination_count":1,"conflict_count":0}
    return (run1,agg,H,R),(run2,acc2,H),(run3,man3,H),(run4,acc4,H)


def test_exact_adapters_preserve_stages_authority_missingness_and_scope():
    stages=adapt_verified_bundle_summaries(eb0_1=_inputs()[0],eb0_2=_inputs()[1],eb0_3=_inputs()[2],eb0_4=_inputs()[3])
    by={x.upstream_stage:x for x in stages}
    assert by["EB0.1"].eligibility_state=="INELIGIBLE_MISSING"
    assert by["EB0.2"].completeness_state=="NOT_OBSERVED"
    assert "window:1:2" in by["EB0.3"].cohort_or_window_identity
    assert by["EB0.4"].eligibility_state=="ELIGIBLE"


def test_eb0_1_requires_explicit_revision_and_exact_schema():
    run,agg,h,_=_inputs()[0]
    with pytest.raises(CrossStageEligibilityAdapterError,match="INVALID_ENGINEERING_REVISION"):
        adapt_eb0_1j(run,agg,h,engineering_revision="")
    run=dict(run);run["invented"]=1
    with pytest.raises(CrossStageEligibilityAdapterError,match="SCHEMA_DRIFT"):
        adapt_eb0_1j(run,agg,h,engineering_revision=R)


def test_bundle_version_digest_and_accounting_fail_closed():
    eb1,eb2,eb3,eb4=_inputs(); bad=dict(eb2[0]);bad["bundle_schema_version"]="bad"
    with pytest.raises(CrossStageEligibilityAdapterError,match="VERSION_MISMATCH"):
        adapt_verified_bundle_summaries(eb0_1=eb1,eb0_2=(bad,*eb2[1:]),eb0_3=eb3,eb0_4=eb4)
    with pytest.raises(CrossStageEligibilityAdapterError,match="INVALID_BUNDLE_DIGEST"):
        adapt_verified_bundle_summaries(eb0_1=eb1,eb0_2=eb2,eb0_3=(eb3[0],eb3[1],{"bundle_digest":"bad"}),eb0_4=eb4)
