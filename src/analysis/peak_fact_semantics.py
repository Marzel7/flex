"""Operation-agnostic retained-evidence peak fact reconstruction.

These facts intentionally do not collapse an immediate/windowed high, the
highest retained lifecycle observation, and the drawdown reference peak into
one overloaded "ATH" field.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PeakObservation:
    price_usd: float
    timestamp: int
    source: str
    resolution: str
    stage: str


def choose_max(observations: Iterable[PeakObservation]) -> PeakObservation | None:
    """Return highest price; earliest timestamp deterministically breaks ties."""
    values = list(observations)
    return min(values, key=lambda x: (-x.price_usd, x.timestamp, x.source)) if values else None


def lifecycle_maximum(*sources: Iterable[PeakObservation]) -> PeakObservation | None:
    """Highest qualified retained observation across supplied lifecycle sources."""
    return choose_max(item for source in sources for item in source)


def evidence_completeness(has_lifecycle: bool, has_supplemental_raw: bool) -> str:
    """Avoid absolute-ATH claims when coverage is retained but not exhaustive."""
    if not has_lifecycle:
        return "INSUFFICIENT_EVIDENCE"
    if has_supplemental_raw:
        return "PARTIAL_RETAINED_WINDOW"
    return "PARTIAL_RETAINED_WINDOW"


FACT_DEFINITIONS = {
    "EARLY_WINDOW_PROVEN_HIGH": "Highest qualified retained MC inside an explicitly bounded early/upside window.",
    "MAX_PROVEN_LIFECYCLE_MC": "Highest qualified retained MC across all locally retained lifecycle observations included in the replay; not an absolute ATH claim.",
    "DRAWDOWN_REFERENCE_PEAK": "Qualified peak observation used by the existing MAX_PROVEN_DRAWDOWN contract; it remains independent of display-high selection.",
}
