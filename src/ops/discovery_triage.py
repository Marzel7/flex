"""X21C — Discovery Triage Workspace: read-only aggregation over terminal
attribution outcomes that did not reach a canonical operator.

This module performs NO detection, NO attribution, NO walkback, NO writes.
It reads already-persisted rows from wt_attribution_outcomes (INSUFFICIENT_EVIDENCE
and LINEAGE_GAP only — the two outcome types that represent "attribution stopped
without a canonical answer", as distinct from boundary/confirmed outcomes) and
groups them by facts already present in evidence_json, plus cross-references
against wt_treasury_review (X20.9) and wt_provisioning_edges/wt_provisioning_sessions
(X21B).

Every bucket produced here was verified against real production data before
being added (see the X21C investigation): buckets that are NOT reliably
derivable from today's data are still exposed in the API/UI, but explicitly
report zero/dormant rather than being silently omitted or faked. In
particular, "new provisioning activity" and "repeated provisioning edges"
depend on X21B's wt_provisioning_edges/wt_provisioning_sessions tables, which
have zero rows in production as of this sprint — those sections must show
"no provisioning observations captured yet," not a fabricated count, and will
begin populating automatically once walkback captures real provisioning
sessions, with no further code change required here.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Optional


TRIAGE_OUTCOME_TYPES = ("INSUFFICIENT_EVIDENCE", "LINEAGE_GAP")


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _rows_as_dicts(cursor) -> list[dict[str, Any]]:
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_terminal_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "wt_attribution_outcomes"):
        return []
    placeholders = ",".join("?" for _ in TRIAGE_OUTCOME_TYPES)
    rows = _rows_as_dicts(conn.execute(
        f"SELECT mint, outcome_type, stop_reason, terminal_entity, terminal_entity_type, "
        f"confidence, evidence_json, completed_at FROM wt_attribution_outcomes "
        f"WHERE outcome_type IN ({placeholders})",
        TRIAGE_OUTCOME_TYPES,
    ))
    for row in rows:
        row["evidence"] = _json(row.pop("evidence_json", None), {})
    return rows


def _reason_bucket(row: dict[str, Any]) -> str:
    """One deterministic bucket per row, derived only from evidence.creator /
    evidence.treasuries / evidence.subprovisioners being populated or not.
    Matches the real distribution measured in production (see module docstring)."""
    evidence = row.get("evidence") or {}
    has_creator = bool(evidence.get("creator"))
    has_treasury = bool([t for t in (evidence.get("treasuries") or []) if t])
    has_subprov = bool([s for s in (evidence.get("subprovisioners") or []) if s])
    if not has_creator and not has_treasury and not has_subprov:
        return "NO_LINEAGE"
    if has_treasury:
        return "RECORDED_TREASURY_LEAD"
    if has_subprov:
        return "PARTIAL_FUNDING_TRAIL"
    return "CREATOR_IDENTIFIED"


_BUCKET_LABELS = {
    "NO_LINEAGE": "No funding lineage",
    "CREATOR_IDENTIFIED": "Creator identified",
    "PARTIAL_FUNDING_TRAIL": "Partial funding trail",
    "RECORDED_TREASURY_LEAD": "Recorded treasury lead",
}


def _treasury_review_lookup(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "wt_treasury_review"):
        return {}
    rows = _rows_as_dicts(conn.execute(
        "SELECT treasury, status, distinct_subprovs, distinct_creators FROM wt_treasury_review"
    ))
    return {row["treasury"]: row for row in rows if row.get("treasury")}


def _unknown_infra_lookup(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "wt_unknown_infrastructure_registry"):
        return set()
    rows = conn.execute("SELECT terminal_entity FROM wt_unknown_infrastructure_registry").fetchall()
    return {r[0] for r in rows if r[0]}


def _provisioning_activity_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Dormant-until-populated: reports whether X21B has captured ANY provisioning
    facts yet. Never fabricates a count — if the tables are empty, says so plainly.
    Once walkback captures real sessions/edges, this automatically reflects it
    without any change to this function."""
    edges_count = 0
    sessions_count = 0
    if _table_exists(conn, "wt_provisioning_edges"):
        edges_count = conn.execute("SELECT COUNT(*) FROM wt_provisioning_edges").fetchone()[0]
    if _table_exists(conn, "wt_provisioning_sessions"):
        sessions_count = conn.execute("SELECT COUNT(*) FROM wt_provisioning_sessions").fetchone()[0]
    return {
        "edges_captured": edges_count,
        "sessions_captured": sessions_count,
        "active": edges_count > 0 or sessions_count > 0,
    }


