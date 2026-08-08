from __future__ import annotations

from dataclasses import replace

from src.evidence.discovery.intelligence import MotifIntelligence
from src.evidence.discovery.population_analysis import MotifPopulationAnalysisEngine


def profile(index,occurrences,*,growth="STABLE",evidence_ppm=1_000_000,
            primitive_ppm=1_000_000,dormancy=0,topology=1):
    complete_occurrences=round((occurrences*evidence_ppm)/1_000_000)
    primitive_total=occurrences*2
    primitive_complete=round((primitive_total*primitive_ppm)/1_000_000)
    return MotifIntelligence(intelligence_id=f"intelligence-{index}",
        motif_id=f"motif-{index}",intelligence_version="1.0.0",replay_version="1",
        occurrence_count=occurrences,observed_population=(f"subject-{index}",),
        measurements={"distinct":{"creators":occurrences,"launches":occurrences,
            "counterparties":occurrences*2,"funding_role_wallets":1,"infrastructure":0},
            "structure":{"primitive_diversity":2,"topology_diversity":topology},
            "completeness":{"evidence_complete_occurrences":complete_occurrences,
                "evidence_total_occurrences":occurrences,"evidence_completeness_ppm":evidence_ppm,
                "primitive_complete_observations":primitive_complete,
                "primitive_total_observations":primitive_total,
                "primitive_completeness_ppm":primitive_ppm},
            "behaviour":{"burst_gap_count":max(0,occurrences-1)}},
        timeline={"first_observed":100,"last_observed":200,"active_duration":100,
                  "dormancy_duration":dormancy},
        growth={"state":growth},stability={"replay":"DETERMINISTIC_BY_CONTRACT"},
        supporting_evidence_ids=(f"evidence-{index}",),
        supporting_primitive_ids=(f"primitive-{index}",),input_digest=f"input-{index}",rank=index)


def population():
    return (profile(1,1,evidence_ppm=0,primitive_ppm=500_000,topology=2),
        profile(2,2,growth="GROWING"),profile(3,4,dormancy=50),
        profile(4,10),profile(5,100))


def test_distribution_percentiles_histogram_and_fragmentation_are_measured():
    report=MotifPopulationAnalysisEngine().analyze(population(),replay_profiles=population())
    summary=report.summary
    assert summary["motifs"]==5 and summary["candidate_occurrences"]==117
    assert summary["minimum_occurrences"]==1 and summary["maximum_occurrences"]==100
    assert summary["median_occurrences_milli"]==4000
    assert (summary["p50"],summary["p75"],summary["p90"],summary["p95"],summary["p99"])==(
        4,10,100,100,100)
    assert summary["histogram"]=={"1":1,"2":1,"3-5":1,"6-10":1,
        "11-25":0,"26-100":1,"101-500":0,"501+":0}
    first=next(item for item in report.motif_rows if item["motif_id"]=="motif-1")
    assert set(first["categories"])=={"SINGLETON","STABLE","FRAGMENTED",
        "EVIDENCE_LIMITED","PRIMITIVE_LIMITED"}


def test_concentration_long_tail_and_pareto_use_occurrence_denominator():
    report=MotifPopulationAnalysisEngine().analyze(population(),replay_profiles=population())
    assert report.concentration[0]["occurrences"]==100
    assert report.concentration[0]["occurrence_share_ppm"]==854_701
    assert report.long_tail[0]["occurrences"]==1
    assert report.summary["long_tail_ratio_ppm"]==400_000
    assert report.pareto["motifs_for_80_percent"]==1
    assert report.pareto["occurrences_at_threshold"]==100


def test_completeness_aggregates_explicit_counts_not_profile_averages():
    report=MotifPopulationAnalysisEngine().analyze(population(),replay_profiles=population())
    assert report.completeness["evidence"]=={"complete":116,"total":117,"unavailable":1,
        "completeness_ppm":991_453}
    assert report.completeness["primitives"]=={"complete":233,"total":234,"unavailable":1,
        "completeness_ppm":995_726}


def test_replay_stability_is_measured_and_changes_are_detected():
    engine=MotifPopulationAnalysisEngine();values=population()
    stable=engine.analyze(values,replay_profiles=tuple(reversed(values)))
    assert all(value is True for key,value in stable.stability.items() if key!="basis")
    changed=list(values);changed[0]=replace(changed[0],occurrence_count=3)
    unstable=engine.analyze(values,replay_profiles=changed)
    assert unstable.stability["distribution_stable"] is False
    assert unstable.stability["occurrence_assignment_stable"] is True
    not_measured=engine.analyze(values)
    assert not_measured.stability["ranking_stable"]=="NOT_MEASURED"


def test_input_order_does_not_change_analysis_identity_or_rows():
    values=population();engine=MotifPopulationAnalysisEngine()
    first=engine.analyze(values,replay_profiles=values)
    second=engine.analyze(tuple(reversed(values)),replay_profiles=tuple(reversed(values)))
    assert first.to_dict()==second.to_dict()
