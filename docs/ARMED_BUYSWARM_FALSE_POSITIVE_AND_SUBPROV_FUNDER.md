# ARMED buy-swarm false positives + SUB_PROV funder attribution

_Session summary — 2026-06-14_

This doc covers two things worked on this session: a **bug** (buy-swarm wallets
leaking into the ARMED list) and two **features** (treasury-outbound webhook
storage fix, and manual SUB_PROV → treasury attribution). The buy-swarm gate
decision is still **open** (awaiting a choice on arm-amount strictness).

---

## 1. Issue — buy-swarm wallets leaking into ARMED (OPEN)

### Symptom
The pre-launch panel showed ARMED rows that are not real creators. They come in
**same-instant pairs/clusters from one treasury** with tiny amounts:

| creator | op | treasury | amount | state |
|---|---|---|---|---|
| DdCebh… | 5c31cdb5 | yUpm7r… | 1.2436 | ✓ armed |
| DZpwz9… | 5c31cdb5 | yUpm7r… | 0.0842 | ✓ armed |
| 21TCM2… | 8908322f | G2CQew… | 0.5574 | ✓ armed |
| 2GU3ya… | 8908322f | G2CQew… | 0.0987 | ✓ armed |

### Root cause
These are **buy-swarm fan-out members**, not creators. On-chain proof
(subprov `BG2JAUnC`, block 426363630, 06:38:28): one subprov wrap-close-funds
**6+ wallets in the same instant** with amounts 8.18 / 101.15 / 32.42 / 28.38 /
18.28 / **1.11203928** SOL. Only the **1.11203928** child (`7W5Tzi…`) is the real
creator — it goes on to `CREATE` "Donald80" on Pump.fun one block later. The rest
are infra/treasury-load wrap-closes that never create.

Two defects let the non-creators arm:

1. **Race condition.** The pre-arm buy-swarm gate (`is_buy_swarm`) is a
   point-in-time check. An arm can win by a few seconds before all sibling
   fundings land; a later detection then flips the wrap-close candidate to
   `BUY_SWARM` — but that verdict never disarmed the live ARMED row. Result:
   **12 rows are `ARMED` in `wt_ops_v2_armed` while simultaneously `BUY_SWARM`
   in `wt_wrap_close_candidates`** (armed_at is 8s after the BUY_SWARM verdict).

2. **Amount gate too loose.** `wrap_close_detector.detect_wrap_close` accepts any
   base in **0.05–7.0 SOL** as a creator (`wrap_close_detector.py:103`). The real
   creator template is **~1.11 (…039280 tail)**; the 0.05–0.13 and 0.55–2.4 SOL
   siblings should never qualify. Leaked amounts: 0.0513, 0.0523, 0.0577, 0.0842,
   0.0898, 0.0968, 0.0987, 0.1019, 0.5574, 1.2436, 1.8465, 2.3793 — **none is
   the canonical 1.11**.

### Fix (partially applied)
- **Reconcile cross-check (applied, `operation_armed.py`):** at the top of the
  reconcile loop, if a creator's wrap-close candidate is now `BUY_SWARM`, disarm
  it immediately (`state='EXPIRED'`, `disarm_reason='buy_swarm_reclassified'`,
  webhook removed). Closes the race retroactively — no RPC.
- **Arm-amount gate (PENDING DECISION):** tighten `detect_wrap_close`'s 0.05–7.0
  band to the real creator template. Options under consideration:
  - **Exact template only** — base ≈ 1.11 (…039280 tail). Strictest; matches the
    on-chain proof; kills the 0.05–2.4 SOL false positives.
  - **Known template library** — base ∈ {1.10, 1.11, 2.10, 0.605, 5.10}.
  - **Narrow band 1.0–1.3** — tolerant of minor variation around 1.11.
- **Stale cleanup (PENDING):** EXPIRE + disenroll the 12 leaked rows.

### The discriminator (chain truth)
In a same-instant wrap-close fan-out, the **only** creator is the
**~1.11203928 (…039280-tailed)** child — it `CREATE`s. Larger siblings (8–101
SOL) are infra capital loads; tiny siblings (0.05–0.13 SOL) are swarm/dust. The
single reliable post-launch confirm remains **CREATE vs SWAP**.

