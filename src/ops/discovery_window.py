"""X29.6.1 — Discovery Window Correctness.

Single source of truth for the Discovery "window" query parameter, shared
by every Discovery-facing route so that no two panels can ever silently
diverge on what "24h"/"7d"/"30d"/"all" means in seconds. Fixes the X29.6
audit finding: prior to this module, individual routes each hand-parsed
`window` with only two effective values (24h vs. everything-else-treated-
as-all), so a launch older than 24h but younger than the true corpus age
was invisible with no way to widen the range from the UI.

No intelligence, attribution, or schema changes -- this module only maps
a request parameter to a window_seconds value; the underlying builders
(investigation_pipeline.py, operational_intelligence.py, funding_topology
.py, etc.) already accepted an arbitrary window_seconds before this sprint.
"""
from __future__ import annotations

WINDOW_24H = "24h"
WINDOW_7D = "7d"
WINDOW_30D = "30d"
WINDOW_ALL = "all"

WINDOW_ORDER = (WINDOW_24H, WINDOW_7D, WINDOW_30D, WINDOW_ALL)

WINDOW_LABELS = {
    WINDOW_24H: "24 Hours",
    WINDOW_7D: "7 Days",
    WINDOW_30D: "30 Days",
    WINDOW_ALL: "All",
}

_WINDOW_SECONDS = {
    WINDOW_24H: 86400,
    WINDOW_7D: 7 * 86400,
    WINDOW_30D: 30 * 86400,
    WINDOW_ALL: 365 * 86400,
}


def parse_window_param(raw: str | None) -> str:
    """Normalizes a raw `window` query-string value to exactly one of
    WINDOW_ORDER. Any unrecognized/missing value defaults to WINDOW_24H —
    matching every route's pre-existing default — rather than silently
    falling through to "all" (the previous, incorrect two-value collapse)."""
    value = (raw or WINDOW_24H).strip().lower()
    if value not in _WINDOW_SECONDS:
        return WINDOW_24H
    return value


def window_seconds_for(window_param: str) -> int:
    """Maps a normalized window param (already passed through
    parse_window_param) to the window_seconds value every builder expects."""
    return _WINDOW_SECONDS.get(window_param, _WINDOW_SECONDS[WINDOW_24H])


def empty_state_message(window_param: str) -> str:
    """Copy for the 'no launches in this window' empty state -- must never
    read as 'Discovery has no data,' since the corpus may simply be older
    than the selected window (the X29.6 root cause)."""
    label = WINDOW_LABELS.get(window_param, WINDOW_LABELS[WINDOW_24H])
    others = [WINDOW_LABELS[w] for w in WINDOW_ORDER if w != window_param]
    return (
        f"No launches were detected within the selected {label} window. "
        f"Try {', '.join(others[:-1])} or {others[-1]}."
    )