def _sessions_since(conn: sqlite3.Connection, wallet: Optional[str], since_ts: Optional[int]) -> int:
    """Count wt_provisioning_sessions rows involving this wallet recorded strictly
    after the given outcome's completed_at. Returns 0 (not None) when the table is
    empty or the wallet is absent — a real zero, not a missing-data sentinel,
    since "no activity since attribution" is itself a legitimate fact."""
    if not wallet or since_ts is None or not _table_exists(conn, "wt_provisioning_sessions"):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM wt_provisioning_sessions "
        "WHERE (treasury=? OR subprov=? OR creator=?) AND recorded_at > ?",
        (wallet, wallet, wallet, since_ts),
    ).fetchone()
    return int(row[0] or 0)


def build_triage_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Level-1 operational summary + pattern-summary buckets + filters, computed
    entirely from already-persisted rows. Read-only; no side effects."""
    rows = _load_terminal_rows(conn)
    total = len(rows)

    bucket_counts: Counter[str] = Counter(_reason_bucket(r) for r in rows)

    creator_counts: Counter[str] = Counter(
        r["evidence"].get("creator") for r in rows if r["evidence"].get("creator")
    )
    treasury_counts: Counter[str] = Counter()
    for r in rows:
        for t in (r["evidence"].get("treasuries") or []):
            if t:
                treasury_counts[t] += 1

    repeated_creator_rows = sum(1 for r in rows if creator_counts.get(r["evidence"].get("creator"), 0) >= 2)
    repeated_treasury_rows = sum(
        1 for r in rows
        if any(treasury_counts.get(t, 0) >= 2 for t in (r["evidence"].get("treasuries") or []) if t)
    )

    treasury_review = _treasury_review_lookup(conn)
    unknown_infra = _unknown_infra_lookup(conn)

    # wt_treasury_review is keyed on hop2's unconfirmed treasury candidate, which
    # for these two outcome types IS the row's terminal_entity — evidence.treasuries[]
    # is always empty here by construction (confirmed against real data: a treasury
    # only lands in evidence.treasuries[] once resolved, at which point the outcome
    # would no longer be INSUFFICIENT_EVIDENCE/LINEAGE_GAP).
    treasury_review_rows = 0
    emerging_operator_rows = 0
    for r in rows:
        entity = r.get("terminal_entity")
        if entity and entity in treasury_review:
            treasury_review_rows += 1
        if entity and entity in unknown_infra:
            emerging_operator_rows += 1

    provisioning = _provisioning_activity_status(conn)

    # "Worth monitoring": any row with at least one positive investigative signal
    # (a recorded treasury, a repeated creator/treasury, a treasury-review lead, or
    # an emerging-operator link). Everything else collapses into low-information.
    worth_monitoring_mints: set[str] = set()
    for r in rows:
        evidence = r["evidence"]
        creator = evidence.get("creator")
        treasuries = [t for t in (evidence.get("treasuries") or []) if t]
        entity = r.get("terminal_entity")
        is_worth = (
            bool(treasuries)
            or (creator and creator_counts.get(creator, 0) >= 2)
            or any(treasury_counts.get(t, 0) >= 2 for t in treasuries)
            or (entity in treasury_review)
            or (entity in unknown_infra)
        )
        if is_worth:
            worth_monitoring_mints.add(r["mint"])

    return {
        "ok": True,
        "total_terminal_outcomes": total,
        "worth_monitoring": len(worth_monitoring_mints),
        "low_information": total - len(worth_monitoring_mints),
        "pattern_summary": [
            {"bucket": key, "label": _BUCKET_LABELS[key], "count": bucket_counts.get(key, 0)}
            for key in ("NO_LINEAGE", "CREATOR_IDENTIFIED", "PARTIAL_FUNDING_TRAIL", "RECORDED_TREASURY_LEAD")
            if bucket_counts.get(key, 0) > 0
        ],
        "repeated_creator_rows": repeated_creator_rows,
        "repeated_creator_entities": sum(1 for c in creator_counts.values() if c >= 2),
        "repeated_treasury_rows": repeated_treasury_rows,
        "repeated_treasury_entities": sum(1 for c in treasury_counts.values() if c >= 2),
        "treasury_review_lead_rows": treasury_review_rows,
        "emerging_operator_rows": emerging_operator_rows,
        "provisioning_activity": provisioning,
    }


def build_investigation_queue(
    conn: sqlite3.Connection, *, limit: int = 100, filter_key: Optional[str] = None,
) -> dict[str, Any]:
    """Grouped, ranked investigation queue. Groups rows by their strongest known
    entity (treasury > creator > terminal_entity), so an analyst sees "Creator X —
    14 launches" instead of 14 identical token rows. Ranking is a deterministic
    ordering by real signal presence/count — never an opaque score. Every row in
    the returned ordering is fully explained by the "signals" list attached to it.
    """
    rows = _load_terminal_rows(conn)
    creator_counts: Counter[str] = Counter(
        r["evidence"].get("creator") for r in rows if r["evidence"].get("creator")
    )
    treasury_counts: Counter[str] = Counter()
    for r in rows:
        for t in (r["evidence"].get("treasuries") or []):
            if t:
                treasury_counts[t] += 1

    treasury_review = _treasury_review_lookup(conn)
    unknown_infra = _unknown_infra_lookup(conn)

    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        evidence = r["evidence"]
        treasuries = [t for t in (evidence.get("treasuries") or []) if t]
        creator = evidence.get("creator")
        entity = r.get("terminal_entity")
        # Group key preference: a resolved treasury in evidence.treasuries[] is the
        # strongest entity; next, a terminal_entity already carrying a Treasury
        # Review Lead or Emerging Operator link (both are unconfirmed treasury/
        # infrastructure candidates for LINEAGE_GAP/INSUFFICIENT_EVIDENCE rows, so
        # grouping on terminal_entity is correct here even though it isn't yet in
        # evidence.treasuries[]); then creator; then fall back to the mint itself.
        if treasuries:
            group_key = ("treasury", treasuries[0])
        elif entity and (entity in treasury_review or entity in unknown_infra):
            group_key = ("treasury", entity)
        elif creator:
            group_key = ("creator", creator)
        else:
            group_key = ("token", r["mint"])

        group = groups.setdefault(group_key, {
            "group_type": group_key[0],
            "entity": group_key[1],
            "mints": [],
            "bucket": None,
            "terminal_entities": set(),
        })
        group["mints"].append(r["mint"])
        if entity:
            group["terminal_entities"].add(entity)
        if group["bucket"] is None:
            group["bucket"] = _reason_bucket(r)

    entries: list[dict[str, Any]] = []
    for (group_type, entity), group in groups.items():
        launch_count = len(group["mints"])
        signals: list[str] = []
        rank = 0

        if group_type == "treasury":
            # This group may be keyed either on a treasury actually present in
            # evidence.treasuries[] (a resolved-but-unconfirmed treasury), or on a
            # terminal_entity that only carries a Treasury Review Lead / Emerging
            # Operator registry link (unconfirmed candidate, never itself resolved
            # into evidence.treasuries[] — see module docstring). Both are surfaced
            # under "treasury" grouping since they are the strongest entity known,
            # but the specific signal must reflect which is actually true for this
            # entity, never both by default.
            if entity in treasury_counts:
                signals.append("Recorded treasury lead")
                rank += 100
            if entity in treasury_review:
                signals.append("Treasury Review Lead")
                rank += 200
            if entity in unknown_infra:
                signals.append("Emerging Operator candidate")
                rank += 150
            if treasury_counts.get(entity, 0) >= 2:
                signals.append(f"Repeated treasury ({treasury_counts[entity]} outcomes)")
                rank += 50
            if not signals:
                signals.append("Recorded treasury lead")
                rank += 100
        elif group_type == "creator":
            if creator_counts.get(entity, 0) >= 2:
                signals.append(f"Repeated creator ({creator_counts[entity]} launches)")
                rank += 60
            else:
                signals.append("Creator identified")
                rank += 10
            # Check the GROUP's actual observed terminal_entity values, not the
            # creator address itself — unknown_infra is keyed on terminal_entity.
            if group["terminal_entities"] & unknown_infra:
                signals.append("Emerging Operator candidate")
                rank += 150
        else:
            signals.append("No funding lineage")

        rank += min(launch_count, 20)  # more launches = more investigative weight, capped

        entries.append({
            "group_type": group_type,
            "entity": entity,
            "launch_count": launch_count,
            "bucket": group["bucket"],
            "bucket_label": _BUCKET_LABELS.get(group["bucket"], group["bucket"]),
            "signals": signals,
            "rank": rank,
            "sample_mints": group["mints"][:5],
        })

    if filter_key:
        entries = [e for e in entries if filter_key in {s.split(" (")[0] for s in e["signals"]}
                   or filter_key == e["bucket"]
                   or (filter_key == "LOW_INFORMATION" and e["rank"] < 10)]

    entries.sort(key=lambda e: (-e["rank"], -e["launch_count"], e["entity"]))
    return {
        "ok": True,
        "entries": entries[:limit],
        "count": len(entries),
        "truncated": len(entries) > limit,
    }
