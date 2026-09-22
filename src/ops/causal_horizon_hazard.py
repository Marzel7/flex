"""Generic offline analysis for causal horizon marks and right-censored hazards."""
from __future__ import annotations

from collections import Counter
from statistics import median


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    return values[round((len(values) - 1) * fraction)]


def distribution(values):
    values = list(values)
    return {
        "n": len(values), "p10": percentile(values, .10), "p25": percentile(values, .25),
        "median": percentile(values, .50), "p75": percentile(values, .75), "p90": percentile(values, .90),
        "ge_1x": sum(x >= 1 for x in values) / len(values) if values else None,
        "ge_1_5x": sum(x >= 1.5 for x in values) / len(values) if values else None,
        "ge_2x": sum(x >= 2 for x in values) / len(values) if values else None,
        "ge_3x": sum(x >= 3 for x in values) / len(values) if values else None,
        "lt_0_5x": sum(x < .5 for x in values) / len(values) if values else None,
    }


def kaplan_meier(rows, horizons):
    """Rows have non-negative event_seconds or censor_seconds, with event taking tie priority."""
    result = []
    survival = 1.0
    at_risk = len(rows)
    events_seen = censored_seen = 0
    times = sorted({x["event_seconds"] for x in rows if x["event_seconds"] is not None} | {x["censor_seconds"] for x in rows})
    cursor = 0
    for horizon in sorted(horizons):
        while cursor < len(times) and times[cursor] <= horizon:
            t = times[cursor]
            events = sum(x["event_seconds"] == t for x in rows)
            censored = sum(x["event_seconds"] is None and x["censor_seconds"] == t for x in rows)
            if events:
                survival *= (1 - events / at_risk)
            at_risk -= events + censored
            events_seen += events; censored_seen += censored; cursor += 1
        # Risk set immediately before the stated horizon, allowing events at horizon.
        risk = sum((x["event_seconds"] is None or x["event_seconds"] >= horizon) and x["censor_seconds"] >= horizon for x in rows)
        result.append({"horizon_seconds": horizon, "risk_set": risk, "collapse_events_cumulative": events_seen,
                       "censoring_cumulative": censored_seen, "survival": survival})
    return result


def interval_hazards(rows, boundaries):
    out = []
    for start, end in zip(boundaries, boundaries[1:]):
        entering = [x for x in rows if (x["event_seconds"] is None or x["event_seconds"] >= start) and x["censor_seconds"] >= start]
        events = sum(x["event_seconds"] is not None and start < x["event_seconds"] <= end for x in entering)
        censored = sum(x["event_seconds"] is None and start < x["censor_seconds"] <= end for x in entering)
        out.append({"start_seconds": start, "end_seconds": end, "entering_risk_set": len(entering),
                    "collapse_events": events, "censored_rows": censored,
                    "conditional_collapse_hazard": events / len(entering) if entering else None})
    return out


def paired_changes(earlier, later, material_threshold=.25):
    changes = [later[k] - earlier[k] for k in sorted(set(earlier) & set(later))]
    return {"n": len(changes), "distribution": {"p25": percentile(changes, .25), "median": percentile(changes, .5), "p75": percentile(changes, .75)},
            "improved": sum(x > 0 for x in changes) / len(changes) if changes else None,
            "worsened": sum(x < 0 for x in changes) / len(changes) if changes else None,
            "material_threshold_multiple_points": material_threshold,
            "materially_improved": sum(x >= material_threshold for x in changes) / len(changes) if changes else None,
            "materially_worsened": sum(x <= -material_threshold for x in changes) / len(changes) if changes else None}
