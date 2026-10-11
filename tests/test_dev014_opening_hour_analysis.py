from scripts import analyze_dev014_watchtower_opening_hour as subject


def test_analysis_excludes_partial_windows_from_complete_conclusions():
    def row(rank, coverage):
        windows={str(w):{"coverage":"COMPLETE_OBSERVED_WINDOW","minimum_mc_usd":80,"maximum_mc_usd":120,"maximum_timestamp":1100,"missing_candles":0} for w in (300,900,1800,3600)}
        windows["3600"]["coverage"]=coverage
        windows["3600"]["missing_candles"]=2 if coverage!="COMPLETE_OBSERVED_WINDOW" else 0
        return {"rank":rank,"mint":f"m{rank}","creation_timestamp":1000,"acquisition_status":"COMPLETED_QUALIFIED_OBSERVATION","anchor_type":"CREATION_TIME_ANCHORED_OBSERVATION","first_observed_mc_usd":100,"evidence_identity":f"e{rank}","windows":windows}
    result=subject.build({"frozen_population":"x","coverage":{},"records":[row(1,"COMPLETE_OBSERVED_WINDOW"),row(2,"PROVIDER_MISSING_COVERAGE")]},"hash")
    assert result["opening_hour_behaviour"]["complete_first_hour_records"]==1
    assert result["coverage_bias"]["comparison"]["partial_first_hour"]["records"]==1
    assert result["method"]["no_imputation"] is True
