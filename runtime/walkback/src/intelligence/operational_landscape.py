"""Read-only OIP v2 landscape projection over immutable EP4 snapshots.

The adapter deliberately does not query legacy operational databases or infer
identity.  It exposes only measurements already present in validated snapshots.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


DATASETS = ("KNOWN_CORPUS_A", "KNOWN_CORPUS_B", "GENERIC_UNLABELLED_POPULATION")
ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "docs" / "evidence_platform"
PILOT_REPORT_ROOT = ROOT / "database" / "evidence_platform" / "oip_v2_1a_pilot" / "reports"
REPORT_NAMES = {
    "population": ("ep4_3_motif_population_analysis.json", "ep4_3.json"),
    "dominant": ("ep4_4_dominant_motif_intelligence.json", "ep4_4.json"),
}


class LandscapeUnavailable(RuntimeError):
    pass


def _report_root() -> Path:
    configured = os.environ.get("OIP_LANDSCAPE_REPORT_ROOT")
    if configured:
        return Path(configured)
    if all((PILOT_REPORT_ROOT / names[1]).is_file() for names in REPORT_NAMES.values()):
        return PILOT_REPORT_ROOT
    return REPORT_ROOT


def _read(kind: str) -> dict[str, Any]:
    root = _report_root()
    frozen_name, pilot_name = REPORT_NAMES[kind]
    path = root / (pilot_name if (root / pilot_name).is_file() else frozen_name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LandscapeUnavailable(f"Immutable landscape snapshot unavailable: {path.name}") from exc


def _dataset(report: dict[str, Any], dataset: str) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise KeyError(dataset)
    for row in report.get("datasets", ()):
        if row.get("validation_dataset") == dataset:
            return row["analysis"]
    raise LandscapeUnavailable(f"Dataset missing from immutable snapshot: {dataset}")


@lru_cache(maxsize=1)
def _snapshots() -> tuple[dict[str, Any], dict[str, Any]]:
    return (_read("population"), _read("dominant"))


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
        "snapshot_source": "OIP_V2_1A_PILOT" if _report_root() == PILOT_REPORT_ROOT else "OIP_V1_FROZEN",
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
