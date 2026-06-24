"""
WATCHTOWER Webhook Lifecycle Manager
=====================================
Manages two persistent Helius webhooks:
  INFRA_WEBHOOK    — treasury, signallers, sub-provisioners, relays
  CANDIDATE_WEBHOOK — predicted creator candidates

Architecture:
  - Local DB (wt_webhook_enrollments) = EXPECTED state
  - Helius detail endpoint            = ACTUAL state
  - Reconciliation worker             = keeps them in sync
  - Never trusts the Helius summary/list API (known to be wrong)

Enrollment flow:
  candidate detected → insert DB → debounce queue → batch PUT to Helius → verify

Removal flow:
  launched / expired / dormant → mark de-enrolled → batch PUT → archive

Reconciliation:
  every RECONCILE_INTERVAL_S seconds, compare DB vs Helius detail,
  re-enroll missing, log orphans, update last_confirmed_at.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_HELIUS_BASE        = "https://api-mainnet.helius-rpc.com/v0/webhooks"
_RECONCILE_INTERVAL = 120          # seconds between reconciliation runs
_DEBOUNCE_WINDOW    = 8            # seconds to collect enrollments before PUT
_MAX_ADDRS_PER_WH   = 100_000      # Helius limit per webhook
_SHARD_THRESHOLD    = 90_000       # create a new shard when approaching limit
_CANDIDATE_COOLDOWN = 3600 * 4     # 4h cooldown after launch before removal
_CANDIDATE_EXPIRY   = 3600 * 96    # 96h: candidate not yet launched → lower priority
_LOG_PREFIX         = "[WH_MGR]"

# Roles for the infra webhook
INFRA_ROLE      = "infra"
CANDIDATE_ROLE  = "candidate"

# ── Webhook registry ─────────────────────────────────────────────────────────

@dataclass
class WebhookDef:
    webhook_id:   str
    role:         str           # INFRA_ROLE or CANDIDATE_ROLE
    env_var:      str           # env var holding the webhook ID
    tx_types:     list[str]     = field(default_factory=lambda: ["ANY"])
    description:  str           = ""


def _webhooks() -> dict[str, WebhookDef]:
    """Return live webhook definitions from env."""
    infra_id = os.getenv("WATCHTOWER_INFRA_WEBHOOK_ID",
                         "106e20f6-f542-42b0-83d5-ca8c7b1a7162")
    cand_id  = os.getenv("CREATOR_MOVEMENT_WEBHOOK_ID",
                         "763810e4-e41c-4f83-9ebc-55e9ab413ea4")
    return {
        INFRA_ROLE: WebhookDef(
            webhook_id  = infra_id,
            role        = INFRA_ROLE,
            env_var     = "WATCHTOWER_INFRA_WEBHOOK_ID",
            tx_types    = ["ANY"],
            description = "WATCHTOWER infrastructure: treasury, sub-provs, relays",
        ),
        CANDIDATE_ROLE: WebhookDef(
            webhook_id  = cand_id,
            role        = CANDIDATE_ROLE,
            env_var     = "CREATOR_MOVEMENT_WEBHOOK_ID",
            tx_types    = ["TRANSFER"],
            description = "Creator candidate movement monitoring",
        ),
    }


# ── DB schema ─────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_webhook_enrollments (
    wallet_address      TEXT    NOT NULL,
    webhook_id          TEXT    NOT NULL,
    role                TEXT    NOT NULL DEFAULT 'candidate',
    state               TEXT    NOT NULL DEFAULT 'ENROLLED',
    -- states: PENDING | ENROLLED | CONFIRMED | COOLDOWN | REMOVED | FAILED
    enrolled_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    confirmed_at        INTEGER,
    de_enrolled_at      INTEGER,
    removed_at          INTEGER,
    failure_count       INTEGER NOT NULL DEFAULT 0,
    last_failure_at     INTEGER,
    last_failure_reason TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    notes               TEXT,
    PRIMARY KEY (wallet_address, webhook_id)
);

CREATE INDEX IF NOT EXISTS idx_wt_enroll_active
    ON wt_webhook_enrollments (is_active, webhook_id);
CREATE INDEX IF NOT EXISTS idx_wt_enroll_state
    ON wt_webhook_enrollments (state, role);
CREATE INDEX IF NOT EXISTS idx_wt_enroll_enrolled
    ON wt_webhook_enrollments (enrolled_at DESC);

CREATE TABLE IF NOT EXISTS wt_webhook_reconciliations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id      TEXT    NOT NULL,
    ran_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    local_count     INTEGER,
    remote_count    INTEGER,
    re_enrolled     INTEGER DEFAULT 0,
    orphans_found   INTEGER DEFAULT 0,
    drift_detected  INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_wt_recon_wid
    ON wt_webhook_reconciliations (webhook_id, ran_at DESC);
"""


