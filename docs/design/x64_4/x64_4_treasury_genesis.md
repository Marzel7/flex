# X64.4 — Treasury Genesis Audit: `4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ`

Read-only, zero-RPC, zero-modification audit. Companion documents:
[treasury_timeline.md](treasury_timeline.md) (Phase 2 — full chronology),
[treasury_downstream.md](treasury_downstream.md) (Phase 4 — every
associated wallet and role), [treasury_age.md](treasury_age.md) (Phases
5/6 — historical evidence and age assessment).

**Headline finding, established before the phase-by-phase detail below**:
across five independent stored tables
(`wt_active_subprov_sessions.subprov_wallet`,
`wt_temp_provision_candidates.wallet`+`treasury`,
`wt_subprov_sig_cursor.subprov_wallet`,
`wt_subprov_sig_retry.subprov_wallet`, and `wt_discovered_subprovs`'s
`first_creator` linkage from its two downstream wallets), **this
system's own existing data model already records `4231KLYi…` in the
SUBPROV role, funded by two separately-confirmed treasuries — never as a
treasury itself.** This changes the shape of the audit: the question is
not "is this a new treasury," it is "is the SUBPROV genuinely new, and is
the TREASURY infrastructure funding it genuinely new" — and those two
have different answers.

## Phase 1 — Treasury birth

**Earliest appearance of `4231KLYi…` anywhere in either database**:
`wt_active_subprov_sessions` row `137026` records the underlying funding
transaction at `funding_time=1784202715` (**2026-07-16T11:51:55 UTC**),
corroborated by `wt_webhook_hits` id `6950` (same timestamp `1784205399`
observed/created_at, signature `4od7LR2K…`, `9hGcxVHF…` → `4231KLYi…`,
1.078342247 SOL). The first `watchtower_events` row
(`SUBPROV_SESSION_OPENED_WS`, id `936060`) fires 3 seconds later at
`1784205408` — the WS-cascade detection lagging the actual on-chain
funding tx, as expected.

- **First event**: the underlying WSOL_WRAP_CLOSE funding transaction,
  2026-07-16T11:51:55 UTC, `9hGcxVHF…` → `4231KLYi…`.
