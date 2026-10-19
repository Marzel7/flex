"""Read-only, deterministic X8 operator identity resolver.

X16B separates resolution into two non-persistent stages:

* :meth:`evaluate` reads existing intelligence and emits observations;
* :meth:`propose` consumes those observations and emits decisions.

There is intentionally no canonical write path in this module.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from src.ops.identity_framework import (
    EvidenceObservation,
    IdentityEvaluation,
    IdentityObservation,
    PromotionDecisionEngine,
)
from src.ops.operator_model import (
    EVIDENCE_CATALOGUE,
    EVIDENCE_CONTEXT,
    EVIDENCE_IDENTITY,
    EVIDENCE_SUPPORTING,
)


IDENTITY_RULES = (
    "SHARED_TREASURY_ROOT",
    "REPEATED_STRUCTURAL_PROVISIONING",
    "CONFIRMED_INFRASTRUCTURE_REUSE",
    "VANITY_ADDRESS_FAMILY",
    "CROSS_OPERATION_WALLET_OVERLAP",
)

_INFRA_ROLES = frozenset({
    "TREASURY", "SUB_PROV", "SUB_PROVISIONER", "SIGNALLER", "SIGNALLER_2",
    "RELAY", "COLLECTOR", "PASS_THROUGH", "DIRECT_FUNDER",
})
_REUSE_ROLES = frozenset({
    "SUB_PROV", "SUB_PROVISIONER", "SIGNALLER", "SIGNALLER_2",
    "RELAY", "COLLECTOR", "PASS_THROUGH", "DIRECT_FUNDER",
})
_VANITY_ROLES = frozenset({"TREASURY", "SIGNALLER", "SIGNALLER_2"})


def _connect_readonly(path: str | Path | None) -> sqlite3.Connection | None:
    if not path:
        return None
    path = str(path)
    try:
        if path == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            conn = sqlite3.connect(
                f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=10
            )
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection | None, name: str) -> bool:
    if conn is None:
        return False
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _candidate_key(prefix: str, values: Iterable[str]) -> str:
    ordered = tuple(sorted(set(str(value) for value in values if value is not None)))
    digest = hashlib.sha256("\x1f".join(ordered).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _legacy_key(identity: str) -> str:
    return f"legacy:{identity.strip().upper()}"


def _confidence(evidence_type: str) -> float:
    return float(EVIDENCE_CATALOGUE[evidence_type]["weight"])


def _details(**kwargs: Any) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(kwargs.items()))


class OperatorResolver:
    """Evaluate existing intelligence without creating or mutating operators.

    ``store`` remains an accepted constructor argument for compatibility with the
    existing route wiring.  It is never read from or written to.
    """

    def __init__(
        self,
        store: Any = None,
        ops_db_path: str | None = None,
        live_db_path: str | None = None,
    ) -> None:
        self._store = store
        self._ops_db_path = ops_db_path
        self._live_db_path = live_db_path

    def evaluate(self) -> IdentityEvaluation:
        identity: list[IdentityObservation] = []
        supporting: list[EvidenceObservation] = []
        context: list[EvidenceObservation] = []

        ops = _connect_readonly(self._ops_db_path)
        live = _connect_readonly(self._live_db_path)
        try:
            identity.extend(self._rule_shared_treasury_root(ops))
            identity.extend(self._rule_repeated_structural_provisioning(ops))
            identity.extend(self._rule_confirmed_infrastructure_reuse(ops))
            identity.extend(self._rule_vanity_address_family(ops))
            identity.extend(self._rule_cross_operation_wallet_overlap(ops))

            legacy_identity, legacy_supporting = self._legacy_lineage_evidence(ops, live)
            identity.extend(legacy_identity)
            supporting.extend(legacy_supporting)
            supporting.extend(self._supporting_playbook(ops))
            supporting.extend(self._supporting_payout(live))

            # Context is emitted only for candidates that already have identity;
            # context can never manufacture a proposal on its own.
            by_candidate: dict[str, list[IdentityObservation]] = defaultdict(list)
            for observation in identity:
                by_candidate[observation.candidate_key].append(observation)
            for key, observations in sorted(by_candidate.items()):
                operations = sorted({op for item in observations for op in item.operations})
                entities = sorted({entity for item in observations for entity in item.entities})
                context.append(EvidenceObservation(
                    candidate_key=key,
                    evidence_type="CHAIN_ACTIVITY",
                    category=EVIDENCE_CONTEXT,
                    confidence=0.0,
                    reason=(f"Identity chain covers {len(operations)} operation(s) and "
                            f"{len(entities)} infrastructure/entity address(es)."),
                    source_tables=tuple(sorted({
                        table for item in observations for table in item.source_tables
                    })),
                    entities=tuple(entities),
                    operations=tuple(operations),
                    details=_details(operation_count=len(operations), entity_count=len(entities)),
                ))
        finally:
            if ops is not None:
                ops.close()
            if live is not None:
                live.close()

        return IdentityEvaluation(
            identity=tuple(identity),
            supporting=tuple(supporting),
            context=tuple(context),
        )

    def propose(self, evaluation: IdentityEvaluation | None = None):
        return PromotionDecisionEngine().decide(evaluation or self.evaluate())

    def run(self) -> dict[str, Any]:
        """Compatibility entry point returning a no-write evaluation report."""
        evaluation = self.evaluate()
        proposals = self.propose(evaluation)
        return {
            "mode": "READ_ONLY",
            "write_performed": False,
            "rules_run": len(IDENTITY_RULES),
            "identity_observations": len(evaluation.identity),
            "supporting_observations": len(evaluation.supporting),
            "context_observations": len(evaluation.context),
            "operators_created": 0,
            "operators_promoted": 0,
            "evidence_added": 0,
            "observations": evaluation.to_dict(),
            "proposals": [proposal.to_dict() for proposal in proposals],
            "errors": [],
        }

    # -- Identity rule 1 -----------------------------------------------------

    def _rule_shared_treasury_root(
        self, conn: sqlite3.Connection | None
    ) -> tuple[IdentityObservation, ...]:
        if not _table_exists(conn, "wt_ops_v2"):
            return ()
        rows = conn.execute(
            """
            SELECT treasury_root,
                   GROUP_CONCAT(DISTINCT operation_uuid) operation_ids,
                   COUNT(DISTINCT operation_uuid) operation_count
            FROM wt_ops_v2
            WHERE treasury_root IS NOT NULL
              AND COALESCE(status, '') NOT IN ('DORMANT', 'ABORTED')
            GROUP BY treasury_root
            HAVING COUNT(DISTINCT operation_uuid) >= 2
            ORDER BY treasury_root
            """
        ).fetchall()
        return tuple(IdentityObservation(
            candidate_key=f"treasury:{row['treasury_root']}",
            evidence_type="SHARED_TREASURY_ROOT",
            category=EVIDENCE_IDENTITY,
            confidence=_confidence("SHARED_TREASURY_ROOT"),
            reason=(f"Confirmed treasury root appears in {row['operation_count']} "
                    "distinct active operations."),
            source_tables=("wt_ops_v2",),
            entities=(row["treasury_root"],),
            operations=tuple((row["operation_ids"] or "").split(",")),
            details=_details(operation_count=row["operation_count"]),
        ) for row in rows)

    # -- Identity rule 2 -----------------------------------------------------

    def _rule_repeated_structural_provisioning(
        self, conn: sqlite3.Connection | None
    ) -> tuple[IdentityObservation, ...]:
        required = ("wt_ops_v2", "wt_ops_v2_wallets", "wt_ops_v2_creators", "wt_ops_v2_edges")
        if not all(_table_exists(conn, table) for table in required):
            return ()
        rows = conn.execute(
            """
            SELECT o.treasury_root treasury, w.wallet sub_provisioner,
                   c.creator_wallet creator,
                   GROUP_CONCAT(DISTINCT o.operation_uuid) operation_ids,
                   COUNT(DISTINCT o.operation_uuid) operation_count
            FROM wt_ops_v2 o
            JOIN wt_ops_v2_wallets w
              ON w.operation_uuid=o.operation_uuid
             AND w.role IN ('SUB_PROV','SUB_PROVISIONER')
            JOIN wt_ops_v2_creators c ON c.operation_uuid=o.operation_uuid
            WHERE EXISTS (
                SELECT 1 FROM wt_ops_v2_edges e1
                WHERE e1.operation_uuid=o.operation_uuid
                  AND e1.from_wallet=o.treasury_root AND e1.to_wallet=w.wallet
            )
              AND EXISTS (
                SELECT 1 FROM wt_ops_v2_edges e2
                WHERE e2.operation_uuid=o.operation_uuid
                  AND e2.from_wallet=w.wallet AND e2.to_wallet=c.creator_wallet
            )
            GROUP BY o.treasury_root, w.wallet, c.creator_wallet
            HAVING COUNT(DISTINCT o.operation_uuid) >= 2
            ORDER BY o.treasury_root, w.wallet, c.creator_wallet
            """
        ).fetchall()
        return tuple(IdentityObservation(
            candidate_key=f"treasury:{row['treasury']}",
            evidence_type="REPEATED_STRUCTURAL_PROVISIONING",
            category=EVIDENCE_IDENTITY,
            confidence=_confidence("REPEATED_STRUCTURAL_PROVISIONING"),
            reason=("The same treasury-to-sub-provisioner-to-creator chain appears "
                    f"in {row['operation_count']} distinct operations."),
            source_tables=required,
            entities=(row["treasury"], row["sub_provisioner"], row["creator"]),
            operations=tuple((row["operation_ids"] or "").split(",")),
            details=_details(operation_count=row["operation_count"]),
        ) for row in rows)

    # -- Identity rule 3 -----------------------------------------------------

    def _rule_confirmed_infrastructure_reuse(
        self, conn: sqlite3.Connection | None
    ) -> tuple[IdentityObservation, ...]:
        if not _table_exists(conn, "wt_ops_v2_wallets"):
            return ()
        placeholders = ",".join("?" for _ in _REUSE_ROLES)
        rows = conn.execute(
            f"SELECT operation_uuid,wallet,role FROM wt_ops_v2_wallets "
            f"WHERE role IN ({placeholders}) ORDER BY operation_uuid,wallet,role",
            tuple(sorted(_REUSE_ROLES)),
        ).fetchall()
        by_operation: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            by_operation[row["operation_uuid"]].add(row["wallet"])

        observations: list[IdentityObservation] = []
        operations = sorted(by_operation)
        for index, op_a in enumerate(operations):
            for op_b in operations[index + 1:]:
                shared = sorted(by_operation[op_a] & by_operation[op_b])
                if len(shared) < 3:
                    continue
                observations.append(IdentityObservation(
                    candidate_key=_candidate_key("operations", (op_a, op_b)),
                    evidence_type="CONFIRMED_INFRASTRUCTURE_REUSE",
                    category=EVIDENCE_IDENTITY,
                    confidence=_confidence("CONFIRMED_INFRASTRUCTURE_REUSE"),
                    reason=(f"Two operations reuse {len(shared)} confirmed "
                            "sub-provisioner, signaller or relay wallets."),
                    source_tables=("wt_ops_v2_wallets",),
                    entities=tuple(shared),
                    operations=(op_a, op_b),
                    details=_details(shared_wallet_count=len(shared)),
                ))
        return tuple(observations)

    # -- Identity rule 4 -----------------------------------------------------

    def _rule_vanity_address_family(
        self, conn: sqlite3.Connection | None
    ) -> tuple[IdentityObservation, ...]:
        families = self._load_vanity_families(conn)
        observations: list[IdentityObservation] = []
        for family in families:
            label = family["label"]
            members = family["members"]
            prefixes = family["prefixes"]
            roles = family["roles"]
            qualifying_prefixes = tuple(prefix for prefix in prefixes if len(prefix) >= 4)
            eligible = tuple(sorted(
                wallet for wallet in members
                if roles.get(wallet, "").upper() in _VANITY_ROLES
                and any(wallet.startswith(prefix) for prefix in qualifying_prefixes)
            ))
            if len(eligible) < 2:
                continue
            identity = label.split("_", 1)[0].upper() if "_" in label else ""
            key = _legacy_key(identity) if identity in {"WATCHTOWER"} else f"vanity:{label}"
            observations.append(IdentityObservation(
                candidate_key=key,
                evidence_type="VANITY_ADDRESS_FAMILY",
                category=EVIDENCE_IDENTITY,
                confidence=_confidence("VANITY_ADDRESS_FAMILY"),
                reason=(f"Confirmed treasury/signaller family has {len(eligible)} wallets "
                        "sharing a deliberate prefix of at least four characters."),
                source_tables=("wt_vanity_families",),
                entities=eligible,
                legacy_source="wt_vanity_families",
                legacy_identifier=label,
                details=_details(prefixes=qualifying_prefixes),
            ))
        return tuple(observations)

    def _load_vanity_families(self, conn: sqlite3.Connection | None) -> tuple[dict, ...]:
        if not _table_exists(conn, "wt_vanity_families"):
            return ()
        rows = conn.execute(
            """
            SELECT family_label,family_prefixes_json,confirmed_wallets_json,
                   roles_json,confidence
            FROM wt_vanity_families
            WHERE UPPER(COALESCE(confidence,''))='CONFIRMED'
            ORDER BY family_label
            """
        ).fetchall()
        families = []
        for row in rows:
            try:
                families.append({
                    "label": row["family_label"],
                    "prefixes": tuple(json.loads(row["family_prefixes_json"] or "[]")),
                    "members": tuple(json.loads(row["confirmed_wallets_json"] or "[]")),
                    "roles": dict(json.loads(row["roles_json"] or "{}")),
                })
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(families)

    # -- Identity rule 5 -----------------------------------------------------

    def _rule_cross_operation_wallet_overlap(
        self, conn: sqlite3.Connection | None
    ) -> tuple[IdentityObservation, ...]:
        if not _table_exists(conn, "wt_ops_v2_wallets"):
            return ()
        placeholders = ",".join("?" for _ in _INFRA_ROLES)
        rows = conn.execute(
            f"""
            SELECT wallet,GROUP_CONCAT(DISTINCT operation_uuid) operation_ids,
                   GROUP_CONCAT(DISTINCT role) roles,
                   COUNT(DISTINCT operation_uuid) operation_count,
                   MIN(first_seen) first_seen,MAX(last_seen) last_seen
            FROM wt_ops_v2_wallets
            WHERE role IN ({placeholders})
            GROUP BY wallet
            HAVING COUNT(DISTINCT operation_uuid)>=2
            ORDER BY wallet
            """,
            tuple(sorted(_INFRA_ROLES)),
        ).fetchall()
        return tuple(IdentityObservation(
            candidate_key=f"wallet:{row['wallet']}",
            evidence_type="CROSS_OPERATION_WALLET_OVERLAP",
            category=EVIDENCE_IDENTITY,
            confidence=_confidence("CROSS_OPERATION_WALLET_OVERLAP"),
            reason=(f"The same confirmed infrastructure wallet appears in "
                    f"{row['operation_count']} distinct operations."),
            source_tables=("wt_ops_v2_wallets",),
            entities=(row["wallet"],),
            operations=tuple((row["operation_ids"] or "").split(",")),
            details=_details(
                roles=tuple(sorted((row["roles"] or "").split(","))),
                first_seen=row["first_seen"], last_seen=row["last_seen"],
            ),
        ) for row in rows)

    # -- Existing supporting evidence ---------------------------------------

    def _supporting_playbook(
        self, conn: sqlite3.Connection | None
    ) -> tuple[EvidenceObservation, ...]:
        required = ("wt_ops_v2_families", "wt_ops_v2_operation_family_links")
        if not all(_table_exists(conn, table) for table in required):
            return ()
        rows = conn.execute(
            """
            SELECT l.family_uuid,f.playbook_signature,
                   GROUP_CONCAT(DISTINCT l.operation_uuid) operation_ids,
                   COUNT(DISTINCT l.operation_uuid) operation_count
            FROM wt_ops_v2_operation_family_links l
            JOIN wt_ops_v2_families f USING(family_uuid)
            WHERE f.playbook_signature IS NOT NULL
            GROUP BY l.family_uuid,f.playbook_signature
            HAVING COUNT(DISTINCT l.operation_uuid)>=2
            ORDER BY l.family_uuid
            """
        ).fetchall()
        return tuple(EvidenceObservation(
            candidate_key=f"playbook:{row['family_uuid']}",
            evidence_type="PLAYBOOK_SIGNATURE_MATCH",
            category=EVIDENCE_SUPPORTING,
            confidence=_confidence("PLAYBOOK_SIGNATURE_MATCH"),
            reason=(f"{row['operation_count']} operations share a playbook signature; "
                    "playbooks do not establish actor identity."),
            source_tables=required,
            operations=tuple((row["operation_ids"] or "").split(",")),
            legacy_identifier=row["family_uuid"],
            details=_details(playbook_signature=row["playbook_signature"]),
        ) for row in rows)

    def _supporting_payout(
        self, conn: sqlite3.Connection | None
    ) -> tuple[EvidenceObservation, ...]:
        if not _table_exists(conn, "operator_creator_edges"):
            return ()
        rows = conn.execute(
            """
            SELECT operator_anchor,MAX(confidence) confidence,
                   COUNT(*) edge_count,
                   COUNT(DISTINCT creator_a)+COUNT(DISTINCT creator_b) creator_observations
            FROM operator_creator_edges
            WHERE edge_type='shared_payout_wallet'
            GROUP BY operator_anchor
            ORDER BY operator_anchor
            """
        ).fetchall()
        return tuple(EvidenceObservation(
            candidate_key=f"payout:{row['operator_anchor']}",
            evidence_type="SHARED_PAYOUT_WALLET",
            category=EVIDENCE_SUPPORTING,
            confidence=min(float(row["confidence"] or 0.0),
                           _confidence("SHARED_PAYOUT_WALLET")),
            reason=(f"{row['edge_count']} creator-pair observations share this payout "
                    "destination; this cannot establish identity alone."),
            source_tables=("operator_creator_edges",),
            entities=(row["operator_anchor"],),
            details=_details(
                edge_count=row["edge_count"],
                creator_observations=row["creator_observations"],
            ),
        ) for row in rows)

    # -- Legacy reconciliation inputs ---------------------------------------

    def _legacy_lineage_evidence(
        self,
        ops: sqlite3.Connection | None,
        live: sqlite3.Connection | None,
    ) -> tuple[tuple[IdentityObservation, ...], tuple[EvidenceObservation, ...]]:
        required = (
            "wt_known_operator_hubs", "wt_operations", "wt_operation_members",
            "creator_funders",
        )
        if not all(_table_exists(live, table) for table in required):
            return (), ()

        rows = live.execute(
            """
            WITH RECURSIVE lineage(operation_id,leaf,current,depth) AS (
                SELECT operation_id,creator_wallet,creator_wallet,0
                FROM wt_operation_members WHERE creator_wallet IS NOT NULL
                UNION
                SELECT l.operation_id,l.leaf,cf.funder_address,l.depth+1
                FROM lineage l JOIN creator_funders cf ON cf.creator_address=l.current
                WHERE l.depth<3
            )
            SELECT h.operator_identity,h.hub_wallet,l.operation_id,l.leaf,MIN(l.depth) depth
            FROM lineage l JOIN wt_known_operator_hubs h ON h.hub_wallet=l.current
            GROUP BY h.operator_identity,h.hub_wallet,l.operation_id,l.leaf
            ORDER BY h.operator_identity,h.hub_wallet,l.operation_id,l.leaf
            """
        ).fetchall()
        by_identity_hub: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for row in rows:
            by_identity_hub[(row["operator_identity"], row["hub_wallet"])][
                str(row["operation_id"])
            ].add(row["leaf"])

        identity_observations: list[IdentityObservation] = []
        supporting: list[EvidenceObservation] = []
        for (identity, hub), operation_creators in sorted(by_identity_hub.items()):
            if len(operation_creators) < 2:
                continue
            operations = tuple(sorted(operation_creators))
            creators = tuple(sorted({
                creator for group in operation_creators.values() for creator in group
            }))
            identity_observations.append(IdentityObservation(
                candidate_key=_legacy_key(identity),
                evidence_type="CROSS_OPERATION_WALLET_OVERLAP",
                category=EVIDENCE_IDENTITY,
                confidence=_confidence("CROSS_OPERATION_WALLET_OVERLAP"),
                reason=(f"Confirmed legacy hub appears in {len(operations)} distinct "
                        f"operation lineages covering {len(creators)} creators."),
                source_tables=required,
                entities=(hub, *creators),
                operations=operations,
                legacy_source="wt_known_operator_hubs",
                legacy_identifier=identity,
                details=_details(hub=hub, creator_count=len(creators)),
            ))

        # WATCHTOWER has a second, independent identity class only where the
        # confirmed treasury/signaller vanity family funds confirmed hubs used by
        # multiple operations.  Family labels alone are not enough.
        vanity = next((
            family for family in self._load_vanity_families(ops)
            if family["label"] == "WATCHTOWER_44OR"
        ), None)
        wt_hubs = {
            hub: operation_creators
            for (identity, hub), operation_creators in by_identity_hub.items()
            if identity.upper() == "WATCHTOWER" and operation_creators
        }
        if vanity and wt_hubs:
            addresses = tuple(sorted(
                wallet for wallet in vanity["members"]
                if vanity["roles"].get(wallet, "").upper() in _VANITY_ROLES
            ))
            placeholders = ",".join("?" for _ in wt_hubs)
            funding_rows = live.execute(
                f"SELECT creator_address,funder_address FROM creator_funders "
                f"WHERE creator_address IN ({placeholders}) ORDER BY creator_address,funder_address",
                tuple(sorted(wt_hubs)),
            ).fetchall()
            funders_by_hub: dict[str, set[str]] = defaultdict(set)
            for row in funding_rows:
                funders_by_hub[row["creator_address"]].add(row["funder_address"])
            qualifying_hubs = sorted(
                hub for hub in wt_hubs if set(addresses).issubset(funders_by_hub[hub])
            )
            operations = tuple(sorted({
                operation for hub in qualifying_hubs for operation in wt_hubs[hub]
            }))
            if len(addresses) >= 3 and len(operations) >= 2:
                identity_observations.append(IdentityObservation(
                    candidate_key=_legacy_key("WATCHTOWER"),
                    evidence_type="CONFIRMED_INFRASTRUCTURE_REUSE",
                    category=EVIDENCE_IDENTITY,
                    confidence=_confidence("CONFIRMED_INFRASTRUCTURE_REUSE"),
                    reason=(f"The same confirmed treasury and two signallers provision "
                            f"{len(qualifying_hubs)} hubs used by {len(operations)} operations."),
                    source_tables=(
                        "wt_known_operator_hubs", "wt_operation_members",
                        "creator_funders", "wt_vanity_families",
                    ),
                    entities=(*addresses, *qualifying_hubs),
                    operations=operations,
                    legacy_source="WATCHTOWER_44OR",
                    legacy_identifier="WATCHTOWER",
                    details=_details(hub_count=len(qualifying_hubs)),
                ))

                if _table_exists(live, "wt_provisioning_hubs"):
                    amount_rows = live.execute(
                        f"SELECT treasury_amount,COUNT(*) hub_count FROM wt_provisioning_hubs "
                        f"WHERE hub_address IN ({','.join('?' for _ in qualifying_hubs)}) "
                        "GROUP BY treasury_amount HAVING COUNT(*)>=2 ORDER BY treasury_amount",
                        tuple(qualifying_hubs),
                    ).fetchall()
                    for amount_row in amount_rows:
                        supporting.append(EvidenceObservation(
                            candidate_key=_legacy_key("WATCHTOWER"),
                            evidence_type="MATCHING_FUNDING_TEMPLATE",
                            category=EVIDENCE_SUPPORTING,
                            confidence=_confidence("MATCHING_FUNDING_TEMPLATE"),
                            reason=(f"{amount_row['hub_count']} confirmed hubs share the exact "
                                    f"{amount_row['treasury_amount']} SOL treasury template."),
                            source_tables=("wt_provisioning_hubs",),
                            entities=tuple(qualifying_hubs),
                            legacy_identifier="WATCHTOWER",
                            details=_details(
                                treasury_amount=amount_row["treasury_amount"],
                                hub_count=amount_row["hub_count"],
                            ),
                        ))

        return tuple(identity_observations), tuple(supporting)
