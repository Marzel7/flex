"""Read-only operation research modules, deliberately separate from membership."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BYZANTINE_LIFECYCLE_ARTIFACT = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_full_lifecycle_peak_reevaluation.v1.json"
BYZANTINE_OPPORTUNITY_ARTIFACT = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_playbook_read_model.v1.json"
BYZANTINE_OPPORTUNITY_READ_MODEL_V3 = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_playbook_read_model.v3.json"
BYZANTINE_OPPORTUNITY_READ_MODEL_V4 = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_playbook_read_model.v4.json"
BYZANTINE_OPPORTUNITY_CASES = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_strategy_evidence_cases.v1.json"
BYZANTINE_RUG_TIMING = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_post_entry_rug_timing.v1.json"
BYZANTINE_POSITION_PEAK_OPPORTUNITY = ROOT / "docs/audits/operation_all_birth_research/byzantine_428_v1/byzantine_64_position_peak_trading_opportunity.v2.json"

def _median(values: list[float]) -> float | None:
    return sorted(values)[(len(values)-1)//2] if values else None

def _price(value: float) -> str:
    """Human-readable USD/token display; exact value remains in provenance."""
    if value >= 1: return f"${value:,.4g}"
    return f"${value:.8f}".rstrip("0").rstrip(".")

def _short(mint: str) -> str:
    return mint[:4] + "…" + mint[-6:]

def _duration(seconds: int) -> str:
    if seconds >= 3600: return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 60}m" if seconds >= 60 else f"{seconds}s"

def read_lifecycle_research_module(conn: sqlite3.Connection, operator_id: str, *,
                                   artifact_path: Path | None = None) -> dict[str, Any] | None:
    """Return independently-scoped research rows; never writes or enrols members."""
    name = conn.execute("SELECT display_name FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
    if not name or str(name[0]).lower() != "byzantine":
        return None
    path = artifact_path or BYZANTINE_LIFECYCLE_ARTIFACT
    if not path.exists():
        return None
    artifact = json.loads(path.read_text())
    scope = artifact.get("scope") or {}
    canonical = {r[0] for r in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=?", (operator_id,))}
    rows = []
    for case in artifact["cases"]:
        if case.get("state") != "RECONSTRUCTED_FULL_LIFECYCLE_PEAK":
            continue
        maximum, low = case["full_lifecycle_maximum"], case["minimum_after_full_lifecycle_maximum"]
        drawdown = case["MAX_PROVEN_DRAWDOWN_WITHIN_7D"]
        rows.append({"mint":case["mint"], "membership":"CANONICAL" if case["mint"] in canonical else "RESEARCH_ONLY",
                     "coverage_state":"MATURE_QUALIFIED", "birth":case["birth"],
                     "maximum":maximum, "post_maximum_low":low, "max_drawdown":drawdown,
                     "max_to_low_seconds":low["timestamp"]-maximum["timestamp"], "mint_short":_short(case["mint"]),
                     "maximum_display":_price(maximum["price_usd"]), "low_display":_price(low["price_usd"]),
                     "max_to_low_display":_duration(low["timestamp"]-maximum["timestamp"]),
                     "thresholds":{str(t):drawdown >= t for t in (.8,.85,.9,.925,.95)}})
    rows.sort(key=lambda r: (-r["max_drawdown"], r["mint"]))
    def group(items: list[dict]) -> dict[str, Any]:
        values=[r["max_drawdown"] for r in items]; durations=[r["max_to_low_seconds"] for r in items]
        return {"count":len(items),"median_drawdown":_median(values),"median_max_to_low_seconds":_median(durations),
                "threshold_counts":{str(t):sum(r["max_drawdown"] >= t for r in items) for t in (.8,.85,.9,.925,.95)}}
    overlap=[r for r in rows if r["membership"] == "CANONICAL"]; research_only=[r for r in rows if r["membership"] == "RESEARCH_ONLY"]
    mature_population = int(scope.get("mature", len(rows)))
    research_population = int(scope.get("current_population", mature_population))
    mature_unresolved = int(scope.get("unresolved_mature", max(0, mature_population - len(rows))))
    return {"module":"LIFECYCLE_BEHAVIOUR_V2", "methodology":"MAX_RETAINED_QUALIFIED_POINT_WITHIN_7D; earliest equal maximum; minimum retained qualified point strictly after maximum", "limitation":"Retained point observations; not true tick/intra-minute ATH. First 2h=1m; remainder through seven days=15m.",
            "canonical_member_count":len(canonical), "research_population":research_population, "mature_population":mature_population,
            "mature_qualified_count":len(rows), "mature_data_pending_count":mature_unresolved,
            "horizon_not_elapsed_count":max(0, research_population - mature_population),
            "completed_count":len(rows), "canonical_overlap_count":len(overlap), "research_only_count":len(research_only), "rows":rows,
            "groups":{"all":group(rows), "canonical":group(overlap), "research_only":group(research_only)}}

def read_trading_opportunity_module(conn: sqlite3.Connection, operator_id: str) -> dict[str, Any] | None:
    name=conn.execute("SELECT display_name FROM operators WHERE operator_id=?",(operator_id,)).fetchone()
    if not name or str(name[0]).lower()!="byzantine" or not BYZANTINE_OPPORTUNITY_ARTIFACT.exists(): return None
    if BYZANTINE_OPPORTUNITY_READ_MODEL_V4.exists():
        return json.loads(BYZANTINE_OPPORTUNITY_READ_MODEL_V4.read_text())
    if BYZANTINE_OPPORTUNITY_READ_MODEL_V3.exists():
        return json.loads(BYZANTINE_OPPORTUNITY_READ_MODEL_V3.read_text())
    if BYZANTINE_POSITION_PEAK_OPPORTUNITY.exists():
        artifact=json.loads(BYZANTINE_POSITION_PEAK_OPPORTUNITY.read_text())
        model=artifact["trading_opportunity"]
        rows=[]
        for row in artifact["rows"]:
            peak=row["position_peak"]
            rows.append({"mint":row["mint"],"display_mint":_short(row["mint"]),"membership":row["membership"],"entry":row["entry"],
                         "post_entry_peak":{"retained_peak_time":peak["timestamp"],"retained_peak_usd_per_token":peak["price"],"retained_peak_equivalent_valuation":peak["valuation"],"peak_multiple":peak["multiple"],"entry_to_peak_seconds":peak["entry_to_peak_seconds"],"source":peak["source"]},
                         "post_entry_observation":row["post_entry_observation"],"collapse":row["collapse"],"evidence":row["evidence"],"strategy":{"status":"PENDING_SCORING"}})
        model["rows"]=rows
        return model
    model=json.loads(BYZANTINE_OPPORTUNITY_ARTIFACT.read_text()).get("trading_opportunity")
    cases=json.loads(BYZANTINE_OPPORTUNITY_CASES.read_text())["cases"]; timing={x["mint"]:x for x in json.loads(BYZANTINE_RUG_TIMING.read_text())["rows"]}
    if len(cases)!=64 or len(timing)!=64 or {x["mint"] for x in cases} != set(timing): return None
    rows=[]
    for x in cases:
        t=timing[x["mint"]]; rows.append({"mint":x["mint"],"display_mint":_short(x["mint"]),"membership":x["membership"],"entry":x["entry"],"post_entry_peak":x["post_entry"],"collapse":t,"evidence":{"qualification":x["qualification"]},"strategy":{"status":"PENDING_SCORING"}})
    model["rows"]=rows
    return model
