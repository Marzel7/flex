"""X75.2 Reframe Walkback as Infrastructure Reconstruction.

Walkback (src/ops/attribution_outcome.py, src/core/walkback_worker.py) does
not identify WATCHTOWER. It reconstructs operational infrastructure that
may have rotated -- wallets are transient, behaviour is comparatively
stable. This module re-projects walkback's ALREADY-COMPUTED terminal
outcomes (wt_attribution_outcomes) into infrastructure-fact language for
presentation, without changing a single byte of what walkback itself
computes, stores, or decides.

It answers "what known operational behaviour exists behind this launch?",
never "is this WATCHTOWER?" -- CANONICAL_OPERATOR_REACHED (the one outcome
type that DOES already carry a WATCHTOWER-confirming operator_id) is
intentionally excluded from this module's output: that outcome already
went through Treasury Review / Operator Identity confirmation via the
existing pipeline, and re-describing it here would blur the line this
module exists to keep sharp -- infrastructure facts are NOT confirmation.

This module performs no detection, no RPC, no writes, and does not alter
attribution_outcome.py's outcome_type vocabulary, disposition_resolver.py,
or operation_attribution.py in any way. It is a pure read-only projection,
same pattern as src/discovery/operation_convergence.py (X75.0), which this
module feeds.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

# Outcome types this module treats as infrastructure-reconstruction
# material (i.e. NOT an already-confirmed canonical attribution). Kept as
# an explicit allowlist, not "everything except CANONICAL_OPERATOR_REACHED",
# so a new outcome type added later must be deliberately included here
# rather than silently absorbed.
_INFRASTRUCTURE_OUTCOME_TYPES = frozenset({
    "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP", "AMBIGUOUS_BRANCH",
    "MAX_DEPTH", "INSUFFICIENT_EVIDENCE", "KNOWN_MULTI_TOKEN_CREATOR",
    "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
})


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


@dataclass(frozen=True, slots=True)
class InfrastructureFact:
    """One reconstructed-behaviour fact about a launch's funding lineage.
    Deliberately vocabulary-only -- 'Known' means observed evidence exists
    for that signal, 'Unknown' means it doesn't, neither implies an
    operator identity."""
    mint: str
    creator: str | None
    facts: tuple[str, ...]                # e.g. ("Unknown Treasury", "Known Subprovider", "Known Fan-Out")
    treasury: str | None
    subprovisioners: tuple[str, ...]
    funding_mechanism: str | None
    fan_out_count: int | None
    walkback_class: str | None
    source_outcome_type: str


def _describe_treasury(treasuries: list[str]) -> str:
    return "Known Treasury" if treasuries else "Unknown Treasury"


def _describe_subprov(subprovisioners: list[str]) -> str:
    return "Known Subprovider" if subprovisioners else "Unknown Subprovider"


def _describe_fanout(record: dict[str, Any] | None) -> tuple[str, int | None]:
    if not record:
        return "Fan-Out Not Observed", None
    creator_count = record.get("creator_count") or 0
    wrap_close_count = record.get("wrap_close_count") or 0
    fanout = max(int(creator_count or 0), int(wrap_close_count or 0))
    if fanout >= 5:
        return "Multi-Level Fan-Out", fanout
    if fanout > 1:
        return "Known Fan-Out", fanout
    return "Fresh Creator", fanout or None


def _describe_funding_mechanism(mechanism: str | None) -> str:
    return f"Known Funding Mechanism ({mechanism})" if mechanism else "Unknown Funding Pattern"


def _describe_settlement(walkback_class: str | None) -> str | None:
    # FULL_WALKBACK is excluded here -- that class only appears alongside
    # CANONICAL_OPERATOR_REACHED, which this module already excludes
    # upstream. Any FULL_WALKBACK row reaching this function is unexpected;
    # treated as no settlement signal rather than guessed at.
    if walkback_class == "PARTIAL_TREASURY":
        return "Known Provisioning (partial lineage)"
    if walkback_class == "LINK_ONLY":
        return "Known Settlement Link"
    return None


def reconstruct_infrastructure_fact(row: dict[str, Any], conn: sqlite3.Connection | None = None) -> InfrastructureFact | None:
    """row: one wt_attribution_outcomes row (as a dict). Returns None for
    outcome types outside this module's scope (i.e. CANONICAL_OPERATOR_REACHED,
    already a confirmed attribution, or anything not in the allowlist).

    conn (optional): when supplied, fan-out/funding-mechanism signals fall
    back to a direct wt_discovered_subprovs lookup for this outcome's
    subprovisioner when evidence_json's own nested unknown_infrastructure
    record is absent -- that nested record is only ever populated for
    UNKNOWN_INFRASTRUCTURE outcomes, but wt_discovered_subprovs itself
    tracks fan-out for every subprovisioner regardless of which outcome
    type a given launch resolved to."""
    outcome_type = row.get("outcome_type")
    if outcome_type not in _INFRASTRUCTURE_OUTCOME_TYPES:
        return None
    evidence = _json(row.get("evidence_json"), {})
    treasuries = evidence.get("treasuries") or []
    subprovisioners = evidence.get("subprovisioners") or []
    walkback_class = evidence.get("walkback_class")
    unknown_infra = evidence.get("unknown_infrastructure") or {}
    record = unknown_infra.get("record") if isinstance(unknown_infra, dict) else None
    mechanism = (record or {}).get("funding_mechanism")

    if record is None and conn is not None and subprovisioners:
        try:
            sp_row = conn.execute(
                "SELECT creator_count, wrap_close_count, funding_mechanism "
                "FROM wt_discovered_subprovs WHERE subprov=?",
                (subprovisioners[0],),
            ).fetchone()
        except sqlite3.Error:
            sp_row = None
        if sp_row:
            record = dict(sp_row) if hasattr(sp_row, "keys") else {
                "creator_count": sp_row[0], "wrap_close_count": sp_row[1], "funding_mechanism": sp_row[2]
            }
            mechanism = record.get("funding_mechanism")

    facts: list[str] = [_describe_treasury(treasuries), _describe_subprov(subprovisioners)]
    fanout_label, fanout_count = _describe_fanout(record)
    facts.append(fanout_label)
    facts.append(_describe_funding_mechanism(mechanism))
    settlement = _describe_settlement(walkback_class)
    if settlement:
        facts.append(settlement)
    if outcome_type in {"KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED"}:
        facts.append("Shared Infrastructure")
    if outcome_type == "KNOWN_MULTI_TOKEN_CREATOR":
        facts.append("Known Creator Behaviour")
    if not treasuries and not subprovisioners:
        facts.append("Unknown Controller")

    return InfrastructureFact(
        mint=row.get("mint"),
        creator=evidence.get("creator"),
        facts=tuple(facts),
        treasury=treasuries[0] if treasuries else None,
        subprovisioners=tuple(subprovisioners),
        funding_mechanism=mechanism,
        fan_out_count=fanout_count,
        walkback_class=walkback_class,
        source_outcome_type=outcome_type,
    )


def recent_infrastructure_facts(conn: sqlite3.Connection, *, limit: int = 100, since: int | None = None) -> list[InfrastructureFact]:
    """conn: read-only connection to database/wt_ops_v2.db."""
    query = "SELECT mint, outcome_type, evidence_json, completed_at FROM wt_attribution_outcomes"
    params: tuple = ()
    if since is not None:
        query += " WHERE completed_at >= ?"
        params = (since,)
    query += " ORDER BY completed_at DESC LIMIT ?"
    params = params + (max(1, min(limit, 1000)),)
    rows = conn.execute(query, params).fetchall()
    facts = []
    for row in rows:
        fact = reconstruct_infrastructure_fact(dict(row), conn)
        if fact:
            facts.append(fact)
    return facts
