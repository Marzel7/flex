"""
WATCHTOWER network detection.

Detects wallets and tokens operationally related to the WATCHTOWER fee-collection
infrastructure. Labels are OPERATIONAL only — no ownership inference.

Labels used:
  "WATCHTOWER-related"
  "Linked to known WATCHTOWER infrastructure"
  "Operationally related to WATCHTOWER"
"""

import sqlite3
import json
import time
from typing import Optional

# ── Known WATCHTOWER infrastructure wallets ───────────────────────────────────

WATCHTOWER_INFRASTRUCTURE: dict[str, str] = {
    "5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H": "WATCHTOWER",
    "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM": "TREASURY",
    "6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1": "TREASURY-UP",
    "4gE46F7x7RtP6KQTbLxXjyDL4bWscDPNYzv5y5E4Afm8": "DEPLOYER",
    "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM": "SIGNALLER",
    "4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q": "PROFIT-RELAY-1",
    "7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A": "PROFIT-RELAY-2",
    "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7":  "PROFIT-RELAY-3",
    "5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7": "PROFIT-RELAY-4",
    "Gc5TEqe9VfQx3mpz3hac8z95LkMBoTUUieErCbEJASTv": "AGGREGATOR-Gc5",
    "Gopx5xiHoDw3fe7hF96SHgw4wQRMgaxT9HRuySM4wRkg": "AGGREGATOR-Gop",
    "EGBQ5t686p1qr4XzTq2Yx5kZBGvYRB1onDatmaMVbhtJ": "COLLECTOR",
    "91sGrsJSZcQkM5JN7nWk3ZwypRU9ph6KeE3heCsfqUGz": "WATCHTOWER-2",
    "3yP29GdJDbMuSJELNj5aydwELWfzZYjS47Yfh7Ey4cDc": "COLLECTOR-2",
    "Du2YEvQEXQvVS8mLC8XVeggq8X73w2JQeW1EdytjvqBe": "COLLECTOR-3",
    "BFneGdwd9yBAh7Dog4nouaXDTuS9KEwMFRsFzePsXWUP": "COLLECTOR-4",
    "46mW9xa7Raq3AQXbs7cDqffxKwfiM9BR9sooxhHrRMEr": "COLLECTOR-5",
    "GRAjn9q2ug1jW9GzHFGoCSsDRffGoedr18sTuE3VAxtA": "COLLECTOR-6",
    "HHsPASDkzEk1p83ef6Ywxz3VNvLdrhhPCtR7ERB5seno": "COLLECTOR-7",
    "5q2cMrPSAY1kzmrWfgazhE8Q5RkDDgxJPmXFCfGnFHEF": "COLLECTOR-8",
    "8Enn7bbCTLCp749sDtrcUyWScKgmiGnEsoQb5xHnfWwK": "COLLECTOR-9",
    "F6nX8qvtgniy6wstEMcVJZPyduFLUYr9Mu7PzCj8RjWY": "COLLECTOR-10",
    "7GYMPGu3UEXRHkJa6MpiXJggGRr5o8My9VNz3wzdqggD": "COLLECTOR-11",
    "BcSScwFvvUCCJT3s3DjZj1FjfwKL178egkzDxGLSwLUg": "COLLECTOR-12",
    "4cGZYGjmbcqush2t5SWv68XeHLsMExiqRA37gvSnSKnt": "COLLECTOR-13",
    "4r65bgGW8bpKfffmfmFiYnfC2y6R1QDWcyFK74AfAvvm": "COLLECTOR-14",
    "2Bvekj5yMhwzAs4D7enGgiReu2sFLHW1CLGesM6MsZd1": "COLLECTOR-15",
    "GbrB9s68e149vDSaseJjYgoHkqJn5gnnW5uibySCP3eB": "COLLECTOR-16",
}

# Addresses to SKIP as scan children — fee collectors and signalling wallets only.
# Profit destinations (COLLECTOR, PROFIT-RELAY, AGGREGATOR) are intentionally
# excluded so the scanner still records edges to them.
_INFRA_SKIP: frozenset[str] = frozenset({
    "5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H",  # WATCHTOWER
    "91sGrsJSZcQkM5JN7nWk3ZwypRU9ph6KeE3heCsfqUGz",   # WATCHTOWER-2
    "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM",   # TREASURY
    "6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1",   # TREASURY-UP
    "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM",   # SIGNALLER
    "4gE46F7x7RtP6KQTbLxXjyDL4bWscDPNYzv5y5E4Afm8",   # DEPLOYER
})

# Full label map — all known infra for display/lookup purposes
_INFRA_SET: frozenset[str] = frozenset(WATCHTOWER_INFRASTRUCTURE.keys())

WATCHTOWER_FEE_AMOUNT_SOL = 0.002839  # fixed operator fee
WATCHTOWER_FEE_TOLERANCE  = 0.000001  # floating-point tolerance

# Weak-signal fingerprints — stored as evidence only, never trigger flag alone
FUNDING_FINGERPRINTS = (".10203928", ".0203928", ".40203928", ".01003928", ".20303928", ".00203928")

# Strong fingerprint batches: (sql_like_pattern, label)
CONFIRMED_FINGERPRINT_BATCHES = [
    ("%.10203928", "April 2026 provisioning batch (.10203928 fingerprint)"),
    ("%.01003928", "May 2026 provisioning batch (.01003928 fingerprint)"),
    ("%.00203928", "May 2026 provisioning batch (.00203928 fingerprint)"),
    ("%.20303928", "May 2026 provisioning batch (.20303928 fingerprint)"),
]

# ── Lineage-aware Rule 2 configuration ────────────────────────────────────────
# Max funding-ancestry hops to walk when looking for WATCHTOWER infra upstream.
# Capped hard: the confirmed topology is creator ← burner ← hub ← TREASURY (3 hops).
LINEAGE_MAX_DEPTH = 3
# Branching cap per node — follow only the largest-SOL funders to avoid fanning
# into the whole graph (and to skip dust/noise edges).
LINEAGE_TOP_FUNDERS = 4
# Ignore funding edges below this (dust pings, rent) when walking ancestry.
LINEAGE_MIN_EDGE_SOL = 0.001

# Provisioning-hub birth signature (for the auto-discovery / signature rule).
HUB_TREASURY_AMOUNTS = (700.0, 800.0)
HUB_TREASURY_TOLERANCE = 5.0          # SOL — treasury injection band per tier
HUB_SIGNALLER_WINDOW_S = 120          # both signaller dust pings within 120s of birth
_SIGNALLER_2 = "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM"

# Service / CEX / infra-internal addresses that must NOT be treated as lineage
# pass-throughs (walking through them would attribute unrelated wallets). Includes
# the System Program and well-known hot wallets; CEX rows are also filtered via the
# creator_funders.is_cex flag at query time.
_LINEAGE_STOP_NODES: frozenset[str] = frozenset({
    "11111111111111111111111111111111",                # System Program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",     # SPL Token program
    "ComputeBudget111111111111111111111111111111",
})


# ── Schema bootstrap ──────────────────────────────────────────────────────────