_schema_ensured = False  # per-process flag — schema DDL runs once only

def ensure_schema(conn: sqlite3.Connection) -> None:
    global _schema_ensured
    if _schema_ensured:
        return  # skip DDL on every subsequent _conn() call — avoids exclusive lock contention

    # Migrate legacy wt_webhook_enrollments (single-col PK) to new composite-PK schema
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wt_webhook_enrollments'"
    ).fetchone()
    if row and "PRIMARY KEY (wallet_address, webhook_id)" not in (row[0] or ""):
        try:
            # Disable foreign keys and use legacy_alter_table to avoid view errors
            conn.execute("PRAGMA legacy_alter_table = ON")
            conn.execute("ALTER TABLE wt_webhook_enrollments RENAME TO wt_webhook_enrollments_old")
            conn.execute(SCHEMA.strip().split(";")[0])  # CREATE TABLE statement
            conn.execute("""
                INSERT OR IGNORE INTO wt_webhook_enrollments
                    (wallet_address, webhook_id, role, state, enrolled_at, is_active)
                SELECT wallet_address, webhook_id, 'candidate', 'ENROLLED', enrolled_at, is_active
                FROM wt_webhook_enrollments_old
            """)
            conn.execute("DROP TABLE wt_webhook_enrollments_old")
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.commit()
            logger.info("%s Migrated wt_webhook_enrollments to composite-PK schema", _LOG_PREFIX)
        except Exception as exc:
            logger.warning("%s Schema migration failed: %s", _LOG_PREFIX, exc)

    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    conn.commit()
    _schema_ensured = True


# ── Helius API helpers ────────────────────────────────────────────────────────

def _api_key() -> str:
    return os.getenv("HELIUS_API_KEY", "").strip()


async def _get_webhook_detail(
    session: aiohttp.ClientSession,
    webhook_id: str,
) -> dict:
    """
    Fetch webhook from DETAIL endpoint (always use this — summary API is unreliable).
    Raises RuntimeError on non-200.
    """
    url = f"{_HELIUS_BASE}/{webhook_id}?api-key={_api_key()}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"GET webhook/{webhook_id} → {r.status}: {body}")
        return body


async def _put_webhook(
    session: aiohttp.ClientSession,
    webhook_id: str,
    existing: dict,
    addresses: list[str],
) -> dict:
    """
    Full-list replacement PUT (Helius requires sending entire address list).
    Preserves webhookURL, webhookType, transactionTypes, authHeader from existing.
    """
    payload: dict = {
        "webhookURL":       existing["webhookURL"],
        "webhookType":      existing.get("webhookType", "enhanced"),
        "transactionTypes": existing.get("transactionTypes", ["ANY"]),
        "accountAddresses": addresses,
    }
    if existing.get("authHeader"):
        payload["authHeader"] = existing["authHeader"]

    url = f"{_HELIUS_BASE}/{webhook_id}?api-key={_api_key()}"
    async with session.put(
        url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
    ) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"PUT webhook/{webhook_id} → {r.status}: {body}")
        return body


# ── Core manager ──────────────────────────────────────────────────────────────

