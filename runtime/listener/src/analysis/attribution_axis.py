"""
attribution_axis.py — derive the first-class ATTRIBUTION axis from existing evidence.

PART OF: classification architecture redesign (Risk / Creator-State / Attribution).
This module implements ONLY Axis 3 (Attribution), and does so as a pure, READ-ONLY
derivation over the data already stored in creator_risk_scores.watchtower_evidence_json.

Why derive instead of trusting creator_risk_scores.evidence_basis.category:
    The stored `category` collapses a creator who is simultaneously launch-linked AND
    extraction-linked into ONE value (observed: 73 creators graded DIRECT_INFRA but
    categorised EXTRACTION_PROFIT_RELAY because they were funded by infra *and* routed
    profit to a relay). Both facts are true; the single-value field can't say so.
    The three-axis model fixes this by treating the headline linkage as the strongest
    single path and the evidence as a multi-value SET.

It writes nothing and changes no classification. It is a lens over existing evidence.

Shape returned by derive_attribution(evidence_json, grade):
    {
      "linkage":     <headline AttributionLinkage>,   # single strongest path, or NONE
      "confidence":  "CONFIRMED" | "STRONG" | "WEAK" | None,
      "evidence":    [ {kind, rule, detail}, ... ],    # multi-value set, all paths
      "is_attributed": bool,
    }
"""
from __future__ import annotations
import json
from typing import Optional


# ── Attribution linkage vocabulary (Axis 3 headline values) ──────────────────
# Mirrors the design ontology. Distinct from RISK (severity) and CREATOR STATE.
LINK_NONE          = "NONE"
LINK_DIRECT        = "WATCHTOWER_DIRECT"        # funded directly by / is infra wallet
LINK_PROVISIONING  = "WATCHTOWER_PROVISIONING"  # lineage terminates at a provisioning hub
LINK_LINEAGE       = "WATCHTOWER_LINEAGE"       # multi-hop lineage to static infra
LINK_RELAY         = "WATCHTOWER_RELAY"         # profit-relay routing (extraction side)
LINK_COLLECTOR     = "WATCHTOWER_COLLECTOR"     # fee collector / aggregator flow
LINK_FINGERPRINT   = "WATCHTOWER_FINGERPRINT"   # fingerprint-only (soft; held)

# Evidence-rule  → (linkage, evidence-kind, default confidence).
# Priority order = the headline hierarchy: a creator reached by several paths takes
# the FIRST matching row as the headline `linkage`; every matching rule is recorded
# in the evidence SET. This is the explicit "single headline + multi evidence" model.
_RULE_MAP: list[tuple[str, str, str, str]] = [
    # rule                              linkage             kind            confidence
    ("direct_infrastructure_wallet",    LINK_DIRECT,        "direct_infra", "CONFIRMED"),
    ("funded_by_infrastructure",        LINK_DIRECT,        "direct_infra", "CONFIRMED"),
    ("watchtower_fee_payment",          LINK_COLLECTOR,     "fee_payment",  "CONFIRMED"),
    ("lineage_to_infrastructure",       LINK_LINEAGE,       "lineage",      "STRONG"),
    ("profit_relay_routing",            LINK_RELAY,         "profit_relay", "STRONG"),
    ("signaller_activation_sequence",   LINK_DIRECT,        "signaller",    "STRONG"),
    ("signaller_dust",                  LINK_DIRECT,        "signaller",    "STRONG"),
    ("watchtower_queue_tag",            LINK_LINEAGE,       "queue_tag",    "STRONG"),
    ("confirmed_fingerprint_batch",     LINK_FINGERPRINT,   "fingerprint",  "WEAK"),
    ("funding_fingerprint",             LINK_FINGERPRINT,   "fingerprint",  "WEAK"),
]
_RULE_LOOKUP = {r[0]: r for r in _RULE_MAP}

_CONF_RANK = {"CONFIRMED": 3, "STRONG": 2, "WEAK": 1, None: 0}

# Fallback: some creators were attributed by the lineage-backfill / fingerprint path
# which sets evidence_basis.method but writes NO watchtower_evidence_json rules
# (observed: treasury_lineage_3hop x45, fingerprint_only x15). For those we derive
# the linkage from the stored `method` instead of from rules.
_METHOD_MAP: dict[str, tuple[str, str]] = {
    # method                  linkage            confidence
    "DIRECT_INFRA":           (LINK_DIRECT,       "CONFIRMED"),
    "FEE_PAYMENT":            (LINK_COLLECTOR,    "CONFIRMED"),
    "fee_payer_observation":  (LINK_COLLECTOR,    "CONFIRMED"),
    "LINEAGE_RULE_2":         (LINK_LINEAGE,      "STRONG"),
    "treasury_lineage_3hop":  (LINK_LINEAGE,      "STRONG"),
    "PROFIT_RELAY":           (LINK_RELAY,        "STRONG"),
    "SIGNALLER_SEQUENCE":     (LINK_DIRECT,       "STRONG"),
    "SIGNALLER_DUST":         (LINK_DIRECT,       "STRONG"),
    "QUEUE_TAG":              (LINK_LINEAGE,      "STRONG"),
    "FINGERPRINT_BATCH":      (LINK_FINGERPRINT,  "WEAK"),
    "fingerprint_only":       (LINK_FINGERPRINT,  "WEAK"),
}

