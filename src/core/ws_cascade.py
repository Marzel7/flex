"""WATCHTOWER real-time WebSocket cascade — standalone daemon.

    python -m src.core.ws_cascade --loop      # continuous (supervised)
    python -m src.core.ws_cascade --once       # single tick (debug)

The creator CANNOT be identified pre-launch (proven on-chain: same-instant wrap-close
siblings are byte-for-byte identical and all carry the …039280 tail). So this service does
NOT predict the creator. Instead:

  confirmed TREASURY → SUB_PROV funding  (webhook writes wt_active_subprov_sessions)
      → WS-watch the SUB_PROV
      → on each wrap-close, extract EVERY closeAccount.destination → WS-watch each candidate
      → the candidate that emits a Pump.fun CREATE is the creator → attribute + record launch
      → tear down all watches (creator + siblings)

Single Helius WS connection; one logsSubscribe per wallet. The DB is the source of truth, so
a reconnect/restart rebuilds every subscription from wt_active_subprov_sessions +
wt_candidate_websocket_watches. Reconcile (operation_armed) stays a fallback only.
"""

from __future__ import annotations

import os
import sys
import json
import time
import signal
import asyncio
import threading
import traceback
from typing import Optional

try:
    import websockets
except Exception:                                    # pragma: no cover
    websockets = None

from src.utils.db_locking import db_connect
from src.core import ws_cascade_store as store
from src.core.ws_cascade_store import OPS_DB_PATH, LIVE_DB_PATH, emit_event
from src.core.wrap_close_detector import extract_close_destinations

# ── config (env, conservative defaults) ──────────────────────────────────────
SESSION_TTL_SEC   = int(os.environ.get("WS_SESSION_TTL_SEC", "600"))     # 10 min
CANDIDATE_TTL_SEC = int(os.environ.get("WS_CANDIDATE_TTL_SEC", "180"))   # 3 min
MAX_CANDIDATES    = int(os.environ.get("WS_MAX_CANDIDATES", "25"))       # per sub-prov
MAX_ACTIVE_SUBPROVS = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS", "10"))
POLL_SEC          = float(os.environ.get("WS_POLL_SEC", "2"))
CLEANUP_SEC       = float(os.environ.get("WS_CLEANUP_SEC", "5"))
HEARTBEAT_SEC     = 30
# catch-up scans the candidate's most-recent sigs immediately after subscribing. Live, the
# CREATE is the newest tx (just funded), so a small window suffices; bump if INSTANT creators
# do >1 action before catch-up runs.
CATCHUP_SIG_LIMIT = int(os.environ.get("WS_CATCHUP_SIG_LIMIT", "8"))
# How often to sweep ACTIVE subprovs for wrap-closes whose WS notification dropped/stalled.
# This is the reliability backstop for the ~100s-miss case (a dropped subprov notification).
# RPC-bounded: one getSignatures per active subprov per sweep, deduped so it doesn't refetch.
SUBPROV_SWEEP_SEC = float(os.environ.get("WS_SUBPROV_SWEEP_SEC", "6"))

# Treasury WS tier: permanently logsSubscribe the (small, stable) confirmed-treasury set so a
# provisioning outbound opens a SUB_PROV session in real-time (~3s) instead of waiting on the
# enhanced webhook (5–390s + ngrok jitter). WS-first; the webhook path remains as a fallback
# (start_session is idempotent on (subprov, funding_sig), so whichever fires first wins).
# Floor for what counts as a launch-PROVISIONING outbound (→ open a SUB_PROV session).
# SOURCE-AWARE: the WS tier only subscribes to CONFIRMED treasuries, so the sender is already
# authoritative — a known treasury seeding ANY provisioning-sized amount is signal regardless of
# size. Real subprovs can start small: GnaMKX's FIRST seed from 5JWii73 was 1◎ before later 100s◎
# top-ups, and 2/22 confirmed-fan-out subprovs were funded ENTIRELY below 50◎. So from a known
# treasury we use a LOW floor (1◎); the 50◎ figure (below) is kept only as the documented
# noise-vs-provision boundary for any future UNATTRIBUTED-source path.
#   Bimodal evidence (154 historical sessions): sub-13◎ cluster = peer-treasury top-ups + dust
#   (mesh noise), 60–990◎ cluster = real provisions. 50◎ sits in the empty 13–60◎ gap.
TREASURY_PROVISION_MIN_SOL       = float(os.environ.get("WS_TREASURY_MIN_SOL", "1"))    # known-treasury floor
TREASURY_PROVISION_NOISE_SOL     = float(os.environ.get("WS_TREASURY_NOISE_SOL", "50")) # unattributed-source floor (ref)
TREASURY_PROVISION_MAX_SOL = float(os.environ.get("WS_TREASURY_MAX_SOL", "1000"))
TREASURY_REFRESH_SEC       = float(os.environ.get("WS_TREASURY_REFRESH_SEC", "60"))

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def _confirmed_treasuries(conn) -> set:
    """The authoritative confirmed-treasury set (wt_confirmed_treasuries, ops DB) — the wallets
    we WS-subscribe permanently. Small + stable (≈12)."""
    try:
        return {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
    except Exception:
        return set()
# pump.fun instruction discriminators (first 8 bytes of the instruction data). The CREATE ix
# carries the mint at accounts[0] — verified stable across fixtures (xmaxxing + Donald80):
#   CREATE = d6904cec5f8b31b4  (16 accounts: [0]=mint [2]=bonding_curve [3]=assoc_bonding_curve)
#   BUY    = 66063d1201daebea  (18 accounts: mint at [2])  — NOT a CREATE
PUMP_CREATE_DISCRIMINATOR = "d6904cec5f8b31b4"
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
WS_URL = os.environ.get("HELIUS_WS_URL", f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URL = os.environ.get("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")

_STOP = False
_CLEANUP_COUNT = 0


def _handle_signal(*_a):
    global _STOP
    _STOP = True


def _log(msg):
    print(f"[WS_CASCADE] {msg}", flush=True)


# ── raw RPC (1 credit each — never the enhanced-tx endpoint) ─────────────────
# IMPORTANT: _rpc uses BLOCKING urllib. It must NEVER be called directly on the asyncio event
# loop — a slow/stalled RPC would freeze the WHOLE loop (ws.recv stops reading, keepalive pings
# stop → "keepalive ping timeout", and live logsNotifications back up unread in the socket
# buffer). All loop-context callers go through `_arpc`/`_aget_tx` (run_in_executor → thread pool),
# so blocking I/O happens off the loop and recv keeps reading. Direct _rpc is fine only in code
# already running in a worker thread (audit phase1, backfill).
def _rpc(method, params, timeout=12):
    import urllib.request
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(RPC_URL, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "ws-cascade/0.1"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("result")
    except Exception as e:
        _log(f"rpc {method} failed: {e}")
        return None


def _get_tx(sig):
    return _rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])


# ── async, off-loop wrappers — run blocking RPC + DB work in the default thread-pool executor
# so the asyncio event loop (ws.recv + keepalive) is NEVER frozen by I/O. ───────────────────
async def _arpc(method, params, timeout=12):
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _rpc(method, params, timeout))


