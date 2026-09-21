"""Read-only, operation-agnostic Playbook view-model registry."""
from __future__ import annotations
import json
import os
from decimal import Decimal
from pathlib import Path

ROOT = Path(os.environ.get("OPERATION_RESEARCH_ROOT") or Path(__file__).resolve().parents[2]).resolve()
REGISTRY_PATH = ROOT / "docs/operations/playbooks/operation_playbook_registry.v1.json"

def _read(path: str) -> dict:
    return json.loads((ROOT / path).read_text())

def _at(value: dict, dotted: str):
    if not dotted:
        return value
    for key in dotted.split('.'):
        value = value[key]
    return value

def artifact_playbooks() -> list[dict]:
    """Load registered artifact-backed Playbooks; no database/network access."""
    registry = _read(str(REGISTRY_PATH.relative_to(ROOT)))
    result = []
    for spec in registry["artifact_playbooks"]:
        sources = {name: _read(path) for name, path in spec["artifacts"].items()}
        model = {key: _at(sources[name], path) for key, (name, path) in spec["fields"].items()}
        # Optional retained overlays preserve the source dataset while exposing a
        # separately-qualified, versioned fact.  This is presentation-only: it
        # never writes or replaces the underlying historical entry fact.
        entry_rows = model.pop("qualified_actionable_entry_rows", None)
        lifecycle_rows = model.pop("qualified_lifecycle_rows", None)
        if entry_rows is not None or lifecycle_rows is not None:
            entries = {row["mint"]: row for row in entry_rows or []}
            lifecycles = {row["mint"]: row for row in lifecycle_rows or []}
            model["tokens"] = [token | {
                "qualified_actionable_entry_mc_usd": entries.get(token["mint"], {}).get("new_actionable_mc_usd"),
                "qualified_actionable_entry_status": entries.get(token["mint"], {}).get("status"),
                "confirmation_second": entries.get(token["mint"], {}).get("confirmation_second"),
                "qualified_lifecycle": lifecycles.get(token["mint"], {}),
            } for token in model["tokens"]]
            if model.get("lifecycle_summary") is not None and entries:
                values = sorted(Decimal(str(row["new_actionable_mc_usd"])) for row in entries.values()
                                if row.get("new_actionable_mc_usd") is not None)
                if values:
                    middle = len(values) // 2
                    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
                    model["lifecycle_summary"] = model["lifecycle_summary"] | {
                        "median_actionable_entry_mc_usd": str(median),
                    }
        model.update(spec.get("constants", {}))
        if spec["operation_id"] == "watchtower":
            # Prospective admission is a fact-first, read-only projection.  It
            # grows with Monitor assignments and never changes the frozen 51.
            from src.ops.watchtower_research_cohorts import read_cohorts
            cohorts = read_cohorts()
            model["cohorts"] = cohorts["statistics"]
            model["prospective_tokens"] = cohorts["prospective_rows"]
            model["tokens"] = model["tokens"] + [{
                "mint": row["mint"], "cohort": "prospective", "entry_mc_usd": row["entry_mc_usd"],
                "max_proven_mc_usd": row["max_proven_mc_usd"], "entry_to_max_multiple": row["entry_to_max_multiple"],
                "time_to_max_seconds": (row.get("ath_bucket_start") or row.get("entry_timestamp")) - row.get("entry_timestamp"),
                "reached_2x": row["reached_2x"], "reached_5x": row["reached_5x"], "reached_10x": row["reached_10x"],
                "horizons": {name: {"multiple": None} for name in ("24h", "72h", "7d")},
                "collapse_gte_85_percent": row.get("terminal_state") == "PRICE_MONITOR_COMPLETE_COLLAPSED",
                "lifecycle_status": row.get("ath_finalization_status"), "provenance": {"digest": row.get("provenance_digest"), "evidence": row.get("final_ath_evidence")},
            } for row in cohorts["prospective_rows"]]
        model.update({"operation_id": spec["operation_id"], "display_name": spec["display_name"],
                      "playbook_version": registry["playbook_version"], "qualification_status": "QUALIFIED",
                      "artifact_references": list(spec["artifacts"].values()), "source_kind": "artifact"})
        result.append(model)
    return result

def db_playbook_view(row: dict) -> dict:
    """Adapt an existing DB projection into the same view model without mutation."""
    return {"operation_id": row["operator_id"], "display_name": row.get("display_name") or row["operator_id"],
            "status": row["qualification_status"], "playbook_version": f"v{row['playbook_version']}",
            "qualification_status": row["qualification_status"], "token_data_completion": row["strategy_status"],
            "sample_size": row["sample_size"], "eligible_population": None, "price_data_coverage": None,
            "fx_coverage": None, "sample_sufficiency": None, "trigger_status": "NOT_WIRED",
            "manual_stage_status": "See operation detail", "last_materialized_at": row.get("created_at"),
            "artifact_references": [], "source_kind": "database"}

def all_playbook_views(db_rows: list[dict]) -> list[dict]:
    items = [db_playbook_view(row) for row in db_rows] + artifact_playbooks()
    return sorted(items, key=lambda item: item["display_name"].lower())

def artifact_playbook(operation_id: str) -> dict | None:
    return next((x for x in artifact_playbooks() if x["operation_id"] == operation_id), None)
