"""Read-only operation-level intelligence assembled from persisted evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
import sqlite3
from statistics import mean, median
from typing import Any


def _ts(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
        return int(number) if number > 1_000_000_000 else None
    except (TypeError, ValueError):
        try:
            return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return None


def _summary(values: list[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return {"count": 0, "minimum": None, "median": None, "average": None, "maximum": None}
    return {
        "count": len(clean), "minimum": min(clean), "median": median(clean),
        "average": mean(clean), "maximum": max(clean),
    }


class OperationIntelligenceAssembler:
    """Join an operation family to existing operational and token evidence."""

    def __init__(self, ops_db_path: str, live_db_path: str) -> None:
        self.ops_db_path = ops_db_path
        self.live_db_path = live_db_path

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def build(self, family: dict[str, Any], all_families: list[dict[str, Any]],
              reconciliation: dict[str, Any]) -> dict[str, Any]:
        mints = sorted(set(family.get("launch_list") or []))
        members = sorted(set(family.get("member_wallets") or []))
        edges, sessions, watch_launches = self._operation_rows(mints, members)
        token_rows = self._token_rows(mints)
        timeline = self._timeline(family, edges, sessions, watch_launches, token_rows)
        performance = self._performance(mints, token_rows, watch_launches)
        behaviour = self._behaviour(family, edges, sessions, token_rows, watch_launches)
        infrastructure = self._infrastructure(family, edges, sessions, token_rows)
        from src.ops.operational_role import derive_operational_role
        operational_role = derive_operational_role(family, infrastructure)
        evidence = self._evidence(family, edges, sessions)
        peers = self._peers(family, all_families)
        ecosystem = self._ecosystem(family, all_families, reconciliation, peers)
        first = family.get("first_seen_at")
        last = family.get("last_material_activity_at")
        return {
            "overview": {
                "discovery_date": first, "first_seen_at": first,
                "last_activity_at": last,
                "operation_age_seconds": max(0, int(last) - int(first)) if first and last else None,
                "current_activity": "Active" if family.get("active_sessions") else "Observed",
                "known_mechanisms": family.get("funding_mechanisms") or [],
                "known_variants": family.get("observed_topology_variants") or [],
                "promotion_status": family.get("promotion_status"),
            },
            "timeline": timeline, "behaviour": behaviour,
            "infrastructure": infrastructure, "performance": performance,
            "evidence_audit": evidence, "comparison_peers": peers,
            "ecosystem_context": ecosystem,
            "operational_role": operational_role,
            "data_contract": {
                "read_only": True, "estimated_metrics": [],
                "unavailable_metrics": [key for key, value in performance.items() if value is None],
                "sources": ["operation registry", "wt_provisioning_edges",
                            "wt_active_subprov_sessions", "wt_watchtower_launches", "token_analysis"],
            },
        }

    def _operation_rows(self, mints: list[str], members: list[str]):
        edges: list[dict[str, Any]] = []
        sessions: list[dict[str, Any]] = []
        watch_launches: list[dict[str, Any]] = []
        try:
            with self._connect(self.ops_db_path) as conn:
                tables = self._tables(conn)
                if (members or mints) and "wt_provisioning_edges" in tables:
                    clauses, params = [], []
                    if members:
                        marks = ",".join("?" for _ in members)
                        clauses.append(f"from_wallet IN ({marks})")
                        clauses.append(f"to_wallet IN ({marks})")
                        params.extend(members); params.extend(members)
                    if mints:
                        marks = ",".join("?" for _ in mints)
                        clauses.append(f"source_mint IN ({marks})")
                        params.extend(mints)
                    edges = [dict(row) for row in conn.execute(
                        f"SELECT * FROM wt_provisioning_edges WHERE {' OR '.join(clauses)} ORDER BY first_observed_by_flex",
                        params,
                    )]
                if (members or mints) and "wt_active_subprov_sessions" in tables:
                    clauses, params = [], []
                    if members:
                        marks = ",".join("?" for _ in members)
                        clauses.append(f"subprov_wallet IN ({marks})")
                        params.extend(members)
                    session_columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(wt_active_subprov_sessions)")
                    }
                    if mints and "source_mint" in session_columns:
                        marks = ",".join("?" for _ in mints)
                        clauses.append(f"source_mint IN ({marks})")
                        params.extend(mints)
                    if clauses:
                        sessions = [dict(row) for row in conn.execute(
                            f"SELECT * FROM wt_active_subprov_sessions WHERE {' OR '.join(clauses)} ORDER BY funding_time",
                            params,
                        )]
                if mints and "wt_watchtower_launches" in tables:
                    marks = ",".join("?" for _ in mints)
                    watch_launches = [dict(row) for row in conn.execute(
                        f"SELECT * FROM wt_watchtower_launches WHERE mint IN ({marks}) ORDER BY create_time", mints
                    )]
        except (OSError, sqlite3.Error):
            pass
        return edges, sessions, watch_launches

    def _token_rows(self, mints: list[str]) -> list[dict[str, Any]]:
        if not mints:
            return []
        try:
            with self._connect(self.live_db_path) as conn:
                if "token_analysis" not in self._tables(conn):
                    return []
                marks = ",".join("?" for _ in mints)
                rows = [dict(row) for row in conn.execute(
                    f"SELECT * FROM token_analysis WHERE mint IN ({marks})", mints
                )]
        except (OSError, sqlite3.Error):
            return []
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            mint = row.get("mint")
            if mint and (_ts(row.get("analyzed_at")) or 0) >= (_ts(latest.get(mint, {}).get("analyzed_at")) or 0):
                latest[mint] = row
        return list(latest.values())

    @staticmethod
    def _timeline(family, edges, sessions, watch_launches, token_rows):
        events = []
        if family.get("first_seen_at"):
            events.append({"timestamp": family["first_seen_at"], "type": "OPERATION_FIRST_OBSERVED",
                           "label": "Operation first observed", "source": "operation registry"})
        seen_treasuries, seen_mechanisms, seen_clients = set(), set(), set()
        for session in sessions:
            timestamp = _ts(session.get("detected_at")) or _ts(session.get("funding_time"))
            treasury = session.get("treasury_wallet")
            mechanism = session.get("funding_mechanism")
            client = session.get("subprov_wallet")
            if treasury and treasury not in seen_treasuries:
                events.append({"timestamp": timestamp, "type": "TREASURY_DISCOVERED",
                               "label": f"Treasury relationship discovered: {treasury}",
                               "source": "wt_active_subprov_sessions"}); seen_treasuries.add(treasury)
            if client and client not in seen_clients:
                events.append({"timestamp": timestamp, "type": "CLIENT_DISCOVERED",
                               "label": f"Persistent client observed: {client}",
                               "source": "wt_active_subprov_sessions"}); seen_clients.add(client)
            if mechanism and mechanism not in seen_mechanisms:
                events.append({"timestamp": timestamp, "type": "MECHANISM_DISCOVERED",
                               "label": f"Funding mechanism observed: {mechanism}",
                               "source": "wt_active_subprov_sessions"}); seen_mechanisms.add(mechanism)
        for edge in edges:
            mechanism = edge.get("funding_mechanism")
            if mechanism and mechanism not in seen_mechanisms:
                events.append({"timestamp": _ts(edge.get("first_observed_by_flex")),
                               "type": "MECHANISM_DISCOVERED", "label": f"Funding mechanism observed: {mechanism}",
                               "source": "wt_provisioning_edges"}); seen_mechanisms.add(mechanism)
            if edge.get("edge_type") == "SUBPROV_TO_CREATOR" and edge.get("source_mint"):
                events.append({"timestamp": _ts(edge.get("first_observed_by_flex")),
                               "type": "LAUNCH_RELATIONSHIP_OBSERVED",
                               "label": f"Creator-funding relationship linked to launch: {edge['source_mint']}",
                               "source": "wt_provisioning_edges", "mint": edge["source_mint"]})
        for launch in watch_launches:
            events.append({"timestamp": _ts(launch.get("create_time")), "type": "LAUNCH_OBSERVED",
                           "label": f"Launch observed: {launch.get('mint')}", "source": "wt_watchtower_launches",
                           "mint": launch.get("mint")})
        # Every canonical launch member receives a launch-specific event even
        # when the Watchtower-only launch table has no row for it. The mint set
        # comes exclusively from family.launch_list; token_analysis supplies
        # detail and timestamp, never additional membership.
        watch_mints = {row.get("mint") for row in watch_launches}
        token_by_mint = {row.get("mint"): row for row in token_rows}
        for mint in family.get("launch_list") or []:
            if mint in watch_mints:
                continue
            token = token_by_mint.get(mint, {})
            events.append({"timestamp": _ts(token.get("created_at")),
                           "type": "LAUNCH_OBSERVED",
                           "label": f"Launch observed: {mint}",
                           "source": "token_analysis", "mint": mint})
        for event in family.get("growth_timeline") or []:
            events.append({"timestamp": event.get("timestamp"), "type": event.get("event_type"),
                           "label": event.get("label"), "source": "operation discovery",
                           "mint": event.get("source_mint")})
        events.append({"timestamp": family.get("last_material_activity_at"), "type": "CURRENT_PROJECTION",
                       "label": f"Current lifecycle projection: {family.get('lifecycle_state')}",
                       "source": "operation registry"})
        unique = {}
        for event in events:
            key = (event.get("timestamp"), event.get("type"), event.get("label"))
            unique[key] = event
        return sorted(unique.values(), key=lambda item: (item.get("timestamp") or 0, item.get("type") or ""))

    @staticmethod
    def _performance(mints, token_rows, watch_launches):
        by_mint = {row.get("mint"): row for row in token_rows}
        watch_by_mint = {row.get("mint"): row for row in watch_launches}
        migration_seconds, peaks, launches = [], [], []
        migrated = 0
        for mint in mints:
            row = by_mint.get(mint, {})
            watch = watch_by_mint.get(mint, {})
            created = _ts(row.get("created_at")) or _ts(watch.get("create_time"))
            migrated_at = _ts(row.get("migrated_at"))
            duration = watch.get("create_to_migration_secs")
            if duration is None and created and migrated_at and migrated_at >= created:
                duration = migrated_at - created
            is_migrated = bool(migrated_at or row.get("migration_tx") or str(row.get("lifecycle_stage") or "").lower() == "migrated")
            migrated += int(is_migrated)
            if duration is not None and float(duration) >= 0:
                migration_seconds.append(float(duration))
            peak = row.get("market_cap_highest")
            if peak is not None:
                peaks.append(float(peak))
            launches.append({"mint": mint, "created_at": created, "migrated": is_migrated,
                             "migration_seconds": duration, "peak_market_cap": peak,
                             "current_market_cap": row.get("market_cap_current"),
                             "lifecycle_stage": row.get("lifecycle_stage"),
                             "token_href": f"/token-intelligence?mint={mint}"})
        launches.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
        top = sorted((item for item in launches if item["peak_market_cap"] is not None),
                     key=lambda item: float(item["peak_market_cap"]), reverse=True)[:10]
        return {
            "total_launches": len(mints), "tokens_with_metrics": len(token_rows),
            "migrated": migrated, "failed": None,
            "migration_seconds": _summary(migration_seconds),
            "peak_market_cap": _summary(peaks), "win_rate": None,
            "success_buckets": None, "top_launches": top,
            "recent_launches": launches[:20], "launches": launches,
        }

    @staticmethod
    def _behaviour(family, edges, sessions, token_rows, watch_launches):
        # One launch contributes one cadence timestamp. Prefer token_analysis,
        # then fall back to the canonical launch record for the same mint.
        launch_times = {row.get("mint"): _ts(row.get("create_time")) for row in watch_launches}
        for row in token_rows:
            launch_times[row.get("mint")] = _ts(row.get("created_at")) or launch_times.get(row.get("mint"))
        times = sorted(value for value in launch_times.values() if value)
        intervals = [(right - left) / 3600 for left, right in zip(times, times[1:])]
        hours = Counter(datetime.fromtimestamp(value, timezone.utc).hour for value in times)
        mechanisms = Counter()
        for row in edges + sessions:
            if row.get("funding_mechanism"):
                mechanisms[row["funding_mechanism"]] += 1
        creators = [row.get("to_wallet") for row in edges if row.get("edge_type") == "SUBPROV_TO_CREATOR" and row.get("to_wallet")]
        treasuries = [row.get("treasury_wallet") for row in sessions if row.get("treasury_wallet")]
        return {
            "launch_cadence_hours": _summary(intervals),
            "launch_frequency_per_active_day": (len(times) / max(1, len({datetime.fromtimestamp(t, timezone.utc).date() for t in times}))) if times else None,
            "utc_hour_distribution": dict(sorted(hours.items())),
            "mechanism_distribution": dict(mechanisms),
            "treasury_reuse": {"relationships": len(treasuries), "unique": len(set(treasuries))},
            "client_reuse": {"relationships": len(sessions), "unique": len(set(row.get("subprov_wallet") for row in sessions if row.get("subprov_wallet")))},
            "creator_reuse": {"relationships": len(creators), "unique": len(set(creators))},
            "operational_changes": family.get("material_change_reasons") or [],
            "behaviour_drift": None,
        }

    @staticmethod
    def _infrastructure(family, edges, sessions, token_rows=None):
        token_labels = {}
        for token in token_rows or []:
            mint = token.get("mint")
            label = token.get("symbol") or token.get("token_symbol") or token.get("name")
            if mint and label:
                token_labels[mint] = f"{label} Token"
        paths, rpc_edges = [], []
        for edge in edges:
            path = {"from": edge.get("from_wallet"), "to": edge.get("to_wallet"),
                    "type": edge.get("edge_type"), "mechanism": edge.get("funding_mechanism"),
                    "signature": edge.get("funding_tx_signature"),
                    "source_mint": edge.get("source_mint"),
                    "launch_label": token_labels.get(edge.get("source_mint")),
                    "transaction_at": _ts(edge.get("funding_block_time")),
                    "observed_at": _ts(edge.get("first_observed_by_flex"))}
            paths.append(path)
            if path["signature"]:
                rpc_edges.append(path)
        connected_treasuries = list(dict.fromkeys(family.get("treasuries") or []))
        connected_set = set(connected_treasuries)
        sessions_by_client = defaultdict(list)
        for session in sessions:
            treasury = session.get("treasury_wallet")
            client = session.get("subprov_wallet")
            funded_at = _ts(session.get("funding_time"))
            if client and treasury in connected_set and funded_at:
                sessions_by_client[client].append((funded_at, treasury))
        for client_sessions in sessions_by_client.values():
            client_sessions.sort()

        # Each launch is linked only to the latest recorded upstream session
        # that began before its persisted creator-funding edge. This is a
        # temporal projection of recorded relationships, not ownership.
        launch_treasury: dict[str, str] = {}
        for edge in sorted(edges, key=lambda item: _ts(item.get("funding_block_time")) or 0):
            if edge.get("edge_type") != "SUBPROV_TO_CREATOR" or not edge.get("source_mint"):
                continue
            observed_at = _ts(edge.get("funding_block_time")) or _ts(edge.get("first_observed_by_flex"))
            if not observed_at:
                continue
            eligible = [item for item in sessions_by_client.get(edge.get("from_wallet"), []) if item[0] <= observed_at]
            if eligible:
                launch_treasury[str(edge["source_mint"])] = eligible[-1][1]
        treasury_counts = Counter(launch_treasury.values())
        launch_total = len(set(family.get("launch_list") or []))
        linked_total = sum(treasury_counts.values())
        launches_by_treasury = [
            {"treasury": treasury, "launch_count": treasury_counts.get(treasury, 0)}
            for treasury in connected_treasuries
        ]
        launches_by_treasury.sort(key=lambda item: item["launch_count"], reverse=True)
        if launch_total > linked_total:
            launches_by_treasury.append({"treasury": None, "launch_count": launch_total - linked_total})
        return {
            "treasuries": connected_treasuries, "persistent_clients": family.get("client_wallets") or [],
            "creators": family.get("unique_creators") or [], "known_relays": [],
            "funding_paths": paths, "mechanisms": family.get("funding_mechanisms") or [],
            "rpc_confirmed_edges": rpc_edges,
            "topology_variants": family.get("observed_topology_variants") or [],
            "session_count": len(sessions),
            "launches_by_treasury": launches_by_treasury,
            "launches_by_treasury_total": sum(item["launch_count"] for item in launches_by_treasury),
        }

    @staticmethod
    def _evidence(family, edges, sessions):
        categories = defaultdict(list)
        for item in family.get("supporting_evidence") or []:
            evidence_type = str(item.get("type") or "OTHER").upper()
            if "SESSION" in evidence_type: category = "session"
            elif "EDGE" in evidence_type or "TRANSACTION" in evidence_type: category = "transaction"
            elif "TOPOLOGY" in evidence_type: category = "topology"
            elif "IDENTITY" in evidence_type or "PROMOTION" in evidence_type: category = "identity"
            elif "RPC" in evidence_type: category = "rpc"
            else: category = "behaviour"
            categories[category].append(item)
        categories["session"].extend({"source": "wt_active_subprov_sessions", "detail": row} for row in sessions)
        categories["transaction"].extend({"source": "wt_provisioning_edges", "detail": row} for row in edges)
        categories["topology"].append({
            "source": "operation registry",
            "detail": {"dominant": family.get("dominant_topology"),
                       "variants": family.get("observed_topology_variants") or []},
        })
        return {
            "categories": dict(categories), "conflicts": family.get("contradictions") or [],
            "exclusions": family.get("exclusion_evidence") or [],
            "outstanding_questions": family.get("blocking_reasons") or [],
            "confidence": family.get("confidence"),
            "confidence_explanation": "Evidence coverage describes completeness; confirmation remains a separate governance decision.",
        }

    @staticmethod
    def _peers(family, all_families):
        peers = []
        own_treasuries = set(family.get("treasuries") or [])
        own_mechanisms = set(family.get("funding_mechanisms") or [])
        for other in all_families:
            if other["family_id"] == family["family_id"]:
                continue
            shared_treasuries = sorted(own_treasuries & set(other.get("treasuries") or []))
            shared_mechanisms = sorted(own_mechanisms & set(other.get("funding_mechanisms") or []))
            peers.append({
                "family_id": other["family_id"], "family_name": other["family_name"],
                "lifecycle_state": other["lifecycle_state"], "launches": other["launches"],
                "treasuries": len(other.get("treasuries") or []), "clients": len(other.get("client_wallets") or []),
                "significance": other["discovery_significance"]["score"],
                "evidence": other["evidence_completeness"]["score"],
                "maturity": other["operational_maturity"]["score"],
                "topology": other.get("dominant_topology"), "mechanisms": other.get("funding_mechanisms") or [],
                "shared_treasuries": shared_treasuries, "shared_mechanisms": shared_mechanisms,
                "profile_href": f"/intelligence/operations/{other['family_id']}",
            })
        return sorted(peers, key=lambda item: (len(item["shared_treasuries"]), len(item["shared_mechanisms"]), item["significance"]), reverse=True)

    @staticmethod
    def _ecosystem(family, all_families, reconciliation, peers):
        def rank(key, reverse=True):
            ordered = sorted(all_families, key=key, reverse=reverse)
            return next((index for index, item in enumerate(ordered, 1) if item["family_id"] == family["family_id"]), None)
        recent = lambda item: next((d["score"] for d in item["discovery_significance"]["dimensions"] if d["key"] == "recent_launch_activity"), 0)
        total = reconciliation.get("total_tokens") or 0
        return {
            "launch_share_percent": (100 * family["launches"] / total) if total else None,
            "relative_activity_score": recent(family),
            "discovery_rank": rank(lambda item: item.get("first_seen_at") or 2**63, reverse=False),
            "launch_rank": rank(lambda item: item.get("launches") or 0),
            "infrastructure_rank": rank(lambda item: len(item.get("treasuries") or []) + len(item.get("client_wallets") or [])),
            "growth_rank": rank(recent), "unknown_population": reconciliation.get("unknown_tokens"),
            "related_operations": [item for item in peers if item["shared_treasuries"] or item["shared_mechanisms"]][:10],
            "reconciliation": reconciliation,
        }