async def _aget_tx(sig):
    return await asyncio.get_event_loop().run_in_executor(None, _get_tx, sig)


async def _ato_thread(fn, *args):
    """Run a blocking function (DB writes, sync handlers) off the event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args))


_B58_ALPHABET = b'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _b58_first8_hex(s):
    """Decode the first 8 bytes of a base58 string to hex (the instruction discriminator).
    Decodes the whole string (cheap — pump ix data is short) and slices."""
    try:
        num = 0
        for ch in s.encode():
            num = num * 58 + _B58_ALPHABET.index(ch)
        full = num.to_bytes((num.bit_length() + 7) // 8, 'big') if num else b''
        full = b'\x00' * (len(s) - len(s.lstrip('1'))) + full
        return full[:8].hex()
    except Exception:
        return ""


def _find_pump_create_ix(tx):
    """Locate the pump.fun CREATE instruction (top-level OR inner) by its discriminator and
    return its account list. The CREATE ix has the mint at accounts[0]. Returns [] if none."""
    msg = tx.get("transaction", {}).get("message", {}) or {}
    meta = tx.get("meta") or {}
    all_ix = list(msg.get("instructions", []) or [])
    for grp in (meta.get("innerInstructions") or []):
        all_ix += grp.get("instructions", []) or []
    for ix in all_ix:
        if ix.get("programId") != PUMP_PROGRAM or "data" not in ix:
            continue
        if _b58_first8_hex(ix.get("data", "")) == PUMP_CREATE_DISCRIMINATOR:
            return ix.get("accounts", []) or []
    return []


def _tx_is_create(tx):
    """Return (is_create, mint, block_time, extra) from a decoded tx.

    A tx is a CREATE only if it contains the actual pump CREATE INSTRUCTION (matched by its
    discriminator) — NOT merely a log line "Instruction: Create", which also appears in later
    swap txs that CPI into pump (the false positive that caused duplicate launches + wrong
    oldest-first selection). Mint is taken DIRECTLY from create_ix.accounts[0] (design item 8;
    [2]=bonding_curve, [3]=assoc_bonding_curve). postTokenBalances is fallback only (item 9)."""
    if not tx:
        return False, None, None, {}
    accts = _find_pump_create_ix(tx)
    if not accts:                                      # no real CREATE instruction → not a CREATE
        return False, None, tx.get("blockTime"), {}

    extra = {"mint_source": "create_instruction"}
    mint = accts[0] if len(accts) > 0 else None
    if len(accts) > 2:
        extra["bonding_curve"] = accts[2]
    if len(accts) > 3:
        extra["associated_bonding_curve"] = accts[3]
    if not mint:                                       # FALLBACK ONLY
        for tb in ((tx.get("meta") or {}).get("postTokenBalances") or []):
            if tb.get("mint") and "So111" not in tb["mint"]:
                mint = tb["mint"]; extra["mint_source"] = "post_token_balances"; break
    return True, mint, tx.get("blockTime"), extra


def _tx_is_swap(tx):
    if not tx:
        return False
    logs = " ".join((tx.get("meta") or {}).get("logMessages", []) or [])
    return ("Instruction: Buy" in logs) or ("Instruction: Sell" in logs)


def _classify_sibling(sib):
    """BLOCKING (RPC) — classify a teardown sibling: did it SWAP (→BUY_SWARM) or stay idle
    (→EXPIRED_SIBLING)? Runs in a worker thread via _ato_thread, never on the event loop."""
    state, reason = "EXPIRED_SIBLING", "sibling_idle"
    try:
        tx_sigs = _rpc("getSignaturesForAddress", [sib, {"limit": 10}]) or []
        for s in tx_sigs:
            if s.get("err"):
                continue
            if _tx_is_swap(_get_tx(s["signature"])):
                state, reason = "BUY_SWARM", "sibling_swapped"; break
    except Exception:
        pass
    return state, reason


# ── subscription manager ─────────────────────────────────────────────────────
class SubscriptionManager:
    """Tracks wallet ↔ helius subscription_id on one WS connection. logsSubscribe per wallet.
    pending_req maps the JSON-RPC request id → wallet until the subscription is confirmed."""

    def __init__(self):
        self.ws = None
        self.next_req = 1
        self.pending_req = {}          # req_id -> (wallet, kind)   kind = 'subprov'|'candidate'
        self.wallet_sub = {}           # wallet -> subscription_id
        self.sub_wallet = {}           # subscription_id -> (wallet, kind)
        self.wallet_kind = {}          # wallet -> kind

    async def subscribe(self, wallet, kind):
        if wallet in self.wallet_sub or wallet in self.wallet_kind:
            return                      # already (being) subscribed
        rid = self.next_req; self.next_req += 1
        self.pending_req[rid] = (wallet, kind, time.time())
        self.wallet_kind[wallet] = kind
        if kind == "treasury":
            # TREASURIES move SOL via plain system:transfer, which emits no program logs that
            # logsSubscribe's `mentions` filter matches — so logsSubscribe NEVER fired for them.
            # accountSubscribe fires on every balance change (a plain transfer always changes the
            # balance), so it's the correct primitive for the treasury tier. (Subprovs/candidates
            # keep logsSubscribe — their wrap-close emits token-program logs that DO mention them.)
            msg = {"jsonrpc": "2.0", "id": rid, "method": "accountSubscribe",
                   "params": [wallet, {"commitment": "confirmed", "encoding": "jsonParsed"}]}
        else:
            msg = {"jsonrpc": "2.0", "id": rid, "method": "logsSubscribe",
                   "params": [{"mentions": [wallet]}, {"commitment": "confirmed"}]}
        await self.ws.send(json.dumps(msg))

    def sweep_stale_pending(self, max_age=30):
        """Clear pending subscribe requests that never confirmed (e.g. an invalid pubkey
        Helius silently rejects). Frees wallet_kind so a future valid retry can proceed and
        avoids a permanent leak. Returns the wallets dropped."""
        now = time.time()
        dropped = []
        for rid, ent in list(self.pending_req.items()):
            wallet, kind, ts = ent
            if now - ts > max_age:
                self.pending_req.pop(rid, None)
                self.wallet_kind.pop(wallet, None)
                dropped.append(wallet)
        return dropped

    async def unsubscribe(self, wallet):
        sub_id = self.wallet_sub.pop(wallet, None)
        self.wallet_kind.pop(wallet, None)
        if sub_id is not None:
            self.sub_wallet.pop(sub_id, None)
            try:
                rid = self.next_req; self.next_req += 1
                await self.ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "method": "logsUnsubscribe", "params": [sub_id]}))
            except Exception:
                pass

    def on_subscribe_confirmed(self, rid, sub_id):
        ent = self.pending_req.pop(rid, None)
        if not ent:
            return None
        wallet, kind = ent[0], ent[1]
        self.wallet_sub[wallet] = sub_id
        self.sub_wallet[sub_id] = (wallet, kind)
        return wallet, kind

    def lookup(self, sub_id):
        return self.sub_wallet.get(sub_id)

    def reset(self):
        self.pending_req.clear(); self.wallet_sub.clear()
        self.sub_wallet.clear(); self.wallet_kind.clear()


# ── core cascade ─────────────────────────────────────────────────────────────
class Cascade:
    def __init__(self):
        self.mgr = SubscriptionManager()
        # idempotency: (candidate, sig) already processed — guards against the WS notification
        # and the catch-up scan handling the same CREATE/SWAP twice. record_launch is also
        # INSERT OR IGNORE on (creator, create_sig), so the LEDGER is idempotent regardless;
        # this set additionally suppresses duplicate events + teardown. Bounded by eviction.
        self._processed = set()
        # subprov sigs already scanned by the subprov catch-up — avoids re-fetching the same
        # wrap-close txs every sweep. (open_candidate_watch is also INSERT OR IGNORE, so even a
        # re-scan can't double-open a candidate; this just saves the RPC.)
        self._subprov_seen = set()

    def _seen(self, candidate, sig):
        key = (candidate, sig)
        if key in self._processed:
            return True
        self._processed.add(key)
        if len(self._processed) > 5000:                # bound memory; evict oldest-ish
            for k in list(self._processed)[:1000]:
                self._processed.discard(k)
        return False

    def _subprov_sig_seen(self, subprov, sig):
        key = (subprov, sig)
        if key in self._subprov_seen:
            return True
        self._subprov_seen.add(key)
        if len(self._subprov_seen) > 5000:
            for k in list(self._subprov_seen)[:1000]:
                self._subprov_seen.discard(k)
        return False

    def _ops(self):
        c = db_connect(OPS_DB_PATH, timeout=20)
        c.execute("PRAGMA busy_timeout=20000")
        store.ensure_cascade_schema(c)
        return c

    # ---- (re)build subscriptions from DB (startup + reconnect) --------------
    async def resync_subscriptions(self):
        conn = self._ops()
        try:
            treasuries = _confirmed_treasuries(conn)
            self._treasuries = treasuries     # cache for the mesh-skip gate in _handle_treasury_tx
            for t in treasuries:
                store.treasury_ws_register(conn, t)
            sessions = store.active_sessions(conn)[:MAX_ACTIVE_SUBPROVS]
            candidates = [c[0] for c in store.watching_candidates(conn)]
        finally:
            conn.close()
        # TREASURY TIER: permanent WS subscriptions on the confirmed-treasury set. Real-time
        # trigger for opening SUB_PROV sessions (replaces the slow webhook→session path).
        for t in treasuries:
            if t not in self.mgr.wallet_kind:
                await self.mgr.subscribe(t, "treasury")
                emit_event("TREASURY_WEBSOCKET_OPENED", wallet=t)
        for s in sessions:
            subprov = s[1]
            if subprov not in self.mgr.wallet_kind:
                await self.mgr.subscribe(subprov, "subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov)
                # catch-up on first subscribe: a wrap-close may have fired in the webhook→session
                # delay before we subscribed (the 25s gap). Recover it immediately.
                await self.catch_up_subprov(subprov)
        for cand in candidates:
            if cand not in self.mgr.wallet_kind:
                await self.mgr.subscribe(cand, "candidate")
                # catch-up on (re)subscribe: a restored WATCHING candidate may have CREATEd
                # while we were reconnecting/restarting.
                await self.catch_up_candidate(cand)

    # ---- handle a TREASURY log notification (provisioning outbound) ---------
    def _handle_treasury_tx(self, treasury, sig):
        """A confirmed treasury did something. If it's a provisioning-sized SOL outbound to
        another wallet, open a SUB_PROV session in real-time (the WS-first trigger). Always
        meter the notification so the UI can spot a treasury turning into a swarm hub.
        Returns the list of newly-opened subprov wallets (to subscribe on the loop)."""
        conn = self._ops()
        opened = []
        try:
            tx = _get_tx(sig)
            if not tx:
                store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)
                return []
            meta = tx.get("meta") or {}
            keys = [k.get("pubkey") if isinstance(k, dict) else k
                    for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or [])]
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            btime = tx.get("blockTime")
            try:
                ti = keys.index(treasury)
            except ValueError:
                store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)
                return []
            # treasury must have SENT SOL (lost lamports)
            if ti >= min(len(pre), len(post)) or post[ti] >= pre[ti]:
                store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)
                return []
            # recipient(s) = wallet(s) that GAINED a provisioning-sized amount.
            # GATE BY RECIPIENT IDENTITY, not amount: the "noise" cluster we want to drop is the
            # treasury↔treasury MESH (peer treasuries topping each other up — 91% of historical
            # noise sessions, ZERO launches). Those are filtered because the recipient is itself a
            # confirmed treasury. A non-treasury recipient is a real SUB_PROV seed and is opened at
            # ANY size ≥ the low floor — real subprovs can start tiny (GnaMKX's first seed from
            # 5JWii73 was 1◎ before later 100s◎ top-ups; 2/22 fan-out subprovs were always <50◎).
            _known_treasuries = getattr(self, "_treasuries", None)
            if _known_treasuries is None:        # before first resync — derive once
                _known_treasuries = _confirmed_treasuries(conn)
                self._treasuries = _known_treasuries
            for i, w in enumerate(keys):
                if i >= min(len(pre), len(post)) or w == treasury:
                    continue
                gain = (post[i] - pre[i]) / 1e9
                if not (TREASURY_PROVISION_MIN_SOL <= gain <= TREASURY_PROVISION_MAX_SOL):
                    continue
                if w in _known_treasuries:
                    # treasury→treasury = MESH top-up, not a subprov seed → meter but don't open.
                    continue
                # REAL-TIME FEED: write the treasury outbound into wt_webhook_hits (live db)
                # tagged source='treasury_ws'. Off-thread + best-effort so a locked live db
                # can't stall the cascade — the webhook backfills the row if this misses.
                # Deduped by UNIQUE(tx_signature, wallet_address).
                threading.Thread(
                    target=store.record_treasury_hit,
                    kwargs=dict(treasury=treasury, counterparty=w, sig=sig,
                                amount_sol=gain, block_time=btime),
                    daemon=True, name="tws-hit").start()
                # open a session on this recipient (the discovered SUB_PROV). Idempotent on
                # (subprov, funding_sig) → if the webhook already opened it, this is a no-op.
                if store.start_session(conn, subprov=w, treasury=treasury, funding_sig=sig,
                                       funding_amount=gain, funding_time=btime,
                                       ttl_seconds=SESSION_TTL_SEC, subprov_known=0):
                    opened.append(w)
                    emit_event("SUBPROV_SESSION_OPENED_WS", wallet=w, related=treasury,
                               payload={"funding_sol": gain, "sig": sig, "via": "treasury_ws"})
                    _log(f"⚡ treasury {treasury[:10]}… → seed {w[:12]}… {gain:.2f} ◎ (WS, session opened)")
            store.treasury_ws_record_notif(conn, treasury, sig, opened_session=bool(opened))
            return opened
        finally:
            conn.close()

    # ---- handle a SUB_PROV log notification (wrap-close fan-out) ------------
    def _handle_subprov_tx(self, subprov, sig):
        conn = self._ops()
        try:
            sess = store.session_for_subprov(conn, subprov)
            if not sess:
                return []                              # session gone/expired
            treasury, funding_time = sess[1], sess[2]
            if store.candidate_count_for_subprov(conn, subprov) >= MAX_CANDIDATES:
                return []
            tx = _get_tx(sig)
            wrap_close_time = (tx or {}).get("blockTime")   # on-chain creator BIRTH time
            dests = extract_close_destinations(tx)
            if not dests:
                return []
            new_watches = []
            for d in dests:
                if store.candidate_count_for_subprov(conn, subprov) >= MAX_CANDIDATES:
                    break
                cand = d["candidate"]
                if store.open_candidate_watch(
                        conn, candidate=cand, subprov=subprov, treasury=treasury,
                        wrap_close_sig=sig, wrap_wallet=d.get("wrap_wallet"),
                        temp_wsol=d.get("temp_wsol_account"),
                        funding_amount=d.get("base_amount_sol"), ttl_seconds=CANDIDATE_TTL_SEC,
                        wrap_close_time=wrap_close_time):
                    new_watches.append(cand)
                    emit_event("WRAP_CLOSE_FANOUT_DETECTED", wallet=subprov, related=cand,
                               payload={"wrap_close_sig": sig, "base": d.get("base_amount_sol")})
                    # VANITY-FAMILY EVIDENCE on the wrap-close participants (candidate, wrap
                    # wallet, subprov) — same-operator signal only, full address stored.
                    try:
                        from src.core.vanity_family import check_and_record as _vf_check
                        for _w in (cand, d.get("wrap_wallet"), subprov):
                            if _w:
                                _vf_check(_w, source_event="wrap_close", source_sig=sig)
                    except Exception:
                        pass
            return new_watches
        finally:
            conn.close()

    # ---- handle a CANDIDATE log notification (CREATE vs SWAP) ---------------
    def _handle_candidate_tx(self, candidate, sig, ws_seen_at=None):
        """Returns ('CREATE', launch_dict) | ('SWAP', None) | (None, None).
        Captures the detection-latency timestamps (ws_seen / tx_fetched / mint_extracted) for
        the launch audit as it goes."""
        ws_seen_at = ws_seen_at or time.time()
        tx = _get_tx(sig)
        tx_fetched_at = time.time()
        is_create, mint, btime, extra = _tx_is_create(tx)
        mint_extracted_at = time.time()
        if is_create:
            create_slot = (tx or {}).get("slot")
            conn = self._ops()
            try:
                row = conn.execute(
                    "SELECT subprov_wallet, treasury_wallet, wrap_close_signature, wrap_close_time, "
                    "funding_amount "
                    "FROM wt_candidate_websocket_watches WHERE candidate_wallet=? "
                    "ORDER BY detected_at DESC LIMIT 1", (candidate,)).fetchone()
                subprov = row[0] if row else None
                treasury = row[1] if row else None
                wrap_sig = row[2] if row else None
                wrap_close_time = row[3] if row else None
                wrap_close_sol = row[4] if row else None    # subprov→creator wrap-close seed
                # birth_to_launch = CREATE time − the creator's BIRTH (the wrap-close that funded
                # it), NOT the treasury→subprov session funding (which adds the subprov pipeline
                # time and mislabels INSTANT launches as STAGED — e.g. Memeville read 125s vs the
                # true 1s). Fall back to the session funding_time only if wrap_close_time is absent.
                birth_time = wrap_close_time
                subprov_funding_sol = None                  # treasury→subprov load (from the session)
                if subprov:
                    sess = store.session_for_subprov(conn, subprov)
                    if sess:
                        if birth_time is None:
                            birth_time = sess[2]
                        # session row: (id, treasury, funding_time, funding_sig, [funding_amount?])
                        try:
                            # most-recent session for this subprov (any state — by launch time it
                            # may have COMPLETED/EXPIRED); the funding_amount is the treasury load.
                            sf = conn.execute(
                                "SELECT funding_amount FROM wt_active_subprov_sessions "
                                "WHERE subprov_wallet=? ORDER BY detected_at DESC LIMIT 1",
                                (subprov,)).fetchone()
                            subprov_funding_sol = sf[0] if sf else None
                        except Exception:
                            subprov_funding_sol = None
                btl = (btime - birth_time) if (btime and birth_time) else None
                newly = store.record_launch(
                    conn, mint=mint, creator=candidate, create_sig=sig, create_time=btime,
                    treasury=treasury, subprov=subprov, wrap_close_sig=wrap_sig,
                    birth_to_launch_s=btl, create_slot=create_slot,
                    subprov_funding_sol=subprov_funding_sol, wrap_close_sol=wrap_close_sol)
                if newly:
                    self._reconcile_bridge(conn, candidate, mint, btime, birth_time, subprov, treasury)
                return "CREATE", {"mint": mint, "subprov": subprov, "treasury": treasury,
                                  "create_time": btime, "btl": btl, "wrap_sig": wrap_sig,
                                  "create_sig": sig, "newly": newly, "create_slot": create_slot,
                                  "bonding_curve": extra.get("bonding_curve"),
                                  "associated_bonding_curve": extra.get("associated_bonding_curve"),
                                  "mint_source": extra.get("mint_source"),
                                  "ws_seen_at": ws_seen_at, "tx_fetched_at": tx_fetched_at,
                                  "mint_extracted_at": mint_extracted_at}
            finally:
                conn.close()
        if _tx_is_swap(tx):
            return "SWAP", None
        return None, None

    # ---- shared candidate-sig processor (WS notification AND catch-up) ------
    async def process_candidate_sig(self, candidate, sig):
        """Process ONE candidate signature: CREATE → record launch + emit + teardown;
        SWAP → BUY_SWARM + unsubscribe. Idempotent via self._seen and record_launch's
        INSERT OR IGNORE. Used by both the live WS notification and the catch-up scan, so a
        CREATE that fired before the subscription went live is still caught."""
        if self._seen(candidate, sig):
            return None
        ws_seen_at = time.time()                       # T1 — the candidate sig in hand
        # belt-and-suspenders: if this candidate already fired, don't reprocess at all
        conn = self._ops()
        try:
            done = conn.execute(
                "SELECT 1 FROM wt_watchtower_launches WHERE creator_wallet=? LIMIT 1",
                (candidate,)).fetchone()
        finally:
            conn.close()
        if done:
            return "CREATE"
        # _handle_candidate_tx does the blocking getTransaction + DB writes → run it OFF the
        # event loop so a slow RPC can't freeze recv / keepalive.
        verdict, launch = await _ato_thread(self._handle_candidate_tx, candidate, sig, ws_seen_at)
        if verdict == "CREATE":
            btl = launch.get("btl")
            mode = "INSTANT" if (btl is not None and btl < 60) else ("STAGED" if btl is not None else "?")
            # only emit + teardown when THIS call newly recorded the launch (idempotent)
            if launch.get("newly"):
                _log(f"🚀 WATCHTOWER LAUNCH creator={candidate[:12]}… mint={launch.get('mint')} "
                     f"btl={btl}s [{mode}] (src={launch.get('mint_source')})")
                emit_event("WATCHTOWER_LAUNCH_DETECTED", wallet=candidate, related=launch.get("subprov"),
                           token_mint=launch.get("mint"),
                           payload={"create_sig": sig, "treasury": launch.get("treasury"),
                                    "birth_to_launch_s": btl, "mode": mode,
                                    "bonding_curve": launch.get("bonding_curve"),
                                    "mint_source": launch.get("mint_source")})
                alert_emitted_at = time.time()         # T4
                # AUDIT phase 1 — off-thread so the realtime path isn't blocked by the
                # buyer-position + curve-replay RPC work.
                self._trigger_audit_phase1(launch, candidate, sig, ws_seen_at, alert_emitted_at)
                await self._teardown_after_create(candidate, launch.get("subprov"))
            return "CREATE"
        elif verdict == "SWAP":
            conn = self._ops()
            try:
                store.close_candidate(conn, candidate, "BUY_SWARM", "swapped")
            finally:
                conn.close()
            await self.mgr.unsubscribe(candidate)
            emit_event("CANDIDATE_CLASSIFIED_BUY_SWARM", wallet=candidate)
            return "SWAP"
        return None

    # ---- candidate catch-up: an INSTANT launch can CREATE before the candidate
    #      subscription is even live. Immediately after opening the watch, scan the
    #      candidate's most-recent signatures and process any that already happened. ----
    async def catch_up_candidate(self, candidate, limit=CATCHUP_SIG_LIMIT):
        try:
            sigs = await _arpc("getSignaturesForAddress", [candidate, {"limit": limit}]) or []
        except Exception:
            return
        # oldest → newest so a CREATE is recorded before any later swap is seen
        for s in sorted([x for x in sigs if not x.get("err")],
                        key=lambda x: x.get("blockTime") or 0):
            sig = s.get("signature")
            if not sig:
                continue
            verdict = await self.process_candidate_sig(candidate, sig)
            if verdict == "CREATE":
                break                                  # creator found; watch torn down

    # ---- subprov-side catch-up: recover a DROPPED/LATE wrap-close notification ----
    #      The subprov's wrap-close logsNotification can be dropped or arrive ~100s late
    #      (WS drop / receive-loop stall). Because the creator wallet is UNKNOWN until we see
    #      the wrap-close, a missed subprov notification delays discovering the creator at all.
    #      This scans an ACTIVE subprov's recent sigs for wrap-closes we haven't processed and
    #      runs the same discover→subscribe→candidate-catch-up flow — turning a ~100s miss into
    #      a few seconds. Polling can't beat a 1s atomic launch, but it makes recovery RELIABLE.
    async def catch_up_subprov(self, subprov, limit=CATCHUP_SIG_LIMIT):
        try:
            sigs = await _arpc("getSignaturesForAddress", [subprov, {"limit": limit}]) or []
        except Exception:
            return
        for s in sorted([x for x in sigs if not x.get("err")],
                        key=lambda x: x.get("blockTime") or 0):
            sig = s.get("signature")
            if not sig or self._subprov_sig_seen(subprov, sig):
                continue
            # process the wrap-close exactly like a live notification would (idempotent:
            # open_candidate_watch is INSERT OR IGNORE on (candidate, wrap_close_sig)) — off-loop.
            new_watches = await _ato_thread(self._handle_subprov_tx, subprov, sig)
            for cand in new_watches:
                await self.mgr.subscribe(cand, "candidate")
                emit_event("CANDIDATE_WEBSOCKET_OPENED", wallet=cand, related=subprov)
                _log(f"👁  watching candidate {cand[:12]}… (subprov {subprov[:10]}… · catch-up)")
                await self.catch_up_candidate(cand)

    # ---- launch audit phase 1 (off-thread) ---------------------------------
    def _trigger_audit_phase1(self, launch, creator, create_sig, ws_seen_at, alert_emitted_at):
        """Fire the immediate audit capture in a daemon thread. Bounded, best-effort — never
        blocks or breaks the realtime detection path."""
        def _run():
            try:
                from src.core import launch_audit
                launch_audit.capture_phase1(
                    mint=launch.get("mint"), creator=creator, treasury=launch.get("treasury"),
                    subprov=launch.get("subprov"), create_signature=create_sig,
                    create_slot=launch.get("create_slot"), create_time=launch.get("create_time"),
                    ws_seen_at=ws_seen_at, tx_fetched_at=launch.get("tx_fetched_at"),
                    mint_extracted_at=launch.get("mint_extracted_at"),
                    alert_emitted_at=alert_emitted_at)
            except Exception as e:
                print(f"[WS_CASCADE] audit phase1 failed: {e}", flush=True)
        import threading
        threading.Thread(target=_run, daemon=True, name="audit-phase1").start()

    # ---- reconcile bridge: keep existing OPS tables consistent -------------
    def _reconcile_bridge(self, conn, creator, mint, launched_at, funded_at, subprov, treasury):
        """One launch truth. Mark wt_wrap_close_candidates FIRED, upsert wt_ops_v2_creators,
        and classify the INSTANT/STAGED mode via the existing helper — so creator panels +
        KPIs reflect the cascade launch without a second ledger."""
        try:
            conn.execute("UPDATE wt_wrap_close_candidates SET state='FIRED' WHERE creator=?", (creator,))
        except Exception:
            pass
        try:
            real_mint = mint or f"launched:{creator}"
            # wt_ops_v2_creators PK = (operation_uuid, creator_wallet, token_mint) — resolve/create
            # the treasury's operation so the row is consistent with the OPS graph.
            op_uuid = None
            try:
                from src.core.wrap_close_detector import ensure_operation_for_treasury
                if treasury:
                    op_uuid = ensure_operation_for_treasury(conn, treasury, subprov=subprov)
            except Exception:
                op_uuid = None
            if not op_uuid:
                # FULL address in the fallback op key — a truncated prefix could merge two
                # distinct treasuries' creators under one operation_uuid.
                op_uuid = f"ws-cascade:{treasury or subprov or creator}"
            conn.execute(
                "INSERT OR IGNORE INTO wt_ops_v2_creators "
                "(operation_uuid, creator_wallet, token_mint, migration_time) VALUES (?,?,?,?)",
                (op_uuid, creator, real_mint, launched_at))
            conn.execute(
                "UPDATE wt_ops_v2_creators SET migration_time=COALESCE(migration_time,?) "
                "WHERE creator_wallet=? AND token_mint=?",
                (launched_at, creator, real_mint))
        except Exception:
            pass
        try:
            from src.core.operation_armed import _classify_creator_mode
            if funded_at and launched_at:
                _classify_creator_mode(conn, creator, funded_at, launched_at)
        except Exception:
            pass
        conn.commit()

    # ---- teardown after a CREATE -------------------------------------------
    async def _teardown_after_create(self, creator, subprov):
        global _CLEANUP_COUNT
        await self.mgr.unsubscribe(creator)
        conn = self._ops()
        try:
            if subprov:
                for (sib,) in store.siblings_of(conn, subprov, creator):
                    # classify sibling OFF-LOOP (blocking RPC): SWAP → BUY_SWARM, idle → EXPIRED_SIBLING
                    state, reason = await _ato_thread(_classify_sibling, sib)
                    store.close_candidate(conn, sib, state, reason)
                    await self.mgr.unsubscribe(sib)
                    if state == "BUY_SWARM":
                        emit_event("CANDIDATE_CLASSIFIED_BUY_SWARM", wallet=sib, related=subprov)
                # close the sub-prov session if no live candidates remain
                if not store.subprov_has_live_candidates(conn, subprov):
                    sess = store.session_for_subprov(conn, subprov)
                    if sess:
                        store.close_session(conn, sess[0], "COMPLETED")
                    await self.mgr.unsubscribe(subprov)
            _CLEANUP_COUNT += 1
            emit_event("WEBSOCKET_CLEANUP_COMPLETED", wallet=creator, related=subprov,
                       payload={"cleanup_count": _CLEANUP_COUNT})
        finally:
            conn.close()

    # ---- TTL cleanup pass --------------------------------------------------
    async def cleanup_pass(self):
        for w in self.mgr.sweep_stale_pending():
            _log(f"⚠ dropped never-confirmed subscription {w[:14]}… (invalid/unsubscribable)")
        conn = self._ops()
        try:
            for (cand,) in store.expire_stale_candidates(conn):
                await self.mgr.unsubscribe(cand)
                emit_event("CANDIDATE_WATCH_EXPIRED", wallet=cand)
            for sid, subprov in store.expire_stale_sessions(conn):
                await self.mgr.unsubscribe(subprov)
                emit_event("SUBPROV_SESSION_EXPIRED", wallet=subprov)
        finally:
            conn.close()

    # ---- subprov sweep: catch-up every ACTIVE subprov (reliability backstop) ----
    async def subprov_sweep_pass(self):
        """Run catch_up_subprov over all ACTIVE subprovs to recover any wrap-close whose WS
        notification dropped/stalled. Bounded (MAX_ACTIVE_SUBPROVS, deduped sigs)."""
        conn = self._ops()
        try:
            subprovs = [s[1] for s in store.active_sessions(conn)[:MAX_ACTIVE_SUBPROVS]]
        finally:
            conn.close()
        for subprov in subprovs:
            await self.catch_up_subprov(subprov)


# ── async runner ─────────────────────────────────────────────────────────────
async def _heartbeat_loop(get_meta):
    while not _STOP:
        try:
            # Heartbeat lives in wt_ops_v2.db (quiet) — NOT the live DB, which is hot with
            # webhook/API writes and was throwing DB_LOCK_ERROR on every heartbeat. The
            # cascade's tables are here anyway, so it's the natural home; the dashboard reads
            # it from the ops db too.
            c = db_connect(OPS_DB_PATH, timeout=15)
            c.execute("PRAGMA busy_timeout=15000")
            c.execute(
                """CREATE TABLE IF NOT EXISTS wt_worker_heartbeat (
                    worker_name TEXT PRIMARY KEY, last_seen INTEGER, status TEXT, meta_json TEXT)""")
            c.execute(
                """INSERT INTO wt_worker_heartbeat (worker_name, last_seen, status, meta_json)
                   VALUES ('ws_cascade', strftime('%s','now'), 'ok', ?)
                   ON CONFLICT(worker_name) DO UPDATE SET
                     last_seen=excluded.last_seen, status=excluded.status, meta_json=excluded.meta_json""",
                (json.dumps(get_meta()),))
            c.commit(); c.close()
        except Exception:
            pass
        await asyncio.sleep(HEARTBEAT_SEC)


async def _deferred_audit_loop():
    """Phase 2: re-visit audited launches at their +5m/+30m/+2h/+24h checkpoints to fill
    peak/outcome/actionability from the snapshot tables. Bounded per tick, off the WS path.
    Reads DB only (no RPC), so it's cheap. Runs in a thread to avoid blocking the loop."""
    import asyncio as _a
    while not _STOP:
        try:
            from src.core import launch_audit

            def _tick():
                due = launch_audit.due_for_checkpoint(limit=20)
                for mint in due:
                    try:
                        launch_audit.run_phase2(mint)
                    except Exception:
                        pass
                return len(due)
            n = await _a.get_event_loop().run_in_executor(None, _tick)
            if n:
                _log(f"audit phase2: advanced {n} launch(es)")
        except Exception as e:
            print(f"[WS_CASCADE] deferred audit error: {e}", flush=True)
        await _a.sleep(60)


