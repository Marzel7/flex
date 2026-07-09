"""
WATCHTOWER attribution — the three-layer model.

The hard lesson from the Cgwr investigation: `migrated token == WATCHTOWER token` is
WRONG. The …039280 ATA tail is universal (every token creator pays ATA rent), so storing
migrations as WATCHTOWER pollutes the set with generic pump.fun creators. The trader-swarm
narrative also collapsed under raw decode — only LEDGER-derived funding-lineage evidence
survived scrutiny.

So attribution is now SEPARATE from storage and SCORED on WATCHTOWER-specific graph
evidence — never on the ATA tail alone.

  Layer 1  migrated_tokens                — store every migration NEUTRALLY (no WT implication)
  Layer 2  watchtower_token_attribution   — score WT-relatedness separately (STRONG/WEAK/NONE)
  Layer 3  webhook gating by tier         — STRONG→webhook, WEAK→review, NONE→store-only

Scoring weights (WATCHTOWER-specific evidence):
  treasury ancestry        HIGH    (0.40)
  collector/subprov link   HIGH    (0.35)
  known signaller          HIGH    (0.35)
  known relay/sweep link   MEDIUM  (0.20)
  creator funding chain    MEDIUM  (0.15)
  ATA tail / template      LOW     (0.10)  ← supports, NEVER triggers alone
  migration alone          LOW     (0.00)  ← carries no WT signal

HARD RULE: the ATA tail / template can only ADD to a score that already has graph
evidence. A token whose ONLY signal is the ATA tail scores NONE.
"""
from __future__ import annotations

import os
import json
import time
from typing import Optional

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout)
        c.row_factory = sqlite3.Row
        return c

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))
LIVE_DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db"))

# ── scoring weights ──────────────────────────────────────────────────────────
W_TREASURY   = 0.40   # ancestry reaches a known treasury
W_COLLECTOR  = 0.35   # ancestry reaches a known collector or sub-provisioner
W_SIGNALLER  = 0.35   # interacts with a known signaller
W_RELAY      = 0.20   # reaches a known relay / sweep wallet
W_FUNDCHAIN  = 0.15   # has a traceable creator-funding chain into known infra
W_TEMPLATE   = 0.10   # carries the …039280 template (SUPPORT ONLY)

STRONG_THRESHOLD = 0.40   # >= one HIGH-weight graph signal
WEAK_THRESHOLD   = 0.15   # some graph evidence, below strong

ATA_RENT_LAMPORTS = 2_039_280


