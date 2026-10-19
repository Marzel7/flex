"""
Operations OS — Canonical Operator Model.

An Operator is a persistent actor identity inferred from evidence across one or
more operations.  It is NOT a detected event and NOT a single wallet.

Design rules:
  - Operators are only created when Identity-class evidence is present.
  - Supporting evidence raises confidence but never independently creates an operator.
  - Context evidence is stored for presentation but never affects identity.
  - Human review decisions are permanent and cannot be silently overwritten.
  - Every operator must be explainable: "why do we believe these wallets belong together?"

Evidence hierarchy
──────────────────
  IDENTITY    — strong enough to support identity resolution on its own
  SUPPORTING  — raises confidence; cannot independently merge operators
  CONTEXT     — presentation only; must never affect identity

Operator states
───────────────
  CANDIDATE         — no validated identity signal; never auto-created by resolution
  REVIEW_CANDIDATE  — one identity evidence class; requires analyst review
  PROVISIONAL       — two or more independent identity classes; not human-confirmed
  CONFIRMED      — human review accepted the attribution
  REJECTED       — human review rejected the attribution
  MERGE_REVIEW   — auto-resolution proposes merging two operators
  SPLIT_REVIEW   — auto-resolution proposes splitting one operator
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Operator states ──────────────────────────────────────────────────────────

CANDIDATE    = "CANDIDATE"
REVIEW_CANDIDATE = "REVIEW_CANDIDATE"
PROVISIONAL  = "PROVISIONAL"
CONFIRMED    = "CONFIRMED"
REJECTED     = "REJECTED"
MERGE_REVIEW = "MERGE_REVIEW"
SPLIT_REVIEW = "SPLIT_REVIEW"
REVIEW       = "REVIEW"
MERGED       = "MERGED"
SPLIT        = "SPLIT"
RETIRED      = "RETIRED"

OPERATOR_STATES = frozenset({
    CANDIDATE, REVIEW_CANDIDATE, PROVISIONAL, CONFIRMED, REJECTED,
    MERGE_REVIEW, SPLIT_REVIEW, REVIEW, MERGED, SPLIT, RETIRED,
})
# States that a human has acted on — never overwrite these automatically
HUMAN_DECIDED = frozenset({CONFIRMED, REJECTED, REVIEW, MERGED, SPLIT, RETIRED})

# ── Evidence categories ──────────────────────────────────────────────────────

EVIDENCE_IDENTITY   = "IDENTITY"
EVIDENCE_SUPPORTING = "SUPPORTING"
EVIDENCE_CONTEXT    = "CONTEXT"

EVIDENCE_CATEGORIES = frozenset({EVIDENCE_IDENTITY, EVIDENCE_SUPPORTING, EVIDENCE_CONTEXT})

# ── Evidence types ──────────────────────────────────────────────────────────
# Each type maps to a category (IDENTITY / SUPPORTING / CONTEXT).
# Weight is advisory only — resolution rules use category, not raw weight.

EVIDENCE_CATALOGUE: dict[str, dict[str, Any]] = {
    # ── IDENTITY evidence ──────────────────────────────────────────────────
    "SHARED_TREASURY_ROOT": {
        "category": EVIDENCE_IDENTITY,
        "weight":   1.0,
        "notes":    "Two operations share the same confirmed treasury root wallet. "
                    "Treasury wallets are single-actor controlled; sharing one is "
                    "the strongest possible link between operations.",
    },
    "REPEATED_STRUCTURAL_PROVISIONING": {
        "category": EVIDENCE_IDENTITY,
        "weight":   0.90,
        "notes":    "The same treasury→sub-provisioner→creator chain appears in "
                    "multiple campaigns. Structural reuse of a provisioning chain "
                    "implies shared operator control.",
    },
    "CONFIRMED_INFRASTRUCTURE_REUSE": {
        "category": EVIDENCE_IDENTITY,
        "weight":   0.85,
        "notes":    "Three or more specific infrastructure wallets (sub-provisioners, "
                    "signallers, or relay wallets) appear in multiple operations. "
                    "Infrastructure wallets are expensive to rotate and strongly "
                    "indicate the same controlling actor.",
    },
    "VANITY_ADDRESS_FAMILY": {
        "category": EVIDENCE_IDENTITY,
        "weight":   0.75,
        "notes":    "Treasury or signaller wallets share a deliberate ground prefix "
                    "(≥4 chars) consistent with vanity address generation. Deliberate "
                    "vanity families indicate the same key-generation toolchain.",
    },
    "CROSS_OPERATION_WALLET_OVERLAP": {
        "category": EVIDENCE_IDENTITY,
        "weight":   0.80,
        "notes":    "The same wallet address appears in confirmed roles across two or "
                    "more separate operations. A wallet playing infrastructure roles "
                    "in multiple operations implies shared control.",
    },
    # ── SUPPORTING evidence ────────────────────────────────────────────────
    "MATCHING_FUNDING_TEMPLATE": {
        "category": EVIDENCE_SUPPORTING,
        "weight":   0.30,
        "notes":    "Operations use identical SOL funding amounts (ATA template). "
                    "Templates are shared tooling, not identity. Many operators can "
                    "use the same template.",
    },
    "WRAP_CLOSE_REUSE": {
        "category": EVIDENCE_SUPPORTING,
        "weight":   0.25,
        "notes":    "Wrap-close pattern reused across campaigns. The pattern is a "
                    "common technique; alone it is only supporting evidence.",
    },
    "TIMING_CADENCE_SIMILARITY": {
        "category": EVIDENCE_SUPPORTING,
        "weight":   0.20,
        "notes":    "Operations show similar inter-launch timing. Cadence is a soft "
                    "signal — multiple operators can share similar timing.",
    },
    "SHARED_PAYOUT_WALLET": {
        "category": EVIDENCE_SUPPORTING,
        "weight":   0.35,
        "notes":    "Two creators route proceeds to the same downstream wallet. "
                    "Stronger than pure timing but payout wallets can be shared "
                    "without shared operation control.",
    },
    "SWARM_PARTICIPATION_OVERLAP": {
        "category": EVIDENCE_SUPPORTING,
        "weight":   0.25,
        "notes":    "Trader wallets overlap between campaigns. Possible shared "
                    "provisioner but individual traders can be reused independently.",
    },
    "PLAYBOOK_SIGNATURE_MATCH": {
        "category": EVIDENCE_SUPPORTING,
        "weight":   0.30,
        "notes":    "Operations match the same high-level playbook signature "
                    "(e.g. ATA+swarm-sweep-recycle). Playbooks represent tooling "
                    "families, not individual operators.",
    },
    # ── CONTEXT evidence ───────────────────────────────────────────────────
    "OPERATING_HOURS": {
        "category": EVIDENCE_CONTEXT,
        "weight":   0.0,
        "notes":    "Operating hour patterns. Context only — must never affect identity.",
    },
    "TOKEN_PERFORMANCE": {
        "category": EVIDENCE_CONTEXT,
        "weight":   0.0,
        "notes":    "Market cap, peak price, migration outcome. Presentation only.",
    },
    "SUCCESS_RATE": {
        "category": EVIDENCE_CONTEXT,
        "weight":   0.0,
        "notes":    "Ratio of migrated vs dumped launches. Context for profiling.",
    },
    "CHAIN_ACTIVITY": {
        "category": EVIDENCE_CONTEXT,
        "weight":   0.0,
        "notes":    "On-chain activity volume, SOL deployed. Context for scale.",
    },
}

# ── Confidence levels (for operators) ────────────────────────────────────────

CONFIDENCE_CERTAIN  = "CERTAIN"
CONFIDENCE_HIGH     = "HIGH"
CONFIDENCE_MEDIUM   = "MEDIUM"
CONFIDENCE_LOW      = "LOW"
CONFIDENCE_UNKNOWN  = "UNKNOWN"

CONFIDENCE_LEVELS   = (CONFIDENCE_CERTAIN, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM,
                       CONFIDENCE_LOW, CONFIDENCE_UNKNOWN)
CONFIDENCE_RANK     = {c: i for i, c in enumerate(CONFIDENCE_LEVELS)}

# ── Entity types within an operator ──────────────────────────────────────────

ENTITY_TREASURY     = "TREASURY"
ENTITY_SUB_PROV     = "SUB_PROVISIONER"
ENTITY_CREATOR      = "CREATOR"
ENTITY_SIGNALLER    = "SIGNALLER"
ENTITY_RELAY        = "RELAY"
ENTITY_COLLECTOR    = "COLLECTOR"
ENTITY_UNKNOWN      = "UNKNOWN"

OPERATOR_ENTITY_TYPES = frozenset({
    ENTITY_TREASURY, ENTITY_SUB_PROV, ENTITY_CREATOR,
    ENTITY_SIGNALLER, ENTITY_RELAY, ENTITY_COLLECTOR, ENTITY_UNKNOWN,
})

# ── DDL ──────────────────────────────────────────────────────────────────────

DDL = """
-- Operator identity table
CREATE TABLE IF NOT EXISTS operators (
    operator_id     TEXT PRIMARY KEY,           -- uuid4
    status          TEXT NOT NULL DEFAULT 'CANDIDATE',
    confidence      TEXT NOT NULL DEFAULT 'UNKNOWN',
    first_seen      INTEGER,                    -- unix ts
    last_seen       INTEGER,                    -- unix ts
    summary         TEXT,                       -- human-readable one-liner
    review_state    TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | IN_REVIEW | REVIEWED
    display_name    TEXT,                       -- optional analyst-set label
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

-- Entity membership within an operator
CREATE TABLE IF NOT EXISTS operator_entities (
    operator_id     TEXT NOT NULL REFERENCES operators(operator_id),
    entity_address  TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'UNKNOWN',
    confidence      TEXT NOT NULL DEFAULT 'UNKNOWN',
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    first_seen      INTEGER,
    last_seen       INTEGER,
    added_at        INTEGER NOT NULL,
    PRIMARY KEY (operator_id, entity_address)
);
CREATE INDEX IF NOT EXISTS ix_ope_entity ON operator_entities(entity_address);

-- Evidence records — one row per piece of identity/supporting evidence
CREATE TABLE IF NOT EXISTS operator_evidence (
    evidence_id     TEXT PRIMARY KEY,           -- uuid4
    operator_id     TEXT NOT NULL REFERENCES operators(operator_id),
    evidence_type   TEXT NOT NULL,              -- key from EVIDENCE_CATALOGUE
    evidence_category TEXT NOT NULL,            -- IDENTITY | SUPPORTING | CONTEXT
    source_operation TEXT,                      -- operation_id that produced this
    entity_a        TEXT,                       -- first wallet/entity involved
    entity_b        TEXT,                       -- second wallet/entity involved (if applicable)
    weight          REAL NOT NULL DEFAULT 0.0,
    details         TEXT,                       -- JSON: extra fields
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oe_operator ON operator_evidence(operator_id);
CREATE INDEX IF NOT EXISTS ix_oe_type     ON operator_evidence(evidence_type);
CREATE INDEX IF NOT EXISTS ix_oe_entity_a ON operator_evidence(entity_a);

-- Human review decisions — append-only, never delete
CREATE TABLE IF NOT EXISTS operator_reviews (
    review_id       TEXT PRIMARY KEY,           -- uuid4
    operator_id     TEXT NOT NULL REFERENCES operators(operator_id),
    decision        TEXT NOT NULL,              -- CONFIRMED | REJECTED | MERGE | SPLIT | FLAGGED
    reviewer        TEXT,                       -- analyst identifier (optional)
    timestamp       INTEGER NOT NULL,
    reason          TEXT,                       -- free-text explanation
    related_operator_id TEXT                    -- for MERGE/SPLIT decisions
);
CREATE INDEX IF NOT EXISTS ix_or_operator ON operator_reviews(operator_id);

-- Governance decisions over read-only promotion proposals.  Rejections and
-- deferrals have no canonical operator_id, so they cannot live in
-- operator_reviews.  This ledger is append-only and preserves the exact
-- evidence package reviewed by the analyst.
CREATE TABLE IF NOT EXISTS operator_promotion_reviews (
    promotion_review_id TEXT PRIMARY KEY,
    proposal_id         TEXT NOT NULL,
    proposal_fingerprint TEXT NOT NULL,
    identity_fingerprint TEXT NOT NULL,
    candidate_key       TEXT NOT NULL,
    decision            TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT','DEFER')),
    reviewer            TEXT NOT NULL,
    timestamp           INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    supporting_notes    TEXT,
    evidence_snapshot   TEXT NOT NULL,
    canonical_operator_id TEXT,
    UNIQUE(proposal_fingerprint, decision)
);
CREATE INDEX IF NOT EXISTS ix_opr_proposal ON operator_promotion_reviews(proposal_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_opr_operator ON operator_promotion_reviews(canonical_operator_id, timestamp DESC);

CREATE TRIGGER IF NOT EXISTS operator_promotion_reviews_immutable_update
BEFORE UPDATE ON operator_promotion_reviews
BEGIN
    SELECT RAISE(ABORT, 'promotion review history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS operator_promotion_reviews_immutable_delete
BEFORE DELETE ON operator_promotion_reviews
BEGIN
    SELECT RAISE(ABORT, 'promotion review history is immutable');
END;
"""


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Operator:
    operator_id:  str
    status:       str
    confidence:   str
    first_seen:   int | None
    last_seen:    int | None
    summary:      str | None
    review_state: str
    display_name: str | None
    created_at:   int
    updated_at:   int

    def __post_init__(self) -> None:
        if self.status not in OPERATOR_STATES:
            raise ValueError(f"Invalid operator status: {self.status!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"Invalid confidence: {self.confidence!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_id":  self.operator_id,
            "status":       self.status,
            "confidence":   self.confidence,
            "first_seen":   self.first_seen,
            "last_seen":    self.last_seen,
            "summary":      self.summary,
            "review_state": self.review_state,
            "display_name": self.display_name,
            "created_at":   self.created_at,
            "updated_at":   self.updated_at,
        }


@dataclass
class OperatorEvidence:
    evidence_id:       str
    operator_id:       str
    evidence_type:     str
    evidence_category: str
    source_operation:  str | None
    entity_a:          str | None
    entity_b:          str | None
    weight:            float
    details:           dict[str, Any]
    created_at:        int

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":       self.evidence_id,
            "operator_id":       self.operator_id,
            "evidence_type":     self.evidence_type,
            "evidence_category": self.evidence_category,
            "source_operation":  self.source_operation,
            "entity_a":          self.entity_a,
            "entity_b":          self.entity_b,
            "weight":            self.weight,
            "details":           self.details,
            "created_at":        self.created_at,
        }


def new_operator_id() -> str:
    return str(uuid.uuid4())


def new_evidence_id() -> str:
    return str(uuid.uuid4())


def new_review_id() -> str:
    return str(uuid.uuid4())
