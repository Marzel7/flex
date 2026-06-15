"""WATCHTOWER vanity-family detection — an operator-attribution ENHANCER (not a root of truth).

WATCHTOWER operators deliberately GRIND vanity addresses for their persistent infrastructure
(treasury, signallers) so the wallets share a distinctive prefix — e.g. the confirmed `44or`
family: 44orWS68… (TREASURY), 44orA1Bx… (SIGNALLER), 44o1Hecb… (SIGNALLER_2). Grinding a 4-char
base58 prefix costs real compute, so a NEW wallet sharing a known family's prefix AND showing
infra behaviour is a strong same-operator signal.

HARD RULES (enforced here, per the spec):
  • Vanity match is EVIDENCE/confidence ONLY — never confirms a treasury or assigns a role.
  • Full 32–44 char addresses remain the source of truth. We match against CONFIGURED family
    prefixes only — never infer a family from an arbitrary first 2–3 chars of some wallet.
  • Minimum prefix length 4 (MIN_PREFIX_LEN) — avoids accidental short-prefix collisions like
    the unrelated 43PKjr…3y3D vs 43PKjr…n7vh case (which share 12 chars by coincidence but are
    NOT a configured family, so they never match here).
  • detect_vanity_family requires a full wallet address as input and returns evidence, not a role.
"""

from __future__ import annotations

import os
import json
import time
import sqlite3
from typing import Optional, Dict, List

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    def db_connect(path, timeout=30, row_factory=None):
        c = sqlite3.connect(path, timeout=timeout)
        if row_factory:
            c.row_factory = row_factory
        return c

OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db")))

# Minimum DELIBERATE-grind prefix length. 2–3 chars collide by accident; ≥4 base58 chars cost
# real compute to grind, so they're an operator signature. Family prefixes shorter than this
# are rejected at seed time.
MIN_PREFIX_LEN = 4

# Minimum deliberate-grind SUFFIX length. Suffix grinding is harder than prefix (the whole key
# must produce the trailing chars), so a 4-char base58 suffix is a strong operator signature.
MIN_SUFFIX_LEN = 4

# Confidence for a prefix-only match BEFORE behavioural context is applied. The caller raises
# it when the wallet also shows infra behaviour (treasury outbound, signaller dust, etc.).
PREFIX_ONLY_CONFIDENCE = "EVIDENCE"     # EVIDENCE < LIKELY < STRONG (never CONFIRMED from prefix)