def ensure_schema(conn) -> None:
    # Layer 1 — neutral migration store. Name does NOT imply WATCHTOWER.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS migrated_tokens (
            mint            TEXT PRIMARY KEY,
            creator         TEXT,
            migration_tx    TEXT,
            migration_time  INTEGER,
            pool            TEXT,
            initial_funder  TEXT,
            funding_amount  REAL,
            raw_tx_parsed   INTEGER DEFAULT 0,   -- 1 once funding decoded from raw tx
            source          TEXT DEFAULT 'pumpfun_migration',
            stored_at       INTEGER NOT NULL
        )""")
    # Layer 2 — WATCHTOWER attribution, separate + scored.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchtower_token_attribution (
            mint                TEXT PRIMARY KEY,
            creator             TEXT,
            score               REAL    NOT NULL DEFAULT 0,
            tier                TEXT    NOT NULL DEFAULT 'NONE',  -- STRONG | WEAK | NONE
            reasons_json        TEXT,
            ancestry_depth      INTEGER,
            matched_treasury    TEXT,
            matched_collector   TEXT,
            matched_subprov     TEXT,
            matched_signaller   TEXT,
            matched_relay       TEXT,
            evidence_paths_json TEXT,
            reviewed_status     TEXT    NOT NULL DEFAULT 'AUTO',  -- AUTO|REVIEW|CONFIRMED|REJECTED
            scored_at           INTEGER NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_wta_tier ON watchtower_token_attribution(tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_mt_time ON migrated_tokens(migration_time DESC)")
    # Layer 2b — unconfirmed WATCHTOWER-like leads (wrap-close lineage, unknown root treasury)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_unconfirmed_watchtower_like (
            mint                TEXT NOT NULL,
            creator_wallet      TEXT,
            subprov_wallet      TEXT,
            unknown_root_wallet TEXT,
            root_hop            INTEGER,
            wrap_close_sig      TEXT,
            amount_sol          REAL,
            first_seen          INTEGER NOT NULL,
            last_seen           INTEGER NOT NULL,
            occurrence_count    INTEGER NOT NULL DEFAULT 1,
            status              TEXT NOT NULL DEFAULT 'REVIEW',
            PRIMARY KEY (mint)
        )""")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_uwl_root
        ON wt_unconfirmed_watchtower_like(unknown_root_wallet)""")
    conn.commit()


# ── Layer 1: neutral store ───────────────────────────────────────────────────
def store_migration(conn, mint, creator, migration_tx=None, migration_time=None,
                    pool=None, initial_funder=None, funding_amount=None,
                    raw_tx_parsed=False, source="pumpfun_migration") -> None:
    """Store a migrated token NEUTRALLY. No WATCHTOWER implication — this is just the
    fact that a token migrated. Attribution happens separately (Layer 2)."""
    ensure_schema(conn)
    conn.execute(
        """INSERT INTO migrated_tokens
             (mint, creator, migration_tx, migration_time, pool, initial_funder,
              funding_amount, raw_tx_parsed, source, stored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(mint) DO UPDATE SET
             creator=COALESCE(excluded.creator, creator),
             migration_tx=COALESCE(excluded.migration_tx, migration_tx),
             pool=COALESCE(excluded.pool, pool),
             initial_funder=COALESCE(excluded.initial_funder, initial_funder),
             funding_amount=COALESCE(excluded.funding_amount, funding_amount),
             raw_tx_parsed=MAX(raw_tx_parsed, excluded.raw_tx_parsed)""",
        (mint, creator, migration_tx, migration_time, pool, initial_funder,
         funding_amount, 1 if raw_tx_parsed else 0, source, int(time.time())))
    conn.commit()

    # Lineage completeness check — zero RPC. Only PARTIAL_*/FULL_WALKBACK consume budget.
    try:
        from src.core.walkback_queue import enqueue_migration
        enqueue_migration(conn, mint=mint, creator=creator)
    except Exception:
        pass


# ── Layer 2: WATCHTOWER attribution scorer ───────────────────────────────────
def _wt_graph(conn):
    """The known WATCHTOWER operational graph, by role, from wt_ops_v2_wallets +
    registered infra. Returns role -> set(wallets)."""
    def rs(role):
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT wallet FROM wt_ops_v2_wallets WHERE role=?", (role,)).fetchall()}
    g = {
        "treasury":  rs("TREASURY"),
        "collector": rs("COLLECTOR") | rs("SUB_PROV"),
        "relay":     rs("PASS_THROUGH"),
    }
    # known hardcoded signallers/treasuries (original WATCHTOWER infra)
    g["signaller"] = {
        "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM",
    }
    return g


def _ancestors(funder_walk_fn, start, max_hops=3):
    """Generic ancestry collector — uses an injected funder lookup (RPC or cached).
    funder_walk_fn(addr) -> list[(funder_wallet, amount_sol)]."""
    seen, frontier, depth_of = set(), [start], {start: 0}
    out = []
    for _ in range(max_hops):
        nxt = []
        for a in frontier:
            for f, amt in (funder_walk_fn(a) or []):
                if f and f not in seen and amt >= 0.05:
                    seen.add(f); nxt.append(f); depth_of[f] = depth_of[a] + 1
                    out.append((f, depth_of[f]))
        frontier = nxt[:6]
        if not frontier:
            break
    return out


def record_unconfirmed_watchtower_like(conn, *, mint: str, creator: str,
                                       subprov: str, unknown_root: str, root_hop: int,
                                       amount_sol: float) -> None:
    """Persist a WATCHTOWER-like lead for human review. Idempotent — increments
    occurrence_count and updates last_seen on repeat."""
    ensure_schema(conn)
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_unconfirmed_watchtower_like
             (mint, creator_wallet, subprov_wallet, unknown_root_wallet, root_hop,
              amount_sol, first_seen, last_seen, occurrence_count, status)
           VALUES (?,?,?,?,?,?,?,?,1,'REVIEW')
           ON CONFLICT(mint) DO UPDATE SET
             last_seen=excluded.last_seen,
             occurrence_count=occurrence_count+1,
             subprov_wallet=COALESCE(excluded.subprov_wallet, subprov_wallet),
             unknown_root_wallet=COALESCE(excluded.unknown_root_wallet, unknown_root_wallet)""",
        (mint, creator, subprov, unknown_root, root_hop, amount_sol, now, now))
    conn.commit()


