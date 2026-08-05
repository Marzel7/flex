"""Read-only X20.9 treasury expansion evidence report.

Given a walkback that reaches an unconfirmed treasury via an already-known
sub-provisioner, this module answers a narrow question using only persisted
intelligence: does the sub-provisioner's upstream structure carry any evidence
that already links it to a confirmed operator's known infrastructure?

This module performs no detection, no RPC, no scoring, and no writes. It never
confirms treasury ownership, never touches wt_confirmed_treasuries, never
creates operator membership, and never alters identity confidence. Its sole
output is an evidence report: which known signals matched, which known signals
were checked but did not match, and which signal types this platform does not
yet persist at all. Confidence bands are a direct, honest function of the
COUNT of matched deterministic signals, not a weighted or opaque score.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID

# X75.0 — display_name -> operator_id lookup so a curated hub's
# operator_identity label (e.g. from wt_known_operator_hubs) can resolve to
# whichever CONFIRMED operator it actually names, not only WATCHTOWER. A
# label with no matching confirmed operator today resolves to None rather
# than being silently dropped or defaulted to WATCHTOWER.
def _resolve_operator_id_by_display_name(conn: "sqlite3.Connection", tables: set, name: str | None) -> str | None:
    if not name or "operators" not in tables:
        return None
    row = conn.execute(
        "SELECT operator_id FROM operators WHERE UPPER(display_name)=UPPER(?) AND status='CONFIRMED' LIMIT 1",
        (name,),
    ).fetchone()
    return row["operator_id"] if row else None


# Deterministic, currently-persisted signal types. Each entry is (key, label).
# This list is the full inventory this resolver knows how to check today;
# anything not listed here is reported under "evidence not currently available"
# rather than silently omitted.
_CHECKED_SIGNALS = (
    ("vanity_family", "Vanity-family relationship to a known operator treasury"),
    ("known_operator_hub", "Known operator provisioning hub (curated)"),
    ("provisioning_hub", "Provisioning hub address overlap"),
)

# Signal types the sprint brief names but that are not currently persisted
# richly enough (or at all) to serve as deterministic evidence. Listed
# explicitly so the report never silently implies these were checked.
_UNAVAILABLE_SIGNALS = (
    "Funding mechanism history (currently >95% unpopulated for discovered sub-provisioners)",
    "Treasury evolution / rotation history (no audit trail persisted for this column today)",
    "Behaviour timeline for this sub-provisioner or treasury",
    "Funding cadence / timing baseline (not currently computed or persisted)",
    "Historical treasury reassignments for this sub-provisioner",
)

def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class TreasuryExpansionResolver:
    """Read-only evidence report for an unconfirmed treasury behind a known sub-provisioner."""

    def __init__(self, ops_db_path: str, core_db_path: str) -> None:
        self.ops_db_path = ops_db_path
        self.core_db_path = core_db_path

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def evaluate(self, subprov: str | None, treasury: str | None) -> dict[str, Any] | None:
        """Return an evidence report for (subprov, treasury), or None if there is nothing to evaluate.

        Returns None when the treasury is already confirmed (this resolver only speaks to
        UNCONFIRMED treasuries) or when neither address is available to check.
        """
        if not treasury and not subprov:
            return None

        ops_conn = self._connect(self.ops_db_path)
        try:
            ops_tables = self._tables(ops_conn)
            if treasury and "operator_entities" in ops_tables:
                already_confirmed = ops_conn.execute(
                    "SELECT 1 FROM operator_entities WHERE entity_address=? LIMIT 1", (treasury,)
                ).fetchone()
                if already_confirmed:
                    # This resolver exists to surface UNCONFIRMED treasuries. A treasury
                    # already in operator_entities has canonical identity already — nothing
                    # for this module to add.
                    return None

            matched: list[dict[str, Any]] = []
            checked_no_match: list[dict[str, Any]] = []

            vanity_hit = self._vanity_family_match(ops_conn, ops_tables, treasury)
            (matched if vanity_hit else checked_no_match).append(
                vanity_hit or {"signal": "vanity_family", "label": _label("vanity_family")}
            )
        finally:
            ops_conn.close()

        core_conn = self._connect(self.core_db_path) if os.path.exists(self.core_db_path) else None
        try:
            core_tables = self._tables(core_conn) if core_conn else set()

            hub_hit = self._known_operator_hub_match(core_conn, core_tables, treasury, subprov)
            (matched if hub_hit else checked_no_match).append(
                hub_hit or {"signal": "known_operator_hub", "label": _label("known_operator_hub")}
            )

            prov_hit = self._provisioning_hub_match(core_conn, core_tables, treasury, subprov)
            (matched if prov_hit else checked_no_match).append(
                prov_hit or {"signal": "provisioning_hub", "label": _label("provisioning_hub")}
            )
        finally:
            if core_conn is not None:
                core_conn.close()

        if not matched and not checked_no_match:
            return None

        # X75.0: candidate_operator_id is derived per-signal where a signal
        # resolves one explicitly (currently only known_operator_hub, since
        # it's the only signal carrying a curated operator label). Other
        # matched signals (vanity_family, provisioning_hub) carry no operator
        # label of their own; for those, WATCHTOWER remains the fallback
        # ONLY because it is the sole operator these signal types have ever
        # been evaluated against — this is unchanged prior behaviour for
        # those two signals, not a new assumption.
        resolved_operator_id = next(
            (m.get("candidate_operator_id") for m in matched if m.get("candidate_operator_id")),
            None,
        )
        candidate_operator_id = resolved_operator_id or (WATCHTOWER_OPERATOR_ID if matched else None)

        # No probabilistic label is assigned here ("possible"/"probable" would be an
        # interpretation this engine is not positioned to make). The output is the count
        # of matched, named, evidence-cited signals; the analyst draws the conclusion.
        return {
            "subprov": subprov,
            "treasury": treasury,
            "candidate_operator_id": candidate_operator_id,
            "matched_signal_count": len(matched),
            "matched_evidence": matched,
            "checked_no_match": checked_no_match,
            "evidence_not_available": list(_UNAVAILABLE_SIGNALS),
            "status": "TREASURY_REVIEW_LEAD" if matched else "NO_MEANINGFUL_MATCH",
        }

    def _vanity_family_match(
        self, conn: sqlite3.Connection, tables: set[str], treasury: str | None
    ) -> dict[str, Any] | None:
        """A treasury is evidence-relevant here only if it is NOT already a known member
        of a vanity family (any role) — a wallet with an existing role (TREASURY,
        SIGNALLER, etc.) is already-known infrastructure, not an unseen candidate this
        resolver exists to surface. Uses the canonical, already-validated
        src.core.vanity_family.detect_vanity_family() rather than reimplementing prefix/
        suffix matching, to avoid duplicating (and risking drifting from) its
        MIN_PREFIX_LEN/MIN_SUFFIX_LEN collision-avoidance rules.
        """
        if not treasury or "wt_vanity_families" not in tables:
            return None
        from src.core.vanity_family import detect_vanity_family
        result = detect_vanity_family(treasury, conn)
        if result.get("matched") and result.get("known_member"):
            # Already a named member of a known family (with its own role) —
            # not a new/unseen candidate; nothing for this resolver to add.
            return None
        if not result.get("matched"):
            return None
        known_confirmed = [
            w for w in (result.get("known_wallets") or [])
            if w != treasury and self._is_confirmed_treasury(conn, tables, w)
        ]
        if not known_confirmed:
            return None
        return {
            "signal": "vanity_family",
            "label": _label("vanity_family"),
            "family_label": result.get("family_label"),
            "co_members": known_confirmed,
        }

    @staticmethod
    def _is_confirmed_treasury(conn: sqlite3.Connection, tables: set[str], address: str) -> bool:
        if "operator_entities" not in tables:
            return False
        return bool(conn.execute(
            "SELECT 1 FROM operator_entities WHERE entity_address=? AND entity_type='TREASURY' LIMIT 1",
            (address,),
        ).fetchone())

    def _known_operator_hub_match(
        self, conn: sqlite3.Connection | None, tables: set[str],
        treasury: str | None, subprov: str | None,
    ) -> dict[str, Any] | None:
        """X75.0: matches ANY curated operator_identity label in
        wt_known_operator_hubs, not only WATCHTOWER (the table already
        curates other identities, e.g. OPERATOR_001/OPERATION_ALPHA, that
        were previously silently discarded here). The matched label is
        resolved to a real confirmed operator_id when one exists today;
        otherwise candidate_operator_id is left unresolved (None) rather
        than guessed."""
        if conn is None or "wt_known_operator_hubs" not in tables:
            return None
        for address in (a for a in (treasury, subprov) if a):
            row = conn.execute(
                "SELECT hub_wallet, operator_identity, confidence FROM wt_known_operator_hubs WHERE hub_wallet=?",
                (address,),
            ).fetchone()
            if row and row["operator_identity"]:
                candidate_operator_id = None
                try:
                    ops_conn = self._connect(self.ops_db_path)
                    try:
                        candidate_operator_id = _resolve_operator_id_by_display_name(
                            ops_conn, self._tables(ops_conn), row["operator_identity"]
                        )
                    finally:
                        ops_conn.close()
                except sqlite3.Error:
                    pass
                return {
                    "signal": "known_operator_hub",
                    "label": _label("known_operator_hub"),
                    "hub_wallet": row["hub_wallet"],
                    "curated_confidence": row["confidence"],
                    "curated_operator_identity": row["operator_identity"],
                    "candidate_operator_id": candidate_operator_id,
                }
        return None

    def _provisioning_hub_match(
        self, conn: sqlite3.Connection | None, tables: set[str],
        treasury: str | None, subprov: str | None,
    ) -> dict[str, Any] | None:
        if conn is None or "wt_provisioning_hubs" not in tables:
            return None
        for address in (a for a in (treasury, subprov) if a):
            row = conn.execute(
                "SELECT hub_address, status, confidence FROM wt_provisioning_hubs WHERE hub_address=?",
                (address,),
            ).fetchone()
            if row and str(row["status"] or "").upper() == "CONFIRMED":
                return {
                    "signal": "provisioning_hub",
                    "label": _label("provisioning_hub"),
                    "hub_address": row["hub_address"],
                    "hub_confidence": row["confidence"],
                }
        return None


def _label(signal_key: str) -> str:
    return dict(_CHECKED_SIGNALS)[signal_key]
