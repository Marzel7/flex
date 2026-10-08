"""Provider-free coverage, cursor, and handoff contracts for ws_cascade.

These contracts are intentionally inert until a separately approved cascade
integration deploys them.  They prevent an aggregate heartbeat or a populated
cursor from being mistaken for complete acquisition coverage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import sqlite3
from typing import Callable, Iterable, Mapping


MAX_COMPACT_RECORD_BYTES = 16_384
MAX_COMPACT_STORE_BYTES = 10_000_000
SUBSCRIBED = "SUBSCRIBED"
RECONCILIATION_ONLY = "RECONCILIATION_ONLY"
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
DECODED = "DECODED"
FAILED = "FAILED"
UNSUPPORTED = "UNSUPPORTED"
HANDOFF_EMITTED = "HANDOFF_EMITTED"
HANDOFF_DEFERRED = "HANDOFF_DEFERRED"


@dataclass(frozen=True)
class AddressCoverage:
    treasury: str
    activity: str
    subscription_status: str
    cursor_signature: str | None
    reason: str


def report_address_coverage(*, confirmed_treasuries: Iterable[str], subscribed: Iterable[str],
                            cursors: Mapping[str, str | None], activity: Mapping[str, str]) -> list[AddressCoverage]:
    """Return exactly one explicit coverage row for every confirmed treasury."""
    confirmed = sorted(set(str(value) for value in confirmed_treasuries if value))
    if len(confirmed) != len(set(confirmed)):
        raise ValueError("DUPLICATE_CONFIRMED_TREASURY")
    subscribed_set = set(str(value) for value in subscribed if value)
    if not subscribed_set <= set(confirmed):
        raise ValueError("SUBSCRIPTION_OUTSIDE_CONFIRMED_SET")
    return [AddressCoverage(
        treasury=treasury,
        activity=str(activity.get(treasury, "ACTIVITY_UNKNOWN")),
        subscription_status=SUBSCRIBED if treasury in subscribed_set else RECONCILIATION_ONLY,
        cursor_signature=cursors.get(treasury),
        reason="LIVE_SUBSCRIPTION_CONFIRMED" if treasury in subscribed_set else "NO_LIVE_SUBSCRIPTION_REQUIRES_BOUNDED_RECONCILIATION",
    ) for treasury in confirmed]


@dataclass
class SelectedPage:
    treasury: str
    prior_cursor: str | None
    signatures: tuple[str, ...]
    outcomes: dict[str, str]

    def __post_init__(self) -> None:
        if not self.treasury or not self.signatures or len(self.signatures) > 20:
            raise ValueError("INVALID_SELECTED_PAGE")
        if len(set(self.signatures)) != len(self.signatures):
            raise ValueError("DUPLICATE_SELECTED_SIGNATURE")
        if self.outcomes and not set(self.outcomes) <= set(self.signatures):
            raise ValueError("OUTCOME_OUTSIDE_SELECTED_PAGE")

    def record(self, signature: str, outcome: str) -> None:
        if signature not in self.signatures or outcome not in {DECODED, FAILED, UNSUPPORTED}:
            raise ValueError("INVALID_DECODE_OUTCOME")
        self.outcomes[signature] = outcome

    @property
    def coverage_status(self) -> str:
        return COMPLETE if len(self.outcomes) == len(self.signatures) and all(
            value == DECODED for value in self.outcomes.values()
        ) else INCOMPLETE

    def advance_cursor(self, proposed_cursor: str) -> str:
        """Only a fully decoded selected page may advance durable state."""
        if self.coverage_status != COMPLETE or not proposed_cursor:
            raise ValueError("CURSOR_ADVANCE_REQUIRES_COMPLETE_PAGE")
        return proposed_cursor

    def compact_record(self) -> dict:
        record = {"treasury": self.treasury, "prior_cursor": self.prior_cursor,
                  "signatures": list(self.signatures), "outcomes": dict(sorted(self.outcomes.items())),
                  "coverage_status": self.coverage_status}
        if len(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()) > MAX_COMPACT_RECORD_BYTES:
            raise ValueError("COMPACT_PAGE_RECORD_TOO_LARGE")
        return record


_PAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dev019_treasury_selected_pages (
 treasury TEXT NOT NULL,
 prior_cursor TEXT,
 signatures_json TEXT NOT NULL,
 outcomes_json TEXT NOT NULL,
 PRIMARY KEY(treasury, prior_cursor)
);
"""