def score_token(conn, mint, creator, funder_walk_fn, funding_amount=None) -> dict:
    """Score a token's WATCHTOWER-relatedness on GRAPH evidence. The ATA template is
    support-only — it can never push a token to a tier by itself.

    funder_walk_fn(addr) -> list[(funder, amount_sol)] supplies ancestry (RPC-backed).
    If funder_walk_fn has a _last_wrap_close_subprov attribute set after the walk,
    wrap-close lineage is known and UNCONFIRMED_WATCHTOWER_LIKE detection is enabled."""
    g = _wt_graph(conn)
    reasons, matched = [], {}
    score = 0.0

    anc = _ancestors(funder_walk_fn, creator, max_hops=3) if creator else []
    anc_wallets = {w for w, _ in anc}
    max_depth = max((d for _, d in anc), default=0)
    paths = []

    # HIGH-weight graph signals
    t = anc_wallets & g["treasury"]
    if t:
        score += W_TREASURY; matched["treasury"] = next(iter(t))
        reasons.append(f"treasury ancestry: {matched['treasury'][:10]}")
    c = anc_wallets & g["collector"]
    if c:
        score += W_COLLECTOR; matched["collector"] = next(iter(c))
        reasons.append(f"collector/sub-prov ancestry: {matched['collector'][:10]}")
    s = anc_wallets & g["signaller"]
    if s:
        score += W_SIGNALLER; matched["signaller"] = next(iter(s))
        reasons.append(f"known signaller in lineage: {matched['signaller'][:10]}")
    # MEDIUM
    r = anc_wallets & g["relay"]
    if r:
        score += W_RELAY; matched["relay"] = next(iter(r))
        reasons.append(f"relay/sweep link: {matched['relay'][:10]}")
    has_graph = bool(t or c or s or r)
    if has_graph and anc:
        score += W_FUNDCHAIN
        reasons.append("traceable creator-funding chain into known infra")
        paths = [w for w, _ in anc][:8]

    # LOW — template SUPPORT ONLY. Never enough on its own.
    has_template = False
    if funding_amount is not None:
        lam = round(float(funding_amount) * 1e9)
        if lam % 1_000_000 == ATA_RENT_LAMPORTS % 1_000_000:
            has_template = True
    if has_template:
        if has_graph:
            score += W_TEMPLATE                 # supports an existing graph match
            reasons.append("carries …039280 template (support)")
        else:
            reasons.append("…039280 template present but NO graph evidence — not WT")

    # tier — STRONG requires a HIGH-weight signal (treasury/collector/signaller). A lone
    # relay/pass-through match is NOT enough for STRONG: the pass-through set is large
    # (100+ wallets) so a single low-SOL hit is often coincidental (proven in the
    # falsification test — pass-through-only creators were noise). Relay-only → WEAK.
    has_high = bool(t or c or s)
    if has_high and score >= STRONG_THRESHOLD:
        tier = "STRONG"
    elif has_graph and score >= WEAK_THRESHOLD:
        tier = "WEAK"                            # relay-only, or sub-threshold graph evidence
    else:
        tier = "NONE"                            # template-only / no graph evidence

    # ── UNCONFIRMED_WATCHTOWER_LIKE detection ────────────────────────────────
    # If the walk found wrap-close lineage at hop-0 but no confirmed treasury matched,
    # the token used WATCHTOWER-style provisioning from an unknown operator.
    # Detect using the _last_wrap_close_subprov tag set by _funders() on the closure.
    uwl_subprov = getattr(funder_walk_fn, "_last_wrap_close_subprov", None)
    uwl_amount  = getattr(funder_walk_fn, "_last_wrap_close_amount", None)
    if tier == "NONE" and uwl_subprov and anc:
        # The root is the deepest ancestor not in our confirmed graph
        confirmed_all = g["treasury"] | g["collector"] | g["relay"] | g["signaller"]
        unknown_ancestors = [(w, d) for w, d in anc if w not in confirmed_all]
        if unknown_ancestors:
            # deepest unknown ancestor = likely the treasury root
            unknown_root, root_hop = max(unknown_ancestors, key=lambda x: x[1])
            tier = "UNCONFIRMED_WATCHTOWER_LIKE"
            reasons.append(f"wrap-close lineage via {uwl_subprov[:10]}… → unknown root {unknown_root[:10]}… (hop {root_hop})")
            matched["uwl_subprov"] = uwl_subprov
            matched["uwl_root"] = unknown_root
            matched["uwl_root_hop"] = root_hop

    return {
        "mint": mint, "creator": creator, "score": round(score, 3), "tier": tier,
        "reasons": reasons, "ancestry_depth": max_depth,
        "matched_treasury": matched.get("treasury"), "matched_collector": matched.get("collector"),
        "matched_subprov": matched.get("collector") or matched.get("uwl_subprov"),
        "matched_signaller": matched.get("signaller"),
        "matched_relay": matched.get("relay"), "evidence_paths": paths,
        "uwl_subprov": matched.get("uwl_subprov"),
        "uwl_root": matched.get("uwl_root"),
        "uwl_root_hop": matched.get("uwl_root_hop"),
    }


