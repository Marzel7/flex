"""
risk_axis.py — derive the first-class RISK (severity) axis as pure severity.

PART OF: classification architecture redesign (Risk / Creator-State / Attribution).
This module implements ONLY Axis 1 (Risk). Like creator_state_axis.py and
attribution_axis.py it is a pure, READ-ONLY derivation over data already stored.
It writes nothing and changes no classification — it is a lens over existing fields.

What Axis 1 answers, and ONLY this:
    "How dangerous is this token?"

What it must NEVER read (these are other axes):
    creator freshness / age / token count / creator state   -> Axis 2
    WATCHTOWER attribution / lineage / operations / funder   -> Axis 3
    migration speed, shared-funder promotion                 -> not severity

The single contaminant removed from the legacy logic is WATCH. In both legacy
producers (risk_scoring_builder._risk_level and token_prediction_builder._risk_level)
a score of 20-39 — and any "fresh" creator — was relabelled WATCH. But WATCH answers
"do we know enough yet?", which is Axis 2 (FRESH). Here, 20-39 is simply LOW severity,
and "no score yet" is UNKNOWN, not WATCH.

Severity inputs that are KEPT (they genuinely answer "how dangerous"):
    the numeric score (operator/creator, outcome, g, funding, network, liquidation);
    liquidation-history floors; outcome-behaviour escalator labels (LIKELY_DUMP, etc.).
These may raise the band, never lower it, and are recorded in risk_basis.

Shape returned by derive_risk(row):
    {
      "risk":       "UNKNOWN|LOW|MEDIUM|HIGH|CRITICAL",
      "risk_score": float | None,
      "risk_basis": {"source", "score", "band", "floors": [...], "escalators": [...]},
    }
"""
from __future__ import annotations
from typing import Any, Optional


# ── Risk (severity) vocabulary — Axis 1 values ───────────────────────────────
# Pure severity. NO WATCH (that was creator-state contamination). UNKNOWN replaces
# the legacy empty string for predictions that never reached COMPLETE.
RISK_UNKNOWN  = "UNKNOWN"
RISK_LOW      = "LOW"
RISK_MEDIUM   = "MEDIUM"
RISK_HIGH     = "HIGH"
RISK_CRITICAL = "CRITICAL"

_BAND_RANK = {RISK_UNKNOWN: -1, RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2, RISK_CRITICAL: 3}

# Severity cutpoints — the legacy thresholds MINUS the WATCH band (20-39 is now LOW).
def _band_for_score(score: float) -> str:
    if score >= 80:
        return RISK_CRITICAL
    if score >= 60:
        return RISK_HIGH
    if score >= 40:
        return RISK_MEDIUM
    return RISK_LOW


# Outcome-behaviour labels that legitimately ESCALATE severity (they describe what the
# token/creator does, not who they are). Kept from the legacy _risk_level label path.
# Effect: lift to at least HIGH (or CRITICAL at score>=80). Recorded in risk_basis.
_ESCALATOR_LABELS = {
    "LIKELY_DUMP", "SELF_FUNDED_TOKEN", "LIQUIDATION_RISK", "NETWORK_RISK_TOKEN",
}
# Labels that hard-pin CRITICAL regardless of score (serious behaviour signals).
_CRITICAL_LABELS = {"CRITICAL_RISK", "SERIAL_OPERATOR_TOKEN"}


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(row: Any, key: str, default: Any = None) -> Any:
    """Read a field from a Mapping or a sqlite3.Row (which lacks .get)."""
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _escalate(band: str, target: str, basis_list: list, tag: str) -> str:
    """Raise band toward target, never lower; record why."""
    if _BAND_RANK[target] > _BAND_RANK[band]:
        basis_list.append(tag)
        return target
    return band


