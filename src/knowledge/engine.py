"""
Knowledge Layer — enrichment engine.

Single public function: enrich(entity_id) -> list[KnowledgeItem]

The engine:
  1. Opens OPS DB + Creator DB (read-only connections).
  2. Assembles an evidence dict for the entity from those two sources.
  3. Runs all registered rules against the evidence.
  4. Returns the resulting KnowledgeItem list.

No writes. No RPC. Target <50ms per call (warm cache + indexed queries).
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from src.knowledge.loader import lookup_address
from src.knowledge.models import KnowledgeItem
from src.knowledge.rules import REGISTRY, Evidence

_REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

OPS_DB_PATH     = os.environ.get("OPS_V2_DB_PATH",         os.path.join(_REPO, "database", "wt_ops_v2.db"))
CREATOR_DB_PATH = os.environ.get("PUMPSWAP_TOKENS_DB_PATH", os.path.join(_REPO, "pumpswap_tokens.db"))


def _ro_conn(path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection. Raises if file does not exist."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"DB not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _assemble_evidence(entity_id: str) -> Evidence:
    """
    Build the evidence dict for one entity (wallet address).

    Evidence keys:
      launch_count          int   — launches in wt_farm_launches where funder=entity_id
      funding_mode          str   — dominant mode across those launches
      known_address_entry   AddressEntry | None
    """
    evidence: Evidence = {
        "launch_count":        0,
        "funding_mode":        "UNKNOWN",
        "known_address_entry": lookup_address(entity_id),
    }

    # ── OPS DB ────────────────────────────────────────────────────────────────
    try:
        conn = _ro_conn(OPS_DB_PATH)
        try:
            # launch count
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM wt_farm_launches WHERE funder = ?",
                (entity_id,),
            ).fetchone()
            evidence["launch_count"] = row["n"] if row else 0

            # dominant funding mode — derived from wrap_close column
            # (funding_mode column does not exist in wt_farm_launches;
            #  wrap_close=1 → WRAP_CLOSE, wrap_close=0 → PLAIN_TRANSFER)
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN wrap_close THEN 1 ELSE 0 END) AS wc_count,
                    COUNT(*) AS total
                FROM wt_farm_launches
                WHERE funder = ?
                """,
                (entity_id,),
            ).fetchone()
            if row and row["total"]:
                wc = row["wc_count"] or 0
                total = row["total"]
                if wc > total / 2:
                    evidence["funding_mode"] = "WRAP_CLOSE"
                elif total - wc > total / 2:
                    evidence["funding_mode"] = "PLAIN_TRANSFER"
        finally:
            conn.close()
    except FileNotFoundError:
        pass   # ops DB absent in test environments; evidence stays default
    except Exception as exc:
        print(f"[KNOWLEDGE] engine OPS_DB error for {entity_id}: {exc}")

    return evidence


def enrich(entity_id: str) -> list[KnowledgeItem]:
    """
    Return all KnowledgeItems derivable for entity_id from current evidence.

    Never raises — returns empty list on any error.
    """
    if not entity_id or not entity_id.strip():
        return []
    try:
        evidence = _assemble_evidence(entity_id)
        return REGISTRY.apply_all(entity_id, evidence)
    except Exception as exc:
        print(f"[KNOWLEDGE] enrich({entity_id}) failed: {exc}")
        return []


def enrich_batch(entity_ids: list[str]) -> dict[str, list[KnowledgeItem]]:
    """
    Enrich multiple entities. Returns {entity_id: [KnowledgeItem, ...]}.

    Opens DB connections once per batch for efficiency.
    """
    results: dict[str, list[KnowledgeItem]] = {}
    if not entity_ids:
        return results

    # Build evidence for all entities in one DB pass.
    launch_counts:  dict[str, int] = {}
    funding_modes:  dict[str, str] = {}

    try:
        conn = _ro_conn(OPS_DB_PATH)
        try:
            placeholders = ",".join("?" * len(entity_ids))

            rows = conn.execute(
                f"SELECT funder, COUNT(*) AS n FROM wt_farm_launches "
                f"WHERE funder IN ({placeholders}) GROUP BY funder",
                entity_ids,
            ).fetchall()
            launch_counts = {r["funder"]: r["n"] for r in rows}

            rows = conn.execute(
                f"""
                SELECT funder,
                    SUM(CASE WHEN wrap_close THEN 1 ELSE 0 END) AS wc_count,
                    COUNT(*) AS total
                FROM wt_farm_launches
                WHERE funder IN ({placeholders})
                GROUP BY funder
                """,
                entity_ids,
            ).fetchall()
            for r in rows:
                funder = r["funder"]
                wc    = r["wc_count"] or 0
                total = r["total"] or 0
                if total > 0:
                    if wc > total / 2:
                        funding_modes[funder] = "WRAP_CLOSE"
                    elif total - wc > total / 2:
                        funding_modes[funder] = "PLAIN_TRANSFER"
        finally:
            conn.close()
    except (FileNotFoundError, Exception):
        pass

    for entity_id in entity_ids:
        evidence: Evidence = {
            "launch_count":        launch_counts.get(entity_id, 0),
            "funding_mode":        funding_modes.get(entity_id, "UNKNOWN"),
            "known_address_entry": lookup_address(entity_id),
        }
        results[entity_id] = REGISTRY.apply_all(entity_id, evidence)

    return results
