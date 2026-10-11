from scripts import analyze_dev014_missing_candle_collapse as subject


def test_topology_and_temporal_collapse_use_retained_buckets_only():
    expected=(60,120,180,240,300)
    top=subject._topology(expected,[180,240,300],[])
    assert top["topology"]=="TRAILING" and top["last_valid_timestamp"]==120
    record={"anchor":{"timestamp":60},"creation_time_anchored_observation":{"results":{"3600":{"observed_maximum_mc_usd":100,"observed_maximum_timestamp":60,"observed_minimum_mc_usd":5,"observed_minimum_timestamp":120}}}}
    result=subject._range(record,top)
    assert result["collapse_before_missing"]=="COLLAPSE_BEFORE_MISSING"
    assert result["last_observed_mc_usd"] is None


def test_missing_before_peak_is_not_described_as_collapse_before_missing():
    top={"first_sustained_missing_timestamp":60,"last_valid_timestamp":300}
    record={"anchor":{"timestamp":60},"creation_time_anchored_observation":{"results":{"3600":{"observed_maximum_mc_usd":100,"observed_maximum_timestamp":180,"observed_minimum_mc_usd":5,"observed_minimum_timestamp":240}}}}
    assert subject._range(record,top)["collapse_before_missing"]=="MISSING_BEFORE_COLLAPSE"
