"""Pure position-lifecycle peak and retained-evidence rug semantics."""
from __future__ import annotations

from typing import Iterable


def position_peak(entry_valuation: float, entry_time: int,
                  retained: Iterable[tuple[int, float]]) -> dict:
    """Keep the retained maximum separate from the counterfactual position max.

    Retained points must be strictly after entry.  Equal position maxima resolve
    to the entry itself, which makes an immediate decline a 1.00x position.
    """
    points = sorted((int(t), float(v)) for t, v in retained if int(t) > entry_time)
    if not points:
        raise ValueError("qualified retained points after entry are required")
    retained_time, retained_value = max(points, key=lambda p: (p[1], -p[0]))
    if retained_value <= entry_valuation:
        peak_time, peak_value = entry_time, float(entry_valuation)
    else:
        peak_time, peak_value = retained_time, retained_value
    return {
        "max_retained_lifecycle_point_after_entry": retained_value,
        "max_retained_lifecycle_point_time": retained_time,
        "position_max_after_entry": peak_value,
        "position_peak_time": peak_time,
        "position_peak_multiple": peak_value / entry_valuation,
        "entry_to_position_peak_seconds": peak_time - entry_time,
    }


def rug_85(entry_time: int, peak: dict, retained: Iterable[tuple[int, float]]) -> dict:
    """First retained (never interpolated) <=15% crossing strictly after peak."""
    threshold = peak["position_max_after_entry"] * 0.15
    crossing = next(((int(t), float(v)) for t, v in sorted(retained)
                     if int(t) > peak["position_peak_time"] and float(v) <= threshold), None)
    if crossing is None:
        return {"threshold": threshold, "threshold_reached": False,
                "rug_state": "RUG_85_NOT_PROVEN", "first_crossing_time": None,
                "crossing_valuation": None, "peak_to_85_seconds": None,
                "entry_to_85_seconds": None}
    time, valuation = crossing
    return {"threshold": threshold, "threshold_reached": True,
            "rug_state": "RUG_85_PROVEN", "first_crossing_time": time,
            "crossing_valuation": valuation,
            "peak_to_85_seconds": time - peak["position_peak_time"],
            "entry_to_85_seconds": time - entry_time}