async def run_cascade():
    if websockets is None:
        _log("FATAL: `websockets` not installed"); return
    casc = Cascade()

    def _meta():
        return {"subs": len(casc.mgr.wallet_sub), "pending": len(casc.mgr.pending_req),
                "cleanups": _CLEANUP_COUNT}

    asyncio.ensure_future(_heartbeat_loop(_meta))
    asyncio.ensure_future(_deferred_audit_loop())
    reconnect_delay = 5
    while not _STOP:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20,
                                          open_timeout=30, close_timeout=10,
                                          max_size=10 * 1024 * 1024) as ws:
                casc.mgr.ws = ws
                casc.mgr.reset()
                _log(f"✓ WS connected ({WS_URL.split('?')[0]})")
                await casc.resync_subscriptions()
                reconnect_delay = 5

                # READER / PROCESSOR / MAINTENANCE split — the whole point of the cascade is to
                # LISTEN for the create tx in real time. The reader does NOTHING but ws.recv() →
                # queue, so a slow message (RPC/DB) can never starve recv or stall keepalive. A
                # separate processor drains the queue; maintenance (poll/cleanup/sweep) is its own
                # task. All blocking RPC/DB inside processing runs off-loop (run_in_executor).
                inbox: asyncio.Queue = asyncio.Queue(maxsize=1000)

                async def _reader():
                    while not _STOP:
                        raw = await ws.recv()              # only job: read, fast
                        try:
                            inbox.put_nowait(raw)
                        except asyncio.QueueFull:
                            # drop oldest to stay live (catch-up/sweep will recover anything missed)
                            try:
                                inbox.get_nowait()
                            except Exception:
                                pass
                            try:
                                inbox.put_nowait(raw)
                            except Exception:
                                pass

                async def _processor():
                    while not _STOP:
                        raw = await inbox.get()
                        try:
                            await _on_message(casc, raw)
                        except Exception as _pe:
                            print(f"[WS_CASCADE] process error: {_pe}", flush=True)

                async def _maintenance():
                    last_poll = last_cleanup = last_sweep = 0.0
                    while not _STOP:
                        now = time.time()
                        if now - last_poll >= POLL_SEC:
                            await casc.resync_subscriptions(); last_poll = now
                        if now - last_cleanup >= CLEANUP_SEC:
                            await casc.cleanup_pass(); last_cleanup = now
                        if now - last_sweep >= SUBPROV_SWEEP_SEC:
                            await casc.subprov_sweep_pass(); last_sweep = now
                        await asyncio.sleep(0.5)

                tasks = [asyncio.ensure_future(t()) for t in (_reader, _processor, _maintenance)]
                try:
                    # if any task exits (e.g. reader on ws close), tear them all down + reconnect
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for p in pending:
                        p.cancel()
                    for d in done:                          # surface the reason (ws closed, etc.)
                        exc = d.exception()
                        if exc:
                            raise exc
                finally:
                    for t in tasks:
                        t.cancel()
        except Exception as e:
            if not _STOP:
                _log(f"WS loop error: {e} — reconnecting in {reconnect_delay}s")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
    _log("stopped")


