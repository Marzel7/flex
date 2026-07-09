"""
Discovery Lead Scoring Engine

Ranks unconfirmed funders by evidence of operator infrastructure behaviour.
Read-only against wt_ops_v2.db. No RPC. No writes. No confirmation implied.

Badge observability tiers (from audit 2026-06-23):
  ALWAYS RESOLVABLE  — show ✓ or ✗ for every lead
  CONDITIONAL        — show ✓ if found; show ? if lineage is unwalked
"""

from __future__ import annotations
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Badge constants ────────────────────────────────────────────────────────
# Always-resolvable: observable from ops DB without any confirmation
BADGE_IDENTICAL_FUNDING    = "IDENTICAL_FUNDING_PATTERN"
BADGE_TREASURY_FANOUT      = "TREASURY_LIKE_FANOUT"
BADGE_SINGLE_USE_CREATORS  = "SINGLE_USE_CREATORS"
BADGE_FORMING_OPERATION    = "FORMING_OPERATION"
BADGE_SWARM                = "SWARM"
BADGE_ACTIVE_RECENTLY      = "ACTIVE_RECENTLY"
BADGE_ACTIVE_7D            = "ACTIVE_7D"
BADGE_REPEAT_FUNDER        = "REPEAT_FUNDER"

# Conditional: show ✓ if found in DB; show ? if lineage is unwalked
BADGE_WRAP_CLOSE           = "WRAP_CLOSE"
BADGE_TREASURY_PROXIMITY   = "TREASURY_PROXIMITY"
BADGE_SUBPROV_PROXIMITY    = "SUBPROV_PROXIMITY"

# Disqualifier
BADGE_REPEAT_CREATOR       = "REPEAT_CREATOR"

# Weights for scoring — conditional badges only add when PRESENT (not 0 when ?)
_WEIGHTS = {
    # Structural (high weight — hard to produce accidentally)
    BADGE_IDENTICAL_FUNDING:   10,
    BADGE_TREASURY_FANOUT:     10,
    BADGE_WRAP_CLOSE:          10,   # only added when confirmed present
    BADGE_TREASURY_PROXIMITY:   8,   # only added when confirmed present
    BADGE_SUBPROV_PROXIMITY:    8,   # only added when confirmed present
    # Behavioural
    BADGE_SINGLE_USE_CREATORS: 10,
    BADGE_REPEAT_FUNDER:        3,
    BADGE_SWARM:                3,
    # Temporal
    BADGE_ACTIVE_RECENTLY:      5,
    BADGE_ACTIVE_7D:            3,
    # FORMING_OPERATION weight is dynamic (5 + conf*5), handled inline
}

# Tier thresholds
TIER_HIGH   = 60
TIER_MEDIUM = 35
TIER_LOW    = 15   # below = NOISE


@dataclass
class BadgeState:
    """Tri-state badge: PRESENT / ABSENT / UNKNOWN."""
    name: str
    state: str        # "PRESENT" | "ABSENT" | "UNKNOWN"
    detail: str = ""  # human-readable reason shown in UI

    @property
    def is_present(self) -> bool:
        return self.state == "PRESENT"

    @property
    def is_unknown(self) -> bool:
        return self.state == "UNKNOWN"

    def symbol(self) -> str:
        return {"PRESENT": "✓", "ABSENT": "✗", "UNKNOWN": "?"}[self.state]


@dataclass
class DiscoveryLead:
    funder: str
    score: int
    tier: str           # HIGH / MEDIUM / LOW / NOISE
    creator_count: int
    single_use_ratio: float
    instant_ratio: float
    op_uuid: Optional[str]
    op_confidence: Optional[float]
    first_seen: Optional[int]
    last_seen: Optional[int]
    badges: list[BadgeState] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    in_review: bool = False     # already in wt_treasury_review PENDING
    disqualified: bool = False  # REPEAT_CREATOR hard filter

    def badge(self, name: str) -> Optional[BadgeState]:
        for b in self.badges:
            if b.name == name:
                return b
        return None

    def to_dict(self) -> dict:
        return {
            "funder": self.funder,
            "score": self.score,
            "tier": self.tier,
            "creator_count": self.creator_count,
            "single_use_ratio": self.single_use_ratio,
            "instant_ratio": self.instant_ratio,
            "op_uuid": self.op_uuid,
            "op_confidence": self.op_confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "in_review": self.in_review,
            "disqualified": self.disqualified,
            "badges": [
                {"name": b.name, "state": b.state, "symbol": b.symbol(), "detail": b.detail}
                for b in self.badges
            ],
            "reasons": self.reasons,
        }


