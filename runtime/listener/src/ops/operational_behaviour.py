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

# X26.8 — canonical funder-role classification. wt_discovered_subprovs.state
# is the platform's own confirmed classification (X26.3 introduced
# REJECTED_INFRASTRUCTURE for wallets promoted purely from funding recurrence
# that turned out to be known automation/CEX/relay/bridge infrastructure).
# A REJECTED* state must override every historical role-shaped signal
# (funding edges, provisioning sessions, creator counts) that this module
# would otherwise narrate as "sub-provisioner" behaviour — those signals
# remain real observations, they just describe a rejected wallet, not a
# valid provisioning role.
ROLE_VALID_SUBPROVISIONER = "VALID_SUBPROVISIONER"
ROLE_REJECTED_INFRASTRUCTURE = "REJECTED_INFRASTRUCTURE"
ROLE_OTHER_REJECTED = "OTHER_REJECTED"
ROLE_UNRESOLVED_FUNDER = "UNRESOLVED_FUNDER"


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _resolve_funder_role(subprov_facts: Optional[dict[str, Any]], subprov: Optional[str]) -> str:
    """X26.8 Phase 2 — determine the funder's CURRENT canonical role before
    building any Behaviour wording. wt_discovered_subprovs.state is
    authoritative when a row exists. The reviewed infrastructure registry
    (src/utils/infra_mapping.py, the same registry X26.3's exclusion logic
    trusts) is also consulted directly so a known infrastructure wallet is
    never narrated as a sub-provisioner even if a stale row/edge persists
    without yet being marked REJECTED* -- the registry match wins."""
    if subprov:
        try:
            from src.utils.infra_mapping import is_known_account
            if is_known_account(subprov):
                return ROLE_REJECTED_INFRASTRUCTURE
        except ImportError:
            pass
    if subprov_facts is None:
        return ROLE_UNRESOLVED_FUNDER
    state = str(subprov_facts.get("state") or "").upper()
    if state.startswith("REJECTED_INFRASTRUCTURE") or state == "REJECTED_INFRASTRUCTURE":
        return ROLE_REJECTED_INFRASTRUCTURE
    if state.startswith("REJECTED"):
        return ROLE_OTHER_REJECTED
    return ROLE_VALID_SUBPROVISIONER


