"""
Creator activity redesign — data models.

Enums and dataclasses that mirror the three new DB tables:
  creator_profile, creator_activity_state, creator_activity_jobs
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HistoryStatus(str, Enum):
    UNKNOWN    = "unknown"
    PARTIAL    = "partial"
    BASELINED  = "baselined"
    STALE      = "stale"


class CoverageMode(str, Enum):
    FORWARD_ONLY = "forward_only"
    FULL         = "full"


class WebhookStatus(str, Enum):
    ACTIVE  = "active"
    STOPPED = "stopped"
    ERROR   = "error"


class StreamHealthStatus(str, Enum):
    HEALTHY      = "healthy"
    LAGGING      = "lagging"
    GAP_DETECTED = "gap_detected"
    UNKNOWN      = "unknown"


class JobType(str, Enum):
    BASELINE              = "baseline"
    INCREMENTAL_RECONCILE = "incremental_reconcile"
    BACKFILL              = "backfill"
    REFRESH               = "refresh"


class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETE  = "complete"
    FAILED    = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CreatorProfile:
    creator_address:          str
    history_status:           HistoryStatus           = HistoryStatus.UNKNOWN
    coverage_mode:            CoverageMode            = CoverageMode.FORWARD_ONLY
    classification_status:    Optional[str]           = None
    token_count_seen:         int                     = 0
    first_seen_at:            Optional[int]           = None
    last_seen_at:             Optional[int]           = None
    last_launch_at:           Optional[int]           = None
    last_create_tx_signature: Optional[str]           = None
    last_activity_at:         Optional[int]           = None
    last_full_scan_at:        Optional[int]           = None
    last_incremental_scan_at: Optional[int]           = None
    webhook_started_at:       Optional[int]           = None
    webhook_status:           Optional[WebhookStatus] = None
    baseline_version:         int                     = 1
    created_at:               Optional[int]           = None
    updated_at:               Optional[int]           = None

    @classmethod
    def from_row(cls, row: dict) -> "CreatorProfile":
        return cls(
            creator_address          = row["creator_address"],
            history_status           = HistoryStatus(row["history_status"]) if row.get("history_status") else HistoryStatus.UNKNOWN,
            coverage_mode            = CoverageMode(row["coverage_mode"]) if row.get("coverage_mode") else CoverageMode.FORWARD_ONLY,
            classification_status    = row.get("classification_status"),
            token_count_seen         = row.get("token_count_seen") or 0,
            first_seen_at            = row.get("first_seen_at"),
            last_seen_at             = row.get("last_seen_at"),
            last_launch_at           = row.get("last_launch_at"),
            last_create_tx_signature = row.get("last_create_tx_signature"),
            last_activity_at         = row.get("last_activity_at"),
            last_full_scan_at        = row.get("last_full_scan_at"),
            last_incremental_scan_at = row.get("last_incremental_scan_at"),
            webhook_started_at       = row.get("webhook_started_at"),
            webhook_status           = WebhookStatus(row["webhook_status"]) if row.get("webhook_status") else None,
            baseline_version         = row.get("baseline_version") or 1,
            created_at               = row.get("created_at"),
            updated_at               = row.get("updated_at"),
        )

    @property
    def is_baselined(self) -> bool:
        return self.history_status == HistoryStatus.BASELINED

    @property
    def is_known(self) -> bool:
        """True if we have any prior knowledge of this creator."""
        return self.history_status != HistoryStatus.UNKNOWN

    @property
    def is_high_volume(self) -> bool:
        return self.token_count_seen >= 5


@dataclass
class CreatorActivityState:
    creator_address:          str
    last_seen_signature:      Optional[str]              = None
    last_seen_slot:           Optional[int]              = None
    oldest_scanned_signature: Optional[str]              = None
    newest_scanned_signature: Optional[str]              = None
    last_reconciled_at:       Optional[int]              = None
    needs_backfill:           bool                       = False
    needs_reconcile:          bool                       = False
    last_gap_detected_at:     Optional[int]              = None
    resume_cursor:            Optional[dict]             = None   # {"signature": str, "slot": int}
    stream_health_status:     Optional[StreamHealthStatus] = None
    created_at:               Optional[int]              = None
    updated_at:               Optional[int]              = None

    @classmethod
    def from_row(cls, row: dict) -> "CreatorActivityState":
        cursor_raw = row.get("resume_cursor")
        cursor = json.loads(cursor_raw) if cursor_raw else None
        return cls(
            creator_address          = row["creator_address"],
            last_seen_signature      = row.get("last_seen_signature"),
            last_seen_slot           = row.get("last_seen_slot"),
            oldest_scanned_signature = row.get("oldest_scanned_signature"),
            newest_scanned_signature = row.get("newest_scanned_signature"),
            last_reconciled_at       = row.get("last_reconciled_at"),
            needs_backfill           = bool(row.get("needs_backfill", 0)),
            needs_reconcile          = bool(row.get("needs_reconcile", 0)),
            last_gap_detected_at     = row.get("last_gap_detected_at"),
            resume_cursor            = cursor,
            stream_health_status     = StreamHealthStatus(row["stream_health_status"]) if row.get("stream_health_status") else None,
            created_at               = row.get("created_at"),
            updated_at               = row.get("updated_at"),
        )


@dataclass
class CreatorActivityJob:
    id:              Optional[int]
    creator_address: str
    job_type:        JobType
    status:          JobStatus       = JobStatus.PENDING
    priority:        int             = 100
    attempt_count:   int             = 0
    next_attempt_at: Optional[int]   = None
    locked_at:       Optional[int]   = None
    started_at:      Optional[int]   = None
    completed_at:    Optional[int]   = None
    error:           Optional[str]   = None
    source_mint:     Optional[str]   = None
    source_reason:   Optional[str]   = None
    created_at:      Optional[int]   = None
    updated_at:      Optional[int]   = None

    @classmethod
    def from_row(cls, row: dict) -> "CreatorActivityJob":
        return cls(
            id              = row.get("id"),
            creator_address = row["creator_address"],
            job_type        = JobType(row["job_type"]),
            status          = JobStatus(row["status"]),
            priority        = row.get("priority") or 100,
            attempt_count   = row.get("attempt_count") or 0,
            next_attempt_at = row.get("next_attempt_at"),
            locked_at       = row.get("locked_at"),
            started_at      = row.get("started_at"),
            completed_at    = row.get("completed_at"),
            error           = row.get("error"),
            source_mint     = row.get("source_mint"),
            source_reason   = row.get("source_reason"),
            created_at      = row.get("created_at"),
            updated_at      = row.get("updated_at"),
        )
