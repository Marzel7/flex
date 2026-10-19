"""
OF-DV34-P2 -- operation-identity separation review analysis primitives.

Pure, read-only, provider-free functions over already-extracted local data
(see docs/audits/of_dv34_p1_local_prediction_freeze.json and
database/local_operation_discovery_corpus.db). No network/RPC calls. No
database writes. These functions exist so that the counterfactual tests in
tests/test_dv34_p2_operation_identity_review.py are genuine (able to fail on
bad logic) rather than assertions against a hand-written JSON blob.

Terminology:
  - "Dv34-shared-funding evidence" = any fact derived from Dv34 (or Dv34's
    upstream CEX hub) being the funder/ancestor of a wallet. This is the
    evidence axis that must be REMOVED for a fact to count as "independent".
  - "independent evidence" = evidence that does NOT route through Dv34 or
    Dv34's upstream hub as the connecting node.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Set, Tuple

DV34_ADDRESS = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
DV34_UPSTREAM_HUB = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"


def compute_watchtower_membership_overlap(
    dv34_mints: Sequence[str],
    dv34_creators: Sequence[str],
    confirmed_treasury_addresses: Iterable[str],
    confirmed_launch_mints: Iterable[str],
    candidate_mints: Iterable[str],
    candidate_creators: Iterable[str],
) -> Dict[str, object]:
    """Separate CONFIRMED Watchtower membership from CANDIDATE-state overlap.

    Never conflates the two authority levels: a hit in `candidate_*` sets
    must never be reported under the confirmed_* keys.
    """
    confirmed_treasury_addresses = set(confirmed_treasury_addresses)
    confirmed_launch_mints = set(confirmed_launch_mints)
    candidate_mints = set(candidate_mints)
    candidate_creators = set(candidate_creators)

    dv34_mints_set = set(dv34_mints)
    dv34_creators_set = set(dv34_creators)

    return {
        "confirmed_treasury_hits": sorted(
            (dv34_creators_set | {DV34_ADDRESS}) & confirmed_treasury_addresses
        ),
        "confirmed_launch_mint_hits": sorted(dv34_mints_set & confirmed_launch_mints),
        "candidate_mint_hits": sorted(dv34_mints_set & candidate_mints),
        "candidate_creator_hits": sorted(dv34_creators_set & candidate_creators),
        "confirmed_membership_count": len(
            (dv34_creators_set | {DV34_ADDRESS}) & confirmed_treasury_addresses
        )
        + len(dv34_mints_set & confirmed_launch_mints),
        "candidate_overlap_count": len(dv34_mints_set & candidate_mints)
        + len(dv34_creators_set & candidate_creators),
    }


def compute_internal_subfamilies(
    creators: Sequence[str],
    secondary_funder_edges: Sequence[Tuple[str, str]],
    excluded_hub_addresses: Iterable[str] = (DV34_ADDRESS, DV34_UPSTREAM_HUB),
) -> Dict[str, object]:
    """Partition `creators` using ONLY non-Dv34-funding evidence.

    secondary_funder_edges: list of (creator_address, funder_address) pairs
    for funders OTHER than Dv34 itself (already excludes Dv34 direct edges
    by construction of the caller's query). This function additionally
    excludes `excluded_hub_addresses` (Dv34 and its own CEX upstream hub) as
    connecting nodes, because those routes are shared-source, not independent
    corroboration -- two creators sharing only a CEX withdrawal hub are not
    evidence of common operator identity.

    Returns a real union-find partition: creators connected by a *non-hub*
    shared funder land in the same cluster; everyone else is a singleton.
    This must be able to produce "all singletons" (no false clustering) or
    "one giant cluster" (true positive) depending on the data -- it is not
    hardcoded to return 23 or 1.
    """
    excluded = set(excluded_hub_addresses)
    creators = list(creators)
    parent = {c: c for c in creators}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    funder_to_creators: Dict[str, Set[str]] = defaultdict(set)
    for creator, funder in secondary_funder_edges:
        if funder in excluded:
            continue
        if creator not in parent:
            continue
        funder_to_creators[funder].add(creator)

    non_independent_links = []
    for funder, linked_creators in funder_to_creators.items():
        if len(linked_creators) > 1:
            linked_list = sorted(linked_creators)
            for other in linked_list[1:]:
                union(linked_list[0], other)
            non_independent_links.append(
                {"funder": funder, "creators": linked_list}
            )

    clusters: Dict[str, List[str]] = defaultdict(list)
    for c in creators:
        clusters[find(c)].append(c)

    cluster_list = sorted(
        (sorted(members) for members in clusters.values()),
        key=lambda m: (-len(m), m[0]),
    )
    multi_member_clusters = [c for c in cluster_list if len(c) > 1]
    singleton_count = sum(1 for c in cluster_list if len(c) == 1)

    return {
        "total_creators": len(creators),
        "cluster_count": len(cluster_list),
        "singleton_count": singleton_count,
        "largest_cluster_size": max((len(c) for c in cluster_list), default=0),
        "clusters": cluster_list,
        "multi_member_clusters": multi_member_clusters,
        "linking_funders_used": non_independent_links,
        "excluded_hub_addresses": sorted(excluded),
        "all_resolve_to_one_family": len(cluster_list) == 1 and len(creators) > 1,
        "no_independent_clustering_found": len(multi_member_clusters) == 0,
    }


def compute_watchtower_boundary_counterfactual(
    dv34_creators: Sequence[str],
    watchtower_confirmed_creator_universe: Iterable[str],
    independent_link_count_to_confirmed_watchtower: int,
) -> Dict[str, object]:
    """Remove Dv34-shared-funding as an attribution feature: does independent
    evidence still connect the 23 to CONFIRMED Watchtower membership?

    `independent_link_count_to_confirmed_watchtower` must be computed by the
    caller from non-Dv34-routed evidence only (e.g. secondary funder overlap
    with confirmed-treasury-funded creators, CREATE-creator identity match
    against confirmed launches, etc.) -- this function just applies the
    decision rule so the rule itself is testable in isolation.
    """
    watchtower_confirmed_creator_universe = set(watchtower_confirmed_creator_universe)
    direct_identity_overlap = sorted(
        set(dv34_creators) & watchtower_confirmed_creator_universe
    )
    counterfactual_result = bool(direct_identity_overlap) or (
        independent_link_count_to_confirmed_watchtower > 0
    )
    return {
        "direct_creator_identity_overlap_with_confirmed_watchtower": direct_identity_overlap,
        "independent_link_count_to_confirmed_watchtower": independent_link_count_to_confirmed_watchtower,
        "counterfactual_still_groups_with_watchtower": counterfactual_result,
        "verdict": "YES" if counterfactual_result else "NO",
    }


def compute_cross_operation_provisioner_signal(
    upstream_hub_direct_funder_roots: Sequence[str],
    dv34_address: str = DV34_ADDRESS,
) -> Dict[str, object]:
    """Does Dv34's own upstream (the CEX hub) ALSO fund other, otherwise
    distinct, direct-funder roots (i.e. other operations/services)? This is
    evidence about the CEX hub being a shared *source*, not evidence that
    Dv34 itself is a multi-operation provisioner -- those are different
    claims and must not be conflated in the output.
    """
    other_roots = sorted(r for r in upstream_hub_direct_funder_roots if r != dv34_address)
    return {
        "other_direct_funder_roots_sharing_dv34_upstream_hub": other_roots,
        "count_other_roots": len(other_roots),
        "interpretation": (
            "CEX_WITHDRAWAL_FANOUT_NOT_OPERATOR_EVIDENCE"
            if other_roots
            else "NO_OTHER_ROOTS_OBSERVED"
        ),
        "note": (
            "This shows the CEX hub upstream of Dv34 also funds unrelated "
            "direct-funder roots. It is evidence the UPSTREAM is a shared "
            "public CEX hop, not evidence that Dv34 itself provisions "
            "multiple operations. Distinct from CROSS_OPERATION_PROVISIONER_SIGNAL, "
            "which would require Dv34 ITSELF funding creators already confirmed "
            "in multiple independently-established operations."
        ),
    }


def check_no_confirmed_state_mutation(sql_statements: Sequence[str]) -> Dict[str, object]:
    """Structural guard: verify a list of SQL statements this module or its
    callers executed contains no INSERT/UPDATE/DELETE against any
    watchtower_*/wt_confirmed_*/operator_* table. Read-only SELECT only.
    """
    forbidden_prefixes = ("insert", "update", "delete", "drop", "alter", "create")
    forbidden_table_markers = ("watchtower_", "wt_confirmed_", "operator_", "wt_watchtower_")
    violations = []
    for stmt in sql_statements:
        lowered = stmt.strip().lower()
        if lowered.startswith(forbidden_prefixes):
            if any(marker in lowered for marker in forbidden_table_markers):
                violations.append(stmt)
    return {
        "statements_checked": len(sql_statements),
        "violations": violations,
        "clean": len(violations) == 0,
    }


def compute_exact_amount_group_independence(
    amount_lamports: int,
    mints_in_group: Sequence[str],
    watchtower_historical_note_source: str,
    dv34_local_evidence_source: str,
) -> Dict[str, object]:
    """Is a Watchtower-noted exact-amount group (e.g. 2.9865 SOL) genuinely
    independent corroboration, or the identical underlying transfer set
    counted twice?

    Two sources are INDEPENDENT only if they were derived from disjoint raw
    evidence pipelines. If both ultimately read the same `creator_funders` /
    `transfer_index` rows, they are the SAME_SOURCE_DOUBLE_COUNT regardless
    of which analysis surfaced them.
    """
    same_source = watchtower_historical_note_source == dv34_local_evidence_source
    return {
        "amount_sol": amount_lamports / 1_000_000_000,
        "mints_in_group": sorted(mints_in_group),
        "group_size": len(mints_in_group),
        "watchtower_historical_note_source": watchtower_historical_note_source,
        "dv34_local_evidence_source": dv34_local_evidence_source,
        "classification": "SAME_SOURCE_DOUBLE_COUNT" if same_source else "INDEPENDENT_CORROBORATION",
    }
