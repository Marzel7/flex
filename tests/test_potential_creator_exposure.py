from scripts.analyze_potential_creator_exposure import state


def test_creator_filter_activity_state_is_independent_of_global_high_waters():
    assert state(4, 4, 4) == "VERY_ACTIVE"
    assert state(1, 1, 1) == "ACTIVE"
    assert state(0, 0, 1) == "COOLING"
    assert state(0, 0, 0) == "DORMANT"


def test_analysis_artifact_retains_all_current_candidates_and_living_candidates():
    import json
    result=json.load(open("docs/audits/potential_operations_multi_token_creator_analysis.v1.json"))
    assert result["population"]["candidates"] == 62
    assert result["wsol"]["candidate_id"] == "p3r-v2-c357da9d0d4d560311e4"
    assert result["eight_hop"]["candidate_id"] == "p3r-v2-dc4953db7adb853337c4"
    assert result["read_only_verification"]["living_publications"] == 0
