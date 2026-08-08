from pathlib import Path

from flask import Flask

from src.intelligence.coverage import COVERAGE_CONTRACT_VERSION, measure
from src.intelligence.operational_landscape import DATASETS, landscape, motif, neighbourhood
from src.intelligence.operational_landscape_routes import register_operational_landscape_routes


ROOT = Path(__file__).resolve().parents[1]


def test_coverage_baseline_is_read_only_and_deterministic():
    first = measure(ROOT)
    second = measure(ROOT)
    assert first == second
    assert first["contract_version"] == COVERAGE_CONTRACT_VERSION
    assert first["read_only"] is True
    assert first["population"]["eligible_migrated_launches"] > 0
    assert first["evidence"]["normalized_records"] > 0


def test_landscape_is_identity_free_and_deterministic():
    first = landscape(DATASETS[0])
    assert first == landscape(DATASETS[0])
    assert first["identity_free"] is True
    assert first["authoritative"] is False
    assert first["drilldown"]["evidence"] == "UNAVAILABLE"
    assert first["profiles"] == sorted(first["profiles"], key=lambda x: (x["rank"], x["motif_id"]))
    profile = motif(first["profiles"][0]["motif_id"], DATASETS[0])
    assert profile and profile["motif_id"]
    group = neighbourhood(first["neighbourhoods"][0]["neighbourhood_id"], DATASETS[0])
    assert group and group["neighbourhood"]["motif_count"] == len(group["motifs"])


def test_landscape_routes_are_read_only_and_validate_dataset():
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.testing = True
    register_operational_landscape_routes(app)
    client = app.test_client()
    assert client.get("/intelligence/landscape").status_code == 200
    response = client.get("/api/intelligence/landscape")
    assert response.status_code == 200
    assert response.json["read_only"] is True
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/intelligence/landscape?dataset=../../etc/passwd").status_code == 400
    assert client.get("/api/intelligence/landscape/motifs/unknown").status_code == 404