# Linkages whose confidence must never exceed WEAK, regardless of stored grade
# (fingerprint alone is a soft signal — consistent with the lineage-Rule-2 policy
# that fingerprint-only attributions are HELD, never promoted).
_CAP_WEAK = {LINK_FINGERPRINT}


def _coerce_evidence(evidence_json) -> list[dict]:
    if not evidence_json:
        return []
    if isinstance(evidence_json, list):
        return evidence_json
    try:
        v = json.loads(evidence_json)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def derive_attribution(evidence_json, grade: Optional[str] = None,
                       method: Optional[str] = None) -> dict:
    """
    Pure derivation of the Attribution axis from stored evidence.

    Primary source = the evidence-rule SET in `evidence_json`. When that is empty
    (lineage-backfill / fingerprint attributions store only a `method`), fall back
    to `method`. `grade` may upgrade derived confidence but never lowers a CONFIRMED,
    and never lifts a soft-capped linkage. Provisioning is detected from a lineage
    rule whose terminal is a hub.
    """
    evidence = _coerce_evidence(evidence_json)
    if not evidence:
        # fallback to stored method (creators attributed without evidence rules)
        m = _METHOD_MAP.get(method or "")
        if m:
            linkage, conf = m
            if linkage in _CAP_WEAK:
                conf = "WEAK"
            elif grade and _CONF_RANK.get(grade, 0) > _CONF_RANK.get(conf, 0):
                conf = grade
            return {"linkage": linkage, "confidence": conf,
                    "evidence": [{"kind": "method_only", "linkage": linkage,
                                  "rule": method, "detail": f"attributed via {method}"}],
                    "is_attributed": True}
        return {"linkage": LINK_NONE, "confidence": None, "evidence": [], "is_attributed": False}

    ev_set: list[dict] = []
    seen = set()
    headline = None
    headline_conf = None

    for e in evidence:
        rule = e.get("rule")
        m = _RULE_LOOKUP.get(rule)
        if not m:
            continue
        _, linkage, kind, conf = m
        # promotion: lineage that terminates at a hub is PROVISIONING, not generic lineage
        if rule == "lineage_to_infrastructure" and e.get("terminal_kind") == "hub":
            linkage = LINK_PROVISIONING
        strength = e.get("strength")
        # a rule tagged 'weak' in the evidence caps its confidence at WEAK
        eff_conf = "WEAK" if strength == "weak" else conf
        key = (linkage, kind)
        if key not in seen:
            seen.add(key)
            ev_set.append({"kind": kind, "linkage": linkage, "rule": rule,
                           "detail": e.get("detail", "")})
        # headline = first strong rule in hierarchy order; track best confidence
        if strength == "strong" and headline is None:
            headline, headline_conf = linkage, eff_conf

    if headline is None:
        # no strong rule → fall back to the strongest weak linkage present, if any
        if ev_set:
            headline = ev_set[0]["linkage"]
            headline_conf = "WEAK"
        else:
            return {"linkage": LINK_NONE, "confidence": None, "evidence": [], "is_attributed": False}

    # let a stronger stored grade lift confidence (never lower a CONFIRMED), EXCEPT
    # soft-capped linkages (fingerprint-only stays WEAK no matter the stored grade).
    if headline in _CAP_WEAK:
        headline_conf = "WEAK"
    elif grade and _CONF_RANK.get(grade, 0) > _CONF_RANK.get(headline_conf, 0):
        headline_conf = grade

    return {
        "linkage": headline,
        "confidence": headline_conf,
        "evidence": ev_set,
        "is_attributed": True,
    }


def linkage_label(linkage: str) -> str:
    """Human label for UI."""
    return {
        LINK_NONE:         "None",
        LINK_DIRECT:       "Direct Infra",
        LINK_PROVISIONING: "Provisioning Hub",
        LINK_LINEAGE:      "Lineage (multi-hop)",
        LINK_RELAY:        "Profit Relay",
        LINK_COLLECTOR:    "Collector Flow",
        LINK_FINGERPRINT:  "Fingerprint (soft)",
    }.get(linkage, linkage)