class WebhookManager:
    """
    Manages enrollment, removal, and reconciliation for WATCHTOWER webhooks.

    Usage:
        mgr = WebhookManager(db_path)
        await mgr.enroll(address, role=CANDIDATE_ROLE)
        await mgr.remove(address, role=CANDIDATE_ROLE, reason="launched")
        await mgr.reconcile()          # run periodically
    """

    def __init__(self, db_path: str) -> None:
        self.db_path   = db_path
        self._lock     = asyncio.Lock()           # one mutation at a time per instance
        self._queue: dict[str, set[str]] = {      # role → pending addresses
            INFRA_ROLE:      set(),
            CANDIDATE_ROLE:  set(),
        }
        self._debounce_tasks: dict[str, asyncio.Task] = {}

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        ensure_schema(conn)
        return conn

    def _db_active_addresses(self, webhook_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT wallet_address FROM wt_webhook_enrollments "
                "WHERE webhook_id=? AND is_active=1",
                (webhook_id,),
            ).fetchall()
        return [r["wallet_address"] for r in rows]

    def _db_upsert(
        self,
        address: str,
        webhook_id: str,
        role: str,
        state: str = "ENROLLED",
        notes: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO wt_webhook_enrollments
                    (wallet_address, webhook_id, role, state, is_active, notes)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(wallet_address, webhook_id) DO UPDATE SET
                    state     = excluded.state,
                    is_active = 1,
                    notes     = COALESCE(excluded.notes, notes)
                """,
                (address, webhook_id, role, state, notes),
            )
            conn.commit()

    def _db_mark_confirmed(self, addresses: list[str], webhook_id: str) -> None:
        now = int(time.time())
        with self._conn() as conn:
            conn.executemany(
                "UPDATE wt_webhook_enrollments "
                "SET state='CONFIRMED', confirmed_at=?, failure_count=0 "
                "WHERE wallet_address=? AND webhook_id=?",
                [(now, a, webhook_id) for a in addresses],
            )
            conn.commit()

    def _db_mark_removed(self, address: str, webhook_id: str, reason: str = "") -> None:
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "UPDATE wt_webhook_enrollments "
                "SET is_active=0, state='REMOVED', de_enrolled_at=?, removed_at=?, notes=? "
                "WHERE wallet_address=? AND webhook_id=?",
                (now, now, reason, address, webhook_id),
            )
            conn.commit()

    def _db_record_failure(
        self, address: str, webhook_id: str, reason: str
    ) -> None:
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "UPDATE wt_webhook_enrollments "
                "SET failure_count = failure_count + 1, "
                "    last_failure_at = ?, last_failure_reason = ?, state = 'FAILED' "
                "WHERE wallet_address=? AND webhook_id=?",
                (now, reason, address, webhook_id),
            )
            conn.commit()

    # ── Enrollment ────────────────────────────────────────────────────────────

    async def enroll(
        self,
        address: str,
        role: str = CANDIDATE_ROLE,
        notes: str = "",
        immediate: bool = False,
    ) -> bool:
        """
        Enroll an address into the webhook for the given role.
        By default uses debounced batching; pass immediate=True to skip debounce.
        Returns True if newly enrolled, False if already active.
        """
        # Never enroll known Tier 2 infra addresses as candidates
        _WT_INFRA_ADDRS = {
            "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM",
            "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM",
            "6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1",
            "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7",
            "C745erBxwn4sJZGDRZpi71FPV3MA3kBQUXWbeJxRsGS4",
            "Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif",
            "F17dbo3EeumSte7hEBgn6wDAv65BEN4U8eba9zXcNTg",
            "EYjGUZamSQ9vJBxZ4yj7pCK2XaZ99MAEQx9xRMrzyMx1",
            "96b4e8qvhjEPsXDtenEgs1VLBTkFqAcgYV8tE16Mgt7h",
            "kFycb9QoQaRgLy3zZpF4Zw5DM5gKoT5HkZogSerq1Hd",
            "3UnqbigDhZvHpQFR29aDhKLEWSNkFcMbuNCrSDk6iikj",
            "67UoZBTBGhFa3irg6hv5dETdwLRTx2HCTyq5H51KFMFu",
        }
        if role == CANDIDATE_ROLE and address in _WT_INFRA_ADDRS:
            logger.warning("%s Refusing to enroll Tier 2 infra address as candidate: %s",
                           _LOG_PREFIX, address[:20])
            return False

        wh = _webhooks().get(role)
        if not wh:
            logger.error("%s Unknown role: %s", _LOG_PREFIX, role)
            return False

        if not _api_key():
            logger.warning("%s HELIUS_API_KEY not set", _LOG_PREFIX)
            return False

        # Check local DB first — avoid redundant Helius calls
        with self._conn() as conn:
            row = conn.execute(
                "SELECT is_active FROM wt_webhook_enrollments "
                "WHERE wallet_address=? AND webhook_id=?",
                (address, wh.webhook_id),
            ).fetchone()
        if row and row["is_active"]:
            logger.debug("%s %s already enrolled", _LOG_PREFIX, address[:16])
            return False

        # Write PENDING to DB immediately
        self._db_upsert(address, wh.webhook_id, role, state="PENDING", notes=notes)

        if immediate:
            await self._flush_role(role)
        else:
            self._queue[role].add(address)
            await self._schedule_debounce(role)

        return True

    async def enroll_batch(
        self,
        addresses: list[str],
        role: str = CANDIDATE_ROLE,
        notes: str = "",
    ) -> int:
        """Enroll multiple addresses at once. Returns count newly enrolled."""
        wh = _webhooks().get(role)
        if not wh or not _api_key():
            return 0

        with self._conn() as conn:
            existing = {
                r["wallet_address"]
                for r in conn.execute(
                    "SELECT wallet_address FROM wt_webhook_enrollments "
                    "WHERE webhook_id=? AND is_active=1",
                    (wh.webhook_id,),
                ).fetchall()
            }

        new_addrs = [a for a in addresses if a not in existing]
        if not new_addrs:
            return 0

        for addr in new_addrs:
            self._db_upsert(addr, wh.webhook_id, role, state="PENDING", notes=notes)
            self._queue[role].add(addr)

        await self._flush_role(role)
        return len(new_addrs)

    async def remove(
        self,
        address: str,
        role: str = CANDIDATE_ROLE,
        reason: str = "",
        cooldown_s: int = 0,
    ) -> None:
        """
        Remove an address from the webhook.
        If cooldown_s > 0, waits before actually removing (for post-launch retention).
        """
        wh = _webhooks().get(role)
        if not wh or not _api_key():
            return

        if cooldown_s > 0:
            await asyncio.sleep(cooldown_s)

        async with self._lock:
            async with aiohttp.ClientSession() as session:
                try:
                    existing    = await _get_webhook_detail(session, wh.webhook_id)
                    current     = existing.get("accountAddresses") or []
                    updated     = [a for a in current if a != address]
                    await _put_webhook(session, wh.webhook_id, existing, updated)
                    self._db_mark_removed(address, wh.webhook_id, reason)
                    logger.info(
                        "%s Removed %s from %s webhook (reason=%s, remaining=%d)",
                        _LOG_PREFIX, address[:16], role, reason, len(updated),
                    )
                except Exception as exc:
                    logger.error(
                        "%s Remove failed for %s: %s", _LOG_PREFIX, address[:16], exc
                    )

    # ── Debounce / flush ──────────────────────────────────────────────────────

    async def _schedule_debounce(self, role: str) -> None:
        """Cancel any existing debounce task and schedule a fresh one."""
        existing = self._debounce_tasks.get(role)
        if existing and not existing.done():
            existing.cancel()
        self._debounce_tasks[role] = asyncio.create_task(
            self._debounce_flush(role)
        )

    async def _debounce_flush(self, role: str) -> None:
        await asyncio.sleep(_DEBOUNCE_WINDOW)
        await self._flush_role(role)

    async def _flush_role(self, role: str) -> None:
        """
        Flush all pending addresses for a role to Helius in a single PUT.
        Uses the local DB as source of truth for the full desired address list.
        """
        wh = _webhooks().get(role)
        if not wh or not _api_key():
            return

        async with self._lock:
            desired = self._db_active_addresses(wh.webhook_id)
            self._queue[role].clear()

            if not desired:
                return

            async with aiohttp.ClientSession() as session:
                try:
                    existing = await _get_webhook_detail(session, wh.webhook_id)
                    await _put_webhook(session, wh.webhook_id, existing, desired)
                    self._db_mark_confirmed(desired, wh.webhook_id)
                    logger.info(
                        "%s Flushed %s webhook — %d addresses enrolled",
                        _LOG_PREFIX, role, len(desired),
                    )
                except Exception as exc:
                    logger.error("%s Flush failed for role=%s: %s", _LOG_PREFIX, role, exc)
                    for addr in desired:
                        self._db_record_failure(addr, wh.webhook_id, str(exc))

    # ── Reconciliation ────────────────────────────────────────────────────────

    async def reconcile(self, role: Optional[str] = None) -> dict:
        """
        Compare local DB vs Helius detail endpoint.
        Re-enroll any addresses missing from Helius.
        Log orphaned addresses (in Helius but not in local DB).
        Returns a summary dict.
        """
        roles_to_check = [role] if role else list(_webhooks().keys())
        summary = {}

        for r in roles_to_check:
            wh = _webhooks().get(r)
            if not wh or not _api_key():
                continue

            t0 = time.monotonic()
            re_enrolled = 0
            orphans     = 0
            drift       = False
            error_msg   = None

            try:
                async with aiohttp.ClientSession() as session:
                    remote = await _get_webhook_detail(session, wh.webhook_id)

                remote_addrs = set(remote.get("accountAddresses") or [])
                local_addrs  = set(self._db_active_addresses(wh.webhook_id))

                missing  = local_addrs - remote_addrs   # in DB but not Helius
                orphaned = remote_addrs - local_addrs   # in Helius but not DB

                if r == CANDIDATE_ROLE:
                    # Candidate webhook: Helius is authoritative for removals.
                    # Addresses in local DB but absent from Helius were purged
                    # (corridor expired, classified, etc.) — mark them removed locally
                    # rather than re-enrolling them.
                    if missing:
                        logger.info(
                            "%s %d stale candidate enrollments removed from local DB "
                            "(absent from Helius — corridor expired or purged)",
                            _LOG_PREFIX, len(missing),
                        )
                        with self._conn() as conn:
                            for addr in missing:
                                conn.execute(
                                    "UPDATE wt_webhook_enrollments SET is_active=0, "
                                    "de_enrolled_at=? WHERE wallet_address=? AND webhook_id=?",
                                    (int(time.time()), addr, wh.webhook_id),
                                )
                    drift = False  # stale locals are not drift; never re-enroll
                else:
                    # Infra webhook: local DB is authoritative — re-enroll missing
                    drift = bool(missing)
                    if missing:
                        logger.warning(
                            "%s DRIFT on %s webhook: %d addresses missing from Helius",
                            _LOG_PREFIX, r, len(missing),
                        )
                        async with self._lock:
                            async with aiohttp.ClientSession() as session:
                                existing = await _get_webhook_detail(session, wh.webhook_id)
                                desired  = list(local_addrs)
                                await _put_webhook(session, wh.webhook_id, existing, desired)
                                self._db_mark_confirmed(desired, wh.webhook_id)
                        re_enrolled = len(missing)

                if orphaned:
                    logger.info(
                        "%s %d orphaned addresses in %s webhook (not in local DB)",
                        _LOG_PREFIX, len(orphaned), r,
                    )
                    orphans = len(orphaned)

                dur_ms = int((time.monotonic() - t0) * 1000)
                self._record_reconciliation(
                    wh.webhook_id, len(local_addrs), len(remote_addrs),
                    re_enrolled, orphans, drift, dur_ms,
                )

                summary[r] = {
                    "webhook_id":   wh.webhook_id,
                    "local":        len(local_addrs),
                    "remote":       len(remote_addrs),
                    "re_enrolled":  re_enrolled,
                    "orphans":      orphans,
                    "drift":        drift,
                    "duration_ms":  dur_ms,
                    "healthy":      not drift,
                }

                logger.info(
                    "%s Reconcile %s — local=%d remote=%d re_enrolled=%d orphans=%d %s",
                    _LOG_PREFIX, r,
                    len(local_addrs), len(remote_addrs),
                    re_enrolled, orphans,
                    "DRIFT_FIXED" if re_enrolled else "OK",
                )

            except Exception as exc:
                error_msg = str(exc)
                logger.error("%s Reconcile failed for %s: %s", _LOG_PREFIX, r, exc)
                dur_ms = int((time.monotonic() - t0) * 1000)
                self._record_reconciliation(
                    wh.webhook_id, 0, 0, 0, 0, False, dur_ms, error=error_msg,
                )
                summary[r] = {"webhook_id": wh.webhook_id, "error": error_msg, "healthy": False}

        return summary

    def _record_reconciliation(
        self,
        webhook_id: str,
        local: int,
        remote: int,
        re_enrolled: int,
        orphans: int,
        drift: bool,
        dur_ms: int,
        error: Optional[str] = None,
    ) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO wt_webhook_reconciliations
                       (webhook_id, local_count, remote_count, re_enrolled,
                        orphans_found, drift_detected, duration_ms, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (webhook_id, local, remote, re_enrolled, orphans,
                     1 if drift else 0, dur_ms, error),
                )
                conn.commit()
        except Exception:
            pass

    # ── Expiry / lifecycle ────────────────────────────────────────────────────

    async def expire_stale_candidates(self, max_age_s: int = _CANDIDATE_EXPIRY) -> int:
        """
        Remove candidates that have been enrolled longer than max_age_s
        without launching. Returns count removed.
        """
        cutoff = int(time.time()) - max_age_s
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT wallet_address, webhook_id FROM wt_webhook_enrollments
                   WHERE role=? AND is_active=1 AND state='CONFIRMED'
                   AND enrolled_at < ?""",
                (CANDIDATE_ROLE, cutoff),
            ).fetchall()

        if not rows:
            return 0

        wh = _webhooks().get(CANDIDATE_ROLE)
        if not wh or not _api_key():
            return 0

        stale = [r["wallet_address"] for r in rows]
        async with self._lock:
            async with aiohttp.ClientSession() as session:
                try:
                    existing = await _get_webhook_detail(session, wh.webhook_id)
                    current  = set(existing.get("accountAddresses") or [])
                    updated  = [a for a in current if a not in stale]
                    await _put_webhook(session, wh.webhook_id, existing, updated)
                    for addr in stale:
                        self._db_mark_removed(addr, wh.webhook_id, reason="expired")
                    logger.info(
                        "%s Expired %d stale candidates (>%dh)",
                        _LOG_PREFIX, len(stale), max_age_s // 3600,
                    )
                    return len(stale)
                except Exception as exc:
                    logger.error("%s Expiry flush failed: %s", _LOG_PREFIX, exc)
                    return 0

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Return local DB counts and last reconciliation time per webhook."""
        result = {}
        try:
            with self._conn() as conn:
                for role, wh in _webhooks().items():
                    counts = conn.execute(
                        "SELECT state, COUNT(*) AS n FROM wt_webhook_enrollments "
                        "WHERE webhook_id=? GROUP BY state",
                        (wh.webhook_id,),
                    ).fetchall()
                    last_recon = conn.execute(
                        "SELECT ran_at, re_enrolled, drift_detected, error "
                        "FROM wt_webhook_reconciliations "
                        "WHERE webhook_id=? ORDER BY ran_at DESC LIMIT 1",
                        (wh.webhook_id,),
                    ).fetchone()
                    active = conn.execute(
                        "SELECT COUNT(*) FROM wt_webhook_enrollments "
                        "WHERE webhook_id=? AND is_active=1",
                        (wh.webhook_id,),
                    ).fetchone()[0]

                    result[role] = {
                        "webhook_id":    wh.webhook_id,
                        "active":        active,
                        "by_state":      {r["state"]: r["n"] for r in counts},
                        "last_recon_at": last_recon["ran_at"] if last_recon else None,
                        "last_drift":    bool(last_recon and last_recon["drift_detected"]) if last_recon else None,
                        "last_error":    last_recon["error"] if last_recon else None,
                        "healthy":       (not last_recon or not last_recon["drift_detected"]) if last_recon else True,
                    }
        except Exception as exc:
            logger.error("%s status() error: %s", _LOG_PREFIX, exc)
        return result


# ── Background reconciliation worker ─────────────────────────────────────────

async def run_reconciliation_worker(
    db_path: str,
    interval_s: int = _RECONCILE_INTERVAL,
) -> None:
    """
    Long-running coroutine: reconciles all webhooks every interval_s seconds.
    Designed to run as an asyncio task inside the listener or Flask startup.
    """
    mgr = WebhookManager(db_path)
    logger.info("%s Reconciliation worker started (interval=%ds)", _LOG_PREFIX, interval_s)

    # Seed local DB from current Helius state on startup
    await _seed_from_helius(mgr)
    # Purge any historical candidates — only CORRIDOR_ACTIVE wallets belong here
    await _purge_stale_candidates(mgr, db_path)

    while True:
        try:
            summary = await mgr.reconcile()
            for role, s in summary.items():
                if s.get("drift"):
                    logger.warning(
                        "%s POST-RECONCILE drift on %s: re_enrolled=%d",
                        _LOG_PREFIX, role, s.get("re_enrolled", 0),
                    )
        except Exception as exc:
            logger.error("%s Reconciliation worker error: %s", _LOG_PREFIX, exc)

        await asyncio.sleep(interval_s)


async def _seed_from_helius(mgr: WebhookManager) -> None:
    """
    On startup, populate local DB from Helius detail endpoints
    so the DB reflects actual current state.
    """
    if not _api_key():
        return
    for role, wh in _webhooks().items():
        try:
            async with aiohttp.ClientSession() as session:
                detail = await _get_webhook_detail(session, wh.webhook_id)
            remote_addrs = detail.get("accountAddresses") or []
            seeded = 0
            for addr in remote_addrs:
                with mgr._conn() as conn:
                    row = conn.execute(
                        "SELECT 1 FROM wt_webhook_enrollments "
                        "WHERE wallet_address=? AND webhook_id=?",
                        (addr, wh.webhook_id),
                    ).fetchone()
                if not row:
                    mgr._db_upsert(addr, wh.webhook_id, role, state="CONFIRMED",
                                   notes="seeded_from_helius")
                    seeded += 1
            logger.info(
                "%s Seeded %s webhook from Helius: %d existing, %d newly seeded",
                _LOG_PREFIX, role, len(remote_addrs), seeded,
            )
        except Exception as exc:
            logger.warning("%s Seed failed for %s: %s", _LOG_PREFIX, role, exc)


async def _purge_stale_candidates(mgr: WebhookManager, db_path: str) -> None:
    """
    On startup, remove all candidate webhook addresses that are NOT currently
    CORRIDOR_ACTIVE. The candidate webhook should only hold wallets inside an
    active F5M window — not historical score≥85 candidates.
    """
    wh = _webhooks().get(CANDIDATE_ROLE)
    if not wh or not _api_key():
        return
    try:
        # Load current CORRIDOR_ACTIVE wallets from DB
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        active_corridors = {
            r["wallet_address"]
            for r in conn.execute(
                "SELECT wallet_address FROM wt_launch_corridors WHERE state='CORRIDOR_ACTIVE'"
            ).fetchall()
        }
        conn.close()

        async with aiohttp.ClientSession() as session:
            detail = await _get_webhook_detail(session, wh.webhook_id)
        remote_addrs = detail.get("accountAddresses") or []
        keep = [a for a in remote_addrs if a in active_corridors]
        purge_count = len(remote_addrs) - len(keep)

        if purge_count == 0:
            logger.info("%s Candidate webhook already clean (%d addresses)", _LOG_PREFIX, len(remote_addrs))
            return

        if not keep:
            # No active corridors — PUT a single known-safe placeholder so Helius
            # actually clears the stale 192 addresses. The placeholder is the
            # WATCHTOWER infra treasury address (already on the infra webhook,
            # harmless on candidate webhook — no meaningful txs will match).
            _PLACEHOLDER = "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM"
            keep = [_PLACEHOLDER]
            print(
                f"[WH_MGR] No active corridors — replacing {purge_count} stale addresses "
                f"with placeholder to clear Helius webhook.",
                flush=True,
            )
        async with mgr._lock:
            async with aiohttp.ClientSession() as session:
                await _put_webhook(session, wh.webhook_id, detail, keep)

        # Mark purged addresses as de-enrolled in local DB
        now = int(time.time())
        with mgr._conn() as conn:
            if active_corridors:
                conn.execute(
                    "UPDATE wt_webhook_enrollments SET is_active=0, de_enrolled_at=? "
                    "WHERE webhook_id=? AND is_active=1 AND wallet_address NOT IN ({})".format(
                        ",".join("?" * len(active_corridors))
                    ),
                    [now, wh.webhook_id] + list(active_corridors),
                )
            else:
                conn.execute(
                    "UPDATE wt_webhook_enrollments SET is_active=0, de_enrolled_at=? "
                    "WHERE webhook_id=? AND is_active=1",
                    (now, wh.webhook_id),
                )

        msg = (
            f"[WH_MGR] Purged {purge_count} stale candidates from webhook "
            f"(kept {len(keep)} active corridor wallets)"
        )
        logger.info(msg)
        print(msg, flush=True)
    except Exception as exc:
        logger.warning("%s Candidate purge failed: %s", _LOG_PREFIX, exc)


# ── Sharding (future scaling) ─────────────────────────────────────────────────

class ShardedWebhookManager(WebhookManager):
    """
    Extends WebhookManager to automatically create additional candidate
    webhooks when the primary approaches _SHARD_THRESHOLD addresses.

    Shard IDs are stored in wt_webhook_shards table (created on demand).
    Currently a stub — activates when needed.
    """

    async def enroll(
        self,
        address: str,
        role: str = CANDIDATE_ROLE,
        notes: str = "",
        immediate: bool = False,
    ) -> bool:
        if role == CANDIDATE_ROLE:
            wh = _webhooks().get(CANDIDATE_ROLE)
            if wh:
                active_count = len(self._db_active_addresses(wh.webhook_id))
                if active_count >= _SHARD_THRESHOLD:
                    logger.warning(
                        "%s Shard threshold reached (%d >= %d) — "
                        "manual intervention needed to create new candidate webhook",
                        _LOG_PREFIX, active_count, _SHARD_THRESHOLD,
                    )
                    # Future: auto-create shard and distribute enrollments
        return await super().enroll(address, role, notes, immediate)


# ── Public convenience functions ──────────────────────────────────────────────

_default_manager: Optional[WebhookManager] = None


def get_manager(db_path: str) -> WebhookManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = WebhookManager(db_path)
    return _default_manager


async def enroll_candidate(address: str, db_path: str, notes: str = "") -> bool:
    return await get_manager(db_path).enroll(address, CANDIDATE_ROLE, notes=notes)


async def enroll_infra(address: str, db_path: str, notes: str = "") -> bool:
    return await get_manager(db_path).enroll(address, INFRA_ROLE, notes=notes)


async def remove_candidate(address: str, db_path: str, reason: str = "") -> None:
    await get_manager(db_path).remove(
        address, CANDIDATE_ROLE, reason=reason, cooldown_s=_CANDIDATE_COOLDOWN
    )