MIGRATION_SQL = """
-- Per-creator WATCHTOWER linkage
ALTER TABLE creator_risk_scores ADD COLUMN watchtower_related INTEGER NOT NULL DEFAULT 0;
ALTER TABLE creator_risk_scores ADD COLUMN watchtower_evidence_json TEXT;
ALTER TABLE creator_risk_scores ADD COLUMN watchtower_checked_at INTEGER;

-- Index so the live dashboard can filter instantly
CREATE INDEX IF NOT EXISTS idx_crs_watchtower ON creator_risk_scores(watchtower_related)
    WHERE watchtower_related = 1;

-- Per-token WATCHTOWER linkage (mirrors creator for fast token-level queries)
ALTER TABLE token_analysis ADD COLUMN watchtower_related INTEGER NOT NULL DEFAULT 0;
ALTER TABLE token_analysis ADD COLUMN watchtower_evidence_json TEXT;

CREATE INDEX IF NOT EXISTS idx_ta_watchtower ON token_analysis(watchtower_related)
    WHERE watchtower_related = 1;
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add watchtower columns if they don't exist yet. Safe to call repeatedly."""
    existing_crs = {
        r[1] for r in conn.execute("PRAGMA table_info(creator_risk_scores)").fetchall()
    }
    existing_ta = {
        r[1] for r in conn.execute("PRAGMA table_info(token_analysis)").fetchall()
    }

    for col, tbl, existing in [
        ("watchtower_related",       "creator_risk_scores", existing_crs),
        ("watchtower_evidence_json", "creator_risk_scores", existing_crs),
        ("watchtower_checked_at",    "creator_risk_scores", existing_crs),
        ("watchtower_related",       "token_analysis",      existing_ta),
        ("watchtower_evidence_json", "token_analysis",      existing_ta),
    ]:
        if col not in existing:
            default = "NOT NULL DEFAULT 0" if col == "watchtower_related" else ""
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {default}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_crs_watchtower "
        "ON creator_risk_scores(watchtower_related) WHERE watchtower_related = 1"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ta_watchtower "
        "ON token_analysis(watchtower_related) WHERE watchtower_related = 1"
    )

    # Provisioning-hub layer (ephemeral, rotating, auto-discoverable). Kept OUT of
    # the static WATCHTOWER_INFRASTRUCTURE constant on purpose — hubs rotate per
    # launch, so they belong in a revocable table, not a hardcoded set. Only rows
    # with status='CONFIRMED' are treated as infra ancestry by lineage Rule 2.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wt_provisioning_hubs (
            hub_address        TEXT PRIMARY KEY,
            treasury_amount    REAL,
            treasury_ts        INTEGER,
            signaller1_ts      INTEGER,
            signaller2_ts      INTEGER,
            born_at            INTEGER,
            creator_seed_count INTEGER NOT NULL DEFAULT 0,
            create_count       INTEGER NOT NULL DEFAULT 0,
            confidence         REAL    NOT NULL DEFAULT 0,
            status             TEXT    NOT NULL DEFAULT 'CANDIDATE',  -- CANDIDATE | CONFIRMED | RETIRED
            discovered_at      INTEGER,
            last_seen_at       INTEGER,
            evidence_json      TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wph_status "
        "ON wt_provisioning_hubs(status) WHERE status = 'CONFIRMED'"
    )
    conn.commit()


# ── Provisioning-hub layer & lineage walk ─────────────────────────────────────

def discover_provisioning_hubs(
    conn: sqlite3.Connection,
    since_ts: Optional[int] = None,
    promote: bool = True,
) -> list[dict]:
    """
    Auto-discover provisioning hubs from ALREADY-INDEXED edges (no RPC).

    A wallet is a provisioning hub if its funding signature matches:
      • received 700 or 800 SOL (±HUB_TREASURY_TOLERANCE) from TREASURY, AND
      • received BOTH signaller dust pings (SIGNALLER + SIGNALLER_2).

    Signaller-window timing (≤HUB_SIGNALLER_WINDOW_S) is enforced only when
    transfer_index timestamps are available; absent timestamps fall back to
    "both signallers present" (the live worker indexes the timing). The 700/800
    amount ALONE never qualifies — the dual-signaller pings are required, per the
    safety rule that amount-only must not classify.

    Returns the list of newly-promoted hub dicts. If promote=True, upserts each as
    status='CONFIRMED' in wt_provisioning_hubs.
    """
    treasury_filter = ""
    params: list = [_TREASURY, *HUB_TREASURY_AMOUNTS]
    bands = " OR ".join(["ABS(amount_sol - ?) <= ?"] * len(HUB_TREASURY_AMOUNTS))
    band_params: list = []
    for amt in HUB_TREASURY_AMOUNTS:
        band_params += [amt, HUB_TREASURY_TOLERANCE]
    if since_ts is not None:
        treasury_filter = "AND first_detected_at >= ?"

    # Candidate hubs = wallets that received a 700/800 SOL injection from TREASURY.
    sql = (
        "SELECT DISTINCT creator_address FROM creator_funders "
        "WHERE funder_address = ? AND (" + bands + ") " + treasury_filter
    )
    q_params = [_TREASURY, *band_params]
    if since_ts is not None:
        q_params.append(since_ts)
    candidates = [r[0] for r in conn.execute(sql, q_params).fetchall()]

    promoted: list[dict] = []
    now = int(time.time())
    for hub in candidates:
        funders = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT funder_address, amount_sol FROM creator_funders "
                "WHERE creator_address = ?",
                (hub,),
            ).fetchall()
        }
        tre_amt = funders.get(_TREASURY)
        if tre_amt is None:
            continue
        # Require BOTH signaller dust pings — amount alone is never sufficient.
        if _SIGNALLER not in funders or _SIGNALLER_2 not in funders:
            continue

        # Optional timing check via transfer_index, when timestamps exist.
        s1_ts = _edge_ts(conn, _SIGNALLER, hub)
        s2_ts = _edge_ts(conn, _SIGNALLER_2, hub)
        tre_ts = _edge_ts(conn, _TREASURY, hub)
        if tre_ts and s1_ts and s2_ts:
            if max(abs(s1_ts - tre_ts), abs(s2_ts - tre_ts)) > HUB_SIGNALLER_WINDOW_S:
                continue  # signallers not within the birth window → not a hub

        evidence = {
            "method": "provisioning_hub_birth_signature",
            "treasury_amount": tre_amt,
            "dual_signaller": True,
            "signaller_window_verified": bool(tre_ts and s1_ts and s2_ts),
            "discovered_from": "indexed_edges",
        }
        if promote:
            conn.execute(
                """
                INSERT INTO wt_provisioning_hubs
                    (hub_address, treasury_amount, treasury_ts, signaller1_ts,
                     signaller2_ts, born_at, confidence, status,
                     discovered_at, last_seen_at, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?, ?, ?)
                ON CONFLICT(hub_address) DO UPDATE SET
                    status        = 'CONFIRMED',
                    treasury_amount = excluded.treasury_amount,
                    last_seen_at  = excluded.last_seen_at,
                    evidence_json = excluded.evidence_json
                """,
                (hub, tre_amt, tre_ts, s1_ts, s2_ts, tre_ts, 1.0,
                 now, now, json.dumps(evidence)),
            )
        promoted.append({"hub_address": hub, **evidence})
    if promote and promoted:
        conn.commit()
    return promoted


