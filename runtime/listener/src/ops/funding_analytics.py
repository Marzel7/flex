"""Read-only derived funding analytics over retained FLEX evidence.

This module is intentionally not an authority, cache, projection writer, or
network identity layer.  It exposes direct analytical answers from the
canonical creator-funding facts and retained Walkback/provisioning evidence.
Callers own their read-only SQLite connections; this module performs SELECT
queries only and never creates schema or writes rows.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _limit(value: int) -> int:
    return max(1, min(int(value), 1_000))


# creator_funders has historical mixed SQLite timestamp representations.  The
# expression preserves epoch values and normalizes ISO-8601 values explicitly.
_FUNDING_EPOCH = """
CASE WHEN typeof(first_detected_at) IN ('integer', 'real')
     THEN CAST(first_detected_at AS INTEGER)
     ELSE unixepoch(first_detected_at)
END
"""


class FundingAnalytics:
    """Composable, non-authoritative read-only funding queries.

    ``main`` supplies creator_funders, token_analysis and CEX attribution.
    ``ops`` supplies retained Walkback, provisioning and operation evidence.
    Neither connection is modified by this class.
    """

    def __init__(self, main: sqlite3.Connection, ops: sqlite3.Connection | None = None):
        self.main = main
        self.ops = ops
        self.main.row_factory = sqlite3.Row
        if self.ops is not None:
            self.ops.row_factory = sqlite3.Row

    @staticmethod
    def _window(start_time: int | None, end_time: int | None) -> tuple[str, list[int]]:
        if start_time is None and end_time is None:
            return "", []
        if start_time is None or end_time is None:
            raise ValueError("start_time and end_time must be supplied together")
        if int(start_time) > int(end_time):
            raise ValueError("start_time must not be after end_time")
        return f" WHERE {_FUNDING_EPOCH} BETWEEN ? AND ?", [int(start_time), int(end_time)]

    def top_funders(self, *, start_time: int | None = None, end_time: int | None = None,
                    limit: int = 10) -> list[dict[str, Any]]:
        where, params = self._window(start_time, end_time)
        return _rows(self.main.execute(f"""
            SELECT funder_address, COUNT(DISTINCT creator_address) AS distinct_creators,
                   SUM(COALESCE(amount_sol, 0)) AS total_amount_sol,
                   MIN({_FUNDING_EPOCH}) AS first_funding_time,
                   MAX({_FUNDING_EPOCH}) AS last_funding_time
            FROM creator_funders{where}
            GROUP BY funder_address
            ORDER BY distinct_creators DESC, total_amount_sol DESC
            LIMIT ?
        """, [*params, _limit(limit)]))

    def top_creators(self, *, start_time: int | None = None, end_time: int | None = None,
                     limit: int = 10) -> list[dict[str, Any]]:
        clauses = ["earliest_tx_creator IS NOT NULL"]
        params: list[Any] = []
        if start_time is not None or end_time is not None:
            if start_time is None or end_time is None or int(start_time) > int(end_time):
                raise ValueError("valid start_time and end_time are required together")
            clauses.append("created_at BETWEEN ? AND ?")
            params.extend([int(start_time), int(end_time)])
        return _rows(self.main.execute(f"""
            SELECT earliest_tx_creator AS creator, COUNT(*) AS launch_count,
                   MIN(created_at) AS first_launch_time, MAX(created_at) AS last_launch_time
            FROM token_analysis WHERE {' AND '.join(clauses)}
            GROUP BY earliest_tx_creator ORDER BY launch_count DESC LIMIT ?
        """, [*params, _limit(limit)]))

    def funders_for_creator(self, creator: str) -> list[dict[str, Any]]:
        return _rows(self.main.execute(f"""
            SELECT creator_address, funder_address, amount_sol, first_detected_at,
                   {_FUNDING_EPOCH} AS funding_time, is_cex, cex_exchange, source_type
            FROM creator_funders WHERE creator_address=?
            ORDER BY funding_time DESC, amount_sol DESC
        """, (creator,)))

    def creators_for_funder(self, funder: str, *, start_time: int | None = None,
                            end_time: int | None = None) -> list[dict[str, Any]]:
        where, params = self._window(start_time, end_time)
        prefix = "WHERE funder_address=?"
        if where:
            prefix += f" AND {_FUNDING_EPOCH} BETWEEN ? AND ?"
        return _rows(self.main.execute(f"""
            SELECT creator_address, funder_address, amount_sol, first_detected_at,
                   {_FUNDING_EPOCH} AS funding_time, is_cex, cex_exchange, source_type
            FROM creator_funders {prefix} ORDER BY funding_time DESC, amount_sol DESC
        """, [funder, *params]))

    def shared_funders(self, *, minimum_creators: int = 2, start_time: int | None = None,
                       end_time: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where, params = self._window(start_time, end_time)
        return _rows(self.main.execute(f"""
            SELECT funder_address, COUNT(DISTINCT creator_address) AS distinct_creators,
                   SUM(COALESCE(amount_sol, 0)) AS total_amount_sol
            FROM creator_funders{where}
            GROUP BY funder_address HAVING COUNT(DISTINCT creator_address) >= ?
            ORDER BY distinct_creators DESC, total_amount_sol DESC LIMIT ?
        """, [*params, max(1, int(minimum_creators)), _limit(limit)]))

    def creators_with_multiple_funders(self, *, minimum_funders: int = 2,
                                       start_time: int | None = None, end_time: int | None = None,
                                       limit: int = 100) -> list[dict[str, Any]]:
        """Return creators with multiple distinct direct funding wallets."""
        where, params = self._window(start_time, end_time)
        return _rows(self.main.execute(f"""
            SELECT creator_address, COUNT(DISTINCT funder_address) AS distinct_funders,
                   SUM(COALESCE(amount_sol, 0)) AS total_amount_sol
            FROM creator_funders{where}
            GROUP BY creator_address HAVING COUNT(DISTINCT funder_address) >= ?
            ORDER BY distinct_funders DESC, total_amount_sol DESC LIMIT ?
        """, [*params, max(1, int(minimum_funders)), _limit(limit)]))

    def funding_amount_ranking(self, *, start_time: int | None = None,
                               end_time: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Rank retained direct funding facts by their stored amount."""
        where, params = self._window(start_time, end_time)
        return _rows(self.main.execute(f"""
            SELECT creator_address, funder_address, amount_sol, first_detected_at,
                   {_FUNDING_EPOCH} AS funding_time, is_cex, cex_exchange, source_type
            FROM creator_funders{where}
            ORDER BY amount_sol DESC, funding_time DESC LIMIT ?
        """, [*params, _limit(limit)]))

    def cex_funded_creators(self, *, start_time: int | None = None,
                            end_time: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where, params = self._window(start_time, end_time)
        clause = "WHERE (cf.is_cex=1 OR cw.cex_address IS NOT NULL)"
        if where:
            clause += f" AND {_FUNDING_EPOCH.replace('first_detected_at', 'cf.first_detected_at')} BETWEEN ? AND ?"
        return _rows(self.main.execute(f"""
            SELECT cf.creator_address, cf.funder_address, cf.amount_sol, cf.first_detected_at,
                   cf.is_cex, COALESCE(cf.cex_exchange, cw.exchange_name) AS exchange_name,
                   cf.source_type
            FROM creator_funders cf
            LEFT JOIN cex_wallets cw ON cw.cex_address=cf.funder_address AND cw.is_active=1
            {clause} ORDER BY cf.first_detected_at DESC LIMIT ?
        """, [*params, _limit(limit)]))

    def funding_activity(self, *, start_time: int, end_time: int, limit: int = 1_000) -> list[dict[str, Any]]:
        return _rows(self.main.execute(f"""
            SELECT creator_address, funder_address, amount_sol, first_detected_at,
                   {_FUNDING_EPOCH} AS funding_time, is_cex, cex_exchange, source_type
            FROM creator_funders WHERE {_FUNDING_EPOCH} BETWEEN ? AND ?
            ORDER BY funding_time DESC LIMIT ?
        """, (int(start_time), int(end_time), _limit(limit))))

    def funding_evidence(self, *, mint: str | None = None,
                         signature: str | None = None) -> list[dict[str, Any]]:
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        if bool(mint) == bool(signature):
            raise ValueError("supply exactly one of mint or signature")
        field, value = ("mint", mint) if mint else ("funder_sig", signature)
        return _rows(self.ops.execute(f"""
            SELECT mint, creator, funder_wallet, funder_amount_sol, funder_sig,
                   funder_slot, funder_block_time, funding_mechanism, status,
                   attribution_source
            FROM wt_walkback_queue WHERE {field}=?
        """, (value,)))

    def launch_funding_evidence(self, mint: str) -> dict[str, Any]:
        """Join provenance-separated funding evidence for one launch.

        ``walkback_selected`` is never inferred from ``creator_funders``.  The
        latter is separately acquired direct-funding evidence and is returned
        only for analytics/presentation.
        """
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        queue = self.ops.execute("SELECT * FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
        if queue is None:
            return {"mint": mint, "evidence_status": "NO_RETAINED_FUNDING_EVIDENCE"}
        queue = dict(queue); creator = queue.get("creator")
        launch_at = queue.get("create_anchor_block_time") or queue.get("funder_block_time") or queue.get("completed_at")
        edges = _rows(self.ops.execute("SELECT selection_status,mechanism,signature,amount_lamports FROM wt_walkback_edge_candidates WHERE mint=?", (mint,)))
        selected = [e for e in edges if e["selection_status"] == "SELECTED"]
        alternatives = [e for e in edges if e["selection_status"] == "ALTERNATIVE"]
        funders = self.funders_for_creator(creator) if creator else []
        for item in funders:
            item["funding_relation"] = ("BEFORE_LAUNCH" if launch_at and item.get("funding_time") and item["funding_time"] <= launch_at else "AFTER_LAUNCH" if launch_at and item.get("funding_time") else "TIME_UNKNOWN")
            item["previously_seen_before_launch"] = item["funding_relation"] == "BEFORE_LAUNCH"
            item["distinct_creators_funded"] = self.main.execute("SELECT COUNT(DISTINCT creator_address) FROM creator_funders WHERE funder_address=?", (item["funder_address"],)).fetchone()[0]
        roles = self.ops.execute("SELECT COUNT(*) FROM wt_walkback_transaction_roles WHERE mint=?", (mint,)).fetchone()[0] if self.ops.execute("SELECT 1 FROM sqlite_master WHERE name='wt_walkback_transaction_roles'").fetchone() else 0
        flows = self.ops.execute("SELECT COUNT(*) FROM wt_walkback_atomic_flows WHERE mint=?", (mint,)).fetchone()[0] if self.ops.execute("SELECT 1 FROM sqlite_master WHERE name='wt_walkback_atomic_flows'").fetchone() else 0
        provisioning = self.ops.execute("SELECT COUNT(*) FROM wt_provisioning_sessions WHERE source_mint=?", (mint,)).fetchone()[0]
        if selected: status = "STRICT_WALKBACK_FUNDER"
        elif queue.get("intelligence_outcome") == "LINEAGE_GAP" and funders: status = "LINEAGE_GAP_WITH_FUNDING"
        elif funders: status = "CREATOR_FUNDING_ONLY"
        elif roles or flows or provisioning or queue.get("subprov") or queue.get("treasury"): status = "DEEPER_EVIDENCE_ONLY"
        else: status = "NO_RETAINED_FUNDING_EVIDENCE"
        return {"mint": mint, "creator": creator, "launch_at": launch_at,
                "walkback_selected": selected, "walkback_outcome": queue.get("intelligence_outcome"),
                "candidate_edge_count": len(edges), "selected_edge_count": len(selected), "alternative_edge_count": len(alternatives),
                "creator_funding_retained": funders, "deeper_role_evidence_count": roles,
                "atomic_flow_count": flows, "provisioning_evidence_count": provisioning,
                "subprov": queue.get("subprov"), "treasury": queue.get("treasury"), "evidence_status": status}

    def operation_funding(self, operator_id: str) -> list[dict[str, Any]]:
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        return _rows(self.ops.execute("""
            SELECT m.operator_id, m.mint, q.creator, q.funder_wallet, q.funder_amount_sol,
                   q.funder_sig, q.funder_slot, q.funder_block_time, q.funding_mechanism
            FROM operator_launch_membership m
            LEFT JOIN wt_walkback_queue q USING(mint)
            WHERE m.operator_id=? ORDER BY q.funder_block_time DESC
        """, (operator_id,)))

    def potential_operation_funding(self, potential_operation_id: str) -> list[dict[str, Any]]:
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        return _rows(self.ops.execute("""
            SELECT p.potential_operation_id, p.evidence_key, p.evidence_type, p.state,
                   e.mint, e.candidate_parent AS funder_wallet, e.signature,
                   e.block_time, e.amount_lamports, e.mechanism, e.selection_status
            FROM potential_operation_evidence_association p
            LEFT JOIN wt_walkback_edge_candidates e ON e.evidence_key=p.evidence_key
            WHERE p.potential_operation_id=? ORDER BY e.block_time DESC
        """, (potential_operation_id,)))

    def provisioning_relationships(self, *, treasury: str | None = None,
                                   subprov: str | None = None, creator: str | None = None,
                                   limit: int = 100) -> list[dict[str, Any]]:
        """Return retained treasury/subprov/creator session facts, never inferred hops."""
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        predicates, params = [], []
        for column, value in (("treasury", treasury), ("subprov", subprov), ("creator", creator)):
            if value:
                predicates.append(f"{column}=?")
                params.append(value)
        if not predicates:
            raise ValueError("supply at least one topology identity")
        return _rows(self.ops.execute(f"""
            SELECT source_mint, treasury, subprov, creator,
                   treasury_to_subprov_block_time, subprov_to_creator_block_time,
                   treasury_to_subprov_amount_sol, subprov_to_creator_amount_sol,
                   treasury_to_subprov_mechanism, subprov_to_creator_mechanism
            FROM wt_provisioning_sessions WHERE {' AND '.join(predicates)}
            ORDER BY subprov_to_creator_block_time DESC LIMIT ?
        """, [*params, _limit(limit)]))

    def shared_funders_across_operations(self, *, minimum_operations: int = 2,
                                         limit: int = 100) -> list[dict[str, Any]]:
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        return _rows(self.ops.execute("""
            SELECT q.funder_wallet, COUNT(DISTINCT m.operator_id) AS distinct_operations,
                   COUNT(DISTINCT m.mint) AS distinct_mints
            FROM wt_walkback_queue q JOIN operator_launch_membership m USING(mint)
            WHERE q.funder_wallet IS NOT NULL
            GROUP BY q.funder_wallet
            HAVING COUNT(DISTINCT m.operator_id) >= ?
            ORDER BY distinct_operations DESC, distinct_mints DESC LIMIT ?
        """, (max(1, int(minimum_operations)), _limit(limit))))

    def funding_chain(self, mint: str) -> list[dict[str, Any]]:
        """Return current evidence-backed ordered funding facts for one mint.

        This deliberately presents only retained signatures/times and explicit
        provisioning session legs; historical first-leg exceptions are exposed
        separately by :meth:`legacy_chain_exceptions`.
        """
        if self.ops is None:
            raise ValueError("operations evidence connection is required")
        selected = _rows(self.ops.execute("""
            SELECT mint, candidate_parent AS source_wallet, wallet AS destination_wallet,
                   signature, block_time, amount_lamports, mechanism, hop_depth,
                   selection_status, evidence_strength
            FROM wt_walkback_edge_candidates
            WHERE mint=? AND selection_status='SELECTED'
            ORDER BY block_time, instruction_index, inner_instruction_index
        """, (mint,)))
        sessions = _rows(self.ops.execute("""
            SELECT source_mint, treasury, subprov, creator,
                   treasury_to_subprov_block_time, subprov_to_creator_block_time,
                   treasury_to_subprov_amount_sol, subprov_to_creator_amount_sol,
                   treasury_to_subprov_mechanism, subprov_to_creator_mechanism
            FROM wt_provisioning_sessions WHERE source_mint=?
        """, (mint,)))
        return [{"kind": "selected_edge", **row} for row in selected] + [
            {"kind": "provisioning_session", **row} for row in sessions
        ]

    def legacy_chain_exceptions(self, *, bridge_funder: str | None = None,
                                target_creator: str | None = None) -> list[dict[str, Any]]:
        """Read the bounded migrated historical first-leg exceptions only.

        The returned ``legacy_*`` fields remain explicitly labelled; current
        direct-funder facts are included separately and never overwritten.
        """
        if not bridge_funder and not target_creator:
            raise ValueError("supply bridge_funder or target_creator")
        predicates, params = [], []
        if bridge_funder:
            predicates.append("e.bridge_funder=?"); params.append(bridge_funder)
        if target_creator:
            predicates.append("a.target_creator=?"); params.append(target_creator)
        return _rows(self.main.execute(f"""
            SELECT e.evidence_id, e.source_creator, e.bridge_funder,
                   e.amount_sol AS legacy_source_to_bridge_amount_sol,
                   e.block_time AS legacy_source_block_time, e.confidence AS legacy_confidence,
                   e.chain_type, e.provenance, a.target_creator,
                   a.bridge_to_target_amount_sol AS legacy_bridge_to_target_amount_sol,
                   cf.amount_sol AS retained_bridge_to_target_amount_sol,
                   cf.first_detected_at AS retained_second_leg_time
            FROM funding_chain_legacy_first_leg_evidence e
            JOIN funding_chain_legacy_target_association a USING(evidence_id)
            LEFT JOIN creator_funders cf ON cf.creator_address=a.target_creator
              AND cf.funder_address=e.bridge_funder
            WHERE {' AND '.join(predicates)}
            ORDER BY e.block_time, a.target_creator
        """, params))


__all__ = ["FundingAnalytics"]