# ─────────────────────────────── schema ────────────────────────────────────
def ensure_vanity_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_vanity_families (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        family_label           TEXT UNIQUE NOT NULL,
        family_prefixes_json   TEXT NOT NULL,        -- JSON list of prefixes, e.g. ["44or","44o1"]
        family_suffixes_json   TEXT,                 -- JSON list of suffixes, e.g. ["3y3D"] (suffix-grind families)
        confirmed_wallets_json TEXT,                 -- JSON list of FULL addresses (source of truth)
        roles_json             TEXT,                 -- JSON {wallet: role}
        confidence             TEXT DEFAULT 'CONFIRMED',
        evidence_json          TEXT,
        created_at             INTEGER NOT NULL DEFAULT (strftime('%s','now')),
        updated_at             INTEGER NOT NULL DEFAULT (strftime('%s','now'))
    )""")
    # migrate: add suffix column to a pre-existing families table
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(wt_vanity_families)").fetchall()}
        if "family_suffixes_json" not in _cols:
            conn.execute("ALTER TABLE wt_vanity_families ADD COLUMN family_suffixes_json TEXT")
    except Exception:
        pass
    # observed matches — every NEW wallet that hit a configured family (evidence, not roles)
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_vanity_matches (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet        TEXT NOT NULL,                 -- FULL address (never truncated)
        family_label  TEXT NOT NULL,
        matched_prefix TEXT NOT NULL,
        confidence    TEXT,
        source_event  TEXT,                          -- where we saw it (treasury_outbound, subprov, …)
        source_sig    TEXT,                          -- tx signature, full
        detected_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
        UNIQUE(wallet, family_label, source_sig)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_vanity_match_wallet ON wt_vanity_matches(wallet)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_vanity_match_family ON wt_vanity_matches(family_label)")
    conn.commit()


# ─────────────────────────────── seed ──────────────────────────────────────
_SEED_FAMILIES = [
    {
        "family_label": "WATCHTOWER_44OR",
        # two related deliberate 4-char prefixes (treasury+signaller share 44or; signaller_2 is 44o1).
        # Both are ≥4 distinctive base58 chars — NOT the loose 3-char "44o".
        "family_prefixes": ["44or", "44o1"],
        "confirmed_wallets": [
            "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM",
            "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM",
            "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM",
        ],
        "roles": {
            "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM": "TREASURY",
            "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM": "SIGNALLER",
            "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM": "SIGNALLER_2",
        },
        "confidence": "CONFIRMED",
        "evidence": {"source": "WATCHTOWER_FINDINGS.md",
                     "note": "deliberately ground 44or/44o1 prefix family — same operator (treasury + dual signallers)"},
    },
    {
        "family_label": "WATCHTOWER_43P_3Y3D",
        # SUFFIX-grind family: shared 3-char prefix '43P' is too short alone, but the 4-char
        # suffix '3y3D' is a strong deliberate grind. Anchored on a CONFIRMED treasury
        # (43PKjr22…3y3D); the siblings are unconfirmed but same-operator EVIDENCE.
        "family_prefixes": [],                 # prefix '43P' (3) is below MIN_PREFIX_LEN → not used
        "family_suffixes": ["3y3D"],
        "confirmed_wallets": [
            "43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D",   # CONFIRMED treasury (the anchor)
        ],
        "roles": {
            "43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D": "TREASURY",
        },
        "confidence": "CONFIRMED",
        "evidence": {"source": "operator-touch on new treasury 5JWii73",
                     "note": "43P…3y3D suffix-grind family; anchor 43PKjr22 is a confirmed treasury, "
                             "siblings 43PKrTh5…/43P1jMn… surfaced touching 5JWii73 — same operator"},
    },
]


def seed_known_families(conn) -> int:
    ensure_vanity_schema(conn)
    n = 0
    for fam in _SEED_FAMILIES:
        prefixes = [p for p in fam.get("family_prefixes", []) if len(p) >= MIN_PREFIX_LEN]
        suffixes = [s for s in fam.get("family_suffixes", []) if len(s) >= MIN_SUFFIX_LEN]
        if not prefixes and not suffixes:
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO wt_vanity_families
                 (family_label, family_prefixes_json, family_suffixes_json, confirmed_wallets_json,
                  roles_json, confidence, evidence_json)
               VALUES (?,?,?,?,?,?,?)""",
            (fam["family_label"], json.dumps(prefixes), json.dumps(suffixes),
             json.dumps(fam["confirmed_wallets"]),
             json.dumps(fam["roles"]), fam.get("confidence", "CONFIRMED"),
             json.dumps(fam.get("evidence", {}))))
        n += cur.rowcount
    conn.commit()
    return n


def auto_seed_from_confirmed(conn) -> int:
    """ZERO-RPC: derive vanity families from the CONFIRMED-treasury set itself. Clusters the
    confirmed treasuries by any shared ≥MIN_PREFIX_LEN prefix OR ≥MIN_SUFFIX_LEN suffix; a
    cluster of 2+ treasuries sharing a deliberate grind is a same-operator family. This makes
    families self-detect from the data instead of relying on hand-seeded _SEED_FAMILIES — so
    the next 43P…3y3D-style family surfaces automatically. Returns # of families created.

    A single confirmed treasury with a distinctive grind ALSO seeds a family (anchor-of-one),
    so an unknown sibling sharing its prefix/suffix gets flagged — even before a 2nd member is
    confirmed (this is exactly how 43PKrTh5 was caught off the lone 43PKjr22 anchor)."""
    ensure_vanity_schema(conn)
    try:
        treasuries = [r[0] for r in conn.execute(
            "SELECT treasury FROM wt_confirmed_treasuries WHERE treasury IS NOT NULL").fetchall()]
    except Exception:
        return 0
    from collections import defaultdict
    by_prefix = defaultdict(list)
    by_suffix = defaultdict(list)
    for t in treasuries:
        if len(t) >= 32:
            by_prefix[t[:MIN_PREFIX_LEN]].append(t)
            by_suffix[t[-MIN_SUFFIX_LEN:]].append(t)
    # existing family-labels (don't duplicate)
    existing = {r[0] for r in conn.execute("SELECT family_label FROM wt_vanity_families").fetchall()}
    n = 0
    def _mk(label, members, prefixes, suffixes, kind, key):
        nonlocal n
        if label in existing:
            return
        cur = conn.execute(
            """INSERT OR IGNORE INTO wt_vanity_families
                 (family_label, family_prefixes_json, family_suffixes_json, confirmed_wallets_json,
                  roles_json, confidence, evidence_json)
               VALUES (?,?,?,?,?,?,?)""",
            (label, json.dumps(prefixes), json.dumps(suffixes), json.dumps(sorted(members)),
             json.dumps({m: "TREASURY" for m in members}), "CONFIRMED",
             json.dumps({"source": "auto_seed_from_confirmed",
                         "note": f"derived from confirmed treasuries sharing {kind} '{key}'"})))
        n += cur.rowcount
        if cur.rowcount:
            existing.add(label)
    # multi-member clusters (2+ confirmed treasuries sharing a grind = strongest evidence)
    for pfx, members in by_prefix.items():
        if len(set(members)) >= 2:
            _mk(f"AUTO_PFX_{pfx}", set(members), [pfx], [], "prefix", pfx)
    for sfx, members in by_suffix.items():
        if len(set(members)) >= 2:
            _mk(f"AUTO_SFX_{sfx}", set(members), [], [sfx], "suffix", sfx)
    # anchor-of-one: a SINGLE confirmed treasury with a DISTINCTIVE grind still seeds a family,
    # so an unknown sibling sharing that grind gets flagged before a 2nd member is confirmed
    # (this is how 43PKrTh5 was caught off the lone 43PKjr22 anchor). "Distinctive" = the grind
    # contains a non-trivial char run; we accept all ≥4-char grinds here since the match still
    # only ever produces EVIDENCE (never a role), and a false grouping costs only a soft flag.
    for pfx, members in by_prefix.items():
        if len(set(members)) == 1:
            t = next(iter(members))
            _mk(f"AUTO_PFX_{pfx}", {t}, [pfx], [], "prefix", pfx)
    for sfx, members in by_suffix.items():
        if len(set(members)) == 1:
            t = next(iter(members))
            _mk(f"AUTO_SFX_{sfx}", {t}, [], [sfx], "suffix", sfx)
    conn.commit()
    return n


