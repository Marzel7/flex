"""Read-only OIP v2 landscape projection over immutable EP4 snapshots.

The adapter deliberately does not query legacy operational databases or infer
identity.  It exposes only measurements already present in validated snapshots.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DATASETS = ("KNOWN_CORPUS_A", "KNOWN_CORPUS_B", "GENERIC_UNLABELLED_POPULATION")
ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "docs" / "evidence_platform"


class LandscapeUnavailable(RuntimeError):
    pass


def _read(name: str) -> dict[str, Any]:
    path = REPORT_ROOT / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LandscapeUnavailable(f"Immutable landscape snapshot unavailable: {name}") from exc


def _dataset(report: dict[str, Any], dataset: str) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise KeyError(dataset)
    for row in report.get("datasets", ()):
        if row.get("validation_dataset") == dataset:
            return row["analysis"]
    raise LandscapeUnavailable(f"Dataset missing from immutable snapshot: {dataset}")


@lru_cache(maxsize=1)
def _snapshots() -> tuple[dict[str, Any], dict[str, Any]]:
    return (_read("ep4_3_motif_population_analysis.json"),
            _read("ep4_4_dominant_motif_intelligence.json"))


def landscape(dataset: str = DATASETS[0]) -> dict[str, Any]:
    population_report, dominant_report = _snapshots()
    population = _dataset(population_report, dataset)
    dominant = _dataset(dominant_report, dataset)
    motifs = sorted(population.get("motifs", ()), key=lambda x: (-x["occurrences"], x["motif_id"]))
    profiles = sorted(dominant.get("profiles", ()), key=lambda x: (x["rank"], x["motif_id"]))
    return {
        "read_only": True,
        "authoritative": False,
        "identity_free": True,
        "dataset": dataset,
        "analysis_id": population["analysis_id"],
        "summary": population["summary"],
        "completeness": population["completeness"],
        "dominant": {
            "count": dominant["dominant_count"],
            "occurrences": dominant["dominant_occurrences"],
            "total_occurrences": dominant["total_occurrences"],
        },
        "motifs": motifs,
        "profiles": profiles,
        "neighbourhoods": sorted(dominant.get("neighbourhoods", ()), key=lambda x: (-x["motif_count"], x["neighbourhood_id"])),
        "relationship_count": len(dominant.get("relationship_graph", {}).get("edges", ())),
        "drilldown": {"occurrences": "UNAVAILABLE", "evidence": "UNAVAILABLE",
                      "reason": "The immutable summary snapshot does not contain occurrence-level Evidence references."},
    }


def motif(motif_id: str, dataset: str = DATASETS[0]) -> dict[str, Any] | None:
    view = landscape(dataset)
    summary = next((x for x in view["motifs"] if x["motif_id"] == motif_id), None)
    profile = next((x for x in view["profiles"] if x["motif_id"] == motif_id), None)
    if summary is None and profile is None:
        return None
    neighbourhoods = [x for x in view["neighbourhoods"] if motif_id in x["motif_ids"]]
    return {"read_only": True, "authoritative": False, "identity_free": True,
            "dataset": dataset, "motif_id": motif_id, "summary": summary,
            "profile": profile, "neighbourhoods": neighbourhoods,
            "drilldown": view["drilldown"]}


def neighbourhood(neighbourhood_id: str, dataset: str = DATASETS[0]) -> dict[str, Any] | None:
    view = landscape(dataset)
    item = next((x for x in view["neighbourhoods"] if x["neighbourhood_id"] == neighbourhood_id), None)
    if item is None:
        return None
    profiles = {x["motif_id"]: x for x in view["profiles"]}
    return {"read_only": True, "authoritative": False, "identity_free": True,
            "dataset": dataset, "neighbourhood": item,
            "motifs": [profiles.get(mid, {"motif_id": mid}) for mid in item["motif_ids"]],
            "drilldown": view["drilldown"]}