- **First observation** (by this system's monitoring): 2026-07-16T12:36:48
  UTC, `SUBPROV_SESSION_OPENED_WS`.
- **First appearance anywhere**: no table shows any row for `4231KLYi…`
  timestamped before 2026-07-16T11:51:55 — confirmed via full scan of
  every table in `wt_ops_v2.db` (9 tables hit, all consistent with this
  start date) and a targeted scan of 11 WATCHTOWER-relevant tables in
  `flex_complete_database.db` (0 rows in any of them).
- **First WATCHTOWER event**: same as first observation above.
- **First operational event**: same.

**Answer: `4231KLYi…` first entered the system on 2026-07-16T11:51:55
UTC (on-chain) / 2026-07-16T12:36:48 UTC (system detection).**

## Phase 2 — Event timeline

Full detail in `treasury_timeline.md`. 15 `watchtower_events` rows across
two windows: a dense ~2h15m burst on 2026-07-16 (funding, session-open,
5 downstream monitoring opens, 2 wrap-close-fanout detections, 2
self-close-ignored, 2 watch-expiries) and a single reactivation event on
2026-07-19, funded by a different upstream treasury.

## Phase 3 — First operational activity

- **First subprov session opened**: 2026-07-16T12:36:48 (the wallet's own
  session, funded by `9hGcxVHF…`).
- **First wrap-close**: 2026-07-16T13:36:58 (`WRAP_CLOSE_FANOUT_DETECTED`,
  two simultaneous targets `82Yzf1hM…`/`56m5gW58…`).
- **First creator funded**: per `wt_subprov_evidence`, `82Yzf1hM…` and
  `56m5gW58…` both received funds at 2026-07-16T13:36:58 — no earlier
  creator-funding event exists for this wallet.
- **First detected launch**: **none found**. Neither `82Yzf1hM…` nor
  `56m5gW58…` appears in `wt_watchtower_launches`, `token_analysis`, or
  `wt_detected_creates` in either database — their downstream fate (did
  either actually CREATE a token?) is not recorded anywhere this audit
  could query. Flagged as a gap, not investigated further (would require
  RPC or a broader mint-address search outside this audit's scope).
- **First candidate**: `wt_temp_provision_candidates` id `1550`,
  `detected_at=1784205399` (2026-07-16T12:36:39), essentially concurrent
  with the first session-open.
- **First treasury-like behaviour**: `4231KLYi…` itself never exhibits
  treasury-like behaviour in the stored data — it only ever appears as a
  funded/funding intermediary (subprov), never opening its own downstream
  treasury-typed session or appearing in `wt_confirmed_treasuries`.

**Does all activity begin within the same few-day window?** Yes — every
event for this wallet falls within 2026-07-16T11:51:55 through
2026-07-19T11:37:42, a span of just under 3 days.

## Phase 4 — Downstream infrastructure

Full detail in `treasury_downstream.md`. 7 associated wallets: 2 upstream
(both independently confirmed treasuries, `9hGcxVHF…` and `DchJqu…`), 2
genuine downstream wrap-close targets (`82Yzf1hM…`, `56m5gW58…`, both
already catalogued in `wt_discovered_subprovs` as `PROVISIONAL_SUBPROV`
with `4231KLYi…` as their `first_creator`), and 5 wallets with only a
single WS-monitoring touch and no further classifiable evidence.

## Phase 5 — Historical evidence

Full detail in `treasury_age.md`. No evidence `4231KLYi…` itself existed
before 2026-07-16T11:51:55. **Strong evidence the treasury infrastructure
funding it existed well before that** — both `9hGcxVHF…` (confirmed 24
days earlier) and `DchJqu…` (confirmed 35 days earlier, independently
linked in prior project work to a known recurring operator cluster) are
pre-existing, already-confirmed treasuries.

## Phase 6 — Age assessment

Full detail in `treasury_age.md`. `4231KLYi…` itself: **recently
activated** (< 3 days total observed lifespan). The treasuries funding
it: **long-lived relative to `4231KLYi…`** (24 and 35 days prior
confirmation, with no claim made about how much further back their own
history extends — out of scope for this audit).

## Phase 7 — Relationship to the confirmed launch (X64.3's Case 1)

Using stored evidence only: does anything link `4231KLYi…` to
`HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY` (Case 1's hop1 wallet, the
disposable subprov that funded creator `7nxHcmxbaM4FC2SxdABWzEWhxtsSU8WX7JXGZdaAwizS`
in the `HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump` launch)?

Checked: `HXMUxU94…` does not appear in any of `4231KLYi…`'s 15
`watchtower_events` rows (neither as `wallet_address` nor
`related_wallet`), does not appear in `wt_active_subprov_sessions` as a
counterparty to any of `4231KLYi…`'s three funding sessions, does not
appear in `wt_discovered_subprovs` linked via `first_creator=4231KLYi…`
(the only two such rows are `82Yzf1hM…` and `56m5gW58…`, neither of which
is `HXMUxU94…`), and `7nxHcmxb…` does not appear anywhere in
`4231KLYi…`'s event history either.

**No persisted lineage exists between the treasury and this launch.**

This directly corroborates X64.3's own Phase 1 finding
(`coverage_table.md`): the user-supplied intermediate hop `7WbkFQAb…`
(the claimed link between `HXMUxU94…` and `4231KLYi…`) is not present in
any table in either database, and this deeper, dedicated genesis audit on
`4231KLYi…` itself — examining all 15 of its own events plus every
associated wallet — independently confirms the same conclusion from the
opposite direction: nothing in `4231KLYi…`'s own recorded history touches
this launch either.

---

## Executive Summary

**1. When was `4231KLYi…` first observed?**
2026-07-16T11:51:55 UTC (on-chain funding transaction); 2026-07-16T12:36:48
UTC (first system-detected event).

**2. Does the database indicate it is a newly introduced treasury?**
**It does not indicate `4231KLYi…` is a treasury at all**, newly
introduced or otherwise — five independent stored tables consistently
record it in the SUBPROV role. As a subprov, it IS newly introduced: its
entire observed lifespan is under 3 days, with no evidence of activity
before 2026-07-16.

**3. Is there evidence it predates this operational window?**
No, for `4231KLYi…` as a distinct address. Yes, decisively, for the
treasury infrastructure funding it: both `9hGcxVHF…` and `DchJqu…` were
independently confirmed as treasuries 24 and 35 days before `4231KLYi…`'s
first appearance.

**4. Does any stored lineage connect it to the confirmed launch
(Case 1, `HHcXBLbn…`/`7nxHcmxb…`)?**
**No.** Checked exhaustively from `4231KLYi…`'s own side (all 15 events,
all associated wallets) — no row anywhere connects it to `HXMUxU94…`,
`7nxHcmxb…`, or `HHcXBLbn…`. This corroborates X64.3's independent finding
from the launch's own side.

**5. Should it be considered a new WATCHTOWER treasury, an existing
treasury with missing lineage, or indeterminate pending RPC?**

**None of the three, precisely as posed — the most accurate
characterization is: a newly-observed, short-lived SUBPROV operating
under two already-confirmed, pre-existing treasuries.** Recharacterizing
the three options against what was actually found:
- Not "a new WATCHTOWER treasury" — it has never been recorded as a
  treasury by this system, and treating it as one would contradict five
  tables' worth of existing, consistent role data.
- Not "an existing treasury with missing lineage" — same reason; there is
  no treasury identity here to have missing lineage.
- Not fully "indeterminate pending RPC" for the treasury question — the
  upstream treasury identities and their relative age ARE determinate
  from stored evidence alone, decisively so.
- What IS indeterminate pending RPC: whether `4231KLYi…`'s two downstream
  wrap-close targets (`82Yzf1hM…`, `56m5gW58…`) ever actually produced a
  CREATE/launch, and whether `4231KLYi…` connects to Case 1's launch
  through some path not captured in these two databases (e.g., the
  `7WbkFQAb…` hop the user separately supplied, which remains
  unverifiable from stored data in either direction).

## Success-criteria answer

This audit distinguishes cleanly between "new treasury with recent
operational history" and "older treasury whose earlier history simply was
not recovered" by establishing that **neither applies to `4231KLYi…`
itself** — the wallet under audit is not functioning as a treasury root
in any stored table. The genuinely new/recent entity is the subprov
(`4231KLYi…`); the genuinely older, already-recovered entities are its
two upstream treasuries (`9hGcxVHF…`, `DchJqu…`). No inference was made
beyond what the stored tables directly and consistently show.
