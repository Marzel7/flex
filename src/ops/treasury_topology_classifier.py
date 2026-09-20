"""Conservative retained-evidence classifier for unknown WATCHTOWER treasuries.

This module does not scan the network and does not mutate attribution state.
It establishes whether a wallet repeatedly occupies the complete retained route

    treasury -> single-use subprov -> wrap-close/fan-out -> creator -> CREATE

across distinct launches.  Promotion is a separate explicit call through
``confirm_topology_candidate`` so discovery can always be run as a dry-run.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Any, Callable, Iterable


WRAP_CLOSE_TYPES = frozenset({"createAccountWithSeed", "initializeAccount", "closeAccount"})


@dataclass(frozen=True)
class TopologyThresholds:
    min_chains: int = 5
    min_amount_consistency: float = 0.80
    min_wrap_amount_sol: float = 0.01


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _instruction_types(value: Any) -> set[str]:
    result: set[str] = set()
    for item in _json_list(value):
        if isinstance(item, dict) and item.get("type"):
            result.add(str(item["type"]))
    return result


def _is_excluded(conn, wallet: str, *, infrastructure_check: Callable[[str], bool] | None) -> str | None:
    tables = _tables(conn)
    if "wt_confirmed_treasuries" in tables and conn.execute(
        "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=? LIMIT 1", (wallet,)
    ).fetchone():
        return "already_confirmed"
    if "wt_known_spam_wallets" in tables and conn.execute(
        "SELECT 1 FROM wt_known_spam_wallets WHERE wallet=? LIMIT 1", (wallet,)
    ).fetchone():
        return "known_spam"
    if "wt_discovered_subprovs" in tables and conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs WHERE subprov=? "
        "AND COALESCE(state,'') NOT LIKE 'REJECTED%' LIMIT 1", (wallet,)
    ).fetchone():
        return "known_subprov"
    if infrastructure_check and infrastructure_check(wallet):
        return "known_infrastructure"
    return None


def classify_unknown_treasury(
    conn,
    wallet: str,
    *,
    thresholds: TopologyThresholds = TopologyThresholds(),
    infrastructure_check: Callable[[str], bool] | None = None,
    _evidence_rows: list[tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    """Return an evidence-grade decision for one wallet; never writes.

    Every admitted chain must have canonical provisioning edges, a valid CREATE
    anchor for the same mint/creator, the exact three-instruction WSOL close
    shape, and a second signer distinct from the SubProv (the fan-out signer).
    A SubProv is rejected if it appears against more than one mint or creator.
    """
    required = {"wt_provisioning_edges", "wt_walkback_queue", "wt_walkback_transaction_roles"}
    missing = sorted(required - _tables(conn))
    if missing:
        return {"wallet": wallet, "verdict": "INSUFFICIENT_SCHEMA", "missing_tables": missing}

    excluded = _is_excluded(conn, wallet, infrastructure_check=infrastructure_check)
    if excluded:
        return {"wallet": wallet, "verdict": "REJECTED", "reason": excluded, "chains": []}
    if infrastructure_check is None:
        return {
            "wallet": wallet, "verdict": "EXCLUSION_CHECK_REQUIRED",
            "reason": "canonical infrastructure exclusion callback is required", "chains": [],
        }

    rows = _evidence_rows if _evidence_rows is not None else conn.execute(
        "WITH subprov_uses AS ("
        " SELECT from_wallet,COUNT(DISTINCT COALESCE(source_mint,'')) AS mint_uses,"
        " COUNT(DISTINCT to_wallet) AS creator_uses "
        " FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' GROUP BY from_wallet"
        ") "
        "SELECT t.to_wallet AS subprov,c.to_wallet AS creator,c.source_mint AS mint,"
        "c.funding_amount_sol AS wrap_amount_sol,c.funding_tx_signature AS wrap_signature,"
        "q.create_anchor_signature,q.create_anchor_audit_state,"
        "r.signers_json,r.outer_shape_json,u.mint_uses,u.creator_uses,"
        "t.funding_tx_signature,t.funding_amount_sol "
        "FROM wt_provisioning_edges t "
        "JOIN wt_provisioning_edges c ON c.edge_type='SUBPROV_TO_CREATOR' "
        " AND c.from_wallet=t.to_wallet "
        "JOIN subprov_uses u ON u.from_wallet=t.to_wallet "
        "JOIN wt_walkback_queue q ON q.mint=c.source_mint AND q.creator=c.to_wallet "
        "JOIN wt_walkback_transaction_roles r ON r.mint=c.source_mint "
        " AND r.signature=c.funding_tx_signature "
        "WHERE t.edge_type='TREASURY_TO_SUBPROV' AND t.from_wallet=? "
        "ORDER BY c.source_mint,c.to_wallet,t.to_wallet",
        (wallet,),
    ).fetchall()

    admitted: list[dict[str, Any]] = []
    rejected_counts: Counter[str] = Counter()
    for row in rows:
        subprov, creator, mint = row[0], row[1], row[2]
        amount, wrap_sig = row[3], row[4]
        anchor_sig, anchor_state = row[5], row[6]
        signers = [str(v) for v in _json_list(row[7])]
        instruction_types = _instruction_types(row[8])

        if not mint or not creator or not subprov or not wrap_sig:
            rejected_counts["incomplete_route"] += 1
            continue
        if not anchor_sig or anchor_state != "VALID":
            rejected_counts["create_anchor_not_valid"] += 1
            continue
        if not WRAP_CLOSE_TYPES.issubset(instruction_types):
            rejected_counts["wrap_close_shape_missing"] += 1
            continue
        fanout_signers = sorted({s for s in signers if s and s != subprov})
        if subprov not in signers or not fanout_signers:
            rejected_counts["fanout_cosigner_missing"] += 1
            continue
        if int(row[9] or 0) != 1 or int(row[10] or 0) != 1:
            rejected_counts["subprov_not_single_use"] += 1
            continue
        if amount is None or float(amount) < thresholds.min_wrap_amount_sol:
            rejected_counts["dust_or_missing_amount"] += 1
            continue
        admitted.append({
            "mint": mint,
            "creator": creator,
            "subprov": subprov,
            "fanout_signers": fanout_signers,
            "wrap_signature": wrap_sig,
            "create_signature": anchor_sig,
            "wrap_amount_sol": round(float(amount), 9),
            "treasury_funding_signature": row[11],
            "treasury_funding_amount_sol": (
                round(float(row[12]), 9) if row[12] is not None else None
            ),
        })

    # Multiple retained role projections for one signature must not inflate a cohort.
    unique = {(c["mint"], c["subprov"], c["creator"], c["wrap_signature"]): c for c in admitted}
    chains = list(unique.values())
    amounts = Counter(round(c["wrap_amount_sol"], 6) for c in chains)
    dominant_amount, dominant_count = amounts.most_common(1)[0] if amounts else (None, 0)
    consistency = dominant_count / len(chains) if chains else 0.0
    distinct_mints = len({c["mint"] for c in chains})
    distinct_subprovs = len({c["subprov"] for c in chains})
    distinct_creators = len({c["creator"] for c in chains})

    qualifies = (
        distinct_mints >= thresholds.min_chains
        and distinct_subprovs >= thresholds.min_chains
        and distinct_creators >= thresholds.min_chains
        and consistency >= thresholds.min_amount_consistency
    )
    reasons: list[str] = []
    if distinct_mints < thresholds.min_chains:
        reasons.append("insufficient_distinct_chains")
    if consistency < thresholds.min_amount_consistency:
        reasons.append("inconsistent_wrap_amount")
    return {
        "wallet": wallet,
        "verdict": "QUALIFIED_TOPOLOGY" if qualifies else "INSUFFICIENT_EVIDENCE",
        "distinct_mints": distinct_mints,
        "distinct_subprovs": distinct_subprovs,
        "distinct_creators": distinct_creators,
        "dominant_wrap_amount_sol": dominant_amount,
        "dominant_amount_count": dominant_count,
        "amount_consistency": round(consistency, 6),
        "reasons": reasons,
        "rejected_evidence": dict(sorted(rejected_counts.items())),
        "chains": sorted(chains, key=lambda c: (c["mint"], c["subprov"])),
    }


def discover_unknown_treasuries(
    conn,
    *,
    wallets: Iterable[str] | None = None,
    thresholds: TopologyThresholds = TopologyThresholds(),
    infrastructure_check: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Classify retained upstream wallets and return qualified candidates."""
    evidence_by_wallet: dict[str, list[tuple[Any, ...]]] | None = None
    if wallets is None:
        # Load the retained topology once. Re-running the CTE once per upstream
        # wallet turns a 33k-edge audit into an accidental N×full-table scan.
        rows = conn.execute(
            "WITH subprov_uses AS ("
            " SELECT from_wallet,COUNT(DISTINCT COALESCE(source_mint,'')) AS mint_uses,"
            " COUNT(DISTINCT to_wallet) AS creator_uses "
            " FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' GROUP BY from_wallet"
            ") "
            "SELECT t.from_wallet,t.to_wallet,c.to_wallet,c.source_mint,"
            "c.funding_amount_sol,c.funding_tx_signature,"
            "q.create_anchor_signature,q.create_anchor_audit_state,"
            "r.signers_json,r.outer_shape_json,u.mint_uses,u.creator_uses,"
            "t.funding_tx_signature,t.funding_amount_sol "
            "FROM wt_provisioning_edges t "
            "JOIN wt_provisioning_edges c ON c.edge_type='SUBPROV_TO_CREATOR' "
            " AND c.from_wallet=t.to_wallet "
            "JOIN subprov_uses u ON u.from_wallet=t.to_wallet "
            "JOIN wt_walkback_queue q ON q.mint=c.source_mint AND q.creator=c.to_wallet "
            "JOIN wt_walkback_transaction_roles r ON r.mint=c.source_mint "
            " AND r.signature=c.funding_tx_signature "
            "WHERE t.edge_type='TREASURY_TO_SUBPROV' "
            "ORDER BY t.from_wallet,c.source_mint,c.to_wallet,t.to_wallet"
        ).fetchall()
        evidence_by_wallet = {}
        for row in rows:
            evidence_by_wallet.setdefault(str(row[0]), []).append(tuple(row[1:]))
        evidence_by_wallet = {
            wallet: evidence for wallet, evidence in evidence_by_wallet.items()
            if len({row[2] for row in evidence}) >= thresholds.min_chains
        }
        wallets = sorted(evidence_by_wallet)
    results = []
    for wallet in wallets:
        results.append(classify_unknown_treasury(
            conn, wallet, thresholds=thresholds,
            infrastructure_check=infrastructure_check,
            _evidence_rows=(evidence_by_wallet or {}).get(wallet) if evidence_by_wallet is not None else None,
        ))
    return [r for r in results if r["verdict"] == "QUALIFIED_TOPOLOGY"]