async def _on_message(casc: Cascade, raw):
    try:
        data = json.loads(raw)
    except Exception:
        return
    # subscription confirmation
    if "result" in data and isinstance(data.get("id"), int):
        casc.mgr.on_subscribe_confirmed(data["id"], data["result"])
        return

    # TREASURY tier uses accountSubscribe → accountNotification (balance change, NO signature).
    # Resolve the treasury's latest signature off-loop, then route to the treasury handler.
    if data.get("method") == "accountNotification":
        params = data.get("params") or {}
        ent = casc.mgr.lookup(params.get("subscription"))
        if not ent or ent[1] != "treasury":
            return
        wallet = ent[0]
        sigs = await _arpc("getSignaturesForAddress", [wallet, {"limit": 1}])
        sig = sigs[0]["signature"] if (sigs and not sigs[0].get("err")) else None
        if not sig or sig in casc._processed:
            return
        casc._processed.add(sig)
        opened = await _ato_thread(casc._handle_treasury_tx, wallet, sig)
        for subprov in opened:
            if subprov not in casc.mgr.wallet_kind:
                await casc.mgr.subscribe(subprov, "subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov, related=wallet)
                await casc.catch_up_subprov(subprov)
        return

    if data.get("method") != "logsNotification":
        return
    params = data.get("params") or {}
    sub_id = params.get("subscription")
    result = (params.get("result") or {})
    value = result.get("value") or {}
    sig = value.get("signature")
    if value.get("err") or not sig:
        return
    ent = casc.mgr.lookup(sub_id)
    if not ent:
        return
    wallet, kind = ent

    if kind == "treasury":
        # _handle_treasury_tx does blocking RPC + DB → off the loop. Opens SUB_PROV sessions
        # in real-time on a provisioning outbound (WS-first trigger).
        opened = await _ato_thread(casc._handle_treasury_tx, wallet, sig)
        for subprov in opened:
            if subprov not in casc.mgr.wallet_kind:
                await casc.mgr.subscribe(subprov, "subprov")    # WS send — stays on the loop
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov, related=wallet)
                # catch-up: the wrap-close may already have fired in the slot or two before
                # this subscription went live (INSTANT provisioning).
                await casc.catch_up_subprov(subprov)
    elif kind == "subprov":
        # _handle_subprov_tx does blocking RPC + DB → run it OFF the event loop so recv keeps
        # reading the next notification while this one's tx is fetched/decoded.
        new_watches = await _ato_thread(casc._handle_subprov_tx, wallet, sig)
        for cand in new_watches:
            await casc.mgr.subscribe(cand, "candidate")     # WS send — stays on the loop
            emit_event("CANDIDATE_WEBSOCKET_OPENED", wallet=cand, related=wallet)
            _log(f"👁  watching candidate {cand[:12]}… (subprov {wallet[:10]}…)")
            # CATCH-UP: an INSTANT launch can CREATE in the ~1-2s before this subscription
            # is live. Scan the candidate's recent sigs NOW so we don't miss it.
            await casc.catch_up_candidate(cand)
    elif kind == "candidate":
        await casc.process_candidate_sig(wallet, sig)


def loop():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _log(f"starting — session_ttl={SESSION_TTL_SEC}s candidate_ttl={CANDIDATE_TTL_SEC}s "
         f"max_candidates={MAX_CANDIDATES} poll={POLL_SEC}s")
    asyncio.run(run_cascade())


def once():
    """Single resync + cleanup tick against the DB (no live WS) — for tests/debug."""
    casc = Cascade()

    async def _tick():
        casc.mgr.ws = _NullWS()
        await casc.resync_subscriptions()
        await casc.cleanup_pass()
    asyncio.run(_tick())
    _log("once: tick complete")


class _NullWS:
    async def send(self, *_a):
        return None


if __name__ == "__main__":
    if "--once" in sys.argv:
        once()
    else:
        loop()