def derive_risk(row: Any) -> dict:
    """
    Pure severity derivation from a row carrying a severity score and (optionally)
    behaviour signals. Works for both legacy producers:

      - token_prediction_scores rows: `prediction_score`, `prediction_status`,
        `prediction_label`, `creator_has_liq`, `network_has_liq`.
      - creator_risk_scores rows: `final_score`, `category`.

    Severity inputs only. NEVER reads freshness/state/attribution. A status that is
    not COMPLETE (or a missing score) yields UNKNOWN — NOT WATCH. The one status that
    is itself a severity signal — NETWORK_RISK_TOKEN (network co-offending) — escalates
    to HIGH even without a completed score, matching the legacy behaviour and the audit
    "KEEP NETWORK_RISK_TOKEN→HIGH" verdict.
    """
    status = _get(row, "prediction_status")
    # Prefer prediction_score (token path); fall back to final_score (creator path).
    score = _to_float(_get(row, "prediction_score"))
    source = "prediction"
    if score is None:
        score = _to_float(_get(row, "final_score"))
        source = "creator"

    # NETWORK_RISK_TOKEN is a severity-bearing status, not just "incomplete".
    if status == "NETWORK_RISK_TOKEN":
        return {"risk": RISK_HIGH, "risk_score": score,
                "risk_basis": {"source": source, "score": score, "band": RISK_HIGH,
                               "floors": [], "escalators": ["NETWORK_RISK_TOKEN_status"]}}

    # Not COMPLETE, or no score at all → UNKNOWN. (Legacy emitted "" or WATCH here.)
    if (status is not None and status != "COMPLETE") or score is None:
        return {"risk": RISK_UNKNOWN, "risk_score": score,
                "risk_basis": {"source": source, "score": score,
                               "band": RISK_UNKNOWN, "floors": [], "escalators": []}}

    floors: list[str] = []
    escalators: list[str] = []
    band = _band_for_score(score)

    # ── Liquidation-history floors (outcome signal → severity; never lowers) ──────
    creator_has_liq = bool(_get(row, "creator_has_liq"))
    network_has_liq = bool(_get(row, "network_has_liq"))
    # creator_risk_scores carries a count rather than a flag
    liq_count = _to_float(_get(row, "liquidation_count")) or 0
    if creator_has_liq or liq_count > 0:
        target = RISK_CRITICAL if score >= 80 else RISK_HIGH
        band = _escalate(band, target, floors, "creator_liq_history")
    elif network_has_liq:
        target = RISK_CRITICAL if score >= 80 else (RISK_HIGH if score >= 60 else RISK_MEDIUM)
        band = _escalate(band, target, floors, "network_liq_history")

    # ── Behaviour-label escalators (what the token does → severity) ──────────────
    label = (_get(row, "prediction_label") or "").upper()
    if label in _CRITICAL_LABELS:
        band = _escalate(band, RISK_CRITICAL, escalators, label)
    elif label in _ESCALATOR_LABELS:
        # Match legacy thresholds exactly (token_prediction_builder._risk_level):
        # >=80 CRITICAL, >=60 HIGH, else MEDIUM. (Faithful parallel — KEEP, don't amplify.)
        target = RISK_CRITICAL if score >= 80 else (RISK_HIGH if score >= 60 else RISK_MEDIUM)
        band = _escalate(band, target, escalators, label)

    return {
        "risk": band,
        "risk_score": score,
        "risk_basis": {"source": source, "score": score, "band": band,
                       "floors": floors, "escalators": escalators},
    }


def risk_label(risk: str) -> str:
    """Human label for UI."""
    return {
        RISK_UNKNOWN:  "Unknown",
        RISK_LOW:      "Low",
        RISK_MEDIUM:   "Medium",
        RISK_HIGH:     "High",
        RISK_CRITICAL: "Critical",
    }.get(risk, risk)


# Migration note (design, not executed here): legacy WATCH does NOT map into Axis 1 —
# it leaves the severity scale entirely. A row that was WATCH becomes risk=LOW (its true
# severity at score 20-39) with the uncertainty carried by Axis 2 creator_state=FRESH.
# The legacy empty-string risk_level (not-COMPLETE predictions) becomes UNKNOWN.
