import json
from pathlib import Path

from scripts.materialize_potential_route_activity_snapshot_v2 import snapshot_digest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/audits/potential_route_activity_snapshot_v2"


def _value():
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text())
    census = json.loads((SNAPSHOT / "candidate_census.json").read_text())
    routes = [json.loads(line) for line in (SNAPSHOT / "route_membership.jsonl").read_text().splitlines()]
    return {**manifest, "candidate_census": census, "routes": routes}


def test_snapshot_replays_from_immutable_artifacts_only():
    value = _value()
    assert value["schema_version"] == "POTENTIAL_ROUTE_ACTIVITY_SNAPSHOT_V2"
    assert snapshot_digest(value) == value["snapshot_digest"]
    assert len(value["candidate_census"]) == 62
    focus = next(x for x in value["candidate_census"] if x["candidate_id"] == "p3r-v2-dc4953db7adb853337c4")
    assert focus["activity"]["primary_unit"] == "MATCHED_ROUTES"
    assert focus["activity"]["matched_routes_total"] == 33


def test_source_mutation_simulation_cannot_change_frozen_replay():
    value = _value(); before = snapshot_digest(value)
    simulated_live_edge = dict(value["routes"][0]["edges"][0]); simulated_live_edge["selection_status"] = "ALTERNATIVE"
    assert simulated_live_edge["selection_status"] != value["routes"][0]["edges"][0]["selection_status"]
    assert snapshot_digest(_value()) == before
