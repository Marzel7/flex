"""Build explainable discovery chains from existing intelligence outputs.

This module deliberately performs no detection, RPC, walkback, scoring, or writes.
It only joins already-materialised records into an analyst-facing narrative.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from src.ops.operator_model import EVIDENCE_CATALOGUE
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
from src.ops.attribution_outcome import emerging_operator_seeds


DISCOVERY_STATES = (
    "UNRESOLVED", "EMERGING", "PROVISIONAL", "CONFIRMED",
    "SUPERSEDED", "REJECTED",
)

_STATE_RANK = {
    "UNRESOLVED": 0, "EMERGING": 1, "PROVISIONAL": 2,
    "CONFIRMED": 3, "SUPERSEDED": 4, "REJECTED": 5,
}


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _confidence(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, str):
        upper = value.upper()
        aliases = {"STRICT": "HIGH", "MANUAL": "HIGH", "CERTAIN": "CERTAIN"}
        if upper in {"CERTAIN", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
            return upper
        return aliases.get(upper, "MEDIUM")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if score > 1:
        score /= 100
    if score >= .95:
        return "CERTAIN"
    if score >= .75:
        return "HIGH"
    if score >= .45:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "UNKNOWN"


def _state(value: Any, *, confirmed: bool = False) -> str:
    raw = str(value or "").upper()
    if raw in {"REJECTED", "REJECT"}:
        return "REJECTED"
    if raw in {"SUPERSEDED", "MERGE_REVIEW", "SPLIT_REVIEW"}:
        return "SUPERSEDED"
    if confirmed or raw in {"CONFIRMED", "CONFIRMED_AUTO", "FIRED", "FIRED_CREATE", "RESOLVED"}:
        return "CONFIRMED"
    if raw in {"PROVISIONAL", "PROVISIONAL_SUBPROV", "CANDIDATE", "DETECTED", "ARMED", "REVIEW"}:
        return "PROVISIONAL"
    if raw in {"FORMING", "PENDING", "PENDING_REVIEW", "DISCOVERED", "WALKING", "RUNNING"}:
        return "EMERGING"
    return "UNRESOLVED"


class DiscoveryService:
    """Read-only provenance composer with bounded, entity-keyed lookups."""

    def __init__(self, ops_db_path: str | None = None, core_db_path: str | None = None):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.ops_db_path = ops_db_path or os.environ.get(
            "WT_OPS_DB_PATH", os.path.join(repo, "database", "wt_ops_v2.db")
        )
        self.core_db_path = core_db_path or os.environ.get(
            "DB_PATH", os.path.join(repo, "database", "flex_complete_database.db")
        )

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    @staticmethod
    def _one(conn: sqlite3.Connection, tables: set[str], table: str,
             where: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        if table not in tables:
            return None
        try:
            row = conn.execute(f"SELECT * FROM {table} WHERE {where} LIMIT 1", args).fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None

    @staticmethod
    def _many(conn: sqlite3.Connection, tables: set[str], table: str,
              where: str, args: tuple[Any, ...], order: str = "", limit: int = 25) -> list[dict[str, Any]]:
        if table not in tables:
            return []
        try:
            suffix = f" ORDER BY {order}" if order else ""
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {where}{suffix} LIMIT ?", (*args, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    @staticmethod
    def _node(
        *, kind: str, state: str, entity_id: str, entity_type: str,
        detector: str, reason: str, timestamp: int | None = None,
        confidence: Any = None, rule: str = "", category: str = "SUPPORTING",
        evidence: list[dict[str, Any]] | None = None,
        connected: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "state": state if state in DISCOVERY_STATES else "UNRESOLVED",
            "entity": {"id": entity_id, "type": entity_type},
            "detector": detector,
            "evidence": evidence or [],
            "confidence": _confidence(confidence),
            "timestamp": int(timestamp) if timestamp else None,
            "supporting_rule": rule,
            "reason": reason,
            "connected_entity": connected,
            "category": category if category in {"IDENTITY", "SUPPORTING", "CONTEXT"} else "CONTEXT",
            "details": details or {},
        }

    def _identify(self, conn: sqlite3.Connection, tables: set[str], entity_id: str,
                  requested: str) -> str:
        if requested and requested != "auto":
            return requested.lower().replace("sub-provisioner", "sub_provisioner")
        checks = (
            ("operator", "operators", "operator_id = ?"),
            ("token", "migrated_tokens", "mint = ?"),
            ("token", "wt_watchtower_launches", "mint = ?"),
            ("token", "watchtower_token_attribution", "mint = ?"),
            ("treasury", "wt_confirmed_treasuries", "treasury = ?"),
            ("sub_provisioner", "wt_discovered_subprovs", "subprov = ?"),
            ("creator", "wt_wrap_close_candidates", "creator = ?"),
            ("creator", "wt_watchtower_launches", "creator_wallet = ?"),
        )
        if {"wt_unknown_infrastructure_registry", "wt_attribution_outcomes"} <= tables:
            if any(row["terminal_entity"] == entity_id for row in emerging_operator_seeds(conn)):
                return "emerging_candidate"
        for kind, table, where in checks:
            if self._one(conn, tables, table, where, (entity_id,)):
                return kind
        tx_checks = (
            ("wt_wrap_close_candidates", "tx_signature = ?"),
            ("wt_watchtower_launches", "create_signature = ? OR wrap_close_signature = ?"),
            ("migrated_tokens", "migration_tx = ?"),
            ("wt_active_subprov_sessions", "funding_signature = ?"),
        )
        for table, where in tx_checks:
            args = (entity_id, entity_id) if " OR " in where else (entity_id,)
            if self._one(conn, tables, table, where, args):
                return "transaction"
        return "unknown"

    def resolve(self, entity_id: str, entity_type: str = "auto") -> dict[str, Any]:
        entity_id = (entity_id or "").strip()
        generated_at = int(time.time())
        if not entity_id or not os.path.exists(self.ops_db_path):
            return self._empty(entity_id, entity_type, generated_at)

        with self._connect(self.ops_db_path) as conn:
            tables = self._tables(conn)
            kind = self._identify(conn, tables, entity_id, entity_type)
            if kind == "operator":
                result = self._operator(conn, tables, entity_id)
            else:
                result = self._entity(conn, tables, entity_id, kind)

        result["generated_at"] = generated_at
        result["discovery_states"] = list(DISCOVERY_STATES)
        return result

    def _empty(self, entity_id: str, entity_type: str, generated_at: int) -> dict[str, Any]:
        return {
            "ok": True,
            "subject": {"id": entity_id, "type": entity_type if entity_type != "auto" else "unknown"},
            "state": "UNRESOLVED",
            "summary": "No existing discovery evidence was found for this entity.",
            "timeline": [], "evidence_groups": {"IDENTITY": [], "SUPPORTING": [], "CONTEXT": []},
            "walkback": {"hops": [], "status": "UNRESOLVED", "stop_reason": "No historical evidence."},
            "attribution_outcome": None,
            "emerging_candidate": None,
            "treasury_expansion": None,
            "operational_behaviour": None,
            "detection_reconciliation": None,
            "creator_activity": None,
            "operator_history": [], "network_discovery": [], "cross_operation": [],
            "generated_at": generated_at, "discovery_states": list(DISCOVERY_STATES),
        }

    def _entity(self, conn: sqlite3.Connection, tables: set[str], requested_id: str,
                kind: str) -> dict[str, Any]:
        timeline: list[dict[str, Any]] = []
        walk_hops: list[dict[str, Any]] = []
        subject_id, subject_type = requested_id, kind

        # Resolve a transaction to its already-known entity without doing chain work.
        if kind == "transaction":
            w = self._one(conn, tables, "wt_wrap_close_candidates", "tx_signature = ?", (requested_id,))
            launch = self._one(conn, tables, "wt_watchtower_launches",
                               "create_signature = ? OR wrap_close_signature = ?", (requested_id, requested_id))
            migration = self._one(conn, tables, "migrated_tokens", "migration_tx = ?", (requested_id,))
            session = self._one(conn, tables, "wt_active_subprov_sessions", "funding_signature = ?", (requested_id,))
            if w:
                subject_id, subject_type = w.get("creator") or requested_id, "creator"
            elif launch:
                subject_id, subject_type = launch.get("mint") or launch.get("creator_wallet") or requested_id, "token"
            elif migration:
                subject_id, subject_type = migration.get("mint") or requested_id, "token"
            elif session:
                subject_id, subject_type = session.get("subprov_wallet") or requested_id, "sub_provisioner"
            timeline.append(self._node(
                kind="TRANSACTION_OBSERVED", state="PROVISIONAL", entity_id=requested_id,
                entity_type="transaction", detector="Recorded transaction evidence",
                reason=f"This transaction is linked to the existing {subject_type.replace('_', ' ')} record.",
                confidence="HIGH", rule="Existing transaction-to-entity linkage", category="SUPPORTING",
                connected={"id": subject_id, "type": subject_type},
                evidence=[{"label": "Transaction", "value": requested_id}],
            ))

        token = creator = subprov = treasury = operation_uuid = None
        attribution_outcome: dict[str, Any] | None = None
        launch: dict[str, Any] | None = None
        migration: dict[str, Any] | None = None
        attrib: dict[str, Any] | None = None
        walk: dict[str, Any] | None = None

        if subject_type == "token":
            token = subject_id
            migration = self._one(conn, tables, "migrated_tokens", "mint = ?", (token,))
            launch = self._one(conn, tables, "wt_watchtower_launches", "mint = ?", (token,))
            lifecycle = self._one(conn, tables, "wt_token_lifecycle", "mint = ?", (token,))
            attrib = self._one(conn, tables, "watchtower_token_attribution", "mint = ?", (token,))
            walk = self._one(conn, tables, "wt_walkback_queue", "mint = ?", (token,))
            attribution_outcome = self._one(
                conn, tables, "wt_attribution_outcomes", "mint = ?", (token,)
            )
            if attribution_outcome:
                attribution_outcome["evidence"] = _json(
                    attribution_outcome.pop("evidence_json", None), {}
                )
            creator = (launch or {}).get("creator_wallet") or (migration or {}).get("creator") or (lifecycle or {}).get("creator") or (attrib or {}).get("creator") or (walk or {}).get("creator")
            subprov = (launch or {}).get("subprov_wallet") or (lifecycle or {}).get("subprov") or (attrib or {}).get("matched_subprov") or (walk or {}).get("subprov")
            treasury = (launch or {}).get("treasury_wallet") or (lifecycle or {}).get("treasury") or (attrib or {}).get("matched_treasury") or (walk or {}).get("treasury")
            operation_uuid = (lifecycle or {}).get("operation_uuid")
            launch_ts = (launch or {}).get("create_time") or (lifecycle or {}).get("launched_at") or (migration or {}).get("migration_time")
            if launch or migration or lifecycle:
                timeline.append(self._node(
                    kind="TOKEN_LAUNCH", state="EMERGING", entity_id=token, entity_type="token",
                    detector="Launch ingestion", timestamp=launch_ts, confidence="HIGH",
                    rule="Existing launch observation", category="CONTEXT",
                    reason="The token launch entered the platform's existing intelligence record.",
                    evidence=[x for x in [
                        {"label": "Mint", "value": token},
                        {"label": "Launch transaction", "value": (launch or {}).get("create_signature")},
                        {"label": "Migration transaction", "value": (migration or {}).get("migration_tx")},
                    ] if x["value"]], connected={"id": creator, "type": "creator"} if creator else None,
                ))
            elif walk and walk.get("creator"):
                # X26.7 — a wt_walkback_queue row can be the ONLY record of this
                # mint (e.g. a PLAIN_XFER-funded launch that never produced a
                # wt_watchtower_launches/migrated_tokens/wt_token_lifecycle row,
                # common for the KNOWN_CEX_REACHED/KNOWN_RELAY_REACHED boundary
                # cases). Previously this meant `timeline` stayed empty even
                # though a real creator, funding mechanism, and funder wallet
                # were already persisted — the page fell into the whole-page
                # "No historical discovery evidence is available" branch and
                # silently dropped Launch Profile/Funding Walkback/Evidence
                # Groups, despite Attribution Outcome (attribution_outcome)
                # being genuinely populated. Surfacing the walkback row itself
                # as evidence closes that gap without inventing anything new.
                timeline.append(self._node(
                    kind="TOKEN_LAUNCH", state="EMERGING", entity_id=token, entity_type="token",
                    detector="Walkback queue record", timestamp=walk.get("completed_at") or walk.get("enqueued_at"),
                    confidence="MEDIUM", rule="Existing walkback-queue observation", category="CONTEXT",
                    reason="The token launch is recorded in the funding walkback queue; no separate launch-ingestion record exists.",
                    evidence=[x for x in [
                        {"label": "Mint", "value": token},
                        {"label": "Funding mechanism", "value": walk.get("funding_mechanism")},
                        {"label": "Funder wallet", "value": walk.get("funder_wallet")},
                        {"label": "Funder transaction", "value": walk.get("funder_sig")},
                    ] if x["value"]], connected={"id": creator, "type": "creator"} if creator else None,
                ))
            # X26.2/X26.2.1: watchtower_token_attribution rows can be written
            # from mere wt_discovered_subprovs membership with NO confirmed
            # treasury at all (walkback_worker.py's confirmed_subprov-OR-treasury
            # gate) — a LINEAGE_GAP outcome is enough. Rendering this as a
            # "Platform Attribution" card for those rows overstates what the
            # backend actually confirmed. This node is Discovery's own
            # presentation choice; other consumers of this table
            # (operation_scheduler.py, walkback_queue.py, operation_dashboard_routes.py,
            # watchtower_funnel.py, attribution_outcome.py) read provisional rows
            # for their own purposes and are unaffected — only Discovery's
            # rendering gate is narrowed here, the table/rows themselves are untouched.
            if attrib and attrib.get("matched_treasury"):
                a_state = "REJECTED" if attrib.get("reviewed_status") == "REJECTED" else "CONFIRMED"
                timeline.append(self._node(
                    kind="CONFIRMED_TREASURY_ATTRIBUTION", state=a_state, entity_id=token, entity_type="token",
                    detector="Platform attribution", timestamp=attrib.get("scored_at"),
                    confidence=attrib.get("score"), rule="Existing platform attribution result",
                    category="IDENTITY", reason=self._attribution_reason(attrib),
                    evidence=[
                        {"label": "Tier", "value": attrib.get("tier")},
                        {"label": "Matched treasury", "value": attrib.get("matched_treasury")},
                        {"label": "Matched sub-provisioner", "value": attrib.get("matched_subprov")},
                    ], connected={"id": attrib.get("matched_treasury"), "type": "treasury"},
                    details={"reasons": _json(attrib.get("reasons_json"), [])},
                ))
            if lifecycle:
                last_ts = lifecycle.get("recycled_at") or lifecycle.get("migrated_at") or lifecycle.get("updated_at")
                timeline.append(self._node(
                    kind="LIFECYCLE_OBSERVED", state="CONFIRMED" if lifecycle.get("migrated_at") else "PROVISIONAL",
                    entity_id=token, entity_type="token", detector="Lifecycle intelligence",
                    timestamp=last_ts, confidence="HIGH", rule="Existing token lifecycle output",
                    category="CONTEXT", reason=f"Lifecycle reached {lifecycle.get('lifecycle_state') or 'an observed state'}.",
                    evidence=[{"label": "Lifecycle state", "value": lifecycle.get("lifecycle_state")}],
                ))

        elif subject_type == "creator":
            creator = subject_id
            launches = self._many(conn, tables, "wt_watchtower_launches", "creator_wallet = ?", (creator,), "recorded_at ASC")
            launch = launches[-1] if launches else None
            token = (launch or {}).get("mint")
            subprov = (launch or {}).get("subprov_wallet")
            treasury = (launch or {}).get("treasury_wallet")
            for item in launches:
                timeline.append(self._node(
                    kind="TOKEN_LAUNCH", state="PROVISIONAL", entity_id=item.get("mint") or creator,
                    entity_type="token" if item.get("mint") else "creator", detector="Create observation",
                    timestamp=item.get("create_time") or item.get("recorded_at"), confidence=item.get("confidence"),
                    rule="Existing creator launch linkage", category="CONTEXT",
                    reason="A token launch was observed from this creator.",
                    evidence=[{"label": "Mint", "value": item.get("mint")}],
                    connected={"id": creator, "type": "creator"},
                ))

        elif subject_type == "sub_provisioner":
            subprov = subject_id
        elif subject_type == "treasury":
            treasury = subject_id
        elif subject_type == "emerging_candidate":
            treasury = subject_id
        elif subject_type == "unknown":
            return self._empty(requested_id, "unknown", int(time.time()))

        # Creator funding provenance (the actual attribution, not a recomputation).
        wrap = self._one(conn, tables, "wt_wrap_close_candidates", "creator = ?", (creator,)) if creator else None
        if wrap:
            subprov = subprov or wrap.get("subprov_wallet")
            treasury = treasury or wrap.get("lineage_source_treasury")
            timeline.append(self._node(
                kind="CREATOR_IDENTIFIED", state="PROVISIONAL", entity_id=creator, entity_type="creator",
                detector="Wrap-Close Detector", timestamp=wrap.get("funded_at") or wrap.get("detected_at"),
                confidence=wrap.get("confidence"), rule="WSOL wrap-close attribution", category="SUPPORTING",
                reason="The creator is the recorded close-account destination of the funding transaction.",
                evidence=[
                    {"label": "Mechanism", "value": wrap.get("funding_mechanism")},
                    {"label": "Extraction", "value": wrap.get("creator_extraction_method")},
                    {"label": "Funding", "value": f"{wrap.get('base_amount_sol')} SOL" if wrap.get("base_amount_sol") is not None else None},
                    {"label": "Transaction", "value": wrap.get("tx_signature")},
                ], connected={"id": subprov, "type": "sub_provisioner"} if subprov else None,
            ))
            walk_hops.append(self._hop(creator, "CREATOR", "Starting entity resolved from close-account destination.", "", wrap.get("confidence")))
        elif creator and launch:
            timeline.append(self._node(
                kind="CREATOR_IDENTIFIED", state="PROVISIONAL", entity_id=creator, entity_type="creator",
                detector="Recorded launch attribution", timestamp=launch.get("create_time") or launch.get("recorded_at"),
                confidence=launch.get("confidence"), rule="Existing creator-to-launch linkage", category="SUPPORTING",
                reason="The creator was identified by the existing launch record; no separate wrap-close record is available.",
                evidence=[
                    {"label": "Funding mechanism", "value": launch.get("funding_mechanism")},
                    {"label": "Extraction method", "value": launch.get("creator_extraction_method")},
                    {"label": "Create transaction", "value": launch.get("create_signature")},
                ], connected={"id": subprov, "type": "sub_provisioner"} if subprov else None,
            ))
            walk_hops.append(self._hop(
                creator, "CREATOR", "Starting entity taken from the stored launch attribution.",
                "" if subprov else "No historical sub-provisioner evidence beyond the creator record.",
                launch.get("confidence"),
            ))
        elif creator and walk:
            # X26.7 — mirrors the wrap/launch branches above for the case where
            # the ONLY persisted record of this creator's funding is a
            # wt_walkback_queue row (no wrap-close, no launch-ingestion row).
            # Common for infrastructure-boundary outcomes (PLAIN_XFER funding
            # from a CEX/relay never produces a wrap-close record).
            timeline.append(self._node(
                kind="CREATOR_IDENTIFIED", state="PROVISIONAL", entity_id=creator, entity_type="creator",
                detector="Walkback queue record", timestamp=walk.get("funder_block_time") or walk.get("completed_at"),
                confidence="MEDIUM", rule="Existing walkback-queue funding record", category="SUPPORTING",
                reason="The creator was identified by the recorded walkback funding evidence; no wrap-close or launch-ingestion record is available.",
                evidence=[
                    {"label": "Funding mechanism", "value": walk.get("funding_mechanism")},
                    {"label": "Funder wallet", "value": walk.get("funder_wallet")},
                    {"label": "Funder transaction", "value": walk.get("funder_sig")},
                ], connected={"id": subprov, "type": "sub_provisioner"} if subprov else None,
            ))
            walk_hops.append(self._hop(
                creator, "CREATOR", "Starting entity taken from the recorded walkback funding evidence.",
                "" if subprov else "No historical sub-provisioner evidence beyond the creator record.",
                "MEDIUM",
            ))

        sp = self._one(conn, tables, "wt_discovered_subprovs", "subprov = ?", (subprov,)) if subprov else None
        # X26.6.1 — a REJECTED* state (e.g. REJECTED_INFRASTRUCTURE, X26.3) means
        # this row was explicitly disqualified as a sub-provisioner (known
        # infrastructure funding creators is not provisioning evidence). The raw
        # funding observations on the row are preserved in the database, but
        # Discovery must never render a SUBPROVISIONER_RESOLVED node — or any
        # "creator observations support the role" wording — for a row the
        # platform has already concluded is not a genuine sub-provisioner.
        sp_rejected = bool(sp) and str(sp.get("state") or "").upper().startswith("REJECTED")
        if sp and not sp_rejected:
            treasury = treasury or sp.get("treasury") or sp.get("immediate_funder")
            timeline.append(self._node(
                kind="SUBPROVISIONER_RESOLVED", state=_state(sp.get("state")),
                entity_id=subprov, entity_type="sub_provisioner", detector="Sub-Provisioner Resolution",
                timestamp=sp.get("first_seen"), confidence=sp.get("confidence"),
                rule="Recorded creator-funding ancestry", category="IDENTITY",
                reason=self._subprov_reason(sp),
                evidence=[
                    {"label": "Creators funded", "value": sp.get("creator_count")},
                    {"label": "Wrap-close observations", "value": sp.get("wrap_close_count")},
                    {"label": "Funding mechanism", "value": sp.get("funding_mechanism")},
                ], connected={"id": treasury, "type": "treasury"} if treasury else None,
            ))
            walk_hops.append(self._hop(subprov, "SUB_PROVISIONER",
                                       "Recorded as the wallet provisioning the creator.", "",
                                       sp.get("confidence")))

        confirmed = self._one(conn, tables, "wt_confirmed_treasuries", "treasury = ?", (treasury,)) if treasury else None
        review = self._one(conn, tables, "wt_treasury_review", "treasury = ?", (treasury,)) if treasury else None
        if treasury:
            t_state = "CONFIRMED" if confirmed else _state((review or {}).get("status"))
            source = confirmed or review or {}
            timeline.append(self._node(
                kind="TREASURY_RESOLVED", state=t_state, entity_id=treasury, entity_type="treasury",
                detector="Treasury Resolution", timestamp=source.get("confirmed_at") or source.get("reviewed_at") or source.get("detected_at"),
                confidence=source.get("confidence"), rule="Confirmed treasury registry" if confirmed else "Existing treasury review",
                category="IDENTITY", reason=self._treasury_reason(confirmed, review),
                evidence=[
                    {"label": "Resolution method", "value": source.get("method") or source.get("detected_via")},
                    {"label": "Recipients", "value": source.get("recipients")},
                    {"label": "SOL out", "value": source.get("out_sol")},
                ], connected={"id": subprov, "type": "sub_provisioner"} if subprov else None,
            ))
            walk_hops.append(self._hop(
                treasury, "TREASURY" if confirmed else "TREASURY_CANDIDATE",
                "Existing lineage identifies this wallet as the upstream funder.",
                "Reached known treasury." if confirmed else "Confidence too low; treasury remains under review.",
                source.get("confidence"),
            ))

        # Some platform generations pre-date the separate attribution table.
        # The strict launch record is itself a materialised attribution output,
        # so surface that provenance when no newer attribution record exists.
        # X25.6: "confirmed" here means a confirmed treasury (the platform's
        # own detection mechanism), not a confirmed operator — this node must
        # never assert WATCHTOWER or any specific operator; operator identity
        # is established solely by canonicalIdentity().
        if token and launch and confirmed and not attrib:
            timeline.append(self._node(
                kind="CONFIRMED_TREASURY_ATTRIBUTION", state="CONFIRMED", entity_id=token,
                entity_type="token", detector="Platform launch attribution",
                timestamp=launch.get("recorded_at") or launch.get("create_time"),
                confidence=launch.get("confidence"), rule="Existing attributed platform launch",
                category="IDENTITY",
                reason="The stored launch record links the token through its creator and sub-provisioner to a confirmed treasury.",
                evidence=[
                    {"label": "Treasury", "value": treasury},
                    {"label": "Sub-provisioner", "value": subprov},
                    {"label": "Creator", "value": creator},
                    {"label": "Funding mechanism", "value": launch.get("funding_mechanism")},
                ], connected={"id": treasury, "type": "treasury"},
            ))

        # Operation resolution may contain a longer already-computed path.
        resolution = self._one(conn, tables, "wt_ops_v2_treasury_resolution", "operation_uuid = ?", (operation_uuid,)) if operation_uuid else None
        if resolution:
            path = _json(resolution.get("evidence_path"), [])
            for i, address in enumerate(path):
                if not address or any(h["address"] == address for h in walk_hops):
                    continue
                role = "TREASURY_CANDIDATE" if i == 0 else "FUNDING_WALLET"
                walk_hops.append(self._hop(address, role, "Included in the stored positional resolution path.", "", resolution.get("confidence")))
            timeline.append(self._node(
                kind="TREASURY_WALKBACK", state=_state(resolution.get("status")),
                entity_id=resolution.get("positional_root_candidate") or resolution.get("current_assigned_treasury") or operation_uuid,
                entity_type="treasury", detector="Treasury Positional Resolution",
                timestamp=resolution.get("resolved_at"), confidence=resolution.get("confidence"),
                rule="Stored treasury resolution path", category="IDENTITY",
                reason=resolution.get("reason") or "The stored walkback path resolved the treasury candidate.",
                evidence=[{"label": "Path length", "value": len(path)}, {"label": "Status", "value": resolution.get("status")}],
            ))

        operator_history, cross_operation, operator_nodes = self._operator_for_entities(
            conn, tables, [x for x in (treasury, subprov, creator) if x]
        )
        timeline.extend(operator_nodes)

        canonical_identity = self._canonical_identity(
            conn, tables, treasury, confirmed
        )

        timeline = self._dedupe_sort(timeline)
        groups = self._groups(timeline)
        overall = self._overall_state(timeline)
        stop_reason = self._stop_reason(walk_hops, confirmed, sp, wrap, resolution)
        walk_status = "CONFIRMED" if confirmed else ("PROVISIONAL" if walk_hops else "UNRESOLVED")
        summary = self._summary(kind, overall, creator, subprov, treasury, bool(confirmed))
        if canonical_identity:
            summary = (
                f"{canonical_identity['operator_name']} · Confirmed Treasury · "
                f"Confidence {canonical_identity['confidence']}"
            )
        if attribution_outcome:
            summary = attribution_outcome["stop_reason"]
            stop_reason = attribution_outcome["stop_reason"]
        emerging_candidate = None
        emerging_entity = (
            attribution_outcome.get("terminal_entity")
            if attribution_outcome and attribution_outcome.get("outcome_type") == "UNKNOWN_INFRASTRUCTURE"
            else subject_id if subject_type == "emerging_candidate" else None
        )
        if emerging_entity:
            try:
                from src.ops.emerging_operator_service import EmergingOperatorService
                emerging_candidate = EmergingOperatorService(
                    self.ops_db_path, self.core_db_path
                ).get(emerging_entity)
                if emerging_candidate:
                    summary = (
                        f"Unknown Infrastructure · {emerging_candidate['observed_launches']} launches · "
                        f"{emerging_candidate['review_label']}"
                    )
            except (OSError, sqlite3.Error, ValueError):
                emerging_candidate = None

        # X20.9: treasury expansion evidence. Never suppresses the emerging_candidate
        # path above (X20) — this is an additive, read-only evidence report answering a
        # narrower question: does this specific unconfirmed treasury/sub-provisioner pair
        # carry any deterministic evidence already linking it to a known operator's
        # infrastructure? It never confirms attribution and only runs when the treasury
        # is not already canonical (see TreasuryExpansionResolver.evaluate).
        treasury_expansion = None
        if not canonical_identity and (treasury or subprov):
            try:
                from src.ops.treasury_expansion_resolver import TreasuryExpansionResolver
                treasury_expansion = TreasuryExpansionResolver(
                    self.ops_db_path, self.core_db_path
                ).evaluate(subprov, treasury)
            except (OSError, sqlite3.Error, ValueError):
                treasury_expansion = None

        # X21E: operational behaviour intelligence. Purely additive/read-only —
        # never touches attribution, walkback, scoring, or identity. Answers
        # "how did this behave" using X21B provisioning facts (when captured)
        # and existing persisted signals, separate from "who is this."
        #
        # X26.10 — attribution_outcome.terminal_entity is sometimes the ONLY
        # place a reviewed CEX/bridge/relay/automation/custody boundary
        # address is recorded (e.g. a wallet stored in
        # wt_walkback_queue.funder_wallet or .treasury rather than .subprov,
        # a different column than the one `subprov` above is derived from).
        # Without this, Operational Behaviour silently rendered empty for
        # those launches even though attribution correctly identified a
        # reviewed terminal boundary. Only ever fills the gap when `subprov`
        # is otherwise unresolved, and only for outcome types that represent
        # a genuine reviewed-infrastructure boundary — never for
        # CANONICAL_OPERATOR_REACHED or a plain creator/unknown termination.
        _REVIEWED_TERMINAL_TYPES = {
            "CEX", "AUTOMATION", "RELAY", "BRIDGE", "CUSTODY",
            "PLATFORM", "PROTOCOL", "SYSTEM", "INFRASTRUCTURE",
        }
        terminal_infrastructure = None
        if (
            attribution_outcome
            and str(attribution_outcome.get("terminal_entity_type") or "").upper() in _REVIEWED_TERMINAL_TYPES
        ):
            terminal_infrastructure = attribution_outcome.get("terminal_entity")

        operational_behaviour = None
        if token or treasury or subprov or creator or terminal_infrastructure:
            try:
                from src.ops.operational_behaviour import OperationalBehaviourService
                operational_behaviour = OperationalBehaviourService(
                    self.ops_db_path, self.core_db_path
                ).build(
                    source_mint=token, treasury=treasury, subprov=subprov, creator=creator,
                    terminal_infrastructure=terminal_infrastructure,
                )
            except (OSError, sqlite3.Error, ValueError):
                operational_behaviour = None

        # X27.1: Creator Activity — the creator wallet's OWN historical
        # launch behaviour (token_analysis-derived), independent of funding
        # provenance, attribution, infrastructure, or operation identity.
        # Purely additive/read-only. Answers "what do we already know about
        # this creator", never "who funded it" or "is this WATCHTOWER" —
        # those questions are already answered by Funding Walkback,
        # Attribution Outcome, and Operational Behaviour respectively.
        creator_activity = None
        if creator:
            try:
                from src.ops.creator_activity import CreatorActivityService
                creator_activity = CreatorActivityService(self.core_db_path).build(creator)
            except (OSError, sqlite3.Error, ValueError):
                creator_activity = None

        # X24.1 Phase 5: how was this attribution obtained — detected live, recovered
        # by catch-up/reconciliation, or only ever recovered retrospectively by
        # walkback? Purely additive/read-only, reuses the X24.1 Phase 4 classifier
        # (src/ops/detection_reconciliation.py) against this token's own mint —
        # never touches attribution, walkback, scoring, detector, or schema.
        detection_reconciliation = None
        if token:
            try:
                from src.ops.detection_reconciliation import classify_walkback_confirmed_launches
                _recon = classify_walkback_confirmed_launches(self.ops_db_path)
                detection_reconciliation = next(
                    (r for r in _recon.get("rows", []) if r.get("mint") == token), None
                )
            except (OSError, sqlite3.Error, ValueError):
                detection_reconciliation = None

        # X25.4: Operation Identity — the confirmed treasury-funding-mesh
        # membership for this launch's treasury, if any. Purely additive/
        # read-only, reuses src/ops/operation_identity.py (no DB writes, no
        # new schema). Absence is honest: a token with no resolved confirmed
        # treasury, or a treasury with no operation membership, returns None
        # rather than a fabricated "UNKNOWN_OPERATION" placeholder.
        operation_identity = None
        if treasury:
            try:
                from src.ops.operation_identity import operation_for_treasury
                _op = operation_for_treasury(treasury, self.ops_db_path)
                if _op:
                    operation_identity = {
                        "operation_id": _op["operation_id"],
                        "display_name": _op["display_name"],
                        "identity_basis": _op["identity_basis"],
                        "confidence": _op["confidence"],
                        "treasury_count": len(_op["treasuries"]),
                        "launch_count": _op["launch_count"],
                        "subject_treasury": treasury,
                        "member_treasuries": [t["wallet"] for t in _op["treasuries"]],
                    }
            except (OSError, sqlite3.Error, ValueError):
                operation_identity = None

        return {
            "ok": True,
            "subject": {"id": requested_id, "type": kind, "resolved_entity": {"id": subject_id, "type": subject_type}},
            "state": overall,
            "summary": summary,
            "timeline": timeline,
            "evidence_groups": groups,
            "walkback": {"hops": walk_hops, "status": walk_status, "stop_reason": stop_reason},
            "operator_history": operator_history,
            "network_discovery": operator_history,
            "cross_operation": cross_operation,
            "canonical_identity": canonical_identity,
            "attribution_outcome": attribution_outcome,
            "emerging_candidate": emerging_candidate,
            "treasury_expansion": treasury_expansion,
            "operational_behaviour": operational_behaviour,
            "detection_reconciliation": detection_reconciliation,
            "creator_activity": creator_activity,
            "launch_profile": self._launch_profile(launch) if subject_type == "token" else None,
            "operation_identity": operation_identity,
        }

    @staticmethod
    def _launch_profile(launch: dict[str, Any] | None) -> dict[str, Any]:
        """X25.1/X25.2 — Launch Profile: PROVISIONED vs OBSERVED_ONLY. Purely
        additive/read-only, derived only from the wt_watchtower_launches row
        already fetched for this token. Answers "how was this launch
        structurally provisioned" — nothing about funding lineage, operation
        identity, infrastructure reuse, attribution outcome, or detection
        provenance, which remain on their own independent tracks."""
        mechanism = (launch or {}).get("funding_mechanism")
        if launch and launch.get("subprov_wallet") and mechanism in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"):
            return {
                "classification": "PROVISIONED",
                "reason": f"Verified sub-provisioner and {mechanism.replace('_', ' ').lower()} funding mechanism.",
                "facts": {
                    "funding_mechanism": mechanism,
                    "birth_to_launch_seconds": launch.get("birth_to_launch_seconds"),
                    "creator_history": "No earlier launch in the admitted population",
                },
            }
        return {
            "classification": "OBSERVED_ONLY",
            "reason": (
                "No verified provisioning session was recorded. The funding chain "
                "shown below, if any, was reconstructed retrospectively from chain "
                "history rather than observed live."
            ),
            "facts": {},
        }

    def _canonical_identity(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        treasury: str | None,
        confirmed: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Compose the reviewed canonical answer; never infer a missing owner."""
        if not treasury or not confirmed or "operator_entities" not in tables or "operators" not in tables:
            return None
        membership = self._one(
            conn, tables, "operator_entities",
            "entity_address = ? AND operator_id = ?",
            (treasury, WATCHTOWER_OPERATOR_ID),
        )
        operator = self._one(
            conn, tables, "operators",
            "operator_id = ? AND display_name = 'WATCHTOWER' AND status = 'CONFIRMED'",
            (WATCHTOWER_OPERATOR_ID,),
        )
        if not membership or not operator:
            return None
        signal_labels = {
            "CONFIRMED_INFRASTRUCTURE_REUSE": "Infrastructure reuse",
            "VANITY_ADDRESS_FAMILY": "Vanity family",
        }
        signals: list[str] = []
        if "operator_evidence" in tables:
            evidence = self._many(
                conn, tables, "operator_evidence", "operator_id = ?",
                (WATCHTOWER_OPERATOR_ID,), "created_at ASC", 200,
            )
            present = {row.get("evidence_type") for row in evidence}
            signals.extend(label for key, label in signal_labels.items() if key in present)
        signals.append("Treasury confirmed")
        return {
            "operator_id": WATCHTOWER_OPERATOR_ID,
            "operator_name": operator.get("display_name"),
            "operator_state": "CONFIRMED",
            "treasury": treasury,
            "treasury_state": "CONFIRMED",
            "confidence": _confidence(confirmed.get("confidence") or membership.get("confidence")),
            "identity_signals": signals,
            "operator_href": f"/intelligence/operator/{WATCHTOWER_OPERATOR_ID}",
        }

    @staticmethod
    def _hop(address: str, role: str, reason_following: str, reason_stopping: str,
             confidence: Any) -> dict[str, Any]:
        return {
            "address": address, "role": role, "reason_following": reason_following,
            "reason_stopping": reason_stopping, "confidence": _confidence(confidence),
        }

    def _operator(self, conn: sqlite3.Connection, tables: set[str], operator_id: str) -> dict[str, Any]:
        op = self._one(conn, tables, "operators", "operator_id = ?", (operator_id,))
        if not op:
            return self._empty(operator_id, "operator", int(time.time()))
        entities = self._many(conn, tables, "operator_entities", "operator_id = ?", (operator_id,), "added_at ASC", 200)
        evidence = self._many(conn, tables, "operator_evidence", "operator_id = ?", (operator_id,), "created_at ASC", 200)
        reviews = self._many(conn, tables, "operator_reviews", "operator_id = ?", (operator_id,), "timestamp ASC", 100)
        promotions = self._many(conn, tables, "operator_promotion_reviews",
                                "canonical_operator_id = ?", (operator_id,), "timestamp ASC", 100)
        timeline = [self._node(
            kind="NETWORK_CANDIDATE", state="EMERGING", entity_id=operator_id, entity_type="operator",
            detector="Operator Resolution", timestamp=op.get("created_at"), confidence=op.get("confidence"),
            rule="Identity evidence required", category="IDENTITY",
            reason="A new operator candidate was established from existing cross-operation evidence.",
            evidence=[{"label": "Initial state", "value": "CANDIDATE"}],
        )]
        for ev in evidence:
            catalogue = EVIDENCE_CATALOGUE.get(ev.get("evidence_type"), {})
            category = ev.get("evidence_category") or catalogue.get("category") or "CONTEXT"
            timeline.append(self._node(
                kind="ATTRIBUTION_EVIDENCE", state="PROVISIONAL" if category == "IDENTITY" else "EMERGING",
                entity_id=ev.get("entity_a") or operator_id,
                entity_type="wallet" if ev.get("entity_a") else "operator",
                detector="Operator Resolution", timestamp=ev.get("created_at"), confidence=ev.get("weight"),
                rule=ev.get("evidence_type", "Existing operator evidence"), category=category,
                reason=catalogue.get("notes") or "Existing evidence contributed to operator resolution.",
                evidence=[
                    {"label": "Evidence type", "value": ev.get("evidence_type")},
                    {"label": "Source operation", "value": ev.get("source_operation")},
                    {"label": "Connected entity", "value": ev.get("entity_b")},
                ], connected={"id": ev.get("entity_b"), "type": "wallet"} if ev.get("entity_b") else None,
                details=_json(ev.get("details"), {}),
            ))
        for review in reviews:
            timeline.append(self._node(
                kind="ANALYST_REVIEW", state=_state(review.get("decision"), confirmed=review.get("decision") == "CONFIRMED"),
                entity_id=operator_id, entity_type="operator", detector="Analyst Review",
                timestamp=review.get("timestamp"), confidence="CERTAIN" if review.get("decision") == "CONFIRMED" else op.get("confidence"),
                rule="Human review decision", category="IDENTITY",
                reason=review.get("reason") or f"Analyst decision: {review.get('decision')}.",
                evidence=[{"label": "Decision", "value": review.get("decision")}, {"label": "Reviewer", "value": review.get("reviewer")}],
            ))
        for promotion in promotions:
            snapshot = _json(promotion.get("evidence_snapshot"), {})
            timeline.append(self._node(
                kind="IDENTITY_EVALUATION", state="PROVISIONAL",
                entity_id=promotion.get("proposal_id") or operator_id,
                entity_type="promotion_proposal", detector="X16B Identity Framework",
                timestamp=promotion.get("timestamp"), confidence=snapshot.get("identity_confidence"),
                rule="Independent identity classes", category="IDENTITY",
                reason=snapshot.get("reason") or "Identity evidence satisfied the reviewed proposal.",
                evidence=[
                    {"label": "Identity classes", "value": ", ".join(snapshot.get("identity_classes", []))},
                    {"label": "Proposal fingerprint", "value": promotion.get("proposal_fingerprint")},
                ], connected={"id": operator_id, "type": "operator"},
            ))
            timeline.append(self._node(
                kind="CANONICAL_PROMOTION", state="CONFIRMED",
                entity_id=operator_id, entity_type="operator",
                detector="Operator Promotion Review", timestamp=promotion.get("timestamp"),
                confidence="CERTAIN", rule="Fingerprint-bound analyst approval", category="IDENTITY",
                reason=promotion.get("reason") or "Analyst approved canonical operator promotion.",
                evidence=[
                    {"label": "Reviewer", "value": promotion.get("reviewer")},
                    {"label": "Proposal fingerprint", "value": promotion.get("proposal_fingerprint")},
                    {"label": "Identity fingerprint", "value": promotion.get("identity_fingerprint")},
                    {"label": "Identity classes", "value": len(snapshot.get("identity_classes", []))},
                ],
                details={"proposal_id": promotion.get("proposal_id"),
                         "supporting_notes": promotion.get("supporting_notes")},
            ))
        final_state = _state(op.get("status"), confirmed=op.get("status") == "CONFIRMED")
        timeline.append(self._node(
            kind="OPERATOR_RESOLUTION", state=final_state, entity_id=operator_id, entity_type="operator",
            detector="Operator Resolution", timestamp=op.get("updated_at"), confidence=op.get("confidence"),
            rule="X8 operator identity model", category="IDENTITY",
            reason=op.get("summary") or f"Operator is currently {op.get('status', 'unresolved').lower()}.",
            evidence=[{"label": "Status", "value": op.get("status")}, {"label": "Known entities", "value": len(entities)}],
        ))
        sources = sorted({e.get("source_operation") for e in evidence if e.get("source_operation")})
        cross = [{"operation_id": s, "reason": "Contributed operator attribution evidence."} for s in sources]
        return {
            "ok": True, "subject": {"id": operator_id, "type": "operator"},
            "state": final_state,
            "summary": op.get("summary") or "Operator discovery history assembled from existing identity evidence.",
            "timeline": self._dedupe_sort(timeline),
            "evidence_groups": self._groups(timeline),
            "walkback": {"hops": [], "status": "UNRESOLVED", "stop_reason": "Open an attributed treasury or creator to view its funding walkback."},
            "operator_history": self._dedupe_sort(timeline),
            "network_discovery": self._dedupe_sort(timeline),
            "cross_operation": cross,
            "attribution_outcome": None,
            "treasury_expansion": None,
            "operational_behaviour": None,
            "detection_reconciliation": None,
            "creator_activity": None,
            "entities": entities,
            "observed_since": op.get("first_seen"),
            "first_attribution": min((e.get("created_at") for e in evidence if e.get("created_at")), default=None),
        }

    def _operator_for_entities(self, conn: sqlite3.Connection, tables: set[str],
                               addresses: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if "operator_entities" not in tables or not addresses:
            return [], [], []
        seen: set[str] = set()
        history: list[dict[str, Any]] = []
        cross: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        for address in addresses:
            memberships = self._many(conn, tables, "operator_entities", "entity_address = ?", (address,), "added_at ASC", 20)
            for membership in memberships:
                op_id = membership.get("operator_id")
                if not op_id or op_id in seen:
                    continue
                seen.add(op_id)
                op_result = self._operator(conn, tables, op_id)
                item = {
                    "operator_id": op_id, "state": op_result.get("state"),
                    "summary": op_result.get("summary"),
                    "observed_since": op_result.get("observed_since"),
                    "first_attribution": op_result.get("first_attribution"),
                }
                history.append(item)
                cross.extend(op_result.get("cross_operation", []))
                nodes.append(self._node(
                    kind="OPERATOR_RESOLUTION", state=op_result.get("state", "UNRESOLVED"),
                    entity_id=op_id, entity_type="operator", detector="Operator Resolution",
                    timestamp=membership.get("added_at"), confidence=membership.get("confidence"),
                    rule="X8 operator identity model", category="IDENTITY",
                    reason=f"{membership.get('entity_type', 'Entity').replace('_', ' ').title()} belongs to this operator based on recorded identity evidence.",
                    evidence=[{"label": "Membership evidence count", "value": membership.get("evidence_count")}],
                    connected={"id": address, "type": membership.get("entity_type", "wallet").lower()},
                ))
        return history, cross, nodes

    @staticmethod
    def _dedupe_sort(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        result = []
        for node in nodes:
            key = (node.get("kind"), node.get("entity", {}).get("id"), node.get("timestamp"))
            if key not in seen:
                seen.add(key)
                result.append(node)
        phase = {
            "NETWORK_CANDIDATE": 0, "IDENTITY_EVALUATION": 1,
            "ATTRIBUTION_EVIDENCE": 2, "ANALYST_REVIEW": 3,
            "CANONICAL_PROMOTION": 4, "OPERATOR_RESOLUTION": 5,
        }
        return sorted(result, key=lambda n: (
            n.get("timestamp") is None, n.get("timestamp") or 0,
            phase.get(n.get("kind", ""), 3), n.get("kind", ""),
        ))

    @staticmethod
    def _groups(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        groups = {"IDENTITY": [], "SUPPORTING": [], "CONTEXT": []}
        for node in nodes:
            groups[node.get("category", "CONTEXT")].append(node)
        return groups

    @staticmethod
    def _overall_state(nodes: list[dict[str, Any]]) -> str:
        if not nodes:
            return "UNRESOLVED"
        # Discovery maturity is governed by attribution/identity evidence. A
        # confirmed market lifecycle event must not make unresolved ancestry
        # appear confirmed.
        decisive = [n for n in nodes if n.get("category") == "IDENTITY"]
        if not decisive:
            decisive = [n for n in nodes if n.get("category") == "SUPPORTING"] or nodes
        states = [n.get("state", "UNRESOLVED") for n in decisive]
        if states[-1] in {"REJECTED", "SUPERSEDED"}:
            return states[-1]
        return max(states, key=lambda s: _STATE_RANK.get(s, 0))

    @staticmethod
    def _attribution_reason(row: dict[str, Any]) -> str:
        reasons = _json(row.get("reasons_json"), [])
        if reasons:
            return str(reasons[0])
        if row.get("matched_treasury"):
            return "Existing ancestry evidence matched a known confirmed treasury."
        if row.get("matched_subprov"):
            return "Existing ancestry evidence matched a known confirmed sub-provisioner."
        return "Attribution remains provisional because no confirmed treasury match is recorded."

    @staticmethod
    def _subprov_reason(row: dict[str, Any]) -> str:
        # X26.6.1 — defensive: this row's underlying observations can never be
        # described as "supporting the sub-provisioner role" once the platform
        # has concluded (state REJECTED*) that they don't. The call site is
        # already gated to never invoke this for a rejected row, but this
        # function must not produce supportive wording even if called from
        # elsewhere in the future.
        if str(row.get("state") or "").upper().startswith("REJECTED"):
            reason = row.get("rejected_reason") or "known infrastructure, not genuine provisioning evidence"
            return f"This wallet was reviewed and rejected as a sub-provisioner ({reason}); its funding observations do not support the role."
        count = row.get("wrap_close_count") or row.get("creator_count") or 0
        if row.get("treasury"):
            return f"The recorded funding chain links this sub-provisioner upstream; {count} creator-funding observation(s) support the role."
        return f"{count} creator-funding observation(s) support the sub-provisioner role, but its treasury is unresolved."

    @staticmethod
    def _treasury_reason(confirmed: dict[str, Any] | None, review: dict[str, Any] | None) -> str:
        if confirmed:
            method = confirmed.get("method") or "existing resolution evidence"
            return f"This wallet is trusted because it is in the confirmed treasury registry via {str(method).replace('_', ' ')}."
        if review:
            return "This wallet is a treasury candidate supported by recorded review evidence; confirmation is still pending."
        return "The existing lineage stops at this upstream wallet, but no treasury confirmation is recorded."

    @staticmethod
    def _stop_reason(hops: list[dict[str, Any]], confirmed: dict[str, Any] | None,
                     subprov: dict[str, Any] | None, wrap: dict[str, Any] | None,
                     resolution: dict[str, Any] | None) -> str:
        if confirmed:
            return "Reached known treasury."
        if resolution and resolution.get("reason"):
            return str(resolution["reason"])
        if subprov and not (subprov.get("treasury") or subprov.get("immediate_funder")):
            return "Funding chain exhausted: the recorded sub-provisioner has no upstream treasury evidence."
        if wrap and not subprov:
            return "No historical sub-provisioner evidence beyond the creator funding transaction."
        if hops:
            return hops[-1].get("reason_stopping") or "Confidence too low to continue beyond the recorded evidence."
        return "No historical evidence."

    @staticmethod
    def _summary(kind: str, state: str, creator: str | None, subprov: str | None,
                 treasury: str | None, confirmed: bool) -> str:
        parts = []
        if creator:
            parts.append("creator identified")
        if subprov:
            parts.append("sub-provisioner resolved")
        if treasury:
            parts.append("confirmed treasury reached" if confirmed else "treasury candidate reached")
        if not parts:
            return "No existing discovery evidence was found for this entity."
        return f"{kind.replace('_', ' ').title()} discovery is {state.lower()}: " + ", ".join(parts) + "."

    def search(self, query: str, limit: int = 25) -> dict[str, Any]:
        q = (query or "").strip()
        limit = max(1, min(int(limit), 50))
        if not q or not os.path.exists(self.ops_db_path):
            return {"ok": True, "query": q, "results": [], "count": 0}
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        with self._connect(self.ops_db_path) as conn:
            tables = self._tables(conn)
            lookups = (
                ("token", "migrated_tokens", "mint", "mint = ?"),
                ("token", "wt_watchtower_launches", "mint", "mint = ?"),
                ("creator", "wt_watchtower_launches", "creator_wallet", "creator_wallet = ?"),
                ("creator", "wt_wrap_close_candidates", "creator", "creator = ?"),
                ("treasury", "wt_confirmed_treasuries", "treasury", "treasury = ?"),
                ("sub_provisioner", "wt_discovered_subprovs", "subprov", "subprov = ?"),
                ("operator", "operators", "operator_id", "operator_id = ?"),
                ("transaction", "wt_wrap_close_candidates", "tx_signature", "tx_signature = ?"),
                ("transaction", "migrated_tokens", "migration_tx", "migration_tx = ?"),
            )
            if {"wt_unknown_infrastructure_registry", "wt_attribution_outcomes"} <= tables:
                for seed in emerging_operator_seeds(conn):
                    if seed["terminal_entity"] == q:
                        seen.add(("emerging_candidate", q))
                        results.append({"id": q, "type": "emerging_candidate",
                                        "label": "Emerging Operator", "state": "EMERGING"})
                        break
            for kind, table, field, where in lookups:
                row = self._one(conn, tables, table, where, (q,))
                if row and (kind, str(row.get(field))) not in seen:
                    seen.add((kind, str(row.get(field))))
                    results.append({"id": row.get(field), "type": kind, "label": kind.replace("_", " ").title()})
            if "operators" in tables and len(results) < limit:
                try:
                    rows = conn.execute(
                        "SELECT operator_id, display_name, status FROM operators "
                        "WHERE display_name LIKE ? OR summary LIKE ? ORDER BY updated_at DESC LIMIT ?",
                        (f"%{q}%", f"%{q}%", limit - len(results)),
                    ).fetchall()
                    for row in rows:
                        key = ("operator", row["operator_id"])
                        if key not in seen:
                            seen.add(key)
                            results.append({"id": row["operator_id"], "type": "operator",
                                            "label": row["display_name"] or "Operator", "state": _state(row["status"])})
                except sqlite3.Error:
                    pass
        return {"ok": True, "query": q, "results": results[:limit], "count": min(len(results), limit)}

    def recent(self, limit: int = 20) -> dict[str, Any]:
        """Significance-separated intelligence streams; recency is per stream."""
        limit = max(1, min(int(limit), 50))
        streams: dict[str, list[dict[str, Any]]] = {
            "watchtower": [], "promotions": [], "emerging_operators": [],
            "reviews": [], "walkbacks": [], "discovery": [], "treasury_expansions": [],
        }
        if not os.path.exists(self.ops_db_path):
            return {"ok": True, "streams": streams, "events": [], "count": 0}
        with self._connect(self.ops_db_path) as conn:
            tables = self._tables(conn)
            if "wt_attribution_outcomes" in tables:
                def outcome_event(row: dict[str, Any]) -> dict[str, Any]:
                    outcome_type = row.get("outcome_type")
                    event_state = (
                        "EMERGING" if outcome_type == "UNKNOWN_INFRASTRUCTURE"
                        else "CONFIRMED" if outcome_type in {
                            "CANONICAL_OPERATOR_REACHED", "KNOWN_MULTI_TOKEN_CREATOR",
                            "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
                        }
                        else "UNRESOLVED"
                    )
                    return {
                        "timestamp": row.get("completed_at"),
                        "state": event_state,
                        "kind": outcome_type,
                        "message": row.get("stop_reason"),
                        "entity": {"id": row.get("mint"), "type": "token"},
                        "href": f"/discovery?entity={row.get('mint')}&type=token",
                    }
                streams["walkbacks"] = [outcome_event(row) for row in self._many(
                    conn, tables, "wt_attribution_outcomes", "1=1", (), "completed_at DESC", limit
                )]
                streams["watchtower"] = [outcome_event(row) for row in self._many(
                    conn, tables, "wt_attribution_outcomes",
                    "outcome_type = 'CANONICAL_OPERATOR_REACHED'", (), "completed_at DESC", limit
                )]
                # X20 replaces raw queue rows below with candidate growth events.
            if "wt_attribution_outcomes" not in tables and {"wt_watchtower_launches", "wt_confirmed_treasuries", "operator_entities", "operators"} <= tables:
                try:
                    rows = conn.execute(
                        "SELECT l.mint,l.treasury_wallet,COALESCE(l.create_time,l.recorded_at) timestamp,"
                        "o.operator_id,o.display_name FROM wt_watchtower_launches l "
                        "JOIN wt_confirmed_treasuries t ON t.treasury=l.treasury_wallet "
                        "JOIN operator_entities oe ON oe.entity_address=t.treasury AND oe.operator_id=? "
                        "JOIN operators o ON o.operator_id=oe.operator_id AND o.status='CONFIRMED' "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (WATCHTOWER_OPERATOR_ID, limit),
                    ).fetchall()
                    for row in rows:
                        streams["watchtower"].append({
                            "timestamp": row["timestamp"], "state": "CONFIRMED",
                            "kind": "WATCHTOWER_LAUNCH",
                            "message": "WATCHTOWER launch attributed to the canonical operator.",
                            "entity": {"id": row["mint"], "type": "token"},
                            "operator": {"id": row["operator_id"], "name": row["display_name"]},
                            "href": f"/discovery?entity={row['mint']}&type=token",
                        })
                except sqlite3.Error:
                    pass
            # Retrospective confirmed walkbacks are equally material and must
            # not disappear merely because no strict launch row exists.
            if "wt_attribution_outcomes" not in tables and {"wt_walkback_queue", "wt_confirmed_treasuries", "operator_entities", "operators"} <= tables:
                known_mints = {e["entity"]["id"] for e in streams["watchtower"]}
                try:
                    rows = conn.execute(
                        "SELECT q.mint,q.treasury,q.completed_at,o.operator_id,o.display_name "
                        "FROM wt_walkback_queue q "
                        "JOIN wt_confirmed_treasuries t ON t.treasury=q.treasury "
                        "JOIN operator_entities oe ON oe.entity_address=t.treasury AND oe.operator_id=? "
                        "JOIN operators o ON o.operator_id=oe.operator_id AND o.status='CONFIRMED' "
                        "WHERE q.status='complete' ORDER BY q.completed_at DESC LIMIT ?",
                        (WATCHTOWER_OPERATOR_ID, limit),
                    ).fetchall()
                    for row in rows:
                        if row["mint"] in known_mints:
                            continue
                        streams["watchtower"].append({
                            "timestamp": row["completed_at"], "state": "CONFIRMED",
                            "kind": "WATCHTOWER_LAUNCH",
                            "message": "WATCHTOWER launch confirmed by persisted walkback.",
                            "entity": {"id": row["mint"], "type": "token"},
                            "operator": {"id": row["operator_id"], "name": row["display_name"]},
                            "href": f"/discovery?entity={row['mint']}&type=token",
                        })
                except sqlite3.Error:
                    pass
                streams["watchtower"].sort(key=lambda e: e.get("timestamp") or 0, reverse=True)
                streams["watchtower"] = streams["watchtower"][:limit]
            for row in self._many(conn, tables, "wt_confirmed_treasuries", "1=1", (), "confirmed_at DESC", limit):
                streams["discovery"].append({"timestamp": row.get("confirmed_at"), "state": "CONFIRMED", "kind": "TREASURY_CONFIRMED",
                                              "message": "Treasury attribution confirmed.", "entity": {"id": row.get("treasury"), "type": "treasury"}})
            for row in self._many(conn, tables, "operator_reviews", "decision IN ('CONFIRMED','REJECTED')", (), "timestamp DESC", limit):
                if "operator_promotion_reviews" in tables and row.get("decision") == "CONFIRMED":
                    promoted = self._one(
                        conn, tables, "operator_promotion_reviews",
                        "canonical_operator_id = ? AND timestamp = ? AND decision = 'APPROVE'",
                        (row.get("operator_id"), row.get("timestamp")),
                    )
                    if promoted:
                        continue  # one governance event, not duplicate canonical review noise
                streams["reviews"].append({"timestamp": row.get("timestamp"), "state": _state(row.get("decision"), confirmed=row.get("decision") == "CONFIRMED"),
                                           "kind": "OPERATOR_REVIEW", "message": f"Operator {str(row.get('decision')).lower()} by analyst review.",
                                           "entity": {"id": row.get("operator_id"), "type": "operator"}})
            for row in self._many(conn, tables, "operator_promotion_reviews", "1=1", (), "timestamp DESC", limit):
                operator_id = row.get("canonical_operator_id")
                decision = row.get("decision")
                streams["promotions"].append({
                    "timestamp": row.get("timestamp"),
                    "state": "CONFIRMED" if decision == "APPROVE" else ("REJECTED" if decision == "REJECT" else "EMERGING"),
                    "kind": "OPERATOR_PROMOTION_REVIEW",
                    "message": f"Operator promotion {str(decision).lower()} by {row.get('reviewer') or 'analyst'}.",
                    "entity": {"id": operator_id or row.get("proposal_id"),
                               "type": "operator" if operator_id else "promotion_proposal"},
                })
            if "wt_attribution_outcomes" not in tables:
                for row in self._many(conn, tables, "wt_walkback_queue", "status = 'complete'", (), "completed_at DESC", limit):
                    streams["walkbacks"].append({"timestamp": row.get("completed_at"), "state": "CONFIRMED" if row.get("treasury") else "UNRESOLVED",
                                                 "kind": "WALKBACK_COMPLETED", "message": "Treasury walkback completed.",
                                                 "entity": {"id": row.get("mint"), "type": "token"}})

            # X20.9: surface treasury-expansion candidates separately from confirmed
            # WATCHTOWER events. Bounded to the same LINEAGE_GAP/UNKNOWN_INFRASTRUCTURE
            # candidates already fetched above (<= limit rows), so this never runs the
            # resolver over the full table.
            if "wt_attribution_outcomes" in tables:
                try:
                    from src.ops.treasury_expansion_resolver import TreasuryExpansionResolver
                    resolver = TreasuryExpansionResolver(self.ops_db_path, self.core_db_path)
                    candidate_rows = [
                        e for e in streams["walkbacks"]
                        if e.get("kind") in ("LINEAGE_GAP", "UNKNOWN_INFRASTRUCTURE")
                    ][:limit]
                    expansion_events: list[dict[str, Any]] = []
                    for row in candidate_rows:
                        mint = (row.get("entity") or {}).get("id")
                        if not mint:
                            continue
                        outcome_row = self._one(conn, tables, "wt_attribution_outcomes", "mint = ?", (mint,))
                        if not outcome_row:
                            continue
                        evidence = _json(outcome_row.get("evidence_json"), {})
                        treasuries = evidence.get("treasuries") or []
                        subprovs = evidence.get("subprovisioners") or []
                        report = resolver.evaluate(
                            subprovs[0] if subprovs else None,
                            treasuries[0] if treasuries else None,
                        )
                        if not report or not report.get("matched_evidence"):
                            continue
                        expansion_events.append({
                            "timestamp": outcome_row.get("completed_at"),
                            "state": "EMERGING",
                            "kind": "TREASURY_EXPANSION_CANDIDATE",
                            "message": (
                                f"{report['matched_signal_count']} structural "
                                f"{'match' if report['matched_signal_count'] == 1 else 'matches'} "
                                "with existing operator infrastructure. Treasury review required."
                            ),
                            "entity": {"id": treasuries[0] if treasuries else mint, "type": "treasury" if treasuries else "token"},
                            "href": f"/discovery?entity={mint}&type=token",
                        })
                    streams["treasury_expansions"] = expansion_events[:limit]
                except (OSError, sqlite3.Error, ValueError):
                    streams["treasury_expansions"] = []
        try:
            from src.ops.emerging_operator_service import EmergingOperatorService
            streams["emerging_operators"] = EmergingOperatorService(
                self.ops_db_path, self.core_db_path
            ).recent_events(limit)
        except (OSError, sqlite3.Error, ValueError):
            streams["emerging_operators"] = []
        # Backward-compatible aggregate, with one allocation per significance
        # stream before remaining items are filled by recency.
        events = [rows[0] for rows in streams.values() if rows]
        seen = {(e.get("kind"), (e.get("entity") or {}).get("id"), e.get("timestamp")) for e in events}
        remainder = [e for rows in streams.values() for e in rows
                     if (e.get("kind"), (e.get("entity") or {}).get("id"), e.get("timestamp")) not in seen]
        remainder.sort(key=lambda e: e.get("timestamp") or 0, reverse=True)
        events.extend(remainder[:max(0, limit - len(events))])
        events.sort(key=lambda e: e.get("timestamp") or 0, reverse=True)
        return {"ok": True, "streams": streams, "events": events[:limit], "count": min(len(events), limit)}
