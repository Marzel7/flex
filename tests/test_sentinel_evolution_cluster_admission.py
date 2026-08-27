import json
from pathlib import Path

from src.ops.operator_reader import _census_presentation


ARTIFACT = Path("docs/audits/sentinel_evolution_cluster_admission.v1.json")


def test_two_qualified_sentinel_variants_are_explicitly_admitted():
    payload = json.loads(ARTIFACT.read_text())
    candidates = payload["admitted_candidates"]
    assert payload["sentinel_near_observations"] == 75
    assert payload["non_qualifying_observations"] + sum(x["observation_count"] for x in candidates) == 75
    assert [x["observation_count"] for x in candidates] == [4, 3]
    assert all(x["relationship"] == "POTENTIAL_VARIANT_OF_SENTINEL" for x in candidates)
    assert all(all(rule["pass"] for rule in x["qualification"].values()) for x in candidates)
    assert payload["harbinger"] == {"related_observations": 97, "qualifying_clusters": 0}


def test_sentinel_registry_projection_exposes_admitted_variants_read_only():
    census = _census_presentation("FOUR_STEP_30_SOL_14_479K_WSOL_LADDER")
    assert census["evolution_watch"]["state"] == "QUALIFIED_VARIANTS_ADMITTED"
    assert len(census["evolution_watch"]["links"]) == 2
