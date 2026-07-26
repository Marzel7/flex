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

X65.35 -- launch_create_times_for_mints() below is a SECOND, separate fix:
the Stage-1 population query every classifier shares
(`wt_attribution_outcomes WHERE completed_at >= since`) was windowing on
walkback-COMPLETION time, not the launch's own create time. A launch
completed by the attribution pipeline today, but created three weeks ago,
was being counted as "within the last 24h" -- and a launch created today
whose walkback hadn't finished yet was invisible until it did. This
resolves each mint's actual create time from the same evidence precedence
already established in operational_intelligence.py's
_enrich_discovery_records (token_analysis.created_at first --
100% coverage in the core DB -- falling back to
wt_watchtower_launches.create_time for the narrower cascade-confirmed
set), so population membership is windowed by launch time while
downstream classification still reads whatever the latest evidence says.
"""
from __future__ import annotations

import sqlite3
from typing import Any

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


def _parse_token_analysis_timestamp(value: Any) -> float | None:
    """token_analysis.created_at holds either an ISO-8601 string
    ('2026-04-11T10:27:27Z') or, for a real subset of rows, an epoch value
    stored as text ('1775915651.24585'). Both are parsed; anything else
    returns None so the caller can exclude that row rather than guess.
    Duplicated from operational_behaviour_tags.py's identical helper
    rather than imported, to keep this module dependency-free of the
    classifier modules that import discovery_window (avoids a cycle)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        import datetime
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def launch_create_times_for_mints(
    ops_conn: sqlite3.Connection, core_conn: sqlite3.Connection, mints: list[str],
) -> dict[str, float]:
    """X65.35 -- resolves each mint's actual launch create time, for
    windowing the Stage-1 population by WHEN A LAUNCH HAPPENED rather than
    when its attribution walkback happened to complete. Explicit
    precedence, matching operational_intelligence.py's
    _enrich_discovery_records (the only other place this resolution
    already happens):
      1. token_analysis.created_at (core DB) -- effectively full coverage
      2. wt_watchtower_launches.create_time (ops DB) -- narrower
         (cascade-confirmed only), used only when (1) is absent
    A mint with neither is omitted from the returned map entirely --
    callers must treat a missing mint as "no trustworthy launch time,"
    never silently default it into or out of a window."""
    if not mints:
        return {}
    placeholders = ",".join("?" for _ in mints)
    result: dict[str, float] = {}

    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone())

    if _table_exists(core_conn, "token_analysis"):
        for row in core_conn.execute(
            f"SELECT mint, created_at FROM token_analysis WHERE mint IN ({placeholders})",
            mints,
        ):
            ts = _parse_token_analysis_timestamp(row["created_at"])
            if ts is not None:
                result[row["mint"]] = ts

    missing = [m for m in mints if m not in result]
    if missing and _table_exists(ops_conn, "wt_watchtower_launches"):
        missing_placeholders = ",".join("?" for _ in missing)
        for row in ops_conn.execute(
            f"SELECT mint, create_time FROM wt_watchtower_launches "
            f"WHERE mint IN ({missing_placeholders}) AND create_time IS NOT NULL",
            missing,
        ):
            if row["mint"] not in result and row["create_time"] is not None:
                result[row["mint"]] = float(row["create_time"])

    return result