def open_isolated_coverage_store(path: str) -> sqlite3.Connection:
    """Test-only persistence proof; never accepts a runtime/canonical database path."""
    resolved = os.path.realpath(path)
    if not resolved.startswith("/private/tmp/"):
        raise ValueError("COVERAGE_STORE_MUST_BE_ISOLATED")
    conn = sqlite3.connect(resolved)
    conn.executescript(_PAGE_SCHEMA)
    if os.path.exists(resolved) and os.path.getsize(resolved) > MAX_COMPACT_STORE_BYTES:
        conn.close()
        raise ValueError("COVERAGE_STORE_SIZE_LIMIT_EXCEEDED")
    return conn


def persist_selected_page(conn: sqlite3.Connection, page: SelectedPage) -> None:
    record = page.compact_record()
    conn.execute("INSERT OR REPLACE INTO dev019_treasury_selected_pages VALUES(?,?,?,?)", (
        record["treasury"], record["prior_cursor"], json.dumps(record["signatures"], separators=(",", ":")),
        json.dumps(record["outcomes"], sort_keys=True, separators=(",", ":")),
    ))
    conn.commit()


def load_selected_page(conn: sqlite3.Connection, *, treasury: str, prior_cursor: str | None) -> SelectedPage | None:
    row = conn.execute("SELECT signatures_json,outcomes_json FROM dev019_treasury_selected_pages WHERE treasury=? AND prior_cursor IS ?", (treasury, prior_cursor)).fetchone()
    if row is None:
        return None
    return SelectedPage(treasury, prior_cursor, tuple(json.loads(row[0])), dict(json.loads(row[1])))


@dataclass(frozen=True)
class CompactFundingFact:
    sender: str
    receiver: str
    signature: str
    slot: int
    transaction_index: int | None
    instruction_index: int | None
    lamports: int
    sender_balance_delta: int
    receiver_balance_delta: int
    provenance: str
    coverage_status: str
    route_semantics: str = "DIRECT"

    def validate(self) -> None:
        if (not self.sender or not self.receiver or not self.signature or self.slot < 0
                or self.lamports <= 0 or self.route_semantics != "DIRECT"
                or self.sender_balance_delta > -self.lamports
                or self.receiver_balance_delta < self.lamports
                or self.coverage_status != COMPLETE):
            raise ValueError("INVALID_COMPACT_FUNDING_FACT")

    @property
    def idempotency_key(self) -> str:
        return f"{self.signature}:{self.instruction_index}:{self.sender}:{self.receiver}"

    def compact_record(self) -> dict:
        self.validate()
        record = asdict(self)
        if len(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()) > MAX_COMPACT_RECORD_BYTES:
            raise ValueError("COMPACT_FUNDING_FACT_TOO_LARGE")
        return record


def emit_nonblocking_handoff(*, fact: CompactFundingFact,
                             consumer: Callable[[dict], None]) -> dict:
    """Never let a downstream DEV-019 consumer failure block ws_cascade."""
    record = fact.compact_record()
    try:
        consumer(record)
    except Exception:
        return {"status": HANDOFF_DEFERRED, "idempotency_key": fact.idempotency_key,
                "provider_calls": 0, "cascade_blocked": False}
    return {"status": HANDOFF_EMITTED, "idempotency_key": fact.idempotency_key,
            "provider_calls": 0, "cascade_blocked": False}
