"""
Operations OS — Canonical Operational Lifecycle.

Defines the platform lifecycle that every operation maps onto.
No operation-specific logic lives here — only the shared vocabulary.

Lifecycle states (ordered):
    IDLE        Nothing of current interest.
    OBSERVING   Tracking candidates/activity; below actionable confidence.
    ARMED       High-confidence evidence that an actionable event is imminent.
    ACTIVE      The predicted event has begun.
    COMPLETED   Full lifecycle observed.
    ARCHIVED    Historical only.

Each operation provides a LifecycleSnapshot via a lifecycle adapter.
Mission Control consumes only the snapshot — never the raw operation state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── State constants ───────────────────────────────────────────────────────────

IDLE      = "IDLE"
OBSERVING = "OBSERVING"
ARMED     = "ARMED"
ACTIVE    = "ACTIVE"
COMPLETED = "COMPLETED"
ARCHIVED  = "ARCHIVED"

STATES_ORDERED: tuple[str, ...] = (IDLE, OBSERVING, ARMED, ACTIVE, COMPLETED, ARCHIVED)
STATES_VALID:   frozenset[str]  = frozenset(STATES_ORDERED)

# UI display metadata per state
STATE_DISPLAY: dict[str, dict] = {
    IDLE:      {"colour": "dim",    "label": "IDLE",      "description": "No active observations"},
    OBSERVING: {"colour": "blue",   "label": "OBSERVING", "description": "Tracking candidates"},
    ARMED:     {"colour": "amber",  "label": "ARMED",     "description": "Actionable event imminent"},
    ACTIVE:    {"colour": "green",  "label": "ACTIVE",    "description": "Event in progress"},
    COMPLETED: {"colour": "muted",  "label": "COMPLETED", "description": "Lifecycle complete"},
    ARCHIVED:  {"colour": "dark",   "label": "ARCHIVED",  "description": "Historical only"},
}


# ── Lifecycle snapshot ────────────────────────────────────────────────────────

@dataclass
class LifecycleSnapshot:
    """
    Represents the current lifecycle state of one operation.

    operation_id        Canonical operation identifier.
    display_name        Human-readable operation name.
    lifecycle_state     One of the STATES_VALID constants.
    state_reason        Short human-readable explanation.
    counts              Per-state entity counts  {OBSERVING: N, ARMED: N, ACTIVE: N, ...}
    confidence          Overall confidence  0.0–1.0 (None if not computable).
    last_transition_at  Unix timestamp of the most recent state change.
    next_expected_state The state this operation is likely to transition to.
    generated_at        Unix timestamp when this snapshot was produced.
    meta                Adapter-specific extra fields (ignored by platform UI).
    """
    operation_id:        str
    display_name:        str
    lifecycle_state:     str
    state_reason:        str
    counts:              dict[str, int]
    confidence:          Optional[float]
    last_transition_at:  Optional[int]
    next_expected_state: Optional[str]
    generated_at:        int
    meta:                dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lifecycle_state not in STATES_VALID:
            raise ValueError(f"Invalid lifecycle state: {self.lifecycle_state!r}")

    def to_dict(self) -> dict:
        return {
            "operation_id":        self.operation_id,
            "display_name":        self.display_name,
            "lifecycle_state":     self.lifecycle_state,
            "state_display":       STATE_DISPLAY[self.lifecycle_state],
            "state_reason":        self.state_reason,
            "counts":              self.counts,
            "confidence":          self.confidence,
            "last_transition_at":  self.last_transition_at,
            "next_expected_state": self.next_expected_state,
            "next_expected_display": STATE_DISPLAY.get(self.next_expected_state or "", {}).get("label"),
            "generated_at":        self.generated_at,
            "meta":                self.meta,
        }


# ── Platform summary ──────────────────────────────────────────────────────────

@dataclass
class PlatformLifecycleSummary:
    """
    Aggregated lifecycle view across all registered operations.
    This is what Mission Control's platform heartbeat strip displays.
    """
    operations_total:    int
    operations_online:   int        # OBSERVING | ARMED | ACTIVE
    observing_total:     int        # sum of counts[OBSERVING] across all ops
    armed_total:         int        # sum of counts[ARMED]
    active_total:        int        # sum of counts[ACTIVE]
    completed_today:     int        # sum of counts[COMPLETED] within 24h window
    snapshots:           list[LifecycleSnapshot]
    generated_at:        int

    def to_dict(self) -> dict:
        return {
            "operations_total":  self.operations_total,
            "operations_online": self.operations_online,
            "observing_total":   self.observing_total,
            "armed_total":       self.armed_total,
            "active_total":      self.active_total,
            "completed_today":   self.completed_today,
            "snapshots":         [s.to_dict() for s in self.snapshots],
            "generated_at":      self.generated_at,
        }
