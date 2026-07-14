"""X21E — Operational Behaviour Intelligence: read-only composition of
persisted provisioning facts into an analyst-facing "how did this behave"
narrative, distinct from "who is this" (attribution/canonical identity).

This module performs NO detection, NO RPC, NO scoring, NO writes, and NEVER
creates or implies attribution/promotion. Every field it returns is either:
  - a Fact: a value read directly from an already-persisted table/column, or
  - explicitly labelled "not yet captured" / "not observed" when the
    supporting table has no matching row.

It NEVER synthesizes a composite behavioural label (e.g. a sprint brief
example, "Token-specific infrastructure") from multiple underlying facts —
each fact is surfaced on its own, so the analyst draws any composite
conclusion themselves. It NEVER renders "fresh wallet" claims (no wallet-
genesis/age data is persisted anywhere in this platform by design — see
src/ops/provisioning_edges.py's own docstring) and NEVER outputs a
probability/percentage/confidence label for behavioural similarity — only
"Observed" / "Not observed" / "Not yet available", per the same
Facts-vs-Opinions discipline established in the X21 architecture doc.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


class OperationalBehaviourService:
    """Composes Part 1-4 of the Discovery Operational Behaviour card for one
    entity (a token/mint, or directly a treasury/subprov/creator address).
    Read-only across both databases, following the same dual-connection
    pattern as TreasuryExpansionResolver."""

    def __init__(self, ops_db_path: str, core_db_path: str) -> None:
        self.ops_db_path = ops_db_path
        self.core_db_path = core_db_path

    def build(
        self, *, source_mint: Optional[str] = None,
        treasury: Optional[str] = None, subprov: Optional[str] = None,
        creator: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Build the full behaviour report for whichever addresses are known.
        Returns None only if NOTHING is known at all (no mint, no addresses) —
        otherwise always returns a report, with individual sections honestly
        reporting "not yet captured" where no data exists."""
        if not source_mint and not treasury and not subprov and not creator:
            return None

        ops_conn = _connect(self.ops_db_path)
        try:
            ops_tables = {r[0] for r in ops_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

            session = self._find_session(ops_conn, ops_tables, source_mint, treasury, subprov, creator)
            if session:
                treasury = treasury or session.get("treasury")
                subprov = subprov or session.get("subprov")
                creator = creator or session.get("creator")

            edges = self._find_edges(ops_conn, ops_tables, treasury, subprov, creator)
            subprov_facts = self._subprov_facts(ops_conn, ops_tables, subprov)
            treasury_review = self._treasury_review_facts(ops_conn, ops_tables, treasury)
        finally:
            ops_conn.close()

        core_conn = _connect(self.core_db_path) if os.path.exists(self.core_db_path) else None
        try:
            core_tables = {r[0] for r in core_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()} if core_conn else set()
            hub_facts = self._hub_facts(core_conn, core_tables, treasury, subprov)
        finally:
            if core_conn is not None:
                core_conn.close()

        behaviour_summary = self._build_behaviour_summary(session, edges, subprov_facts)
        timing = self._build_timing(session)
        infrastructure_pattern = self._build_infrastructure_pattern(
            edges, subprov_facts, treasury_review, hub_facts
        )
        consistency = self._build_consistency(edges, subprov_facts, treasury_review, hub_facts, timing)
        missing_evidence = self._build_missing_evidence(
            session, edges, subprov_facts, treasury_review, hub_facts, timing
        )

        return {
            "behaviour_summary": behaviour_summary,
            "timing": timing,
            "infrastructure_pattern": infrastructure_pattern,
            "operational_consistency": consistency,
            "missing_evidence": missing_evidence,
            "entities": {"treasury": treasury, "subprov": subprov, "creator": creator, "source_mint": source_mint},
        }

    # ── raw fact lookups ──────────────────────────────────────────────────

    def _find_session(
        self, conn: sqlite3.Connection, tables: set[str],
        source_mint: Optional[str], treasury: Optional[str],
        subprov: Optional[str], creator: Optional[str],
    ) -> Optional[dict[str, Any]]:
        if "wt_provisioning_sessions" not in tables:
            return None
        if source_mint:
            row = conn.execute(
                "SELECT * FROM wt_provisioning_sessions WHERE source_mint=?", (source_mint,)
            ).fetchone()
            if row:
                return dict(row)
        for addr, col in ((treasury, "treasury"), (subprov, "subprov"), (creator, "creator")):
            if not addr:
                continue
            row = conn.execute(
                f"SELECT * FROM wt_provisioning_sessions WHERE {col}=? ORDER BY recorded_at DESC LIMIT 1",
                (addr,),
            ).fetchone()
            if row:
                return dict(row)
        return None

    def _find_edges(
        self, conn: sqlite3.Connection, tables: set[str],
        treasury: Optional[str], subprov: Optional[str], creator: Optional[str],
    ) -> dict[str, Optional[dict[str, Any]]]:
        result: dict[str, Optional[dict[str, Any]]] = {
            "treasury_to_subprov": None, "subprov_to_creator": None,
        }
        if "wt_provisioning_edges" not in tables:
            return result
        if treasury and subprov:
            row = conn.execute(
                "SELECT * FROM wt_provisioning_edges WHERE edge_type='TREASURY_TO_SUBPROV' "
                "AND from_wallet=? AND to_wallet=?", (treasury, subprov),
            ).fetchone()
            result["treasury_to_subprov"] = dict(row) if row else None
        if subprov and creator:
            row = conn.execute(
                "SELECT * FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' "
                "AND from_wallet=? AND to_wallet=?", (subprov, creator),
            ).fetchone()
            result["subprov_to_creator"] = dict(row) if row else None
        return result

    def _subprov_facts(
        self, conn: sqlite3.Connection, tables: set[str], subprov: Optional[str]
    ) -> Optional[dict[str, Any]]:
        if not subprov or "wt_discovered_subprovs" not in tables:
            return None
        row = conn.execute(
            "SELECT subprov, creator_count, treasury, treasury_known, funding_mechanism, "
            "wrap_close_count FROM wt_discovered_subprovs WHERE subprov=?", (subprov,)
        ).fetchone()
        return dict(row) if row else None

    def _treasury_review_facts(
        self, conn: sqlite3.Connection, tables: set[str], treasury: Optional[str]
    ) -> Optional[dict[str, Any]]:
        if not treasury or "wt_treasury_review" not in tables:
            return None
        row = conn.execute(
            "SELECT treasury, distinct_subprovs, distinct_creators, status "
            "FROM wt_treasury_review WHERE treasury=?", (treasury,)
        ).fetchone()
        return dict(row) if row else None

    def _hub_facts(
        self, conn: Optional[sqlite3.Connection], tables: set[str],
        treasury: Optional[str], subprov: Optional[str],
    ) -> dict[str, Any]:
        result = {"known_operator_hub": None, "provisioning_hub": None}
        if conn is None:
            return result
        for addr in (a for a in (treasury, subprov) if a):
            if "wt_known_operator_hubs" in tables and result["known_operator_hub"] is None:
                row = conn.execute(
                    "SELECT hub_wallet, operator_identity FROM wt_known_operator_hubs WHERE hub_wallet=?",
                    (addr,),
                ).fetchone()
                if row:
                    result["known_operator_hub"] = dict(row)
            if "wt_provisioning_hubs" in tables and result["provisioning_hub"] is None:
                row = conn.execute(
                    "SELECT hub_address, status FROM wt_provisioning_hubs WHERE hub_address=? AND status='CONFIRMED'",
                    (addr,),
                ).fetchone()
                if row:
                    result["provisioning_hub"] = dict(row)
        return result

    # ── Part 1: Behaviour Summary ─────────────────────────────────────────

    def _build_behaviour_summary(
        self, session: Optional[dict[str, Any]], edges: dict[str, Optional[dict[str, Any]]],
        subprov_facts: Optional[dict[str, Any]],
    ) -> list[str]:
        """Plain factual statements, each independently true and citable.
        Never a synthesized composite claim."""
        statements: list[str] = []
        t_to_s = edges.get("treasury_to_subprov")
        s_to_c = edges.get("subprov_to_creator")

        if s_to_c and t_to_s:
            t_bt = t_to_s.get("funding_block_time")
            s_bt = s_to_c.get("funding_block_time")
            if t_bt is not None and s_bt is not None and s_bt >= t_bt:
                statements.append("Creator funded after sub-provisioner (observed order, per persisted block times)")
        if s_to_c and s_to_c.get("funding_mechanism"):
            statements.append(f"Sub-provisioner funded creator via {s_to_c['funding_mechanism']}")
        if t_to_s and t_to_s.get("funding_mechanism"):
            statements.append(f"Treasury funded sub-provisioner via {t_to_s['funding_mechanism']}")
        if subprov_facts is not None:
            count = subprov_facts.get("creator_count")
            if count is not None:
                statements.append(
                    f"Sub-provisioner has funded {count} creator{'s' if count != 1 else ''} (per wt_discovered_subprovs)"
                )
        if session is not None:
            statements.append("Walkback completed successfully (provisioning session recorded)")
        return statements

    # ── timing (X21B sessions only) ──────────────────────────────────────

    def _build_timing(self, session: Optional[dict[str, Any]]) -> dict[str, Any]:
        if session is None:
            return {"available": False}
        fields = (
            ("treasury_to_subprov_latency_seconds", "Treasury → Sub-Provisioner"),
            ("subprov_to_creator_latency_seconds", "Sub-Provisioner → Creator"),
            ("creator_to_launch_latency_seconds", "Creator → Launch"),
        )
        observed = [
            {"stage": label, "seconds": session[key]}
            for key, label in fields if session.get(key) is not None
        ]
        return {"available": bool(observed), "observations": observed}

    # ── Part 2: Infrastructure Pattern ───────────────────────────────────

    def _build_infrastructure_pattern(
        self, edges: dict[str, Optional[dict[str, Any]]], subprov_facts: Optional[dict[str, Any]],
        treasury_review: Optional[dict[str, Any]], hub_facts: dict[str, Any],
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []

        if subprov_facts is not None:
            count = subprov_facts.get("creator_count")
            if count is not None:
                patterns.append({
                    "label": f"Sub-provisioner funded {count} creator{'s' if count != 1 else ''}",
                    "source": "wt_discovered_subprovs.creator_count",
                })

        s_to_c = edges.get("subprov_to_creator")
        if s_to_c is not None:
            obs = s_to_c.get("observation_count")
            if obs == 1:
                patterns.append({
                    "label": "First time this exact sub-provisioner→creator funding path was observed",
                    "source": "wt_provisioning_edges.observation_count",
                })
            elif obs and obs > 1:
                patterns.append({
                    "label": f"This sub-provisioner→creator funding path observed {obs} times",
                    "source": "wt_provisioning_edges.observation_count",
                })
            if s_to_c.get("funding_mechanism") == "WSOL_WRAP_CLOSE":
                patterns.append({"label": "Wrap-close creator funding", "source": "wt_provisioning_edges.funding_mechanism"})

        if treasury_review is not None:
            dc = treasury_review.get("distinct_creators")
            ds = treasury_review.get("distinct_subprovs")
            if dc is not None and dc >= 2:
                patterns.append({
                    "label": f"Treasury (review lead) linked to {dc} distinct creators",
                    "source": "wt_treasury_review.distinct_creators",
                })
            if ds is not None and ds >= 2:
                patterns.append({
                    "label": f"Treasury (review lead) linked to {ds} distinct sub-provisioners",
                    "source": "wt_treasury_review.distinct_subprovs",
                })

        if hub_facts.get("known_operator_hub"):
            patterns.append({
                "label": f"Known provisioning hub ({hub_facts['known_operator_hub']['operator_identity']})",
                "source": "wt_known_operator_hubs",
            })
        if hub_facts.get("provisioning_hub"):
            patterns.append({"label": "Confirmed provisioning hub address", "source": "wt_provisioning_hubs"})

        return patterns

    # ── Part 3: Operational Consistency (Observed/Not observed only) ─────

    def _build_consistency(
        self, edges: dict[str, Optional[dict[str, Any]]], subprov_facts: Optional[dict[str, Any]],
        treasury_review: Optional[dict[str, Any]], hub_facts: dict[str, Any], timing: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Never a percentage or probability — each signal is a plain
        Observed / Not observed / Not yet available fact."""
        def _status(condition: Optional[bool]) -> str:
            if condition is None:
                return "Not yet available"
            return "Observed" if condition else "Not observed"

        infra_reuse = None
        if hub_facts.get("known_operator_hub") is not None or hub_facts.get("provisioning_hub") is not None:
            infra_reuse = bool(hub_facts.get("known_operator_hub") or hub_facts.get("provisioning_hub"))

        funding_structure = None
        s_to_c = edges.get("subprov_to_creator")
        if s_to_c is not None:
            funding_structure = s_to_c.get("funding_mechanism") == "WSOL_WRAP_CLOSE"

        repeated_treasury = None
        if treasury_review is not None:
            dc = treasury_review.get("distinct_creators")
            repeated_treasury = dc is not None and dc >= 2

        provisioning_sequence = None
        if edges.get("treasury_to_subprov") and edges.get("subprov_to_creator"):
            provisioning_sequence = True
        elif edges.get("subprov_to_creator") or edges.get("treasury_to_subprov"):
            provisioning_sequence = False

        return [
            {"signal": "Infrastructure reuse", "status": _status(infra_reuse)},
            {"signal": "Creator funding structure (wrap-close)", "status": _status(funding_structure)},
            {"signal": "Repeated treasury", "status": _status(repeated_treasury)},
            {"signal": "Full provisioning sequence recorded", "status": _status(provisioning_sequence)},
            {"signal": "Observed timing", "status": "Observed" if timing.get("available") else "Not yet available"},
        ]

    # ── Part 4: Missing Evidence ──────────────────────────────────────────

    def _build_missing_evidence(
        self, session: Optional[dict[str, Any]], edges: dict[str, Optional[dict[str, Any]]],
        subprov_facts: Optional[dict[str, Any]], treasury_review: Optional[dict[str, Any]],
        hub_facts: dict[str, Any], timing: dict[str, Any],
    ) -> list[str]:
        missing: list[str] = []
        if treasury_review is None or (treasury_review.get("distinct_creators") or 0) < 2:
            missing.append("Repeated treasury (multiple creators funded by the same treasury)")
        s_to_c = edges.get("subprov_to_creator")
        if not s_to_c or (s_to_c.get("observation_count") or 0) < 2:
            missing.append("Repeated provisioning edges (this funding path observed more than once)")
        if not timing.get("available"):
            missing.append("Observed timing history")
        if subprov_facts is None or (subprov_facts.get("creator_count") or 0) < 2:
            missing.append("Multiple launches from this sub-provisioner")
        if not hub_facts.get("known_operator_hub") and not hub_facts.get("provisioning_hub"):
            missing.append("Provisioning hub reuse")
        return missing