def confirm_topology_candidate(
    conn,
    candidate: dict[str, Any],
    *,
    now: int | None = None,
    thresholds: TopologyThresholds = TopologyThresholds(),
    infrastructure_check: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Revalidate and explicitly promote a candidate via the audited bank path."""
    if candidate.get("verdict") != "QUALIFIED_TOPOLOGY" or not candidate.get("chains"):
        raise ValueError("candidate is not topology-qualified")
    # Never trust a serialized/stale/forged candidate as mutation authority.
    # Rebuild the decision from current retained evidence immediately before
    # entering the treasury bank's write boundary.
    fresh = classify_unknown_treasury(
        conn, str(candidate.get("wallet") or ""), thresholds=thresholds,
        infrastructure_check=infrastructure_check,
    )
    if fresh.get("verdict") != "QUALIFIED_TOPOLOGY":
        raise ValueError("candidate no longer topology-qualified")
    from src.core.treasury_bank import auto_confirm_from_topology_cohort

    return auto_confirm_from_topology_cohort(conn, fresh, now=now)


def replay_topology_candidate(
    conn,
    candidate: dict[str, Any],
    *,
    allowed_mints: Iterable[str],
    now: int,
    infrastructure_check: Callable[[str], bool],
    core_conn=None,
) -> dict[str, Any]:
    """Replay only explicitly allow-listed terminal lineage gaps.

    This is intentionally narrower than the normal worker: it performs no RPC,
    discovers no additional rows, and refuses any row whose retained queue and
    provisioning-session identities do not exactly match freshly classified
    topology evidence.  The caller owns the transaction and commit.
    """
    wallet = str(candidate.get("wallet") or "")
    fresh = classify_unknown_treasury(
        conn, wallet, infrastructure_check=infrastructure_check,
    )
    if fresh.get("verdict") != "QUALIFIED_TOPOLOGY":
        raise ValueError("candidate no longer topology-qualified")
    allowed = tuple(sorted(set(allowed_mints)))
    chains = {str(c["mint"]): c for c in fresh.get("chains") or []}
    if not allowed or any(mint not in chains for mint in allowed):
        raise ValueError("allow-list is not a subset of fresh topology evidence")

    for mint in allowed:
        chain = chains[mint]
        row = conn.execute(
            "SELECT q.status,q.intelligence_outcome,q.creator,q.subprov,q.treasury,"
            "q.funder_sig,q.funding_mechanism,s.treasury,s.subprov,s.creator,"
            "s.subprov_to_creator_mechanism FROM wt_walkback_queue q "
            "JOIN wt_provisioning_sessions s ON s.source_mint=q.mint WHERE q.mint=?",
            (mint,),
        ).fetchone()
        if not row:
            raise ValueError(f"missing queue/session evidence: {mint}")
        expected = (
            "complete", "LINEAGE_GAP", chain["creator"], chain["subprov"],
            None, chain["wrap_signature"], "WSOL_WRAP_CLOSE", wallet,
            chain["subprov"], chain["creator"], "WSOL_WRAP_CLOSE",
        )
        if tuple(row) != expected:
            raise ValueError(f"replay precondition mismatch: {mint}")

    from src.ops.attribution_outcome import materialize_outcome
    from src.ops.watchtower_candidates import sync_walkback_result

    for mint in allowed:
        chain = chains[mint]
        conn.execute(
            "INSERT INTO watchtower_token_attribution "
            "(mint,creator,matched_subprov,matched_treasury,score,tier,reasons_json,scored_at) "
            "VALUES (?,?,?,?,100,'CONFIRMED',?,?) "
            "ON CONFLICT(mint) DO UPDATE SET creator=excluded.creator,"
            "matched_subprov=excluded.matched_subprov,matched_treasury=excluded.matched_treasury,"
            "score=excluded.score,tier=excluded.tier,reasons_json=excluded.reasons_json,"
            "scored_at=excluded.scored_at",
            (mint, chain["creator"], chain["subprov"], wallet,
             json.dumps(["QUALIFIED_TOPOLOGY_COHORT"], separators=(",", ":")), int(now)),
        )
        changed = conn.execute(
            "UPDATE wt_walkback_queue SET treasury=?,attribution_source='topology_cohort_replay',"
            "intelligence_outcome='WATCHTOWER_CONFIRMED',updated_at=? "
            "WHERE mint=? AND status='complete' AND intelligence_outcome='LINEAGE_GAP' "
            "AND treasury IS NULL AND creator=? AND subprov=?",
            (wallet, int(now), mint, chain["creator"], chain["subprov"]),
        ).rowcount
        if changed != 1:
            raise ValueError(f"bounded queue update failed: {mint}")
        materialize_outcome(conn, mint, core_conn=core_conn)
        sync_walkback_result(conn, mint)
    return {"wallet": wallet, "replayed": list(allowed), "count": len(allowed)}