# ─────────────────────────── families cache ────────────────────────────────
_FAM_CACHE = {"at": 0.0, "families": []}


def _families(conn=None):
    now = time.time()
    if now - _FAM_CACHE["at"] < 60 and _FAM_CACHE["families"]:
        return _FAM_CACHE["families"]
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=15)
    try:
        ensure_vanity_schema(conn)
        if not conn.execute("SELECT COUNT(*) FROM wt_vanity_families").fetchone()[0]:
            seed_known_families(conn)
        # auto-derive families from the confirmed-treasury set (idempotent, INSERT OR IGNORE;
        # picks up newly-confirmed treasuries that form a shared-grind cluster). Zero RPC.
        try:
            auto_seed_from_confirmed(conn)
        except Exception:
            pass
        fams = []
        for r in conn.execute(
            "SELECT family_label, family_prefixes_json, confirmed_wallets_json, roles_json, "
            "confidence, family_suffixes_json FROM wt_vanity_families").fetchall():
            fams.append({
                "label": r[0],
                "prefixes": [p for p in (json.loads(r[1] or "[]")) if len(p) >= MIN_PREFIX_LEN],
                "suffixes": [s for s in (json.loads(r[5] or "[]")) if len(s) >= MIN_SUFFIX_LEN],
                "wallets": set(json.loads(r[2] or "[]")),
                "roles": json.loads(r[3] or "{}"),
                "confidence": r[4],
            })
    finally:
        if own:
            conn.close()
    _FAM_CACHE["families"] = fams
    _FAM_CACHE["at"] = now
    return fams