def _safe_log2_score(count: int) -> int:
    """Logarithmic creator-count score, max 30 pts."""
    if count < 1:
        return 0
    return min(30, round(10 * math.log2(count)))


def score_lead(lead: DiscoveryLead) -> None:
    """Compute score in-place from badge states. Mutates lead.score and lead.tier."""
    s = 0

    for b in lead.badges:
        if b.name == BADGE_REPEAT_CREATOR:
            lead.disqualified = True
            lead.tier = "NOISE"
            lead.score = 0
            return

    # Creator count (log2)
    s += _safe_log2_score(lead.creator_count)

    for b in lead.badges:
        if b.name == BADGE_FORMING_OPERATION:
            # Dynamic: base 5 + conf*5
            conf = lead.op_confidence or 0.0
            s += 5 + round(conf * 5)
        elif b.name in _WEIGHTS and b.state == "PRESENT":
            s += _WEIGHTS[b.name]
        # UNKNOWN badges contribute 0 (not negative)

    lead.score = min(100, s)
    if lead.score >= TIER_HIGH:
        lead.tier = "HIGH"
    elif lead.score >= TIER_MEDIUM:
        lead.tier = "MEDIUM"
    elif lead.score >= TIER_LOW:
        lead.tier = "LOW"
    else:
        lead.tier = "NOISE"


def build_reasons(lead: DiscoveryLead) -> list[str]:
    """Human-readable 'why surfaced' bullet list."""
    r = []
    if lead.creator_count >= 2:
        r.append(f"{lead.creator_count} creators funded from this wallet")
    elif lead.creator_count == 1:
        r.append("1 creator funded from this wallet")

    if lead.single_use_ratio == 1.0 and lead.creator_count > 0:
        r.append("All creators are single-use (1 token each)")
    elif lead.single_use_ratio > 0:
        r.append(f"{round(lead.single_use_ratio * 100)}% of creators are single-use")

    if lead.instant_ratio == 1.0 and lead.creator_count > 0:
        r.append("All creators launched within 1s of funding (INSTANT mode)")
    elif lead.instant_ratio > 0:
        r.append(f"{round(lead.instant_ratio * 100)}% of creators launched instantly")

    b_idfp = lead.badge(BADGE_IDENTICAL_FUNDING)
    if b_idfp and b_idfp.is_present:
        r.append(b_idfp.detail or "Identical funding amounts across creators (template tail)")

    if lead.op_confidence is not None:
        r.append(f"Forming operation in system (confidence {lead.op_confidence:.1f})")

    b_swarm = lead.badge(BADGE_SWARM)
    if b_swarm and b_swarm.is_present:
        r.append("SWARM buy activity observed on associated mints")

    b_wrap = lead.badge(BADGE_WRAP_CLOSE)
    if b_wrap and b_wrap.is_present:
        r.append("Wrap-close creator-funding mechanism observed")

    b_tp = lead.badge(BADGE_TREASURY_PROXIMITY)
    if b_tp and b_tp.is_present:
        r.append("Directly funds a confirmed WATCHTOWER treasury")

    b_sp = lead.badge(BADGE_SUBPROV_PROXIMITY)
    if b_sp and b_sp.is_present:
        r.append("Sits directly above a confirmed sub-provisioner")

    now = int(time.time())
    if lead.last_seen:
        age_h = (now - lead.last_seen) // 3600
        if age_h < 1:
            r.append("Active within the last hour ← TIME SENSITIVE")
        elif age_h < 24:
            r.append(f"Active {age_h}h ago ← TIME SENSITIVE")
        elif age_h < 168:
            r.append(f"Active {age_h // 24}d ago")
        else:
            r.append(f"Last seen {age_h // 24}d ago")

    return r


