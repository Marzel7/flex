from src.ops.potential_candidate_matcher import PotentialCandidateMatchSpec, match_signature

S=((1,"WSOL_WRAP_CLOSE",999),)
OTHER=((2,"PLAIN_XFER",10),)

def test_unique_no_match_multi_and_insufficient_contract():
    a=PotentialCandidateMatchSpec("a",S); b=PotentialCandidateMatchSpec("b",OTHER)
    assert match_signature(S,(a,b)).state == "UNIQUE_MATCH"
    assert match_signature(((1,"WSOL_WRAP_CLOSE",998),),(a,b)).state == "NO_MATCH"
    assert match_signature(None,(a,b)).state == "INSUFFICIENT_INPUT"
    assert match_signature(S,(a,PotentialCandidateMatchSpec("c",S))).state == "MULTI_MATCH"

def test_ambiguous_and_unavailable_specs_never_match():
    assert match_signature(S,(PotentialCandidateMatchSpec("a",S,"MATCHER_AMBIGUOUS"),PotentialCandidateMatchSpec("b",S,"MATCHER_UNAVAILABLE"))).state == "NO_MATCH"

def test_qualified_artifact_replay_and_wsol_eight_hop_separation():
    import json
    artifact=json.load(open("docs/audits/potential_operations_incremental_candidate_matcher_qualification.v1.json"))
    assert artifact["qualification_counts"] == {"MATCHER_QUALIFIED":59,"MATCHER_PARTIAL":0,"MATCHER_AMBIGUOUS":2,"MATCHER_UNAVAILABLE":1}
    assert sum(x["recovered"] for x in artifact["historical_replay"]) == 1275
    assert sum(x["misses"] + x["wrong_candidate_matches"] for x in artifact["historical_replay"]) == 0
    assert artifact["wsol_potential_matcher_status"] == "MATCHER_QUALIFIED"
    assert artifact["eight_hop_matcher_status"] == "MATCHER_QUALIFIED"
    assert artifact["p3r_vs_wsol"].startswith("SEPARATE")