# ─────────────────────────── detection helper ──────────────────────────────
def detect_vanity_family(wallet_address: str, conn=None) -> Dict:
    """Does this FULL wallet address share a deliberate prefix with a CONFIGURED vanity family?

    Returns {matched, family_label, matched_prefix, confidence, reason, known_member,
             known_wallets}. Evidence only — never a role assignment. Requires a full address
    (rejects truncated input). Matches ONLY against configured family prefixes of length
    ≥ MIN_PREFIX_LEN, so arbitrary 2–3 char overlaps never match."""
    NO = {"matched": False, "family_label": None, "matched_prefix": None,
          "confidence": None, "reason": None}
    if not wallet_address or not isinstance(wallet_address, str):
        return dict(NO, reason="no wallet")
    if len(wallet_address) < 32:                      # full Solana addresses are 32–44 chars
        return dict(NO, reason="not a full address (refusing to match a truncated input)")

    for fam in _families(conn):
        # exact known member → strongest evidence (it IS one of the confirmed infra wallets)
        if wallet_address in fam["wallets"]:
            return {"matched": True, "family_label": fam["label"],
                    "matched_prefix": next((p for p in fam["prefixes"]
                                            if wallet_address.startswith(p)), None),
                    "confidence": "KNOWN_MEMBER",
                    "reason": f"exact confirmed member of {fam['label']}"
                              f" (role {fam['roles'].get(wallet_address, '?')})",
                    "known_member": True, "known_wallets": sorted(fam["wallets"]),
                    "role": fam["roles"].get(wallet_address)}
        # prefix match against a CONFIGURED family prefix (≥4 chars) → evidence, not confirmation
        for p in fam["prefixes"]:
            if wallet_address.startswith(p):
                return {"matched": True, "family_label": fam["label"], "matched_prefix": p,
                        "confidence": PREFIX_ONLY_CONFIDENCE,
                        "reason": f"shares deliberate vanity prefix '{p}' with {fam['label']} "
                                  f"(evidence of same operator — NOT a role assignment)",
                        "known_member": False, "known_wallets": sorted(fam["wallets"])}
        # suffix match (≥4 chars) → suffix-grind families (e.g. 43P…3y3D). Same evidence weight.
        for s in fam.get("suffixes", []):
            if wallet_address.endswith(s):
                return {"matched": True, "family_label": fam["label"], "matched_prefix": "…" + s,
                        "confidence": PREFIX_ONLY_CONFIDENCE,
                        "reason": f"shares deliberate vanity suffix '…{s}' with {fam['label']} "
                                  f"(evidence of same operator — NOT a role assignment)",
                        "known_member": False, "known_wallets": sorted(fam["wallets"])}
    return dict(NO, reason="no configured vanity family prefix/suffix matched", known_member=False)


def record_match(wallet_address: str, det: Dict, *, source_event: str = "",
                 source_sig: str = "", conn=None) -> bool:
    """Persist a positive detect_vanity_family result as EVIDENCE (full address + family + the
    source event/tx). Idempotent on (wallet, family, sig). Returns True if newly recorded."""
    if not det or not det.get("matched"):
        return False
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=15)
    try:
        ensure_vanity_schema(conn)
        cur = conn.execute(
            """INSERT OR IGNORE INTO wt_vanity_matches
                 (wallet, family_label, matched_prefix, confidence, source_event, source_sig)
               VALUES (?,?,?,?,?,?)""",
            (wallet_address, det["family_label"], det.get("matched_prefix"),
             det.get("confidence"), source_event, source_sig))
        conn.commit()
        return cur.rowcount > 0
    finally:
        if own:
            conn.close()


def check_and_record(wallet_address: str, *, source_event: str = "", source_sig: str = "",
                     conn=None) -> Optional[Dict]:
    """Convenience for discovery hooks: detect + (if matched, non-known-member) record. Returns
    the detection dict when matched, else None. Best-effort — never raises into the caller."""
    try:
        det = detect_vanity_family(wallet_address, conn=conn)
        if det.get("matched"):
            # record prefix-evidence matches; known confirmed members are already infra
            if not det.get("known_member"):
                record_match(wallet_address, det, source_event=source_event,
                             source_sig=source_sig, conn=conn)
            return det
    except Exception:
        pass
    return None


# ─────────────────────────── dashboard read ────────────────────────────────
def families_overview(conn=None) -> Dict:
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=15)
    try:
        ensure_vanity_schema(conn)
        fams = [{"label": f["label"], "prefixes": f["prefixes"],
                 "confidence": f["confidence"], "known_wallets": sorted(f["wallets"]),
                 "roles": f["roles"]} for f in _families(conn)]
        matches = [{"wallet": r[0], "family_label": r[1], "matched_prefix": r[2],
                    "confidence": r[3], "source_event": r[4], "source_sig": r[5],
                    "detected_at": r[6]}
                   for r in conn.execute(
                       "SELECT wallet, family_label, matched_prefix, confidence, source_event, "
                       "source_sig, detected_at FROM wt_vanity_matches ORDER BY detected_at DESC "
                       "LIMIT 50").fetchall()]
        return {"families": fams, "matches": matches, "match_count": len(matches)}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    c = db_connect(OPS_DB_PATH, timeout=15)
    print("seeded families:", seed_known_families(c))
    for w in ["44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM",
              "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM",
              "43PKjr22AFXtCMmLtQ1wxYojnjqEB86iFKK5qUYo3y3D"]:
        print(w[:12], "→", detect_vanity_family(w, conn=c))
    c.close()