def _detect_identical_funding(conn: sqlite3.Connection, funder: str,
                               amounts: list[float]) -> BadgeState:
    """
    Check if funding amounts share the …039280 lamport tail OR have near-zero std-dev.
    Uses data already in memory (amounts list from the query).
    """
    if len(amounts) < 2:
        # Single creator — can't establish pattern, but check the tail
        if amounts and round(amounts[0] * 1e9) % 1_000_000 == 39280:
            return BadgeState(
                BADGE_IDENTICAL_FUNDING, "PRESENT",
                f"Funding amount has WSOL-rent template tail (…039280)"
            )
        return BadgeState(BADGE_IDENTICAL_FUNDING, "ABSENT")

    lamports = [round(a * 1e9) for a in amounts if a]
    # Template tail check: all end in 039280
    tail_match = all(l % 1_000_000 == 39280 for l in lamports)
    # Variance check: all within 0.001 SOL of each other
    if lamports:
        spread = (max(lamports) - min(lamports)) / 1e9
        near_zero_var = spread < 0.001
    else:
        near_zero_var = False

    if tail_match:
        unique_bases = len(set(l // 1_000_000 for l in lamports))
        detail = (f"All {len(amounts)} creators funded with identical amounts "
                  f"({amounts[0]:.6f} SOL)" if unique_bases == 1
                  else f"All {len(amounts)} creators funded with …039280 WSOL-rent template tail")
        return BadgeState(BADGE_IDENTICAL_FUNDING, "PRESENT", detail)
    if near_zero_var:
        return BadgeState(BADGE_IDENTICAL_FUNDING, "PRESENT",
                          f"All {len(amounts)} creators funded with near-identical amounts (spread <0.001 SOL)")
    return BadgeState(BADGE_IDENTICAL_FUNDING, "ABSENT")


def _detect_wrap_close(conn: sqlite3.Connection, funder: str,
                       creator_wallets: list[str]) -> BadgeState:
    """
    CONDITIONAL badge. Check wt_wrap_close_candidates (creator-keyed).
    If any creator from this funder appears there, WRAP_CLOSE is confirmed.
    If the table doesn't exist or no creators match, return UNKNOWN (not ABSENT)
    because the funder's on-chain wrap-close txs may simply not have been seen
    by the cascade yet.
    """
    if not creator_wallets:
        return BadgeState(BADGE_WRAP_CLOSE, "UNKNOWN",
                          "No creators to check — unresolvable without on-chain scan")
    try:
        placeholders = ",".join("?" * len(creator_wallets))
        row = conn.execute(
            f"SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE creator IN ({placeholders})",
            creator_wallets
        ).fetchone()
        count = row[0] if row else 0
        if count > 0:
            return BadgeState(BADGE_WRAP_CLOSE, "PRESENT",
                              f"Wrap-close funding confirmed for {count} creator(s)")
        # Table exists, creators checked, none matched — but we only observe through
        # the cascade, so absence here means unobserved, not confirmed-absent.
        return BadgeState(BADGE_WRAP_CLOSE, "UNKNOWN",
                          "Not yet observed via cascade — unresolvable without confirmation")
    except sqlite3.OperationalError:
        return BadgeState(BADGE_WRAP_CLOSE, "UNKNOWN", "Table not available")


def _detect_treasury_proximity(conn: sqlite3.Connection, funder: str) -> BadgeState:
    """
    CONDITIONAL badge. Check wt_treasury_funders: does this wallet fund a confirmed treasury?
    ABSENT is valid here because wt_treasury_funders is populated from webhook hits on
    confirmed treasuries — if funder is in the table, the signal is real.
    If not in the table, it means either: (a) doesn't fund a confirmed treasury, or
    (b) its downstream treasury hasn't been confirmed. We can't distinguish — return UNKNOWN.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM wt_treasury_funders WHERE funder=?", (funder,)
        ).fetchone()
        if row and row[0] > 0:
            tcount = conn.execute(
                "SELECT COUNT(DISTINCT treasury) FROM wt_treasury_funders WHERE funder=?",
                (funder,)
            ).fetchone()[0]
            return BadgeState(BADGE_TREASURY_PROXIMITY, "PRESENT",
                              f"Directly funds {tcount} confirmed WATCHTOWER treasury/treasuries")
        # Not found — could be (a) or (b), can't distinguish
        return BadgeState(BADGE_TREASURY_PROXIMITY, "UNKNOWN",
                          "No confirmed-treasury downstream yet — unresolvable")
    except sqlite3.OperationalError:
        return BadgeState(BADGE_TREASURY_PROXIMITY, "UNKNOWN", "Table not available")


def _detect_subprov_proximity(conn: sqlite3.Connection, funder: str) -> BadgeState:
    """
    CONDITIONAL badge. Check if this funder directly funds a wallet in wt_discovered_subprovs
    with treasury_known=1, or appears in wt_ops_v2_wallets as a treasury root whose
    immediate children are confirmed subprovs.
    If can't be determined, return UNKNOWN.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM wt_discovered_subprovs WHERE immediate_funder=? AND treasury_known=1",
            (funder,)
        ).fetchone()
        if row and row[0] > 0:
            return BadgeState(BADGE_SUBPROV_PROXIMITY, "PRESENT",
                              f"Directly funds {row[0]} known sub-provisioner(s)")
        return BadgeState(BADGE_SUBPROV_PROXIMITY, "UNKNOWN",
                          "Sub-provisioner lineage not yet traced — unresolvable")
    except sqlite3.OperationalError:
        return BadgeState(BADGE_SUBPROV_PROXIMITY, "UNKNOWN", "Table not available")


def _detect_swarm(conn: sqlite3.Connection, mints: list[str]) -> BadgeState:
    """SWARM is always resolvable: wt_swarm_buys is populated regardless of confirmation."""
    if not mints:
        return BadgeState(BADGE_SWARM, "ABSENT")
    try:
        placeholders = ",".join("?" * len(mints))
        row = conn.execute(
            f"SELECT COUNT(DISTINCT mint) FROM wt_swarm_buys WHERE mint IN ({placeholders})",
            mints
        ).fetchone()
        count = row[0] if row else 0
        if count > 0:
            return BadgeState(BADGE_SWARM, "PRESENT",
                              f"Coordinated swarm buys observed on {count} associated mint(s)")
        return BadgeState(BADGE_SWARM, "ABSENT")
    except sqlite3.OperationalError:
        return BadgeState(BADGE_SWARM, "ABSENT")


def _gather_funder_creators(conn: sqlite3.Connection) -> dict:
    """
    Dual-source union of all candidate funders and their creators.
    Source 1: wt_ops_v2_creators JOIN wt_ops_v2  (covers FORMING ops — migration/watch path)
    Source 2: wt_creator_birth_launch             (covers ARMED pipeline path)

    Returns: {funder: {"creators": set, "mints": set, "amounts": list[float],
                        "modes": list[str], "first_seen": int, "last_seen": int,
                        "op_uuid": str|None, "op_confidence": float|None,
                        "token_counts": list[int]}}
    """
    data: dict[str, dict] = {}

    def _ensure(funder):
        if funder not in data:
            data[funder] = {
                "creators": set(), "mints": set(), "amounts": [],
                "modes": [], "first_seen": None, "last_seen": None,
                "op_uuid": None, "op_confidence": None, "token_counts": []
            }
        return data[funder]

    # Source 1: FORMING ops in wt_ops_v2 (treasury_root is the funder for our purposes)
    try:
        rows = conn.execute("""
            SELECT o.treasury_root  AS funder,
                   o.operation_uuid AS op_uuid,
                   o.confidence     AS op_conf,
                   o.first_seen,
                   o.last_seen,
                   oc.creator_wallet,
                   oc.token_mint,
                   oc.funding_amount_sol
            FROM wt_ops_v2 o
            JOIN wt_ops_v2_creators oc ON oc.operation_uuid = o.operation_uuid
            WHERE o.status = 'FORMING'
              AND (o.op_type IS NULL OR o.op_type != 'UNTEMPLATED')
        """).fetchall()
        for r in rows:
            f = _ensure(r["funder"])
            f["creators"].add(r["creator_wallet"])
            if r["token_mint"]:
                f["mints"].add(r["token_mint"])
            if r["funding_amount_sol"]:
                f["amounts"].append(r["funding_amount_sol"])
            if r["op_uuid"]:
                f["op_uuid"] = r["op_uuid"]
                f["op_confidence"] = r["op_conf"]
            if r["first_seen"]:
                f["first_seen"] = min(f["first_seen"], r["first_seen"]) if f["first_seen"] else r["first_seen"]
            if r["last_seen"]:
                f["last_seen"] = max(f["last_seen"], r["last_seen"]) if f["last_seen"] else r["last_seen"]
    except sqlite3.OperationalError:
        pass

    # Source 2: wt_creator_birth_launch (ARMED pipeline path)
    try:
        rows = conn.execute("""
            SELECT treasury AS funder,
                   creator,
                   token_mint,
                   base_amount_sol,
                   creator_mode,
                   funded_at,
                   launched_at
            FROM wt_creator_birth_launch
            WHERE treasury IS NOT NULL
        """).fetchall()
        for r in rows:
            f = _ensure(r["funder"])
            f["creators"].add(r["creator"])
            if r["token_mint"]:
                f["mints"].add(r["token_mint"])
            if r["base_amount_sol"]:
                f["amounts"].append(r["base_amount_sol"])
            if r["creator_mode"]:
                f["modes"].append(r["creator_mode"])
            ts = r["funded_at"] or r["launched_at"]
            if ts:
                f["first_seen"] = min(f["first_seen"], ts) if f["first_seen"] else ts
                f["last_seen"] = max(f["last_seen"], ts) if f["last_seen"] else ts
    except sqlite3.OperationalError:
        pass

    # Fetch token_count per creator from wt_ops_v2 evidence or use wt_ops_v2_creators
    # We check single-use via wt_ops_v2: each creator_wallet should map to exactly 1 token_mint
    # Already captured via the joins above (one row per creator+mint combination).
    # Count distinct mints per creator across both sources for token_count proxy.
    # For strict accuracy, check wt_ops_v2_creators for multi-mint creators:
    try:
        multi_rows = conn.execute("""
            SELECT creator_wallet, COUNT(DISTINCT token_mint) as mint_count
            FROM wt_ops_v2_creators
            GROUP BY creator_wallet
            HAVING mint_count > 1
        """).fetchall()
        multi_creators = {r["creator_wallet"]: r["mint_count"] for r in multi_rows}
        for funder, f in data.items():
            for c in f["creators"]:
                f["token_counts"].append(multi_creators.get(c, 1))
    except sqlite3.OperationalError:
        pass

    return data


def _get_review_status(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {treasury: status} for all wallets in wt_treasury_review."""
    try:
        rows = conn.execute("SELECT treasury, status FROM wt_treasury_review").fetchall()
        return {r["treasury"]: r["status"] for r in rows}
    except sqlite3.OperationalError:
        return {}


def _get_confirmed(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()
        return {r["treasury"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def build_leads(conn: sqlite3.Connection,
                include_noise: bool = False,
                include_confirmed: bool = False) -> list[DiscoveryLead]:
    """
    Main entry point. Returns scored, ranked discovery leads.
    Suppresses CONFIRMED and REJECTED by default.
    Flags PENDING_REVIEW as in_review=True.
    """
    now = int(time.time())
    funder_data = _gather_funder_creators(conn)
    review_status = _get_review_status(conn)
    confirmed_set = _get_confirmed(conn)

    leads = []

    for funder, d in funder_data.items():
        # Skip confirmed treasuries (they're already in the system)
        if funder in confirmed_set and not include_confirmed:
            continue

        # Review suppression
        status = review_status.get(funder)
        if status in ("CONFIRMED", "REJECTED"):
            continue
        in_review = status == "PENDING_REVIEW"

        creators = list(d["creators"])
        mints = list(d["mints"])
        amounts = d["amounts"]
        modes = d["modes"]
        creator_count = len(creators)

        if creator_count == 0:
            continue

        # Single-use ratio: token_counts gathered above; if empty assume 1 each
        token_counts = d["token_counts"] if d["token_counts"] else [1] * creator_count
        single_use_count = sum(1 for tc in token_counts if tc <= 1)
        single_use_ratio = single_use_count / creator_count

        # Instant ratio from modes (wt_creator_birth_launch source)
        instant_count = modes.count("INSTANT")
        instant_ratio = (instant_count / len(modes)) if modes else 0.0

        # ── Build badges ──────────────────────────────────────────────────

        badges: list[BadgeState] = []

        # REPEAT_CREATOR disqualifier (any creator with >1 token)
        if any(tc > 1 for tc in token_counts):
            badges.append(BadgeState(BADGE_REPEAT_CREATOR, "PRESENT",
                                     "One or more creators has deployed multiple tokens"))
            lead = DiscoveryLead(
                funder=funder, score=0, tier="NOISE",
                creator_count=creator_count, single_use_ratio=single_use_ratio,
                instant_ratio=instant_ratio, op_uuid=d["op_uuid"],
                op_confidence=d["op_confidence"], first_seen=d["first_seen"],
                last_seen=d["last_seen"], badges=badges, reasons=[], in_review=in_review,
                disqualified=True
            )
            if include_noise:
                leads.append(lead)
            continue

        # IDENTICAL_FUNDING_PATTERN (always resolvable)
        badges.append(_detect_identical_funding(conn, funder, amounts))

        # TREASURY_LIKE_FANOUT (always resolvable — ≥3 distinct creators)
        if creator_count >= 3:
            badges.append(BadgeState(BADGE_TREASURY_FANOUT, "PRESENT",
                                     f"{creator_count} distinct creators funded — treasury-like distribution"))
        else:
            badges.append(BadgeState(BADGE_TREASURY_FANOUT, "ABSENT"))

        # SINGLE_USE_CREATORS (always resolvable)
        if single_use_ratio == 1.0:
            badges.append(BadgeState(BADGE_SINGLE_USE_CREATORS, "PRESENT",
                                     "All creators are single-use (1 token each)"))
        elif single_use_ratio > 0:
            badges.append(BadgeState(BADGE_SINGLE_USE_CREATORS, "PRESENT",
                                     f"{round(single_use_ratio*100)}% of creators are single-use"))
        else:
            badges.append(BadgeState(BADGE_SINGLE_USE_CREATORS, "ABSENT"))

        # FORMING_OPERATION (always resolvable)
        if d["op_uuid"]:
            badges.append(BadgeState(BADGE_FORMING_OPERATION, "PRESENT",
                                     f"Forming operation {d['op_uuid'][:8]}… "
                                     f"(confidence {d['op_confidence']:.2f})"))
        else:
            badges.append(BadgeState(BADGE_FORMING_OPERATION, "ABSENT"))

        # SWARM (always resolvable)
        badges.append(_detect_swarm(conn, mints))

        # REPEAT_FUNDER (always resolvable)
        if creator_count >= 3 and d["first_seen"] and d["last_seen"]:
            span_h = (d["last_seen"] - d["first_seen"]) / 3600
            if span_h >= 24:
                badges.append(BadgeState(BADGE_REPEAT_FUNDER, "PRESENT",
                                         f"Active across {span_h/24:.0f}d with {creator_count} creators"))
            else:
                badges.append(BadgeState(BADGE_REPEAT_FUNDER, "ABSENT"))
        else:
            badges.append(BadgeState(BADGE_REPEAT_FUNDER, "ABSENT"))

        # ACTIVE_RECENTLY / ACTIVE_7D (always resolvable)
        last = d["last_seen"] or 0
        age_s = now - last
        if age_s < 86400:
            badges.append(BadgeState(BADGE_ACTIVE_RECENTLY, "PRESENT",
                                     f"Activity within the last {age_s//3600}h"))
            badges.append(BadgeState(BADGE_ACTIVE_7D, "PRESENT", "Active within 7 days"))
        elif age_s < 604800:
            badges.append(BadgeState(BADGE_ACTIVE_RECENTLY, "ABSENT"))
            badges.append(BadgeState(BADGE_ACTIVE_7D, "PRESENT",
                                     f"Activity {age_s//86400}d ago"))
        else:
            badges.append(BadgeState(BADGE_ACTIVE_RECENTLY, "ABSENT"))
            badges.append(BadgeState(BADGE_ACTIVE_7D, "ABSENT"))

        # ── Conditional badges ────────────────────────────────────────────
        badges.append(_detect_wrap_close(conn, funder, creators))
        badges.append(_detect_treasury_proximity(conn, funder))
        badges.append(_detect_subprov_proximity(conn, funder))

        lead = DiscoveryLead(
            funder=funder, score=0, tier="NOISE",
            creator_count=creator_count, single_use_ratio=single_use_ratio,
            instant_ratio=instant_ratio, op_uuid=d["op_uuid"],
            op_confidence=d["op_confidence"], first_seen=d["first_seen"],
            last_seen=d["last_seen"], badges=badges, reasons=[], in_review=in_review
        )

        score_lead(lead)
        lead.reasons = build_reasons(lead)

        if lead.tier == "NOISE" and not include_noise:
            continue

        leads.append(lead)

    leads.sort(key=lambda l: (-l.score, l.funder))
    return leads
