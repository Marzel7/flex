"""Canonical terminal outcomes for token attribution.

Legacy queue states are inputs.  This module materialises the one typed answer
consumed by analysts and by the future unknown-operator pipeline.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any

from src.core.database_write_service import execute_script
from src.ops.operator_writer import OperatorWriter
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


CANONICAL_OPERATOR_REACHED = "CANONICAL_OPERATOR_REACHED"
KNOWN_MULTI_TOKEN_CREATOR = "KNOWN_MULTI_TOKEN_CREATOR"
KNOWN_CEX_REACHED = "KNOWN_CEX_REACHED"
KNOWN_BRIDGE_REACHED = "KNOWN_BRIDGE_REACHED"
KNOWN_RELAY_REACHED = "KNOWN_RELAY_REACHED"
UNKNOWN_INFRASTRUCTURE = "UNKNOWN_INFRASTRUCTURE"
LINEAGE_GAP = "LINEAGE_GAP"
AMBIGUOUS_BRANCH = "AMBIGUOUS_BRANCH"
MAX_DEPTH = "MAX_DEPTH"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

OUTCOME_TYPES = (
    CANONICAL_OPERATOR_REACHED,
    KNOWN_MULTI_TOKEN_CREATOR,
    KNOWN_CEX_REACHED,
    KNOWN_BRIDGE_REACHED,
    KNOWN_RELAY_REACHED,
    UNKNOWN_INFRASTRUCTURE,
    LINEAGE_GAP,
    AMBIGUOUS_BRANCH,
    MAX_DEPTH,
    INSUFFICIENT_EVIDENCE,
)

MIN_LAUNCHER_HISTORY = 5
MIN_LAUNCHER_OBSERVATION_SECONDS = 7 * 86400
FRESH_PROVISIONING_SECONDS = 48 * 3600


DDL = f"""
CREATE TABLE IF NOT EXISTS wt_attribution_outcomes (
    mint                          TEXT PRIMARY KEY,
    outcome_type                  TEXT NOT NULL CHECK(outcome_type IN ({','.join(repr(x) for x in OUTCOME_TYPES)})),
    stop_reason                   TEXT NOT NULL,
    terminal_entity               TEXT,
    terminal_entity_type          TEXT NOT NULL,
    confidence                    TEXT NOT NULL,
    evidence_json                 TEXT NOT NULL,
    operator_id                   TEXT,
    should_seed_emerging_operator INTEGER NOT NULL CHECK(should_seed_emerging_operator IN (0,1)),
    should_retry                  INTEGER NOT NULL CHECK(should_retry IN (0,1)),
    completed_at                  INTEGER NOT NULL,
    source_queue_updated_at       INTEGER,
    materialized_at               INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wao_type_time
    ON wt_attribution_outcomes(outcome_type, completed_at DESC);
CREATE INDEX IF NOT EXISTS ix_wao_terminal
    ON wt_attribution_outcomes(terminal_entity, outcome_type);
CREATE INDEX IF NOT EXISTS ix_wao_completed_at
    ON wt_attribution_outcomes(completed_at DESC);

CREATE TABLE IF NOT EXISTS wt_unknown_infrastructure_registry (
    terminal_entity      TEXT PRIMARY KEY,
    terminal_entity_type TEXT NOT NULL,
    first_source_mint    TEXT NOT NULL,
    latest_source_mint   TEXT NOT NULL,
    observation_count    INTEGER NOT NULL DEFAULT 1,
    confidence           TEXT NOT NULL,
    evidence_json        TEXT NOT NULL,
    eligible             INTEGER NOT NULL DEFAULT 1 CHECK(eligible IN (0,1)),
    first_seen_at        INTEGER NOT NULL,
    last_seen_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wuir_eligible
    ON wt_unknown_infrastructure_registry(eligible, last_seen_at DESC);
"""


@dataclass(frozen=True)
class AttributionOutcome:
    outcome_type: str
    stop_reason: str
    terminal_entity: str | None
    terminal_entity_type: str
    confidence: str
    evidence: dict[str, Any]
    operator_id: str | None
    should_seed_emerging_operator: bool
    should_retry: bool
    completed_at: int


def ensure_schema(conn) -> None:
    # X77.5 fix: execute_script() is DDL-only and deliberately never commits
    # (see database_write_service.execute_script's own docstring) -- the
    # caller owns the transaction boundary. This function previously never
    # called commit() at all, so a fully-successful call still left the
    # write lease held on TrackedConnection's thread-local guard
    # (_acquire_write_lane fires on the first CREATE/ALTER inside the
    # script and is only released by commit()/rollback()/close()). Any
    # later write on the same thread (walkback_worker.run_loop() calls this
    # immediately after _ensure_walkback_schema, on the same startup
    # connection, before ever reaching the main loop) then self-nested with
    # NestedDatabaseWriteError -- observed live crash-looping walkback_worker
    # during the X77.5 soak, confirmed reproduced in isolation (lease
    # remained held after a fully successful ensure_schema() call).
    execute_script(conn, DDL)
    conn.commit()


def open_core_readonly(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _columns(conn, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _one(conn, sql: str, args: tuple = ()) -> dict[str, Any] | None:
    try:
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def _normalize_timestamp(value: Any) -> int | None:
    """X27.9 Phase 4 — deterministic UTC normalization for a single launch
    timestamp that may be an int/float epoch, a numeric string, or an
    ISO-8601 string ("2026-07-16T16:53:35Z"). Returns None (never fabricates
    "now" or 0) for anything that cannot be interpreted unambiguously as a
    real timestamp — an invalid value must be ignored, not silently coerced
    (a bare CAST(... AS INTEGER) on an ISO string truncates it to its
    leading digits, e.g. "2026-07-16..." -> 2026, a wrong-but-plausible-
    looking epoch second that would corrupt MIN/MAX)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.lstrip("-").isdigit():
            return int(s)
        try:
            import datetime
            s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt = datetime.datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return int(dt.timestamp())
        except (ValueError, TypeError):
            return None
    return None


def _creator_launch_history_span(
    core_conn, creator: str, measured_conditions: list[tuple[str, tuple, str]],
    time_columns: list[str],
) -> tuple[int, int | None, int | None]:
    """X27.9 Phase 1/3 — the canonical creator activity span: derived
    EXCLUSIVELY from the creator's actual launch records in token_analysis,
    never from creator_funders.first_detected_at (a funder-discovery
    timestamp, not an activity-span measurement — X27.8 found a creator
    with a 3-month launch history measured as 6 seconds because all of its
    funder rows were backfilled in the same 6-second pass).

    Returns (valid_timestamp_count, first_seen, last_seen). Per Phase 4,
    fewer than two valid timestamps must fail the observation-window gate
    honestly rather than fabricate a span from a single point or zero
    points — callers must check the count, not just truthiness of the
    returned timestamps.

    X27.9.1 Phase 1 — no longer gated by launch_count: the cost driver in
    the original implementation was SELECT * (every column, every row),
    not the row count itself. Selecting only the timestamp columns keeps
    this a single linear pass with memory proportional to row count (a few
    ints per row) regardless of creator size — measured against the
    platform's largest creator (~16,000 launches) without issue. No O(N^2)
    behaviour: exactly one query and one pass per creator."""
    time_col_list = ",".join(time_columns) if time_columns else "NULL"
    all_ts: list[int] = []
    for where, args, include in measured_conditions:
        rows = core_conn.execute(
            f"SELECT {include} AS ok, {time_col_list} FROM token_analysis WHERE {where}", args,
        ).fetchall()
        for row in rows:
            d = dict(row)
            if not d.get("ok"):
                continue
            for col in time_columns:
                if col in d and d[col] is not None:
                    ts = _normalize_timestamp(d[col])
                    if ts is not None:
                        all_ts.append(ts)
                    break
    if len(all_ts) < 2:
        return len(all_ts), None, None
    return len(all_ts), min(all_ts), max(all_ts)


def evaluate_launcher_profile(
    ops_conn,
    core_conn,
    creator: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Require a stable, non-canonical launcher profile; repetition alone is insufficient."""
    now = int(now or time.time())
    profile = {
        "creator": creator,
        "launch_count": 0,
        "observation_seconds": 0,
        "canonical_operator_linked": False,
        "fresh_provisioning_evidence": False,
        "material_infrastructure_change": False,
        "minimum_launch_count": MIN_LAUNCHER_HISTORY,
        "minimum_observation_seconds": MIN_LAUNCHER_OBSERVATION_SECONDS,
        "established": False,
    }
    if not creator or core_conn is None or not _table(core_conn, "token_analysis"):
        return profile
    columns = _columns(core_conn, "token_analysis")
    creator_columns = [c for c in ("pf_ws_creator", "earliest_tx_creator", "creator") if c in columns]
    time_columns = [c for c in ("first_observed_at", "analyzed_at", "created_at", "block_time") if c in columns]
    if not creator_columns:
        return profile
    creator_expr = "COALESCE(" + ",".join(creator_columns) + ")" if len(creator_columns) > 1 else creator_columns[0]
    if "pf_ws_creator" in creator_columns and "earliest_tx_creator" in creator_columns:
        # pf_ws_creator is the authoritative launch creator. earliest_tx_creator
        # is a recovery hint and may be a common transaction authority shared by
        # thousands of unrelated launches, so it cannot establish a launcher.
        conditions = (("pf_ws_creator=?", (creator,), "1"),)
    else:
        conditions = ((f"{creator_expr}=?", (creator,), "1"),)
    launch_count = 0
    measured_conditions = []
    for where, args, include in conditions:
        row = core_conn.execute(
            f"SELECT COALESCE(SUM({include}),0) launch_count FROM token_analysis WHERE {where}",
            args,
        ).fetchone()
        measured_conditions.append((where, args, include))
        if row:
            launch_count += int(row["launch_count"] or 0)
    profile["launch_count"] = launch_count

    # X27.9 — funders is retained ONLY for historical_funder_count and
    # material_infrastructure_change (its recency signal is still valid for
    # "did new funding infrastructure just appear"). It must never supply
    # the observation-window span itself: creator_funders.first_detected_at
    # measures when WATCHTOWER discovered the funder, not when the creator
    # was actually active — X27.8 found a creator with a 3-month launch
    # history whose 5 funder rows were all backfilled in the same 6-second
    # pass, producing observation_seconds=6 instead of the true ~893,000.
    funders = None
    if _table(core_conn, "creator_funders"):
        observed_expr = (
            "CASE WHEN typeof(first_detected_at) IN ('integer','real') "
            "THEN CAST(first_detected_at AS INTEGER) ELSE unixepoch(first_detected_at) END"
        )
        funders = core_conn.execute(
            f"SELECT COUNT(DISTINCT funder_address) n,MIN({observed_expr}) first_seen,"
            f"MAX({observed_expr}) latest FROM creator_funders WHERE creator_address=?",
            (creator,),
        ).fetchone()
        profile["historical_funder_count"] = int(funders["n"] or 0) if funders else 0
        profile["latest_funder_observed_at"] = int(funders["latest"] or 0) if funders else None

    # Canonical creator activity span — Phase 1/3: derived exclusively from
    # the creator's own launch records in token_analysis, timestamp-
    # normalized (Phase 4) so mixed epoch/ISO-8601 values never corrupt
    # MIN/MAX via lexical truncation. X27.9.1 Phase 1 — no launch_count
    # ceiling: qualification must not depend on creator size (a creator
    # with 1001 launches must not be less classifiable than one with 999).
    valid_ts_count = first_seen = last_seen = None
    if launch_count:
        valid_ts_count, first_seen, last_seen = _creator_launch_history_span(
            core_conn, creator, measured_conditions, time_columns)
    if first_seen is not None and last_seen is not None:
        profile["first_seen"] = first_seen
        profile["last_seen"] = last_seen
        profile["observation_seconds"] = max(0, int(last_seen - first_seen))
    else:
        # Phase 4 — fewer than two valid historical launch timestamps must
        # fail the observation-window gate honestly, never fabricate a span.
        profile["first_seen"] = None
        profile["last_seen"] = None
        profile["observation_seconds"] = 0
    profile["valid_launch_timestamp_count"] = valid_ts_count or 0

    if _table(ops_conn, "operator_entities"):
        profile["canonical_operator_linked"] = bool(ops_conn.execute(
            "SELECT 1 FROM operator_entities WHERE entity_address=? LIMIT 1", (creator,)
        ).fetchone())

    freshness_cutoff = now - FRESH_PROVISIONING_SECONDS
    recent_checks = (
        ("wt_wrap_close_candidates", "creator", "COALESCE(funded_at,detected_at)"),
        ("wt_creator_birth_launch", "creator", "COALESCE(funded_at,measured_at)"),
    )
    for table, field, timestamp in recent_checks:
        if _table(ops_conn, table) and ops_conn.execute(
            f"SELECT 1 FROM {table} WHERE {field}=? AND {timestamp}>=? LIMIT 1",
            (creator, freshness_cutoff),
        ).fetchone():
            profile["fresh_provisioning_evidence"] = True
            break
    # Candidate history is indexed by candidate_wallet but not by observation
    # time. Treat any candidate record conservatively as provisioning evidence;
    # this prevents false exclusion without scanning millions of rows to prove
    # freshness. It may retain an old creator for walkback, which is safe.
    if not profile["fresh_provisioning_evidence"] and _table(ops_conn, "wt_candidate_websocket_watches"):
        profile["fresh_provisioning_evidence"] = bool(ops_conn.execute(
            "SELECT 1 FROM wt_candidate_websocket_watches WHERE candidate_wallet=? LIMIT 1",
            (creator,),
        ).fetchone())

    profile["material_infrastructure_change"] = bool(
        funders and (funders["n"] or 0) > 1 and (funders["latest"] or 0) >= freshness_cutoff
    )

    profile["established"] = bool(
        profile["launch_count"] >= MIN_LAUNCHER_HISTORY
        and profile["observation_seconds"] >= MIN_LAUNCHER_OBSERVATION_SECONDS
        and not profile["canonical_operator_linked"]
        and not profile["fresh_provisioning_evidence"]
        and not profile["material_infrastructure_change"]
    )
    return profile


def _boundary(core_conn, creator: str | None, terminal: str | None) -> dict[str, Any] | None:
    """Resolve only reviewed/static attribution boundaries."""
    from src.utils.infra_mapping import CEX_ACCOUNTS, INFRASTRUCTURE_ACCOUNTS

    candidates = [x for x in (terminal,) if x]
    for address in candidates:
        if address in CEX_ACCOUNTS:
            item = CEX_ACCOUNTS[address]
            return {"type": KNOWN_CEX_REACHED, "address": address, "entity_type": "CEX",
                    "name": item.get("exchange") or item.get("name") or "Known CEX",
                    "source": "CEX_ACCOUNTS"}
        item = INFRASTRUCTURE_ACCOUNTS.get(address)
        if item:
            category = item.get("category") or "infrastructure"
            # The canonical vocabulary has a specific bridge boundary and a
            # general known-intermediary/relay boundary. Reviewed platforms,
            # custody, protocol and automation addresses must never leak into
            # UNKNOWN_INFRASTRUCTURE merely because their legacy category is
            # more specific than the outcome vocabulary.
            kind = KNOWN_BRIDGE_REACHED if category == "bridge" else KNOWN_RELAY_REACHED
            return {"type": kind, "address": address, "entity_type": category.upper(),
                    "name": item.get("name") or category.title(),
                    "source": "INFRASTRUCTURE_ACCOUNTS"}
        if core_conn is not None and _table(core_conn, "address_labels"):
            label = _one(
                core_conn,
                "SELECT label_name,category,tags FROM address_labels WHERE address=?",
                (address,),
            )
            category = str((label or {}).get("category") or "").upper()
            tags = str((label or {}).get("tags") or "").lower()
            if category == "BRIDGE" or "bridge" in tags:
                return {"type": KNOWN_BRIDGE_REACHED, "address": address, "entity_type": "BRIDGE",
                        "name": label.get("label_name") or "Known bridge", "source": "address_labels"}
            if "relay" in tags:
                return {"type": KNOWN_RELAY_REACHED, "address": address, "entity_type": "RELAY",
                        "name": label.get("label_name") or "Known relay", "source": "address_labels"}
    # This potentially large historical lookup is used only when classification
    # already identified a CEX boundary; ordinary terminal outcomes never scan it.
    if core_conn is not None and creator and _table(core_conn, "creator_funders"):
        row = core_conn.execute(
            "SELECT funder_address,cex_exchange,cex_type FROM creator_funders "
            "WHERE creator_address=? AND is_cex=1 ORDER BY first_detected_at ASC LIMIT 1",
            (creator,),
        ).fetchone()
        if row:
            return {
                "type": KNOWN_CEX_REACHED,
                "address": row["funder_address"],
                "entity_type": "CEX",
                "name": row["cex_exchange"] or row["cex_type"] or "Known CEX",
                "source": "creator_funders.is_cex",
            }
    return None


def _known_unknown_infrastructure(conn, address: str | None) -> dict[str, Any] | None:
    if not address:
        return None
    if _table(conn, "wt_treasury_review"):
        row = _one(conn, "SELECT * FROM wt_treasury_review WHERE treasury=?", (address,))
        if row and (
            row.get("has_walkback_evidence")
            or row.get("detected_via") == "walkback_hop2"
            or (row.get("distinct_subprovs") or 0) >= 2
        ):
            return {"source": "wt_treasury_review", "record": row}
    if _table(conn, "wt_discovered_subprovs"):
        row = _one(conn, "SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (address,))
        if row and ((row.get("creator_count") or 0) >= 2 or (row.get("wrap_close_count") or 0) >= 2):
            return {"source": "wt_discovered_subprovs", "record": row}
    return None


def derive_outcome(
    conn,
    mint: str,
    *,
    core_conn=None,
    now: int | None = None,
) -> AttributionOutcome | None:
    now = int(now or time.time())
    queue = _one(conn, "SELECT * FROM wt_walkback_queue WHERE mint=?", (mint,))
    if not queue or str(queue.get("status") or "").lower() not in {"complete", "skipped", "failed"}:
        return None
    completed_at = int(queue.get("completed_at") or queue.get("updated_at") or now)
    launch = _one(conn, "SELECT * FROM wt_watchtower_launches WHERE mint=?", (mint,)) or {}
    attribution = _one(conn, "SELECT * FROM watchtower_token_attribution WHERE mint=?", (mint,)) or {}
    creator = queue.get("creator") or launch.get("creator_wallet") or attribution.get("creator")
    subprovs = {x for x in (
        queue.get("subprov"), launch.get("subprov_wallet"), attribution.get("matched_subprov")
    ) if x}
    treasuries = {x for x in (
        queue.get("treasury"), launch.get("treasury_wallet"), attribution.get("matched_treasury")
    ) if x}
    terminal = queue.get("treasury") or queue.get("funder_wallet") or queue.get("subprov") or creator
    evidence: dict[str, Any] = {
        "legacy_queue_outcome": queue.get("intelligence_outcome"),
        "walkback_class": queue.get("walkback_class"),
        "attribution_source": queue.get("attribution_source"),
        "creator": creator,
        "subprovisioners": sorted(subprovs),
        "treasuries": sorted(treasuries),
        "funder_wallet": queue.get("funder_wallet"),
        "retry_condition": None,
    }

    operator_ids: set[str] = set()
    if treasuries and _table(conn, "operator_entities"):
        placeholders = ",".join("?" for _ in treasuries)
        operator_ids = {
            row[0] for row in conn.execute(
                f"SELECT DISTINCT oe.operator_id FROM operator_entities oe "
                f"JOIN operators o ON o.operator_id=oe.operator_id "
                f"WHERE oe.entity_address IN ({placeholders}) "
                "AND upper(COALESCE(o.status,'CONFIRMED')) NOT IN ('REJECTED','RETIRED')",
                tuple(sorted(treasuries)),
            )
        }
    if len(operator_ids) == 1:
        operator_id = next(iter(operator_ids))
        name_row = _one(conn, "SELECT display_name FROM operators WHERE operator_id=?", (operator_id,)) or {}
        name = name_row.get("display_name") or operator_id
        evidence["branch_convergence"] = len(treasuries) > 1
        return AttributionOutcome(
            CANONICAL_OPERATOR_REACHED,
            f"Attribution complete. Canonical operator: {name}.",
            operator_id, "CANONICAL_OPERATOR", "HIGH", evidence,
            operator_id, False, False, completed_at,
        )
    if len(operator_ids) > 1:
        evidence["operator_ids"] = sorted(operator_ids)
        return AttributionOutcome(
            AMBIGUOUS_BRANCH,
            "Walkback stopped because persisted branches resolve to different canonical operators.",
            terminal, "INFRASTRUCTURE", "LOW", evidence, None, False, True, completed_at,
        )

    boundary = _boundary(
        core_conn,
        creator if queue.get("attribution_source") == "cex_funded" else None,
        terminal,
    )
    if boundary:
        evidence["boundary"] = boundary
        wording = {
            KNOWN_CEX_REACHED: "Known CEX wallet",
            KNOWN_BRIDGE_REACHED: "Known bridge wallet",
            KNOWN_RELAY_REACHED: "Known infrastructure boundary",
        }[boundary["type"]]
        return AttributionOutcome(
            boundary["type"], f"Attribution boundary reached. {wording}: {boundary['name']}.",
            boundary["address"], boundary["entity_type"], "HIGH", evidence,
            None, False, False, completed_at,
        )

    if creator and queue.get("attribution_source") in {"serial_deployer", "known_multi_token_creator"}:
        profile = evaluate_launcher_profile(conn, core_conn, creator, now=now)
        evidence["launcher_profile"] = profile
        if profile["established"]:
            return AttributionOutcome(
                KNOWN_MULTI_TOKEN_CREATOR,
                f"Walkback stopped. Known multi-token creator with {profile['launch_count']} historical launches.",
                creator, "CREATOR", "HIGH", evidence, None, False, False, completed_at,
            )

    legacy = str(queue.get("intelligence_outcome") or "")
    if legacy in {"GRAPH_QUALITY_ISSUE", "AMBIGUOUS_BRANCH"} or queue.get("walkback_class") in {
        "OP_GRAPH_ROLE_MISMATCH", "SELF_ROOTED_OPERATION"
    }:
        return AttributionOutcome(
            AMBIGUOUS_BRANCH,
            "Walkback stopped because the persisted attribution branches are inconsistent.",
            terminal, "INFRASTRUCTURE", "LOW", evidence, None, False, True, completed_at,
        )
    if legacy == "MAX_DEPTH" or "MAX_DEPTH" in str(queue.get("last_error") or ""):
        return AttributionOutcome(
            MAX_DEPTH, "Walkback stopped at the configured maximum lineage depth.",
            terminal, "INFRASTRUCTURE", "LOW", evidence, None, False, True, completed_at,
        )

    unknown = _known_unknown_infrastructure(conn, terminal)
    if unknown:
        evidence["unknown_infrastructure"] = unknown
        return AttributionOutcome(
            UNKNOWN_INFRASTRUCTURE,
            "Unknown infrastructure identified. Eligible for emerging-operator monitoring.",
            terminal, "INFRASTRUCTURE", "MEDIUM", evidence, None, True, False, completed_at,
        )
    if legacy == "LINEAGE_GAP" or treasuries or subprovs:
        evidence["retry_condition"] = "NEW_EVIDENCE_ONLY"
        return AttributionOutcome(
            LINEAGE_GAP,
            "Walkback stopped at a lineage gap. Retry only when new evidence arrives.",
            terminal, "INFRASTRUCTURE" if terminal else "UNKNOWN", "LOW", evidence,
            None, False, True, completed_at,
        )
    return AttributionOutcome(
        INSUFFICIENT_EVIDENCE,
        "Walkback stopped because the persisted evidence is insufficient for attribution.",
        terminal, "CREATOR" if terminal == creator and creator else "UNKNOWN", "INSUFFICIENT",
        evidence, None, False, True, completed_at,
    )


def persist_outcome(conn, mint: str, outcome: AttributionOutcome, *, source_updated_at: int | None = None) -> dict[str, Any]:
    # Schema creation is an explicit process-start/backfill concern. Terminal
    # writes must remain ordinary DML inside their owning transaction.
    evidence_json = json.dumps(outcome.evidence, sort_keys=True, separators=(",", ":"), default=str)
    now = int(time.time())
    previous = _one(
        conn, "SELECT outcome_type,terminal_entity FROM wt_attribution_outcomes WHERE mint=?", (mint,)
    )
    conn.execute(
        "INSERT INTO wt_attribution_outcomes "
        "(mint,outcome_type,stop_reason,terminal_entity,terminal_entity_type,confidence,evidence_json,"
        " operator_id,should_seed_emerging_operator,should_retry,completed_at,source_queue_updated_at,materialized_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET "
        "outcome_type=excluded.outcome_type,stop_reason=excluded.stop_reason,"
        "terminal_entity=excluded.terminal_entity,terminal_entity_type=excluded.terminal_entity_type,"
        "confidence=excluded.confidence,evidence_json=excluded.evidence_json,operator_id=excluded.operator_id,"
        "should_seed_emerging_operator=excluded.should_seed_emerging_operator,should_retry=excluded.should_retry,"
        "completed_at=excluded.completed_at,source_queue_updated_at=excluded.source_queue_updated_at,"
        "materialized_at=excluded.materialized_at",
        (
            mint, outcome.outcome_type, outcome.stop_reason, outcome.terminal_entity,
            outcome.terminal_entity_type, outcome.confidence, evidence_json, outcome.operator_id,
            int(outcome.should_seed_emerging_operator), int(outcome.should_retry), outcome.completed_at,
            source_updated_at, now,
        ),
    )
    if outcome.outcome_type == UNKNOWN_INFRASTRUCTURE and outcome.terminal_entity:
        conn.execute(
            "INSERT INTO wt_unknown_infrastructure_registry "
            "(terminal_entity,terminal_entity_type,first_source_mint,latest_source_mint,observation_count,"
            " confidence,evidence_json,eligible,first_seen_at,last_seen_at) VALUES (?,?,?,?,1,?,?,1,?,?) "
            "ON CONFLICT(terminal_entity) DO UPDATE SET latest_source_mint=excluded.latest_source_mint,"
            "observation_count=excluded.observation_count,"
            "confidence=excluded.confidence,evidence_json=excluded.evidence_json,eligible=1,last_seen_at=excluded.last_seen_at",
            (
                outcome.terminal_entity, outcome.terminal_entity_type, mint, mint,
                outcome.confidence, evidence_json, outcome.completed_at, outcome.completed_at,
            ),
        )
    refresh_entities = {x for x in (
        outcome.terminal_entity if outcome.outcome_type == UNKNOWN_INFRASTRUCTURE else None,
        (previous or {}).get("terminal_entity") if (previous or {}).get("outcome_type") == UNKNOWN_INFRASTRUCTURE else None,
    ) if x}
    for terminal_entity in refresh_entities:
        count = conn.execute(
            "SELECT COUNT(*) FROM wt_attribution_outcomes "
            "WHERE terminal_entity=? AND outcome_type='UNKNOWN_INFRASTRUCTURE'",
            (terminal_entity,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE wt_unknown_infrastructure_registry SET observation_count=?,eligible=? "
            "WHERE terminal_entity=?",
            (count, int(count > 0), terminal_entity),
        )
    return {"mint": mint, **asdict(outcome)}


def materialize_outcome(conn, mint: str, *, core_conn=None, core_db_path: str | None = None) -> dict[str, Any] | None:
    own_core = None
    if core_conn is None:
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = core_db_path or os.environ.get("DB_PATH", os.path.join(repo, "database", "flex_complete_database.db"))
        if os.path.exists(path):
            own_core = open_core_readonly(path)
            core_conn = own_core
    try:
        outcome = derive_outcome(conn, mint, core_conn=core_conn)
        if outcome is None:
            return None
        queue = _one(conn, "SELECT updated_at FROM wt_walkback_queue WHERE mint=?", (mint,)) or {}
        return persist_outcome(conn, mint, outcome, source_updated_at=queue.get("updated_at"))
    finally:
        if own_core is not None:
            own_core.close()


def backfill_outcomes(conn, *, core_conn=None) -> dict[str, Any]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT mint FROM wt_walkback_queue WHERE lower(status) IN ('complete','skipped','failed') ORDER BY enqueued_at"
    ).fetchall()
    counts: dict[str, int] = {}
    materialized = 0
    for row in rows:
        result = materialize_outcome(conn, row["mint"], core_conn=core_conn)
        if not result:
            continue
        materialized += 1
        key = result["outcome_type"]
        counts[key] = counts.get(key, 0) + 1
    return {"total_terminal_rows": len(rows), "materialized": materialized, "by_outcome": counts}


def emerging_operator_seeds(conn) -> list[dict[str, Any]]:
    """The sole X20 intake contract: current UNKNOWN_INFRASTRUCTURE only."""
    return [dict(row) for row in conn.execute(
        "SELECT r.terminal_entity,r.terminal_entity_type,r.first_source_mint,"
        "r.latest_source_mint,r.observation_count,r.confidence,r.first_seen_at,r.last_seen_at "
        "FROM wt_unknown_infrastructure_registry r WHERE r.eligible=1 AND EXISTS ("
        " SELECT 1 FROM wt_attribution_outcomes o WHERE o.terminal_entity=r.terminal_entity "
        " AND o.outcome_type='UNKNOWN_INFRASTRUCTURE' AND o.should_seed_emerging_operator=1"
        ") ORDER BY r.last_seen_at DESC"
    )]


def initialize_schema(db_path: str, *, write_service: Any = None) -> None:
    writer = OperatorWriter(db_path, write_service=write_service)
    writer.transaction("attribution-outcome-schema", lambda conn: ensure_schema(conn))
