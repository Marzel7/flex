"""
creator_state_axis.py — derive the first-class CREATOR-STATE axis from existing data.

PART OF: classification architecture redesign (Risk / Creator-State / Attribution).
This module implements ONLY Axis 2 (Creator State). Like attribution_axis.py it is a
pure, READ-ONLY derivation over data already stored in creator_risk_scores. It writes
nothing and changes no classification — it is a lens over existing fields.

Why a separate axis at all:
    The current model wedges WATCH into _risk_level() at score 20-39, i.e. WATCH lives
    *inside* the severity scale (LOW/MEDIUM/HIGH/CRITICAL). But WATCH answers a different
    question — "do we know enough about this creator yet?" — not "how dangerous is this
    token?". FRESH_UNLINKED_EVENT similarly smuggles freshness into a risk *label*.
    Axis 2 pulls creator state out into its own dimension so risk can be pure severity.

Why derive from history, NOT from final_score:
    A live audit (2026-06-07) showed final_score=0 is NOT "fresh": 15,192 creators have
    score 0 but average 10.5 tokens (122 of them have migrations). They are UNSCORED, not
    NEW. Meanwhile 1,428 creators with 20+ tokens sit in category LOW. So creator state
    must come from observable history (token output, migrations, liquidations), never from
    the risk score — that is the whole point of separating the axes.

Shape returned by derive_creator_state(row):
    {
      "state":       <CreatorState>,    # FRESH | EMERGING | ESTABLISHED | SERIAL | WATCHLIST
      "known":       bool,              # have we observed enough to characterise them?
      "signals":     [str, ...],        # the observable facts behind the state
      "token_count": int,
    }
"""
from __future__ import annotations
from typing import Any, Mapping, Optional


# ── Creator-state vocabulary (Axis 2 values) ─────────────────────────────────
# These describe what we KNOW about a creator, independent of token RISK (Axis 1)
# and operator ATTRIBUTION (Axis 3). Ordered by how much history we have.
STATE_FRESH       = "FRESH"        # first/only token, no scored history — the old WATCH
STATE_EMERGING    = "EMERGING"     # a few tokens, history forming, not yet established
STATE_ESTABLISHED = "ESTABLISHED"  # meaningful track record (many tokens / migrations)
STATE_SERIAL      = "SERIAL"       # high-volume launcher — the defining serial pattern
STATE_WATCHLIST   = "WATCHLIST"    # explicitly flagged for monitoring (overrides volume)

# A creator is "known" once we have enough history to characterise them. FRESH means
# we don't yet — which is exactly the question the old WATCH risk level conflated.
_KNOWN_STATES = {STATE_EMERGING, STATE_ESTABLISHED, STATE_SERIAL, STATE_WATCHLIST}

# Token-count thresholds for the volume-based states. Derived from the live
# distribution (2026-06-07): single-token creators dominate (~38k); 20+ is a small,
# high-signal tail (~2k); 5-19 is the "history forming" middle band.
_EMERGING_MIN    = 2
_ESTABLISHED_MIN = 5
_SERIAL_MIN      = 20

# Stored categories that mean a creator was explicitly placed on a watchlist /
# flagged as a serial-risk operator. These OVERRIDE pure volume — a flagged creator
# is WATCHLIST even with few tokens, because someone/something already characterised
# them. (These are RISK/operator categories today; here we read them only as a
# "has been flagged" signal, not as a risk value.)
_WATCHLIST_CATEGORIES = {
    "WATCHLIST",
    "HIGH_RISK_OPERATOR",
    "SERIAL_DUMPER",
    "CRITICAL_OPERATOR",
}


def _get(row: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a Mapping or a sqlite3.Row (which lacks .get)."""
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def derive_creator_state(row: Mapping[str, Any]) -> dict:
    """
    Pure derivation of the Creator-State axis from a creator_risk_scores row.

    Reads `total_tokens`, `migrated_tokens`, `liquidation_count`, `final_score`,
    `category`. An explicit watchlist/operator flag wins outright; otherwise state
    is a function of observed token output, lifted by migration history. `final_score`
    is used ONLY to distinguish "scored" from "never scored" for the `known` flag —
    never to set the state (see module docstring: score 0 != fresh).
    """
    total   = _to_int(_get(row, "total_tokens"))
    migrated = _to_int(_get(row, "migrated_tokens"))
    liq     = _to_int(_get(row, "liquidation_count"))
    score   = _to_int(_get(row, "final_score"))
    category = (_get(row, "category") or "").upper()

    signals: list[str] = []
    if total:
        signals.append(f"{total} token{'s' if total != 1 else ''}")
    if migrated:
        signals.append(f"{migrated} migrated")
    if liq:
        signals.append(f"{liq} liquidation{'s' if liq != 1 else ''}")

    # 1) Explicit flag overrides volume — already characterised by the engine.
    if category in _WATCHLIST_CATEGORIES:
        signals.append(f"flagged: {category}")
        return {"state": STATE_WATCHLIST, "known": True,
                "signals": signals, "token_count": total}

    # 2) Volume-based state, lifted by migration history. A creator who has shipped
    #    real migrations has a track record even at lower raw counts, so migrations
    #    can promote EMERGING → ESTABLISHED.
    if total >= _SERIAL_MIN:
        state = STATE_SERIAL
    elif total >= _ESTABLISHED_MIN or migrated >= 2:
        state = STATE_ESTABLISHED
    elif total >= _EMERGING_MIN:
        state = STATE_EMERGING
    else:
        state = STATE_FRESH

    # 3) `known` reflects whether we have enough to characterise them. A FRESH creator
    #    that was nonetheless scored is still FRESH (one token), but everything above
    #    FRESH is known. We surface the scored/unscored fact as a signal for clarity.
    if state == STATE_FRESH and score == 0:
        signals.append("unscored")
    known = state in _KNOWN_STATES

    return {"state": state, "known": known,
            "signals": signals, "token_count": total}


def state_label(state: str) -> str:
    """Human label for UI."""
    return {
        STATE_FRESH:       "Fresh",
        STATE_EMERGING:    "Emerging",
        STATE_ESTABLISHED: "Established",
        STATE_SERIAL:      "Serial",
        STATE_WATCHLIST:   "Watchlist",
    }.get(state, state)


# Migration note (design, not executed here): the legacy WATCH *risk level* maps onto
# Axis 2 as FRESH (score 20-39 with thin history) or WATCHLIST (when explicitly
# flagged) — it leaves the risk scale entirely. FRESH_UNLINKED_EVENT becomes
# state=FRESH + attribution=NONE, with risk carried purely by Axis 1.
