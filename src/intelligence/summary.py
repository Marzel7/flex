"""
Entity Intelligence — deterministic summary generation.

Rules, not LLM. Each rule inspects the aggregated evidence and emits
zero or more plain-English sentences. The sentence list is the summary.

Rules are applied in a fixed order. Sentences are deduped (order preserved).

Design:
  - Every sentence is derivable from the inputs with no inference beyond
    what the evidence explicitly states.
  - No sentence is ever emitted without a supporting observation or
    knowledge item.
  - "Unknown entity" is the only sentence that fires on zero evidence.
"""

from __future__ import annotations

from src.intelligence.models import (
    ENTITY_TYPE_TREASURY,
    ENTITY_TYPE_SUB_PROVISIONER,
    ENTITY_TYPE_OPERATOR,
    OperationObservation,
    TimelineEvent,
)


def _ops_by_id(operations: list[OperationObservation]) -> dict[str, list[OperationObservation]]:
    result: dict[str, list[OperationObservation]] = {}
    for obs in operations:
        result.setdefault(obs.operation_id, []).append(obs)
    return result


def _knowledge_by_type(knowledge: list[dict]) -> dict[str, dict]:
    return {item["type"]: item for item in knowledge}


def generate_summary(
    entity_type: str,
    operations:  list[OperationObservation],
    knowledge:   list[dict],
    timeline:    list[TimelineEvent],
    confidence:  str,
) -> list[str]:
    """
    Return a list of plain-English sentences that summarise the entity.
    Pure function — no IO, no side effects.
    """
    sentences: list[str] = []

    by_op  = _ops_by_id(operations)
    by_kn  = _knowledge_by_type(knowledge)

    wt_obs     = by_op.get("watchtower", [])
    lo_obs     = by_op.get("launcher-observatory", [])

    wt_roles   = {o.role for o in wt_obs}
    lo_roles   = {o.role for o in lo_obs}

    # ── Identity sentences ─────────────────────────────────────────────────────

    if "TREASURY" in wt_roles:
        obs = next(o for o in wt_obs if o.role == "TREASURY")
        method = obs.facts.get("method", "unknown")
        conf   = obs.facts.get("confidence", "unknown")
        out    = obs.facts.get("out_sol")
        recs   = obs.facts.get("recipients")
        sentences.append(
            f"Confirmed WATCHTOWER treasury "
            f"(method={method}, confidence={conf}"
            + (f", {out} SOL distributed to {recs} recipients" if out and recs else "")
            + ")."
        )

    if "SUB_PROVISIONER" in wt_roles:
        obs = next(o for o in wt_obs if o.role == "SUB_PROVISIONER")
        state   = obs.facts.get("state", "unknown")
        creators = obs.facts.get("creator_count", "?")
        sentences.append(
            f"Discovered as WATCHTOWER sub-provisioner (state={state}, "
            f"{creators} creator wallet(s) seeded)."
        )

    if "PERSISTENT_FUNDER" in lo_roles:
        obs = next(o for o in lo_obs if o.role == "PERSISTENT_FUNDER")
        n   = obs.facts.get("launch_count", "?")
        sentences.append(f"Persistent launcher — {n} launches observed by Launcher Observatory.")

    if "SINGLE_FUNDER" in lo_roles:
        obs = next(o for o in lo_obs if o.role == "SINGLE_FUNDER")
        n   = obs.facts.get("launch_count", 1)
        sentences.append(f"Single launch observed by Launcher Observatory ({n} total).")

    # ── Knowledge sentences ────────────────────────────────────────────────────

    if "WRAP_CLOSE" in by_kn:
        sentences.append("Uses WSOL wrap-close pattern for creator wallet funding.")

    if "PLAIN_TRANSFER" in by_kn:
        sentences.append("Uses plain SOL transfer for creator wallet funding.")

    if "KNOWN_CEX" in by_kn:
        label = by_kn["KNOWN_CEX"].get("value", "Known CEX")
        sentences.append(f"Identified as {label}.")

    if "KNOWN_PLATFORM" in by_kn:
        label = by_kn["KNOWN_PLATFORM"].get("value", "Known launch platform")
        sentences.append(f"Identified as {label}.")

    if "JITO" in by_kn:
        label = by_kn["JITO"].get("value", "Jito infrastructure")
        sentences.append(f"Identified as {label}.")

    # ── Cross-operation sentences ──────────────────────────────────────────────

    if wt_obs and lo_obs:
        sentences.append("Observed by both WATCHTOWER and Launcher Observatory.")
    elif not wt_obs and not lo_obs and not knowledge:
        sentences.append("Unknown entity — no observations or knowledge derived.")
    elif not wt_obs and "PERSISTENT_FUNDER" in lo_roles:
        sentences.append("No WATCHTOWER attribution.")

    # ── Migration / outcome sentences ──────────────────────────────────────────

    for obs in lo_obs:
        rate = obs.facts.get("migration_rate")
        n    = obs.facts.get("launch_count", 0)
        if rate is not None and n >= 3:
            pct = round(rate * 100)
            sentences.append(f"Migration rate: {pct}% of observed launches migrated to PumpSwap.")

    # ── Confidence ─────────────────────────────────────────────────────────────

    if operations or knowledge:
        sentences.append(f"Confidence {confidence}.")

    return sentences