def assert_no_infrastructure_subprovisioner_conflict(
    attribution_outcome: Optional[dict[str, Any]],
    operational_behaviour: Optional[dict[str, Any]],
) -> None:
    """X26.8 Phase 8 — response-level invariant: the same wallet must never
    be presented BOTH as a Known infrastructure boundary (attribution_outcome
    .terminal_entity/terminal_entity_type) AND as a valid sub-provisioner in
    Operational Behaviour's own wording within one Discovery response. Raises
    AssertionError if violated. Intended for tests and optional defensive use
    by callers that assemble a full Discovery response; not wired into the
    hot request path itself, since the two sections are built independently
    and this is a cross-cutting sanity check, not a per-request gate."""
    if not attribution_outcome or not operational_behaviour:
        return
    terminal_type = str(attribution_outcome.get("terminal_entity_type") or "").upper()
    if terminal_type not in {"INFRASTRUCTURE", "AUTOMATION", "RELAY", "CEX", "CUSTODY", "BRIDGE"}:
        return
    terminal_entity = attribution_outcome.get("terminal_entity")
    if not terminal_entity:
        return
    entities = operational_behaviour.get("entities") or {}
    if entities.get("subprov") != terminal_entity:
        return
    role_wording = (
        operational_behaviour.get("behaviour_summary", [])
        + [p.get("label", "") for p in operational_behaviour.get("infrastructure_pattern", [])]
    )
    for line in role_wording:
        assert not str(line).startswith("Sub-provisioner "), (
            f"Known infrastructure boundary ({terminal_entity}) is also being narrated as a "
            f"valid sub-provisioner in Operational Behaviour: {line!r}"
        )


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
        creator: Optional[str] = None, terminal_infrastructure: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Build the full behaviour report for whichever addresses are known.
        Returns None only if NOTHING is known at all (no mint, no addresses) —
        otherwise always returns a report, with individual sections honestly
        reporting "not yet captured" where no data exists.

        X26.10 — `terminal_infrastructure` is the reviewed-infrastructure
        address Discovery's attribution_outcome resolved as the terminal
        boundary (attribution_outcome.terminal_entity), passed in ONLY when
        that address is not already available as `subprov` (e.g. a CEX/
        bridge/relay wallet recorded in wt_walkback_queue.funder_wallet or
        .treasury rather than .subprov -- a different column than the one
        this service otherwise reads). This closes a real coverage gap found
        by this sprint's audit: attribution correctly identifies these
        wallets as reviewed terminal infrastructure, but Operational
        Behaviour previously rendered completely empty for them because it
        was never given the address at all. If `subprov` is already set,
        `terminal_infrastructure` is ignored -- this parameter only ever
        fills a gap, never overrides an existing value."""
        if not source_mint and not treasury and not subprov and not creator and not terminal_infrastructure:
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

            # X26.10 — only ever fills the gap when subprov is otherwise
            # unresolved; a genuine sub-provisioner address always wins.
            if not subprov and terminal_infrastructure:
                subprov = terminal_infrastructure

            edges = self._find_edges(ops_conn, ops_tables, treasury, subprov, creator)
            subprov_facts = self._subprov_facts(ops_conn, ops_tables, subprov)
            treasury_review = self._treasury_review_facts(ops_conn, ops_tables, treasury)

            # X26.8 Phase 2 — the funder's CURRENT canonical role must be
            # resolved before any wording is built, so a REJECTED*
            # wt_discovered_subprovs state (or a registry-known infrastructure
            # match) overrides every historical role-shaped signal below, in
            # every section at once. Computed here (inside the ops_conn block)
            # so the X26.9.1 infrastructure-activity lookup below can share
            # the same connection.
            funder_role = _resolve_funder_role(subprov_facts, subprov)
            infra_activity = self._infrastructure_activity_facts(ops_conn, ops_tables, subprov, funder_role)
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

        behaviour_summary = self._build_behaviour_summary(session, edges, subprov_facts, funder_role, infra_activity, subprov)
        timing = self._build_timing(session)
        infrastructure_pattern = self._build_infrastructure_pattern(
            edges, subprov_facts, treasury_review, hub_facts, funder_role, infra_activity, subprov
        )
        consistency = self._build_consistency(edges, subprov_facts, treasury_review, hub_facts, timing, funder_role)
        missing_evidence = self._build_missing_evidence(
            session, edges, subprov_facts, treasury_review, hub_facts, timing, funder_role
        )

        return {
            "behaviour_summary": behaviour_summary,
            "timing": timing,
            "infrastructure_pattern": infrastructure_pattern,
            "operational_consistency": consistency,
            "missing_evidence": missing_evidence,
            # X26.9.1 — explicitly named infrastructure activity fields, never
            # overloaded onto creator_count. None for VALID_SUBPROVISIONER/
            # UNRESOLVED_FUNDER (those roles keep using wt_discovered_subprovs
            # .creator_count via the sections above).
            "infrastructure_activity": infra_activity,
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
            "wrap_close_count, state, rejected_reason FROM wt_discovered_subprovs WHERE subprov=?", (subprov,)
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

    def _infrastructure_activity_facts(
        self, conn: sqlite3.Connection, tables: set[str], subprov: Optional[str], funder_role: str,
    ) -> Optional[dict[str, int]]:
        """X26.9.1 — for a REJECTED_INFRASTRUCTURE funder, wt_discovered_subprovs
        .creator_count is a frozen historical value from a since-superseded
        promotion path (X26.9's audit measured it as a narrow subset scoped to
        one specific outcome type, not a live or all-time count). The correct,
        live-queryable activity metrics for an infrastructure/CEX/relay/bridge
        entity are:
          - attributed_launch_count: distinct launches whose attribution
            walkback terminated here (the canonical "how many launches
            dead-ended at this boundary" figure).
          - observed_creator_count: distinct creators recorded as having
            transacted with this wallet across the walkback queue, regardless
            of final outcome (broader than the frozen subprov count, narrower
            than nothing -- the best available "how many creators" answer).
        Returns None for VALID_SUBPROVISIONER/UNRESOLVED_FUNDER -- these
        metrics are specific to a rejected/infrastructure role; a genuine
        sub-provisioner keeps using wt_discovered_subprovs.creator_count."""
        if funder_role not in (ROLE_REJECTED_INFRASTRUCTURE, ROLE_OTHER_REJECTED) or not subprov:
            return None
        attributed_launch_count = None
        if "wt_attribution_outcomes" in tables:
            row = conn.execute(
                "SELECT COUNT(DISTINCT mint) AS n FROM wt_attribution_outcomes WHERE terminal_entity=?",
                (subprov,),
            ).fetchone()
            attributed_launch_count = row["n"] if row else 0
        observed_creator_count = None
        if "wt_walkback_queue" in tables:
            row = conn.execute(
                "SELECT COUNT(DISTINCT creator) AS n FROM wt_walkback_queue "
                "WHERE (funder_wallet=? OR subprov=?) AND creator IS NOT NULL",
                (subprov, subprov),
            ).fetchone()
            observed_creator_count = row["n"] if row else 0
        if attributed_launch_count is None and observed_creator_count is None:
            return None
        return {
            "attributed_launch_count": attributed_launch_count or 0,
            "observed_creator_count": observed_creator_count or 0,
            # X26.9.1 Phase 8 (per the sprint brief) — these counts represent
            # launches/creators present in the persisted attribution and
            # walkback datasets, not an exhaustive chain-wide total; a launch
            # never routed through walkback (e.g. resolved by a different
            # detection path) would not be reflected here.
            "coverage_note": "Reflects launches/creators present in the persisted attribution and walkback datasets, not an exhaustive chain-wide total.",
        }

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

    @staticmethod
    def _infrastructure_label(subprov: Optional[str]) -> Optional[str]:
        """X26.8 — the reviewed-registry display name for a rejected funder
        (e.g. 'Axiom'), so rejected wording can name what the funder actually
        is instead of the generic 'reviewed infrastructure wallet'."""
        if not subprov:
            return None
        try:
            from src.utils.infra_mapping import get_funder_label
            label = get_funder_label(subprov)
            return label.get("name") if label else None
        except ImportError:
            return None

    # X26.10 Phase 3 — presentation-only TERMINAL_INFRASTRUCTURE subtypes.
    # Not a database state, not an attribution concept -- exists solely to
    # pick the right noun phrase for "Funding source: {name} · reviewed
    # {phrase}" so every reviewed terminal-infrastructure class (CEX,
    # automation, bridge, relay, custody, and any future registry category)
    # renders the same evidence model, differing only in this one phrase.
    # Adding a new registry category later requires nothing beyond adding it
    # to this map (or it falls through to the OTHER default) -- no new
    # rendering branch is ever needed (Phase 7 future-proofing).
    _SUBTYPE_PHRASES = {
        "CEX": "exchange",
        "AUTOMATION": "automation infrastructure",
        "BRIDGE": "bridge",
        "RELAY": "relay",
        "CUSTODY": "custody infrastructure",
        "PLATFORM": "platform infrastructure",
        "PROTOCOL": "protocol infrastructure",
        "SYSTEM": "system infrastructure",
    }

    @classmethod
    def _terminal_infrastructure_label(cls, subprov: Optional[str]) -> Optional[str]:
        """X26.10 — returns 'Name · reviewed <subtype phrase>' (e.g. 'Axiom ·
        reviewed automation infrastructure', 'Binance · reviewed exchange')
        for any wallet in the reviewed infrastructure/CEX registry, using the
        SAME registry X26.3's exclusion logic and X26.8's role resolution
        already trust -- no new registry, no duplicated classification."""
        if not subprov:
            return None
        try:
            from src.utils.infra_mapping import CEX_ACCOUNTS, INFRASTRUCTURE_ACCOUNTS
        except ImportError:
            return None
        cex = CEX_ACCOUNTS.get(subprov)
        if cex:
            name = cex.get("name") or cex.get("exchange") or "Known CEX"
            return f"{name} · reviewed {cls._SUBTYPE_PHRASES['CEX']}"
        infra = INFRASTRUCTURE_ACCOUNTS.get(subprov)
        if infra:
            name = infra.get("name") or "Known infrastructure"
            category = str(infra.get("category") or "").upper()
            phrase = cls._SUBTYPE_PHRASES.get(category, "infrastructure")
            return f"{name} · reviewed {phrase}"
        return None

    def _build_behaviour_summary(
        self, session: Optional[dict[str, Any]], edges: dict[str, Optional[dict[str, Any]]],
        subprov_facts: Optional[dict[str, Any]], funder_role: str,
        infra_activity: Optional[dict[str, int]] = None, subprov: Optional[str] = None,
    ) -> list[str]:
        """Plain factual statements, each independently true and citable.
        Never a synthesized composite claim.

        X26.8 — every statement that would otherwise assert or imply the
        "sub-provisioner" role is reworded to a neutral, role-free
        observation when funder_role is not VALID_SUBPROVISIONER.

        X26.9.1 — for a rejected/infrastructure funder, wt_discovered_subprovs
        .creator_count is never surfaced (X26.9's audit found it to be a
        frozen historical value from a superseded promotion path, not a live
        or all-time count). Instead, infra_activity's live-queryable
        attributed_launch_count / observed_creator_count are shown, with
        their own explicit field names -- never overloaded onto the
        sub-provisioner-shaped "creator_count" concept."""
        statements: list[str] = []
        t_to_s = edges.get("treasury_to_subprov")
        s_to_c = edges.get("subprov_to_creator")
        is_valid = funder_role == ROLE_VALID_SUBPROVISIONER
        # X26.10 — prefer the actual resolved address (works even when no
        # wt_discovered_subprovs row exists, e.g. a pure registry/CEX/bridge
        # boundary with zero subprov history); fall back to subprov_facts
        # only if the caller didn't pass the address directly.
        subprov = subprov or (subprov_facts or {}).get("subprov")

        if s_to_c and t_to_s:
            t_bt = t_to_s.get("funding_block_time")
            s_bt = s_to_c.get("funding_block_time")
            if t_bt is not None and s_bt is not None and s_bt >= t_bt:
                statements.append(
                    "Creator funded after sub-provisioner (observed order, per persisted block times)"
                    if is_valid else
                    "Creator funded after the upstream funding wallet (observed order, per persisted block times)"
                )
        if s_to_c and s_to_c.get("funding_mechanism"):
            mechanism = s_to_c["funding_mechanism"]
            if is_valid:
                statements.append(f"Sub-provisioner funded creator via {mechanism}")
            else:
                statements.append(f"Creator funding observed via {mechanism}")
        # X26.10.1 Phase 2/3 — a wt_provisioning_edges row with
        # edge_type='TREASURY_TO_SUBPROV' is historical pipeline metadata,
        # not proof of a genuine treasury/sub-provisioner relationship. It
        # must never dictate role-specific wording on its own -- only when
        # funder_role is VALID_SUBPROVISIONER (i.e. treasury and
        # sub-provisioner are BOTH independently, currently established)
        # does "Treasury funded sub-provisioner" describe reality. For a
        # terminal-infrastructure funder, the address the pipeline recorded
        # as "treasury" is often just another wallet that happened to send
        # funds to the same reviewed infrastructure address (confirmed live:
        # a wallet that sent ~59,000 SOL to Binance was being labelled a
        # "treasury" purely from this edge's historical shape) -- omitted
        # entirely rather than reworded, since the creator-funding line
        # above already communicates the one effective observed
        # relationship (same mechanism, same terminal address) without
        # duplicating it under a second, unearned label.
        if is_valid and t_to_s and t_to_s.get("funding_mechanism"):
            statements.append(f"Treasury funded sub-provisioner via {t_to_s['funding_mechanism']}")

        if is_valid:
            if subprov_facts is not None:
                count = subprov_facts.get("creator_count")
                if count is not None:
                    statements.append(
                        f"Sub-provisioner has funded {count} creator{'s' if count != 1 else ''}"
                    )
        elif funder_role == ROLE_REJECTED_INFRASTRUCTURE:
            # X26.10 — unified TERMINAL_INFRASTRUCTURE evidence model: the
            # same "Funding source: {name} · reviewed {subtype}" shape for
            # every reviewed terminal class (CEX, automation, bridge, relay,
            # custody), differing only in the subtype phrase.
            terminal_label = self._terminal_infrastructure_label(subprov)
            statements.append(
                f"Funding source: {terminal_label}" if terminal_label
                else "Funding source is reviewed infrastructure"
            )
            if infra_activity is not None:
                statements.append(f"Launches attributed here: {infra_activity['attributed_launch_count']}")
                statements.append(f"Distinct creators observed: {infra_activity['observed_creator_count']}")
        elif funder_role == ROLE_OTHER_REJECTED:
            statements.append("Funding source is excluded from sub-provisioner classification")
            if infra_activity is not None:
                statements.append(f"Launches attributed here: {infra_activity['attributed_launch_count']}")
                statements.append(f"Distinct creators observed: {infra_activity['observed_creator_count']}")

        if session is not None:
            if is_valid:
                statements.append("Walkback completed successfully (provisioning session recorded)")
            else:
                # X26.8 Phase 5 — wt_provisioning_sessions is operation-agnostic
                # and can record a session for a rejected/infrastructure funder;
                # row existence alone must not imply a valid provisioning role.
                # X26.10.1 Phase 4 — reworded to avoid exposing internal table/
                # column names or implementation conditions in analyst-facing
                # prose; the rest of the card (role-neutral funding line,
                # "Funding source: ... reviewed ...") already communicates
                # that this is not a valid sub-provisioner without restating it.
                statements.append("Funding relationship reconstructed from historical chain data")
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
        treasury_review: Optional[dict[str, Any]], hub_facts: dict[str, Any], funder_role: str,
        infra_activity: Optional[dict[str, int]] = None, subprov: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """X26.8 — role-specific wording ('Sub-provisioner funded...') is only
        used when funder_role is VALID_SUBPROVISIONER.

        X26.9.1 — wt_discovered_subprovs.creator_count is NEVER used for a
        rejected/infrastructure funder (X26.9's audit found it to be a frozen
        historical value, not a live or all-time count). infra_activity's
        attributed_launch_count / observed_creator_count are used instead,
        under their own explicit field names."""
        patterns: list[dict[str, Any]] = []
        is_valid = funder_role == ROLE_VALID_SUBPROVISIONER
        # X26.10 — see _build_behaviour_summary's identical fix: prefer the
        # actual resolved address over subprov_facts, which is None for a
        # pure registry-only boundary with no wt_discovered_subprovs row.
        subprov = subprov or (subprov_facts or {}).get("subprov")

        if is_valid:
            if subprov_facts is not None:
                count = subprov_facts.get("creator_count")
                if count is not None:
                    patterns.append({
                        "label": f"Sub-provisioner funded {count} creator{'s' if count != 1 else ''}",
                        "source": "wt_discovered_subprovs.creator_count",
                    })
        elif funder_role == ROLE_REJECTED_INFRASTRUCTURE:
            infra_name = self._infrastructure_label(subprov)
            prefix = f"Infrastructure wallet ({infra_name})" if infra_name else "Infrastructure wallet"
            if infra_activity is not None:
                patterns.append({
                    "label": f"{prefix}: {infra_activity['attributed_launch_count']} launches attributed here",
                    "source": "wt_attribution_outcomes.terminal_entity",
                })
                patterns.append({
                    "label": f"{prefix}: {infra_activity['observed_creator_count']} distinct creators observed",
                    "source": "wt_walkback_queue",
                })
        elif funder_role == ROLE_OTHER_REJECTED:
            if infra_activity is not None:
                patterns.append({
                    "label": f"{infra_activity['attributed_launch_count']} launches attributed to this excluded funding source",
                    "source": "wt_attribution_outcomes.terminal_entity",
                })
                patterns.append({
                    "label": f"{infra_activity['observed_creator_count']} distinct creators observed from this excluded funding source",
                    "source": "wt_walkback_queue",
                })

        s_to_c = edges.get("subprov_to_creator")
        if s_to_c is not None:
            obs = s_to_c.get("observation_count")
            if obs == 1:
                patterns.append({
                    "label": "First time this exact sub-provisioner→creator funding path was observed"
                        if is_valid else "First observation of this exact funding relationship",
                    "source": "wt_provisioning_edges.observation_count",
                })
            elif obs and obs > 1:
                patterns.append({
                    "label": f"This sub-provisioner→creator funding path observed {obs} times"
                        if is_valid else f"This exact funding relationship observed {obs} times",
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
        funder_role: str,
    ) -> list[dict[str, str]]:
        """Never a percentage or probability — each signal is a plain
        Observed / Not observed / Not yet available / Not applicable fact.

        X26.8 Phase 6 — a provisioning-specific comparison (full sequence,
        repeated treasury) is meaningless when the funder isn't a valid
        sub-provisioner at all; showing "Not observed" there would read as
        "we checked for provisioning and found none" when the real answer is
        "there is no valid provisioning role to check". Facts that remain
        meaningful regardless of role (funding structure, timing, exact
        funding-edge repetition, infrastructure reuse) are unchanged."""
        def _status(condition: Optional[bool]) -> str:
            if condition is None:
                return "Not yet available"
            return "Observed" if condition else "Not observed"

        is_valid = funder_role == ROLE_VALID_SUBPROVISIONER

        infra_reuse = None
        if hub_facts.get("known_operator_hub") is not None or hub_facts.get("provisioning_hub") is not None:
            infra_reuse = bool(hub_facts.get("known_operator_hub") or hub_facts.get("provisioning_hub"))

        funding_structure = None
        s_to_c = edges.get("subprov_to_creator")
        if s_to_c is not None:
            funding_structure = s_to_c.get("funding_mechanism") == "WSOL_WRAP_CLOSE"

        rows = [
            {"signal": "Infrastructure reuse", "status": _status(infra_reuse)},
            {"signal": "Creator funding structure (wrap-close)", "status": _status(funding_structure)},
        ]

        if is_valid:
            repeated_treasury = None
            if treasury_review is not None:
                dc = treasury_review.get("distinct_creators")
                repeated_treasury = dc is not None and dc >= 2
            provisioning_sequence = None
            if edges.get("treasury_to_subprov") and edges.get("subprov_to_creator"):
                provisioning_sequence = True
            elif edges.get("subprov_to_creator") or edges.get("treasury_to_subprov"):
                provisioning_sequence = False
            rows.append({"signal": "Repeated treasury", "status": _status(repeated_treasury)})
            rows.append({"signal": "Full provisioning sequence recorded", "status": _status(provisioning_sequence)})
        else:
            rows.append({"signal": "Repeated treasury", "status": "Not applicable"})
            rows.append({"signal": "Full provisioning sequence recorded", "status": "Not applicable"})

        rows.append({"signal": "Observed timing", "status": "Observed" if timing.get("available") else "Not yet available"})
        return rows

    # ── Part 4: Missing Evidence ──────────────────────────────────────────

    def _build_missing_evidence(
        self, session: Optional[dict[str, Any]], edges: dict[str, Optional[dict[str, Any]]],
        subprov_facts: Optional[dict[str, Any]], treasury_review: Optional[dict[str, Any]],
        hub_facts: dict[str, Any], timing: dict[str, Any], funder_role: str,
    ) -> list[str]:
        """X26.8 Phase 7 — a fact can be MISSING (genuinely absent, could
        still appear), NOT YET AVAILABLE (timing — no signal either way), or
        NOT APPLICABLE (there is no valid sub-provisioner/treasury lineage for
        this concept to apply to at all). Conflating the last case into
        "missing" told the analyst the platform expected and failed to find
        provisioning evidence for a launch that legitimately terminated at
        known infrastructure instead."""
        missing: list[str] = []
        is_valid = funder_role == ROLE_VALID_SUBPROVISIONER

        if is_valid:
            if treasury_review is None or (treasury_review.get("distinct_creators") or 0) < 2:
                missing.append("Repeated treasury (multiple creators funded by the same treasury)")
            s_to_c = edges.get("subprov_to_creator")
            if not s_to_c or (s_to_c.get("observation_count") or 0) < 2:
                missing.append("Repeated provisioning edges (this funding path observed more than once)")
            if subprov_facts is None or (subprov_facts.get("creator_count") or 0) < 2:
                missing.append("Multiple launches from this sub-provisioner")
            if not hub_facts.get("known_operator_hub") and not hub_facts.get("provisioning_hub"):
                missing.append("Provisioning hub reuse")
        elif funder_role == ROLE_REJECTED_INFRASTRUCTURE:
            missing.append("Sub-provisioner recurrence: not applicable — funding source is reviewed infrastructure")
        elif funder_role == ROLE_OTHER_REJECTED:
            missing.append("Sub-provisioner recurrence: not applicable — funding source is excluded from sub-provisioner classification")
        # ROLE_UNRESOLVED_FUNDER: no funder identity resolved at all yet, so
        # none of these concepts have been evaluated either way — omit rather
        # than assert MISSING or NOT APPLICABLE for an unresolved case.

        if not timing.get("available"):
            missing.append("Observed timing history")
        return missing
