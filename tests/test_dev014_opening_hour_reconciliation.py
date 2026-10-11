from scripts import reconcile_dev014_watchtower_opening_hour as subject


def test_reconciliation_keeps_protected_and_unqualified_states_separate():
    chronology={"population_identity":"WATCHTOWER_FORENSIC_POPULATION_V2_20261009","content_hash":"chrono","ranked":[
        {"rank":1,"mint":"a","creation_timestamp":100},{"rank":2,"mint":"b","creation_timestamp":90}],"unranked":[{}]*42}
    chronology["ranked"] *= 0
    chronology["ranked"] = [{"rank":n,"mint":f"m{n}","creation_timestamp":1000-n} for n in range(1,598)]
    normal={"chronological_rank":1,"mint":"m1","request_identity":"a","state":"COMPLETED","normalization_status":"NORMALIZED","evidence_identity":"e", "anchor":{"class":"CREATION_TIME_ANCHORED_OBSERVATION"},"creation_time_anchored_observation":{"first_observed_mc_usd":100.0,"first_observed_timestamp":1000,"provider_provenance":"BIRDEYE_1M_MCAP","results":{str(w):{"coverage_status":"COMPLETE_OBSERVED_WINDOW","minimum_status":"COMPLETE_OBSERVED_WINDOW","valid_bucket_count":w//60,"missing_bucket_timestamps":[],"observed_minimum_mc_usd":50.0,"observed_maximum_mc_usd":150.0,"observed_maximum_timestamp":1100} for w in subject.WINDOWS}},"_journal_sha256":"h"}
    attempted={"chronological_rank":383,"mint":"m383","request_identity":"b","state":"ATTEMPTED","_journal_sha256":"h"}
    failed={"chronological_rank":74,"mint":"m74","request_identity":"c","state":"COMPLETED","normalization_status":"FAILED","evidence_identity":"f","_journal_sha256":"h"}
    result=subject.build(chronology=chronology,population={},authorization={"content_hash":"auth"},records={"a":normal,"b":attempted,"c":failed},journal_hashes={"j":"h"})
    assert result["coverage"]["completed_provider_identities"]==2
    assert result["coverage"]["qualified_price_observations"]==1
    assert result["coverage"]["normalization_failures"]==1
    assert result["coverage"]["protected_uncertain_outcomes"]==1
    assert result["records"][73]["acquisition_status"]=="COMPLETED_EVIDENCE_UNQUALIFIED"
    assert result["records"][382]["acquisition_status"]=="PROTECTED_ATTEMPTED"