def _edge_ts(conn: sqlite3.Connection, source: str, dest: str) -> Optional[int]:
    """Earliest block_time of a source→dest edge in transfer_index, or None."""
    try:
        row = conn.execute(
            "SELECT MIN(block_time) FROM transfer_index "
            "WHERE source = ? AND destination = ? AND is_valid = 1",
            (source, dest),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row and row[0] else None


def load_confirmed_hubs(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    Return {hub_address: {metadata}} for CONFIRMED provisioning hubs.

    These are treated as WATCHTOWER infra ancestry by lineage Rule 2, but are kept
    in a table (not the static constant) because hubs are ephemeral and rotate.
    Returns {} if the table doesn't exist yet (pre-migration).
    """
    try:
        rows = conn.execute(
            "SELECT hub_address, treasury_amount, born_at, confidence "
            "FROM wt_provisioning_hubs WHERE status = 'CONFIRMED'"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        r[0]: {"treasury_amount": r[1], "born_at": r[2], "confidence": r[3]}
        for r in rows
    }


def _funders_of(
    addr: str,
    conn: sqlite3.Connection,
    infra: frozenset[str],
) -> list[tuple[str, float, str]]:
    """
    Funding ancestors of `addr` from indexed DB tables, largest-SOL first.

    Sources: creator_funders (direct, with CEX flag) ∪ transfer_index (inbound).
    Returns [(funder_address, amount_sol, source_table)]. CEX-flagged funders and
    known service/stop nodes are excluded UNLESS they are themselves WATCHTOWER
    infra (we never want to skip a real infra terminal).
    """
    agg: dict[str, tuple[float, str]] = {}

    for funder, amount, is_cex in conn.execute(
        "SELECT funder_address, amount_sol, is_cex FROM creator_funders "
        "WHERE creator_address = ?",
        (addr,),
    ).fetchall():
        if not funder or funder == addr:
            continue
        if funder not in infra and (is_cex or funder in _LINEAGE_STOP_NODES):
            continue
        amt = amount or 0.0
        if amt < LINEAGE_MIN_EDGE_SOL and funder not in infra:
            continue
        prev = agg.get(funder)
        if prev is None or amt > prev[0]:
            agg[funder] = (amt, "creator_funders")

    try:
        ti_rows = conn.execute(
            "SELECT source, amount_lamports FROM transfer_index "
            "WHERE destination = ? AND is_valid = 1",
            (addr,),
        ).fetchall()
    except sqlite3.OperationalError:
        ti_rows = []
    for source, lamports in ti_rows:
        if not source or source == addr:
            continue
        if source not in infra and source in _LINEAGE_STOP_NODES:
            continue
        amt = (lamports or 0) / 1e9
        if amt < LINEAGE_MIN_EDGE_SOL and source not in infra:
            continue
        prev = agg.get(source)
        if prev is None or amt > prev[0]:
            agg[source] = (amt, "transfer_index")

    return sorted(
        ((a, v[0], v[1]) for a, v in agg.items()),
        key=lambda t: -t[1],
    )


def lineage_to_infra(
    creator_address: str,
    conn: sqlite3.Connection,
    infra: frozenset[str],
    hubs: dict[str, dict],
    max_depth: int = LINEAGE_MAX_DEPTH,
) -> Optional[dict]:
    """
    Walk funding ancestry up to `max_depth` hops looking for a WATCHTOWER terminal.

    A terminal is any node in `infra` (static WATCHTOWER_INFRASTRUCTURE) OR any
    CONFIRMED provisioning hub in `hubs`. Returns the first/shortest match as:
        {"depth": int, "terminal": addr, "terminal_kind": "infra"|"hub",
         "terminal_label": str, "path": [creator, …, terminal]}
    or None if no terminal within `max_depth`.

    Breadth-first so the shortest lineage wins. Branching is capped to the top
    LINEAGE_TOP_FUNDERS by SOL at each node; visited-set prevents cycles.
    """
    # frontier holds (node, path_so_far); start one hop out from the creator
    visited: set[str] = {creator_address}
    frontier: list[tuple[str, list[str]]] = [(creator_address, [creator_address])]

    for depth in range(1, max_depth + 1):
        next_frontier: list[tuple[str, list[str]]] = []
        for node, path in frontier:
            for funder, _amt, _src in _funders_of(node, conn, infra)[:LINEAGE_TOP_FUNDERS]:
                new_path = path + [funder]
                if funder in infra:
                    return {
                        "depth": depth,
                        "terminal": funder,
                        "terminal_kind": "infra",
                        "terminal_label": WATCHTOWER_INFRASTRUCTURE[funder],
                        "path": new_path,
                    }
                if funder in hubs:
                    return {
                        "depth": depth,
                        "terminal": funder,
                        "terminal_kind": "hub",
                        "terminal_label": f"PROVISIONING-HUB ({funder[:8]}…)",
                        "path": new_path,
                    }
                if funder not in visited:
                    visited.add(funder)
                    next_frontier.append((funder, new_path))
        frontier = next_frontier
        if not frontier:
            break
    return None


def _format_lineage_path(path: list[str], infra: frozenset[str], hubs: dict) -> str:
    """Human-readable 'creator ← … ← TREASURY' chain for evidence detail."""
    def lbl(a: str) -> str:
        if a in WATCHTOWER_INFRASTRUCTURE:
            return WATCHTOWER_INFRASTRUCTURE[a]
        if a in hubs:
            return f"HUB:{a[:6]}…"
        return f"{a[:6]}…"
    return " ← ".join(lbl(a) for a in path)


# ── Core detection logic ──────────────────────────────────────────────────────

def detect_watchtower_linkage(
    creator_address: str,
    conn: sqlite3.Connection,
) -> tuple[bool, list[dict]]:
    """
    Evaluate whether a creator wallet is operationally related to WATCHTOWER.

    Returns (is_related, evidence_list).
    evidence_list entries: {"rule": str, "detail": str, "strength": "strong"|"weak"}

    Strong rules trigger the flag. Weak (fingerprint) rules are stored only.
    """
    evidence: list[dict] = []
    strong_hit = False

    # ── Rule 1: creator wallet IS an infrastructure wallet ─────────────────
    if creator_address in _INFRA_SET:
        label = WATCHTOWER_INFRASTRUCTURE[creator_address]
        evidence.append({
            "rule": "direct_infrastructure_wallet",
            "detail": f"Creator address is known WATCHTOWER infrastructure: {label}",
            "strength": "strong",
        })
        strong_hit = True

    # ── Rule 2: creator was funded directly by an infrastructure wallet ────
    funder_rows = conn.execute(
        "SELECT funder_address, amount_sol FROM creator_funders WHERE creator_address = ?",
        (creator_address,),
    ).fetchall()

    for funder_addr, amount_sol in funder_rows:
        if funder_addr in _INFRA_SET:
            label = WATCHTOWER_INFRASTRUCTURE[funder_addr]
            evidence.append({
                "rule": "funded_by_infrastructure",
                "detail": f"Creator funded by {label} ({funder_addr[:20]}…) — {amount_sol:.6f} SOL",
                "strength": "strong",
            })
            strong_hit = True

        # ── Rule 5 (weak): funding fingerprint ────────────────────────────
        if amount_sol is not None:
            amount_str = f"{amount_sol:.8f}"
            for fp in FUNDING_FINGERPRINTS:
                if fp in amount_str:
                    evidence.append({
                        "rule": "funding_fingerprint",
                        "detail": f"Funded with {amount_sol:.8f} SOL (contains '{fp}' fingerprint) "
                                  f"from {funder_addr[:20]}…",
                        "strength": "weak",
                    })
                    break

    # ── Rule 2-lineage: WATCHTOWER infra (or a CONFIRMED provisioning hub) in
    #    the creator's funding ancestry within LINEAGE_MAX_DEPTH hops. This is the
    #    fix for the post-May provisioning-hub topology, where the direct funder is
    #    an ephemeral hub/burner rather than a static infra wallet:
    #        creator ← burner ← HUB ← TREASURY
    #    Only runs if the cheap direct checks above didn't already flag it.
    if not strong_hit:
        _hubs = load_confirmed_hubs(conn)
        lineage = lineage_to_infra(creator_address, conn, _INFRA_SET, _hubs)
        if lineage is not None:
            path_str = _format_lineage_path(lineage["path"], _INFRA_SET, _hubs)
            if lineage["terminal_kind"] == "hub":
                detail = (
                    f"Funding ancestry reaches CONFIRMED WATCHTOWER provisioning hub "
                    f"{lineage['terminal'][:12]}… at depth {lineage['depth']}: {path_str}"
                )
            else:
                detail = (
                    f"Funding ancestry reaches WATCHTOWER {lineage['terminal_label']} "
                    f"at depth {lineage['depth']}: {path_str}"
                )
            evidence.append({
                "rule": "lineage_to_infrastructure",
                "detail": detail,
                "strength": "strong",
                "lineage_depth": lineage["depth"],
                "terminal": lineage["terminal"],
                "terminal_kind": lineage["terminal_kind"],
                "path": lineage["path"],
            })
            strong_hit = True

    # ── Rule 2b: creator previously funded another creator that is infra ──
    # (creator appears in second_hop_lite_queue with watchtower reason codes)
    wt_queue_row = conn.execute(
        "SELECT reason_codes FROM second_hop_lite_queue "
        "WHERE funder_address = ? AND reason_codes LIKE '%watchtower%'",
        (creator_address,),
    ).fetchone()
    if wt_queue_row:
        evidence.append({
            "rule": "watchtower_queue_tag",
            "detail": f"Wallet queued with WATCHTOWER reason code: {wt_queue_row[0]}",
            "strength": "strong",
        })
        strong_hit = True

    # ── Rule 3: WATCHTOWER fee pattern ─────────────────────────────────────
    watchtower_addr = "5Ww9G6XuSHgXLoNmWusVz2SbESAeL7Q6stZMeEhPU25H"
    fee_row = conn.execute(
        "SELECT amount_sol FROM creator_funders "
        "WHERE creator_address = ? AND funder_address = ?",
        (watchtower_addr, creator_address),
    ).fetchone()
    # creator_funders stores inbound — also check via outbound if tracked
    # The fee goes creator→WATCHTOWER; check if creator is in the funder col
    fee_sent_row = conn.execute(
        "SELECT amount_sol FROM creator_funders "
        "WHERE funder_address = ? AND creator_address = ?",
        (creator_address, watchtower_addr),
    ).fetchone()

    # Check fee amount in either direction
    for row in [fee_row, fee_sent_row]:
        if row and row[0] is not None:
            diff = abs(row[0] - WATCHTOWER_FEE_AMOUNT_SOL)
            if diff <= WATCHTOWER_FEE_TOLERANCE:
                evidence.append({
                    "rule": "watchtower_fee_payment",
                    "detail": f"Wallet transacted exactly {row[0]:.6f} SOL "
                               "— matches WATCHTOWER fixed operator fee (0.002839 SOL)",
                    "strength": "strong",
                })
                strong_hit = True

    # ── Rule 4: SIGNALLER activation pattern ──────────────────────────────
    # SIGNALLER sends dust → TREASURY funds within ~60s
    # Proxy: creator was funded by TREASURY AND has a funder_address matching SIGNALLER
    signaller = "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM"
    treasury  = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
    has_signaller = any(r[0] == signaller for r in funder_rows)
    has_treasury  = any(r[0] == treasury  for r in funder_rows)
    if has_signaller and has_treasury:
        evidence.append({
            "rule": "signaller_activation_sequence",
            "detail": "Wallet received dust from SIGNALLER and funding from TREASURY "
                      "— matches WATCHTOWER creator activation pattern",
            "strength": "strong",
        })
        strong_hit = True
    elif has_signaller:
        evidence.append({
            "rule": "signaller_dust",
            "detail": "Wallet received dust from SIGNALLER (WATCHTOWER trigger wallet)",
            "strength": "strong",
        })
        strong_hit = True

    # ── Rule 4b: creator profits routed through known PROFIT-RELAY wallets ─
    # creator_outgoing_transfers or creator_receivers tables if populated
    profit_relays = {
        "4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q": "PROFIT-RELAY-1",
        "7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A": "PROFIT-RELAY-2",
        "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7":  "PROFIT-RELAY-3",
        "5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7": "PROFIT-RELAY-4",
    }
    try:
        outbound_rows = conn.execute(
            "SELECT recipient_address, amount_sol FROM creator_outgoing_transfers "
            "WHERE creator_address = ?",
            (creator_address,),
        ).fetchall()
        for recv_addr, amount_sol in outbound_rows:
            if recv_addr in profit_relays:
                label = profit_relays[recv_addr]
                evidence.append({
                    "rule": "profit_relay_routing",
                    "detail": f"Creator sent {amount_sol:.4f} SOL to {label} ({recv_addr[:20]}…) "
                               "— known WATCHTOWER profit extraction relay",
                    "strength": "strong",
                })
                strong_hit = True
    except sqlite3.OperationalError:
        pass  # table may not exist yet

    # ── Rule 5: funded with a known WATCHTOWER provisioning fingerprint ────
    # SAFETY: a fingerprint amount ALONE is no longer a strong hit. Fingerprints
    # are decimal coincidences that can appear on unrelated funding, so on their
    # own they belong to the soft/weak tier. A fingerprint is only promoted to
    # STRONG when it CORROBORATES an independent infra/lineage hit (i.e. another
    # strong rule already fired for this creator). This is the
    #   "fingerprint amount alone ≠ WATCHTOWER" rule.
    for like_pat, batch_label in CONFIRMED_FINGERPRINT_BATCHES:
        fingerprint_funder_row = conn.execute(
            "SELECT funder_address, amount_sol FROM creator_funders "
            "WHERE creator_address = ? AND amount_sol LIKE ?",
            (creator_address, like_pat),
        ).fetchone()
        if fingerprint_funder_row:
            funder_addr, amount_sol = fingerprint_funder_row
            paired = strong_hit  # an infra/lineage strong rule already fired
            evidence.append({
                "rule": "confirmed_fingerprint_batch",
                "detail": (
                    f"Funded with {amount_sol:.8f} SOL — matches WATCHTOWER "
                    f"{batch_label} from {funder_addr[:20]}… "
                    + ("(corroborates infra/lineage evidence)" if paired
                       else "(soft evidence — no infra/lineage lineage; not sufficient alone)")
                ),
                "strength": "strong" if paired else "weak",
            })
            # Never sets strong_hit on its own — only corroborates.
            break  # one fingerprint match is enough

    return strong_hit, evidence


# ── Persistence helpers ───────────────────────────────────────────────────────

# Map the strongest evidence rule → (evidence_grade, evidence_basis). Order in the
# dict is priority order: the first matching strong rule present wins.
_RULE_BASIS: list[tuple[str, str, str]] = [
    ("direct_infrastructure_wallet", "CONFIRMED", "DIRECT_INFRA"),
    ("funded_by_infrastructure",     "CONFIRMED", "DIRECT_INFRA"),
    ("watchtower_fee_payment",       "CONFIRMED", "FEE_PAYMENT"),
    ("profit_relay_routing",         "STRONG",    "PROFIT_RELAY"),
    ("lineage_to_infrastructure",    "STRONG",    "LINEAGE_RULE_2"),
    ("watchtower_queue_tag",         "STRONG",    "QUEUE_TAG"),
    ("signaller_activation_sequence","STRONG",    "SIGNALLER_SEQUENCE"),
    ("signaller_dust",               "STRONG",    "SIGNALLER_DUST"),
    ("confirmed_fingerprint_batch",  "STRONG",    "FINGERPRINT_BATCH"),
]

# Operational CATEGORY of a WATCHTOWER link — distinguishes "this creator was
# launched by WATCHTOWER" from "this creator received WATCHTOWER profit flow
# later". Without this, watchtower_related=TRUE conflates two very different
# signals. Categories:
#   LAUNCH_PROVISIONING    — funded via a provisioning hub (the new topology)
#   LAUNCH_DIRECT          — funded directly by TREASURY/root/deployer/signaller
#   EXTRACTION_PROFIT_RELAY— routed funds to/from a profit relay (extraction side)
#   COLLECTOR_FLOW         — linked via a fee COLLECTOR wallet
#   INFRA_OTHER            — infra-linked but none of the above
CAT_LAUNCH_PROVISIONING   = "LAUNCH_PROVISIONING"
CAT_LAUNCH_DIRECT         = "LAUNCH_DIRECT"
CAT_EXTRACTION            = "EXTRACTION_PROFIT_RELAY"
CAT_COLLECTOR             = "COLLECTOR_FLOW"
CAT_INFRA_OTHER           = "INFRA_OTHER"

# Which infra labels mean "launch side" vs "extraction/collector side".
_LAUNCH_INFRA_PREFIXES    = ("TREASURY", "WATCHTOWER", "DEPLOYER", "SIGNALLER")


def _infra_category(label: str) -> str:
    """Map a terminal infra wallet's label → operational category."""
    if label.startswith("PROFIT-RELAY"):
        return CAT_EXTRACTION
    if label.startswith("COLLECTOR") or label.startswith("AGGREGATOR"):
        return CAT_COLLECTOR
    if label.startswith(_LAUNCH_INFRA_PREFIXES):
        return CAT_LAUNCH_DIRECT
    return CAT_INFRA_OTHER


def _category_for(evidence: list[dict]) -> Optional[str]:
    """
    Derive the operational category from the strongest evidence.

    Lineage-to-a-hub is always LAUNCH_PROVISIONING. Otherwise the category is
    taken from the terminal infra wallet's role (profit-relay vs treasury vs
    collector). Returns None when no strong infra/lineage evidence is present.
    """
    by_rule = {e["rule"]: e for e in evidence if e.get("strength") == "strong"}

    if "lineage_to_infrastructure" in by_rule:
        e = by_rule["lineage_to_infrastructure"]
        if e.get("terminal_kind") == "hub":
            return CAT_LAUNCH_PROVISIONING
        # lineage that terminates at a static infra wallet → categorize by it
        return _infra_category(WATCHTOWER_INFRASTRUCTURE.get(e.get("terminal", ""), ""))

    if "profit_relay_routing" in by_rule:
        return CAT_EXTRACTION

    for rule in ("direct_infrastructure_wallet", "funded_by_infrastructure"):
        e = by_rule.get(rule)
        if e:
            # detail text carries the infra label; map via the known set instead
            for addr, lbl in WATCHTOWER_INFRASTRUCTURE.items():
                if addr[:20] in e.get("detail", "") or lbl in e.get("detail", ""):
                    return _infra_category(lbl)
            return CAT_INFRA_OTHER

    if {"signaller_activation_sequence", "signaller_dust"} & set(by_rule):
        return CAT_LAUNCH_DIRECT
    return CAT_INFRA_OTHER


def _grade_and_basis(
    is_related: bool, evidence: list[dict]
) -> tuple[Optional[str], Optional[str]]:
    """Derive (evidence_grade, evidence_basis) from the strongest evidence present."""
    if not is_related:
        return None, None
    strong_rules = {e["rule"] for e in evidence if e.get("strength") == "strong"}
    for rule, grade, basis in _RULE_BASIS:
        if rule in strong_rules:
            return grade, basis
    return "WEAK", "UNSPECIFIED"


def _write_creator_result(
    conn: sqlite3.Connection,
    creator_address: str,
    is_related: bool,
    evidence: list[dict],
) -> None:
    now = int(time.time())
    evidence_json = json.dumps(evidence) if evidence else None
    grade, basis = _grade_and_basis(is_related, evidence)
    category = _category_for(evidence) if is_related else None
    # evidence_basis is a JSON column elsewhere in this schema; keep that shape.
    # `category` distinguishes launch vs extraction so the flag stays explainable.
    basis_json = (
        json.dumps({"method": basis, "category": category, "graded_at": str(now)})
        if basis else None
    )
    # NOT a single INSERT…ON CONFLICT: creator_risk_scores has AFTER INSERT/UPDATE
    # triggers that do `INSERT OR REPLACE INTO token_rescore_queue`. An outer
    # ON CONFLICT clause propagates into the trigger, downgrading its REPLACE to
    # ABORT → "UNIQUE constraint failed: token_rescore_queue.mint" when the queue
    # row already exists. A plain UPDATE + guarded INSERT (no conflict clause) lets
    # the trigger's REPLACE work. (Same fix as watchtower_lineage_backfill.)
    cur = conn.execute(
        """
        UPDATE creator_risk_scores SET
            watchtower_related        = ?,
            watchtower_evidence_json  = ?,
            watchtower_checked_at     = ?,
            evidence_grade            = ?,
            evidence_basis            = ?
        WHERE creator_address = ?
        """,
        (int(is_related), evidence_json, now, grade, basis_json, creator_address),
    )
    if cur.rowcount == 0:
        conn.execute(
            """
            INSERT INTO creator_risk_scores (creator_address, watchtower_related,
                watchtower_evidence_json, watchtower_checked_at,
                evidence_grade, evidence_basis)
            SELECT ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM creator_risk_scores WHERE creator_address = ?
            )
            """,
            (creator_address, int(is_related), evidence_json, now, grade,
             basis_json, creator_address),
        )


def _write_token_result(
    conn: sqlite3.Connection,
    mint: str,
    is_related: bool,
    evidence: list[dict],
) -> None:
    evidence_json = json.dumps(evidence) if evidence else None
    conn.execute(
        """
        UPDATE token_analysis
        SET watchtower_related       = ?,
            watchtower_evidence_json = ?
        WHERE mint = ?
        """,
        (int(is_related), evidence_json, mint),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_creator(
    creator_address: str,
    db_path: str,
    mint: Optional[str] = None,
) -> dict:
    """
    Run WATCHTOWER detection for a creator and persist results.

    Returns a result dict with:
      is_related      bool
      evidence        list[dict]
      label           str | None  — human-readable if flagged
    """
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        is_related, evidence = detect_watchtower_linkage(creator_address, conn)

        _write_creator_result(conn, creator_address, is_related, evidence)

        if mint:
            _write_token_result(conn, mint, is_related, evidence)

        conn.commit()

        label = None
        if is_related:
            strong = [e for e in evidence if e["strength"] == "strong"]
            rule = strong[0]["rule"] if strong else "unknown"
            label_map = {
                "direct_infrastructure_wallet":    "Linked to known WATCHTOWER infrastructure",
                "funded_by_infrastructure":        "WATCHTOWER-related — funded by infrastructure wallet",
                "watchtower_queue_tag":            "WATCHTOWER-related — operator wallet",
                "watchtower_fee_payment":          "WATCHTOWER-related — participated in fee sweep",
                "signaller_activation_sequence":   "WATCHTOWER-related — activated via SIGNALLER pattern",
                "signaller_dust":                  "Linked to known WATCHTOWER infrastructure",
                "profit_relay_routing":            "WATCHTOWER-related — profits routed through relay",
                "confirmed_fingerprint_batch":     "WATCHTOWER-related — April 2026 provisioning batch",
                "lineage_to_infrastructure":       "WATCHTOWER-related — funding lineage to infrastructure",
            }
            label = label_map.get(rule, "Operationally related to WATCHTOWER")

        return {"is_related": is_related, "evidence": evidence, "label": label}

    finally:
        conn.close()


def analyze_creator_from_conn(
    creator_address: str,
    conn: sqlite3.Connection,
    mint: Optional[str] = None,
) -> dict:
    """Same as analyze_creator but uses an existing connection (no commit)."""
    is_related, evidence = detect_watchtower_linkage(creator_address, conn)
    _write_creator_result(conn, creator_address, is_related, evidence)
    if mint:
        _write_token_result(conn, mint, is_related, evidence)

    label = None
    if is_related:
        strong = [e for e in evidence if e["strength"] == "strong"]
        rule = strong[0]["rule"] if strong else "unknown"
        label_map = {
            "direct_infrastructure_wallet":    "Linked to known WATCHTOWER infrastructure",
            "funded_by_infrastructure":        "WATCHTOWER-related — funded by infrastructure wallet",
            "watchtower_queue_tag":            "WATCHTOWER-related — operator wallet",
            "watchtower_fee_payment":          "WATCHTOWER-related — participated in fee sweep",
            "signaller_activation_sequence":   "WATCHTOWER-related — activated via SIGNALLER pattern",
            "signaller_dust":                  "Linked to known WATCHTOWER infrastructure",
            "profit_relay_routing":            "WATCHTOWER-related — profits routed through relay",
            "confirmed_fingerprint_batch":     "WATCHTOWER-related — April 2026 provisioning batch",
        }
        label = label_map.get(rule, "Operationally related to WATCHTOWER")

    return {"is_related": is_related, "evidence": evidence, "label": label}


# ── Wallet lifecycle state management ────────────────────────────────────────

_PROFIT_RELAY_SET = frozenset({
    "4LpEjcq3PwkE9Hwt1xLdYHCxyNYB13wEahUPCRkzZa9Q",
    "7UyCwmSUcG7utdSPikn5caL9QwbEnAs1aDcbdWvGs37A",
    "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7",
    "5GZvPqYggF9HS59xBazaTVogMGyCmdMV3sE4oWzJv5Y7",
})
_SIGNALLER = "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM"
_TREASURY  = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
_DORMANT_FEE_DAYS = 7


def update_wallet_state(address: str, conn: sqlite3.Connection) -> str:
    """
    Derive and persist lifecycle state for a WATCHTOWER operator wallet.
    Returns the new state string.

    States: provisioned | activated | operational | launching | extracting | dormant
    """
    now = int(time.time())

    # Check fee payer record
    fp_row = conn.execute(
        "SELECT tx_count, total_sol_sent, first_seen_at, last_seen_at "
        "FROM watchtower_fee_payers WHERE address = ?",
        (address,),
    ).fetchone()

    # Check SIGNALLER / TREASURY funding
    funder_rows = conn.execute(
        "SELECT funder_address FROM creator_funders WHERE creator_address = ?",
        (address,),
    ).fetchall()
    funders = {r[0] for r in funder_rows}
    has_signaller = _SIGNALLER in funders
    has_treasury  = _TREASURY  in funders

    # Check profit relay routing
    has_profit_relay = False
    try:
        pr_row = conn.execute(
            "SELECT 1 FROM creator_outgoing_transfers "
            "WHERE creator_address = ? AND recipient_address IN "
            f"({','.join('?' * len(_PROFIT_RELAY_SET))}) LIMIT 1",
            (address, *_PROFIT_RELAY_SET),
        ).fetchone()
        has_profit_relay = pr_row is not None
    except sqlite3.OperationalError:
        pass

    # Check token launch history and lifecycle
    token_row = conn.execute(
        "SELECT lifecycle_stage, dex FROM token_analysis "
        "WHERE COALESCE(earliest_tx_creator, pf_ws_creator) = ? "
        "ORDER BY COALESCE(migrated_at, 0) DESC LIMIT 1",
        (address,),
    ).fetchone()
    token_stage = token_row[0] if token_row else None
    token_dex   = token_row[1] if token_row else None

    # Determine state
    if has_profit_relay:
        state = "extracting"
    elif token_stage == "migrated" and token_dex == "pumpswap":
        state = "migrated_pumpswap"
    elif token_stage == "migrated":
        state = "migrated_raydium"
    elif token_stage in ("bonding_curve", "migration_pending"):
        state = "launched"
    elif has_signaller and has_treasury:
        state = "activated"
    elif fp_row and fp_row[0] > 0:
        # Has paid fees — check if dormant (no fee in 7+ days)
        last_fee = fp_row[3]
        if last_fee and (now - last_fee) > _DORMANT_FEE_DAYS * 86400:
            state = "dormant"
        else:
            state = "operational"
    else:
        state = "provisioned"

    # Upsert state
    existing = conn.execute(
        "SELECT state FROM watchtower_wallet_state WHERE address = ?", (address,)
    ).fetchone()

    state_changed_at = now if (existing is None or existing[0] != state) else None

    conn.execute(
        """
        INSERT INTO watchtower_wallet_state
            (address, state, state_changed_at, first_fee_at, last_fee_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            state            = excluded.state,
            state_changed_at = COALESCE(excluded.state_changed_at, state_changed_at),
            first_fee_at     = COALESCE(excluded.first_fee_at, first_fee_at),
            last_fee_at      = COALESCE(excluded.last_fee_at, last_fee_at),
            updated_at       = excluded.updated_at
        """,
        (
            address,
            state,
            state_changed_at,
            fp_row[2] if fp_row else None,
            fp_row[3] if fp_row else None,
            now,
        ),
    )
    return state


def seed_wallet_states(db_path: str) -> dict:
    """
    Seed watchtower_wallet_state for all known fee payers.
    Safe to run repeatedly — uses INSERT OR REPLACE logic via update_wallet_state().
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT address FROM watchtower_fee_payers").fetchall()
        counts: dict[str, int] = {}
        for i, row in enumerate(rows):
            state = update_wallet_state(row["address"], conn)
            counts[state] = counts.get(state, 0) + 1
            if (i + 1) % 50 == 0:
                conn.commit()
        conn.commit()
        print(f"[WT-STATE] Seeded {len(rows)} operators: {counts}", flush=True)
        return {"total": len(rows), "by_state": counts}
    finally:
        conn.close()


def backfill_all_creators(db_path: str) -> dict:
    """
    Run detection against every creator in creator_risk_scores that hasn't
    been checked yet. Intended for one-time backfill after deployment.
    Returns counts.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT creator_address FROM creator_risk_scores "
            "WHERE watchtower_checked_at IS NULL"
        ).fetchall()

        flagged = 0
        checked = 0
        for row in rows:
            creator = row["creator_address"]
            is_related, evidence = detect_watchtower_linkage(creator, conn)
            _write_creator_result(conn, creator, is_related, evidence)
            if is_related:
                flagged += 1
            checked += 1
            if checked % 500 == 0:
                conn.commit()
                print(f"[WT-BACKFILL] {checked}/{len(rows)} checked, {flagged} flagged", flush=True)

        conn.commit()
        print(f"[WT-BACKFILL] Done — {checked} checked, {flagged} flagged", flush=True)
        return {"checked": checked, "flagged": flagged}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Creator Candidate Scoring Engine
# ═══════════════════════════════════════════════════════════════════════════════

_TREASURY  = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
_SIGNALLER = "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM"
_PUMP_AMM  = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
_PUMP_FEE  = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def _check_wallet_freshness(address: str, conn: sqlite3.Connection) -> bool:
    """Return True if address has no pre-existing history in known tables."""
    in_crs = conn.execute(
        "SELECT 1 FROM creator_risk_scores WHERE creator_address=? LIMIT 1", (address,)
    ).fetchone()
    if in_crs:
        return False
    in_activity = conn.execute(
        "SELECT 1 FROM address_activity WHERE address=? LIMIT 1", (address,)
    ).fetchone() if _table_exists(conn, "address_activity") else None
    if in_activity:
        return False
    in_transfers = conn.execute(
        "SELECT 1 FROM funder_incoming_transfers WHERE funder_address=? LIMIT 1", (address,)
    ).fetchone() if _table_exists(conn, "funder_incoming_transfers") else None
    if in_transfers:
        return False
    return True


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _trace_lineage(address: str, conn: sqlite3.Connection, max_hops: int = 3) -> tuple[int, list]:
    """
    Walk creator_funders and wt_sub_provisioners upward up to max_hops.
    Returns (score, path).  Score decays by hop distance.
    """
    path: list[str] = []
    current = address
    for hop in range(max_hops):
        funder_row = conn.execute(
            "SELECT funder_address, amount_sol FROM creator_funders "
            "WHERE creator_address=? LIMIT 1", (current,)
        ).fetchone()
        if not funder_row:
            # Also check watchtower_operator_graph (child → operator)
            op_row = conn.execute(
                "SELECT operator_address FROM watchtower_operator_graph "
                "WHERE child_address=? LIMIT 1", (current,)
            ).fetchone()
            if not op_row:
                break
            funder = op_row[0]
        else:
            funder = funder_row[0]
        path.append(funder)

        sp = conn.execute(
            "SELECT 1 FROM wt_sub_provisioners WHERE address=?", (funder,)
        ).fetchone()
        if sp:
            return max(25 - hop * 10, 5), path

        if funder == _TREASURY:
            return max(20 - hop * 5, 5), path

        if funder in WATCHTOWER_INFRASTRUCTURE:
            return max(15 - hop * 5, 3), path

        current = funder
    return 0, path


def _score_funding_fingerprint(address: str, conn: sqlite3.Connection) -> int:
    """Check if address received a known provisioning fingerprint amount."""
    rows = conn.execute(
        "SELECT amount_sol FROM creator_funders WHERE creator_address=? LIMIT 5", (address,)
    ).fetchall()
    if not rows:
        # Also check watchtower_operator_graph
        rows = conn.execute(
            "SELECT amount_sol FROM watchtower_operator_graph WHERE child_address=? LIMIT 5",
            (address,)
        ).fetchall()
    for row in rows:
        amt = row[0] if row[0] else 0.0
        amt_str = f"{amt:.8f}"
        for pattern, _ in CONFIRMED_FINGERPRINT_BATCHES:
            suffix = pattern.replace("%", "")
            if amt_str.endswith(suffix):
                return 20
        # Variant: any amount with .3928 suffix
        frac = amt % 1
        frac_str = f"{frac:.8f}"
        if "3928" in frac_str[3:]:
            return 10
    return 0


def _check_no_pamm_activity(address: str, conn: sqlite3.Connection) -> bool:
    """Return True if address has zero pAMM interactions recorded."""
    has_pamm = conn.execute(
        "SELECT 1 FROM wt_pamm_interactions WHERE wallet_address=? LIMIT 1", (address,)
    ).fetchone()
    if has_pamm:
        return False
    # Also check wt_staged_wallets first_move_type
    move = conn.execute(
        "SELECT first_move_type FROM wt_staged_wallets WHERE wallet_address=?", (address,)
    ).fetchone()
    if move and move[0] == "pAMM_trader":
        return False
    return True


def _score_tx_count(address: str, conn: sqlite3.Connection) -> int:
    """Score based on total transaction count — creators have 1-3 tx."""
    # Check wt_staged_wallets for known tx count signals
    staged = conn.execute(
        "SELECT first_move_type, first_move_sig FROM wt_staged_wallets WHERE wallet_address=?",
        (address,)
    ).fetchone()
    if staged:
        # If already has a first_move, that's at least 2 tx (receive + move)
        if staged[1]:  # has first_move_sig
            return 10  # still ≤3 range
    # Fall back: presence in tables implies some history
    in_launch = conn.execute(
        "SELECT 1 FROM wt_creator_launches WHERE creator_wallet=? LIMIT 1", (address,)
    ).fetchone()
    if in_launch:
        return 5  # launched = at least 2 tx
    return 10  # no confirmed activity = assume fresh


def _score_outbound_inactivity(address: str, conn: sqlite3.Connection) -> int:
    """Score based on absence of outbound transfers to non-infra destinations."""
    staged = conn.execute(
        "SELECT first_move_type, first_move_sig FROM wt_staged_wallets WHERE wallet_address=?",
        (address,)
    ).fetchone()
    if not staged or not staged[1]:
        return 10  # no outbound recorded
    move_type = staged[0] or ""
    if move_type in ("pump_fee", "pumpfun_create"):
        return 10  # only outbound is to pump.fun = perfect
    if move_type in ("pAMM_trader", "raydium_lp", "large_sol_outbound"):
        return 0
    return 5


def score_creator_candidate(address: str, conn: sqlite3.Connection) -> tuple[int, dict]:
    """
    Composite creator candidate score 0–100.

    Components:
      freshness          20 pts
      funding_lineage    25 pts
      amount_fingerprint 20 pts
      amm_absence        15 pts
      tx_count           10 pts
      outbound_inactivity 10 pts
    """
    evidence: dict = {}

    freshness = 20 if _check_wallet_freshness(address, conn) else 0
    evidence["freshness"] = freshness

    lineage_score, lineage_path = _trace_lineage(address, conn)
    evidence["lineage"] = {"score": lineage_score, "path": lineage_path}

    fp_score = _score_funding_fingerprint(address, conn)
    evidence["fingerprint"] = fp_score

    amm_clean = 15 if _check_no_pamm_activity(address, conn) else 0
    evidence["amm_absence"] = amm_clean

    tx_score = _score_tx_count(address, conn)
    evidence["tx_count"] = tx_score

    ob_score = _score_outbound_inactivity(address, conn)
    evidence["outbound_inactivity"] = ob_score

    score = min(freshness + lineage_score + fp_score + amm_clean + tx_score + ob_score, 100)
    evidence["total"] = score
    return score, evidence


def score_and_persist(address: str, conn: sqlite3.Connection) -> tuple[int, dict]:
    """Score a candidate and write result to wt_candidate_scores."""
    score, evidence = score_creator_candidate(address, conn)
    lineage_path = evidence.get("lineage", {}).get("path", [])
    reaches_treasury = int(_TREASURY in lineage_path)
    conn.execute("""
        INSERT INTO wt_candidate_scores
            (wallet_address, score, score_breakdown, lineage_path, reaches_treasury, scored_at)
        VALUES (?, ?, ?, ?, ?, strftime('%s','now'))
        ON CONFLICT(wallet_address) DO UPDATE SET
            score           = excluded.score,
            score_breakdown = excluded.score_breakdown,
            lineage_path    = excluded.lineage_path,
            reaches_treasury = excluded.reaches_treasury,
            rescored_at     = strftime('%s','now')
    """, (address, score, json.dumps(evidence), json.dumps(lineage_path), reaches_treasury))
    return score, evidence


# ═══════════════════════════════════════════════════════════════════════════════
# Topology Graph Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def upsert_graph_node(address: str, node_type: str, conn: sqlite3.Connection,
                      campaign_id: str | None = None, state: str | None = None,
                      score: int | None = None, evidence: dict | None = None) -> None:
    conn.execute("""
        INSERT INTO wt_graph_nodes (address, node_type, campaign_id, state, score, evidence_json, last_active_at)
        VALUES (?, ?, ?, ?, ?, ?, strftime('%s','now'))
        ON CONFLICT(address) DO UPDATE SET
            node_type      = CASE WHEN excluded.node_type != 'UNKNOWN' THEN excluded.node_type ELSE node_type END,
            campaign_id    = COALESCE(excluded.campaign_id, campaign_id),
            state          = COALESCE(excluded.state, state),
            score          = COALESCE(excluded.score, score),
            evidence_json  = COALESCE(excluded.evidence_json, evidence_json),
            last_active_at = strftime('%s','now')
    """, (address, node_type, campaign_id, state, score,
          json.dumps(evidence) if evidence else None))


def upsert_graph_edge(from_addr: str, to_addr: str, edge_type: str,
                      conn: sqlite3.Connection, amount_sol: float | None = None,
                      tx_sig: str | None = None, block_time: int | None = None,
                      campaign_id: str | None = None) -> None:
    conn.execute("""
        INSERT OR IGNORE INTO wt_graph_edges
            (from_address, to_address, edge_type, amount_sol, tx_signature, block_time, campaign_id)
        VALUES (?, ?, ?, ?, ?, COALESCE(?, strftime('%s','now')), ?)
    """, (from_addr, to_addr, edge_type, amount_sol, tx_sig, block_time, campaign_id))


def trace_funding_topology(address: str, conn: sqlite3.Connection,
                           max_hops: int = 5) -> dict:
    """
    BFS from address upward through creator_funders / watchtower_operator_graph / TREASURY.
    Returns {nodes, edges, reaches_treasury, hop_count}.
    """
    visited: dict[str, dict] = {}
    queue: list[tuple[str, int, str | None]] = [(address, 0, None)]
    edges: list[dict] = []

    while queue:
        current, hop, parent = queue.pop(0)
        if current in visited or hop > max_hops:
            continue

        role = "TREASURY" if current == _TREASURY else (
            "SUB_PROV" if conn.execute(
                "SELECT 1 FROM wt_sub_provisioners WHERE address=?", (current,)
            ).fetchone() else (
            "CREATOR" if conn.execute(
                "SELECT 1 FROM wt_staged_wallets WHERE wallet_address=?", (current,)
            ).fetchone() else
            WATCHTOWER_INFRASTRUCTURE.get(current, "UNKNOWN")
        ))
        visited[current] = {"address": current, "hop": hop, "role": role}

        if parent:
            amt_row = (
                conn.execute(
                    "SELECT amount_sol FROM creator_funders WHERE creator_address=? AND funder_address=? LIMIT 1",
                    (current, parent)
                ).fetchone() or
                conn.execute(
                    "SELECT amount_sol FROM watchtower_operator_graph WHERE child_address=? AND operator_address=? LIMIT 1",
                    (current, parent)
                ).fetchone()
            )
            edges.append({
                "from": parent, "to": current,
                "amount_sol": amt_row[0] if amt_row else None,
                "hop": hop
            })

        if current == _TREASURY:
            break

        funder = (
            conn.execute(
                "SELECT funder_address FROM creator_funders WHERE creator_address=? LIMIT 1",
                (current,)
            ).fetchone() or
            conn.execute(
                "SELECT operator_address FROM watchtower_operator_graph WHERE child_address=? LIMIT 1",
                (current,)
            ).fetchone()
        )
        if funder:
            queue.append((funder[0], hop + 1, current))

    hop_count = max((v["hop"] for v in visited.values()), default=0)
    return {
        "nodes": list(visited.values()),
        "edges": edges,
        "reaches_treasury": _TREASURY in visited,
        "hop_count": hop_count,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Trader Swarm Detection
# ═══════════════════════════════════════════════════════════════════════════════

def detect_trader_swarm(sub_provisioner: str, conn: sqlite3.Connection,
                        window_seconds: int = 7200) -> dict | None:
    """
    Detects simultaneous fanout from sub_provisioner to N fresh wallets with
    small round amounts within window_seconds.  Returns result dict or None.
    """
    edges = conn.execute("""
        SELECT child_address, amount_sol, first_seen_at
        FROM watchtower_operator_graph
        WHERE operator_address = ?
          AND amount_sol < 0.5
          AND amount_sol > 0.005
          AND ABS(amount_sol - ROUND(amount_sol, 3)) < 0.0005
        ORDER BY first_seen_at
    """, (sub_provisioner,)).fetchall()

    if len(edges) < 10:
        return None

    # Find densest 2-hour window using sliding window
    times = [(e[2] or 0) for e in edges]
    best_window_edges = edges
    if len(edges) > 10:
        best: list = []
        for i, e in enumerate(edges):
            t0 = e[2] or 0
            window = [x for x in edges if x[2] and 0 <= t0 - x[2] <= window_seconds]
            if len(window) > len(best):
                best = window
        best_window_edges = best if len(best) >= 10 else edges

    if len(best_window_edges) < 10:
        return None

    avg_amount = sum(e[1] or 0 for e in best_window_edges) / len(best_window_edges)
    is_known_sp = bool(conn.execute(
        "SELECT 1 FROM wt_sub_provisioners WHERE address=?", (sub_provisioner,)
    ).fetchone())

    confidence_score = 60
    if len(best_window_edges) >= 20:
        confidence_score += 10
    if len(best_window_edges) >= 50:
        confidence_score += 10
    if is_known_sp:
        confidence_score += 10
    # Check for ATA setup burst (wt_staged_wallets having multiple DORMANT_FUNDED in window)
    w_start = best_window_edges[0][2] or 0
    w_end   = best_window_edges[-1][2] or int(time.time())
    ata_count = conn.execute("""
        SELECT COUNT(*) FROM wt_staged_wallets
        WHERE provisioner_address = ?
          AND provisioned_at BETWEEN ? AND ?
    """, (sub_provisioner, w_start, w_end)).fetchone()[0]
    if ata_count > len(best_window_edges) * 0.5:
        confidence_score += 15

    return {
        "provisioner":      sub_provisioner,
        "wallet_count":     len(best_window_edges),
        "window_start":     w_start,
        "window_end":       w_end,
        "avg_amount_sol":   avg_amount,
        "ata_setups":       ata_count,
        "confidence_score": min(confidence_score, 95),
        "confidence":       "HIGH" if confidence_score >= 75 else "MEDIUM",
        "is_known_sp":      is_known_sp,
    }


def detect_synchronized_pamm_campaign(token_mint: str, conn: sqlite3.Connection,
                                       window_seconds: int = 300) -> dict | None:
    """
    Detects N WATCHTOWER-linked wallets interacting with the same token within window_seconds.
    Requires wt_pamm_interactions to be populated.
    """
    now = int(time.time())
    interactors = conn.execute("""
        SELECT wallet_address, MIN(block_time) as first_at
        FROM wt_pamm_interactions
        WHERE token_mint = ? AND block_time > ?
        GROUP BY wallet_address
    """, (token_mint, now - window_seconds)).fetchall()

    if len(interactors) < 10:
        return None

    # Filter to wallets in the WATCHTOWER graph
    linked: list[dict] = []
    for row in interactors:
        wallet = row[0]
        op = conn.execute(
            "SELECT operator_address FROM watchtower_operator_graph WHERE child_address=? LIMIT 1",
            (wallet,)
        ).fetchone()
        if op:
            linked.append({"wallet": wallet, "first_at": row[1], "operator": op[0]})
        elif conn.execute(
            "SELECT 1 FROM wt_staged_wallets WHERE wallet_address=?", (wallet,)
        ).fetchone():
            prov = conn.execute(
                "SELECT provisioner_address FROM wt_staged_wallets WHERE wallet_address=?", (wallet,)
            ).fetchone()
            linked.append({"wallet": wallet, "first_at": row[1],
                           "operator": prov[0] if prov else None})

    if len(linked) < 10:
        return None

    # Cluster by operator
    by_op: dict[str, list] = {}
    for w in linked:
        op = w["operator"] or "unknown"
        by_op.setdefault(op, []).append(w)
    top_op = max(by_op, key=lambda k: len(by_op[k]))
    top_wallets = by_op[top_op]

    if len(top_wallets) < 10:
        return None

    is_known_sp = bool(conn.execute(
        "SELECT 1 FROM wt_sub_provisioners WHERE address=?", (top_op,)
    ).fetchone())
    creator_linked = bool(conn.execute(
        "SELECT 1 FROM wt_creator_launches WHERE mint_address=? LIMIT 1", (token_mint,)
    ).fetchone())

    confidence_score = 65
    if is_known_sp:
        confidence_score += 20
    if creator_linked:
        confidence_score += 10

    return {
        "token_mint":       token_mint,
        "linked_wallets":   len(top_wallets),
        "top_operator":     top_op,
        "window_seconds":   window_seconds,
        "is_known_sp":      is_known_sp,
        "creator_linked":   creator_linked,
        "confidence_score": min(confidence_score, 95),
        "confidence":       "HIGH" if confidence_score >= 85 else "MEDIUM",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Sweep Epoch Detection
# ═══════════════════════════════════════════════════════════════════════════════

def detect_sweep_epoch(provisioner: str, conn: sqlite3.Connection,
                        window_seconds: int = 3600) -> dict | None:
    """
    Detects cluster of WATCHTOWER-linked wallets returning SOL to provisioner within 1h.
    Data source: watchtower_infra_events (direction=inbound).
    """
    now = int(time.time())
    sweeps = conn.execute("""
        SELECT counterparty, amount_sol, block_time
        FROM watchtower_infra_events
        WHERE infra_address = ?
          AND direction = 'inbound'
          AND block_time > ?
        ORDER BY block_time
    """, (provisioner, now - window_seconds)).fetchall()

    if len(sweeps) < 3:
        return None

    # Filter to wallets we know are WATCHTOWER-linked
    known_children: set[str] = set(
        r[0] for r in conn.execute(
            "SELECT child_address FROM watchtower_operator_graph WHERE operator_address=?",
            (provisioner,)
        ).fetchall()
    )
    known_children.update(
        r[0] for r in conn.execute(
            "SELECT wallet_address FROM wt_staged_wallets WHERE provisioner_address=?",
            (provisioner,)
        ).fetchall()
    )

    linked_sweeps = [s for s in sweeps if s[0] in known_children]

    if len(linked_sweeps) < 3:
        return None

    total_sol = sum(s[1] or 0 for s in linked_sweeps)
    is_known_sp = bool(conn.execute(
        "SELECT 1 FROM wt_sub_provisioners WHERE address=?", (provisioner,)
    ).fetchone())

    confidence_score = 75 if is_known_sp else 55
    if len(linked_sweeps) >= 10:
        confidence_score += 10
    if total_sol >= 5:
        confidence_score += 10

    return {
        "provisioner":      provisioner,
        "sweep_count":      len(linked_sweeps),
        "total_sol_swept":  total_sol,
        "window_start":     linked_sweeps[0][2],
        "window_end":       linked_sweeps[-1][2],
        "is_known_sp":      is_known_sp,
        "confidence_score": min(confidence_score, 95),
        "confidence":       "HIGH" if confidence_score >= 80 else "MEDIUM",
    }


def detect_reload_cycle(provisioner: str, conn: sqlite3.Connection,
                         window_seconds: int = 86400) -> dict | None:
    """
    Detects provisioner sending SOL back to wallets that previously swept to it.
    A reload cycle = sweep epoch followed by outbound to same wallets.
    """
    now = int(time.time())
    # Wallets that swept TO provisioner recently
    swept_wallets: set[str] = set(
        r[0] for r in conn.execute("""
            SELECT counterparty FROM watchtower_infra_events
            WHERE infra_address=? AND direction='inbound' AND block_time > ?
        """, (provisioner, now - window_seconds)).fetchall()
    )
    if not swept_wallets:
        return None

    # Outbound from provisioner to same wallets after they swept
    reload_rows = conn.execute("""
        SELECT counterparty, amount_sol, block_time
        FROM watchtower_infra_events
        WHERE infra_address=? AND direction='outbound' AND block_time > ?
    """, (provisioner, now - window_seconds)).fetchall()

    reloaded = [r for r in reload_rows if r[0] in swept_wallets]
    if len(reloaded) < 3:
        return None

    return {
        "provisioner":    provisioner,
        "reload_count":   len(reloaded),
        "total_sol_sent": sum(r[1] or 0 for r in reloaded),
        "window_seconds": window_seconds,
        "confidence":     "HIGH",
        "confidence_score": 80,
    }