def persist_attribution(conn, a: dict, reviewed_status="AUTO") -> None:
    ensure_schema(conn)
    conn.execute(
        """INSERT INTO watchtower_token_attribution
             (mint, creator, score, tier, reasons_json, ancestry_depth, matched_treasury,
              matched_collector, matched_subprov, matched_signaller, matched_relay,
              evidence_paths_json, reviewed_status, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(mint) DO UPDATE SET
             score=excluded.score, tier=excluded.tier, reasons_json=excluded.reasons_json,
             ancestry_depth=excluded.ancestry_depth, matched_treasury=excluded.matched_treasury,
             matched_collector=excluded.matched_collector, matched_subprov=excluded.matched_subprov,
             matched_signaller=excluded.matched_signaller, matched_relay=excluded.matched_relay,
             evidence_paths_json=excluded.evidence_paths_json, scored_at=excluded.scored_at""",
        (a["mint"], a["creator"], a["score"], a["tier"], json.dumps(a["reasons"]),
         a["ancestry_depth"], a["matched_treasury"], a["matched_collector"],
         a["matched_subprov"], a["matched_signaller"], a["matched_relay"],
         json.dumps(a["evidence_paths"]), reviewed_status, int(time.time())))
    conn.commit()


# ── Layer 3: webhook gating by tier ──────────────────────────────────────────
def webhook_decision(tier: str) -> str:
    """STRONG → webhook immediately. WEAK → dashboard review (optional webhook).
    NONE → store only, no alert. The single gate that stops generic creators polluting."""
    return {"STRONG": "WEBHOOK", "WEAK": "REVIEW",
            "UNCONFIRMED_WATCHTOWER_LIKE": "REVIEW", "NONE": "STORE_ONLY"}.get(tier, "STORE_ONLY")


# ── intake pipeline: migration → store → score → tier → gate ─────────────────
def intake_migration(conn, mint, creator, funder_walk_fn, *, migration_tx=None,
                     migration_time=None, pool=None, initial_funder=None,
                     funding_amount=None, source="pumpfun_migration") -> dict:
    """The full pipeline for one migration:
       1. store NEUTRALLY (Layer 1)
       2. score WATCHTOWER attribution (Layer 2)
       3. assign tier + webhook decision (Layer 3)
    Returns {tier, score, webhook_action, reasons}. Does NOT itself call the webhook —
    the caller acts on webhook_action (keeps this module side-effect-free / testable)."""
    store_migration(conn, mint, creator, migration_tx, migration_time, pool,
                    initial_funder, funding_amount, raw_tx_parsed=bool(funding_amount),
                    source=source)
    a = score_token(conn, mint, creator, funder_walk_fn, funding_amount=funding_amount)
    # WEAK tokens go to the review queue; STRONG are auto; NONE stay AUTO/none
    # UNCONFIRMED_WATCHTOWER_LIKE → REVIEW (store lead, no webhook yet)
    if a["tier"] == "UNCONFIRMED_WATCHTOWER_LIKE":
        reviewed = "REVIEW"
        try:
            record_unconfirmed_watchtower_like(
                conn, mint=mint, creator=creator or "",
                subprov=a.get("uwl_subprov") or "",
                unknown_root=a.get("uwl_root") or "",
                root_hop=a.get("uwl_root_hop") or 0,
                amount_sol=getattr(funder_walk_fn, "_last_wrap_close_amount", None) or 0.0)
        except Exception:
            pass
    else:
        reviewed = "REVIEW" if a["tier"] == "WEAK" else "AUTO"
    persist_attribution(conn, a, reviewed_status=reviewed)
    a["webhook_action"] = webhook_decision(a["tier"])
    return a