#### Can the FUNDING tx itself identify the creator? — NO (verified on-chain)
Decoded all 6 funding txs of the `BG2JAUnC` fan-out (slot 426363630). They are
**byte-for-byte structurally identical**: same 5 instructions
(`system / spl-associated-token-account / system / spl-token / spl-token`), same
2 signatures, same 8 account keys, 1 inner-instruction group, 27 logs, fee
10000. The wrap-close is deliberately uniform — **nothing in the funding tx's
structure separates the creator from an infra sibling.**

The `…2039280` ATA-rent tail is present on **every** amount (it's the same
createIdempotent rent), so the tail is **NOT** a discriminator either — both the
creator and the 8/18/28/32/101-SOL siblings carry it. That is precisely why the
current detector (keys on the …039280 tail + a 0.05–7.0 base) mis-arms them.

**The ONLY identifying signal is the base amount ≈ 1.11:**

| fund lamports | base SOL | role |
|---|---|---|
| 1 112 039 280 | **1.11** | ✅ CREATOR (→ CREATE Donald80) |
| 8 182 039 280 | 8.18 | infra sibling |
| 18 282 039 280 | 18.28 | infra sibling |
| 28 382 039 280 | 28.38 | infra sibling |
| 32 422 039 280 | 32.42 | infra sibling |
| 101 152 039 280 | 101.15 | infra sibling |

Conclusion: identification must be **base-amount-driven** (≈1.11), not
structure- or tail-driven. There is no positional/ordering signal — all share one
slot+blockTime. This settles the arm-amount gate question: gate on
**base ≈ 1.11 (the …039280-tailed canonical seed)**, not the wide 0.05–7.0 band.

---

## 2. Feature — treasury-outbound webhook storage fix (DONE, committed `c9f4759`)

Confirmed-TREASURY **outbound** webhooks were silently dropped (0 rows stored)
while inbound + SUB_PROV outbounds stored fine. Cause: the forward-walk ran
synchronously inside the webhook handler, self-deadlocking the handler's own WAL
write transaction; the exception aborted the payload **before** `conn.commit()`,
losing every insert. A mid-function `import threading` shadow compounded it.

Fixes (`main.py`, `_process_wt_infra_payload`):
- Record the `infra_events` row **first**, independent of arm/forward-walk
  consumers (storage is a plain insert; the walk is a decoupled side-effect).
- Run the forward-walk **off-thread**.
- `_wt_exec()` helper — per-statement lock-retry that skips one row instead of
  aborting the payload.
- Use the `_threading` alias consistently; drop a duplicate insert that hardcoded
  `44orWS68…` as the address.
- Fixed an unrelated `WT_SWARM_SCANNER` stray `conn.close()` (UnboundLocalError
  every idle cycle).

---

## 3. Feature — SUB_PROV → treasury funder attribution (DONE, committed `c9f4759`)

The Sub-Provisioners panel surfaces wrap-close subprovs whose funding treasury is
UNKNOWN (leads to a new treasury). You can now **add or remove** a subprov's
funder from the UI.

- **POST `/api/ops-v2/intel/subprov-funder`** (`action: set | remove`)
  - `set` — 1-RPC verify (the subprov's oldest-tx funder must match the address
    you type; `override:true` to bypass), write the subprov→treasury link
    (`treasury_known=1`), **auto-confirm** the treasury into
    `wt_confirmed_treasuries`, and webhook it. The ops connection is closed
    **before** the enroll to avoid a DB lock.
  - `remove` — clear the funder → back to an UNKNOWN lead. Does **not** un-confirm
    the treasury (it may fund other subprovs).
- **UI** (`watchtower_operational_intelligence.html`) — Funder column with
  "＋ set funder" / "✕ remove" controls and a mismatch-override dialog that shows
  the real on-chain funder.

Caveat: the 1-RPC check confirms *who* funded the subprov, not that the funder is
a genuine treasury (vs a pass-through). Since `set` auto-confirms, only set
funders sanity-checked as real treasuries.

---

## Files touched
- `src/core/main.py` — treasury-outbound storage + swarm-scanner fix
- `src/core/operation_dashboard_routes.py` — subprov-funder endpoint (new file in git)
- `templates/watchtower_operational_intelligence.html` — subprov funder UI
- `src/core/operation_armed.py` — buy-swarm reconcile cross-check (this session, uncommitted)
- `src/core/wrap_close_detector.py` — arm-amount gate (PENDING decision)
