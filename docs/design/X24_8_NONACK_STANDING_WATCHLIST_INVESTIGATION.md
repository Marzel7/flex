# X24.8 — Non-Acknowledging Standing-Watchlist Subscription Investigation

## Objective

Determine why two `dust`-tier standing-watchlist wallets —
`EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3` and
`5JW1HStyNushonoqZ6ceooMuWgqFpmhQpE49U5ZxifXBi` — never receive a WebSocket
subscription acknowledgement, despite the corrected 45-second timeout and
bounded 3-attempt retry shipped in X27.7.

The X27.7 investigation had provisionally attributed these two wallets to
`WS_PROMOTE_DISCOVERED`/`_promotable_subprovs()` (sourced from the stale
`wt_wrap_close_candidates` table). **That attribution was wrong** — this
sprint traces their actual origin and finds a different, more specific
defect.

## Conclusion: D — Stale/invalid-watchlist defect

Both wallets are **malformed 33-byte pubkeys stored as 45-character base58
strings** (one byte too long — a valid Solana ed25519 pubkey is exactly 32
bytes / typically 43-44 base58 characters). Helius accepts the
`logsSubscribe` request for an invalid pubkey without a validation error, but
never emits a matching notification for it, because no valid on-chain
account corresponds to it — hence permanent, deterministic non-acknowledgement
indistinguishable from a hung connection until checked byte-for-byte.

**This is not a provider outage, not a local correlation/duplicate-state
bug, and not related to the X27.7 timeout race.** The retry-and-exhaust
behavior is working exactly as designed; it is retrying wallets that were
never subscribable in the first place.

## Evidence

### Origin (Phase B)

Both wallets are **not** `_promotable_subprovs()`/`WS_PROMOTE_DISCOVERED`
entries — neither has a `wt_discovered_subprovs` row, a
`wt_wrap_close_candidates` row, an active/any-state
`wt_active_subprov_sessions` row, or a `wt_confirmed_treasuries` row. Both
are `wt_dust_markers` rows, subscribed via `resync_subscriptions()`'s P5 tier
(`kind="dust"`, `SUB_PRIORITY_OTHER`, `ws_cascade.py:2371`):

```
wallet                                          label                        first_seen (all rows)
EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3   EF11 · shared (EFKV)         1783159899 (2026-07-02)
5JW1HStyNushonoqZ6ceooMuWgqFpmhQpE49U5ZxifXBi   5JW1 · shared (5JWi)         1783159899 (2026-07-02)
```

Both were inserted at the same timestamp as all other dust markers (a single
bulk seed on 2026-07-02), sourced from `SEED_DUST_WALLETS` in
`src/core/dust_observatory.py` — a hardcoded list of vanity-prefix companion
wallets to a set of confirmed WATCHTOWER treasuries, used purely for
longitudinal dust-transfer observation (`dust_observatory.py`'s own docstring:
"purely observational... does NOT subscribe recipients" — the subscription
itself is `ws_cascade.py`'s job, driven by this list).

### Pubkey validity (Phase C-adjacent, the actual root cause)

```python
import base58
base58.b58decode("EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3")  # 45 chars -> 33 bytes
base58.b58decode("5JW1HStyNushonoqZ6ceooMuWgqFpmhQpE49U5ZxifXBi")  # 45 chars -> 33 bytes
```

Checked against every other active dust marker and two known-valid controls
(the pump.fun program ID, a confirmed treasury):

| label | wallet length (chars) | decoded length (bytes) | valid |
|---|---|---|---|
| 43P1 | 44 | 32 | ✅ |
| 43PK | 44 | 32 | ✅ |
| Cgwr | 44 | 32 | ✅ |
| 2q5A | 44 | 32 | ✅ |
| Dch1 | 44 | 32 | ✅ |
| Dtw1 | 44 | 32 | ✅ |
| 9hG1 | 44 | 32 | ✅ |
| **EF11** | **45** | **33** | **❌** |
| 41i1 | 44 | 32 | ✅ |
| **5JW1** | **45** | **33** | **❌** |

Exactly the two non-acknowledging wallets are the only two that decode to 33
bytes instead of 32 — a clean, total correlation, not a partial or
probabilistic one.

### Prior partial fix already exists in the codebase

`dust_observatory.py:78` — a **commented-out** `SEED_DUST_WALLETS` entry from
an earlier session:

```python
# "G212UVo8uwfcAKvU2eb4TtWpa1cJzE3DJQRP1fqf2ewPZ",  # INVALID (33-byte key) — needs correct G2CQ dust companion address
```

This wallet independently confirms as a 33-byte invalid pubkey
(`G212UVo8uwfcAKvU2eb4TtWpa1cJzE3DJQRP1fqf2ewPZ` → 45 chars → 33 bytes) and
was correctly identified and disabled (`wt_dust_markers.active=0` for this
wallet, confirmed live). **The exact same validation was never applied to
`EF11p7bnxFZMCk…` or `5JW1HStyNushon…`**, which are structurally identical
failures that were never caught. This sprint is closing the gap an earlier
session left half-finished, not discovering a new class of bug.

### Provider-level comparison (Phase C, required by the brief)

Added read-only, per-kind lifetime sent/confirmed/exhausted instrumentation
(`SubscriptionManager.sub_kind_breakdown()`, surfaced via the existing
`/api/ops-v2/intel/ws-cascade` heartbeat — the same mechanism X27.7
established, no new dashboard) to distinguish "this whole tier never acks"
from "these specific wallets never ack." Observed live, ~8 minutes after a
restart to load the instrumentation:

```
dust:     sent=10  confirmed=8   exhausted=0   (mid-cycle at snapshot time)
treasury: sent=58  confirmed=58  exhausted=0
cdc:      sent=1   confirmed=1   exhausted=0
```

8 of 10 `dust`-kind subscriptions (all valid-pubkey wallets) confirm
successfully, matching the 100% success rate of `treasury`/`cdc` tiers using
the identical request-construction code path
(`SubscriptionManager.subscribe()`, `ws_cascade.py:1782`). Payload shape is
byte-identical apart from the wallet address and request ID for every kind
in this tier — the only variable that correlates with failure is pubkey
validity.

### Retry/exhaustion behavior (confirms X27.7's fix is orthogonal and correct)

12 consecutive complete drop→3-retry→exhaust cycles were observed for each
wallet over the log window, evenly spaced, zero variance — every single
attempt genuinely sends (`next_req` increments, `ws.send()` completes) and
every single one times out at exactly 45s, with `_cold_retry_count`
incrementing and clearing correctly, and `_COLD_RETRY_EXHAUSTED_COUNT`
climbing (20 observed across both wallets by the time of this check). This
rules out a local no-op/duplicate-request race in the retry logic itself —
the retry mechanism has no bug; it is faithfully retrying something that
cannot succeed.

## Answering the required questions directly

- **Does the provider respond?** No, for these two specific requests — and
  this is expected/correct provider behavior given the pubkeys are invalid,
  not a Helius defect.
- **Does the local client process responses correctly?** Yes — confirmed via
  the per-kind breakdown showing 100% ack success for every wallet with a
  valid pubkey in the same tier, same connection cycle, same code path.
- **Why do these wallets remain in the standing-watchlist population?** A
  data-entry/copy error in `SEED_DUST_WALLETS` (`dust_observatory.py`) that
  predates this sprint and was only half-caught by an earlier session (one
  of three malformed entries was found and disabled; two were missed).
- **Is continuing to subscribe them operationally justified?** No. They can
  never produce a valid on-chain notification and only consume retry-budget
  and log volume (12+ cycles observed, indefinitely repeating).

## Standing-watchlist validity audit (Phase D)

`_promotable_subprovs()` itself was re-examined as instructed, since X27.7
had (incorrectly) implicated it. It remains a separate, narrower concern from
this sprint's findings:

- It sources from `wt_wrap_close_candidates`, independently confirmed stale
  since 2026-06-23 in the X27.7 investigation.
- Neither `EF11p7bnxFZMCk…` nor `5JW1HStyNushon…` appear in that table or in
  `wt_discovered_subprovs` at all — they were never `_promotable_subprovs()`
  candidates. **This sprint corrects that misattribution from X27.7's
  writeup.**
- Whether `wt_wrap_close_candidates` remains a valid canonical source for
  `WS_PROMOTE_DISCOVERED` is a genuinely open, separate question (per X27.7's
  own scoping) — not resolved here, since no promoted-subprov wallet was
  found to be part of the non-acking population this sprint investigated.

## What was not changed

Per the brief's explicit scope: no change to active session-tier subscription
behavior, Rapid Birth thresholds, creator detection, attribution, treasury
logic, Investigation Queue priority, or sweep throughput.
`COLD_SUB_STALE_SEC`/`COLD_SUB_RETRY_MAX` (X27.7) are untouched. **No
speculative fix was implemented** — `wt_dust_markers.active` was not flipped
to 0 for either wallet, and `SEED_DUST_WALLETS` was not edited, pending an
explicit decision on remediation scope (a one-line data fix, not a logic
change, would resolve this — analogous to the existing disabled `G212…`
entry).

## Instrumentation added (read-only, generalized — not wallet-specific)

`SubscriptionManager.sub_kind_breakdown()` (`ws_cascade.py`) — lifetime
sent/confirmed/exhausted counters per subscription `kind`, surfaced via the
existing `/api/ops-v2/intel/ws-cascade` heartbeat as `sub_kind_breakdown`.
Answers, for any current or future subscription tier, whether it is
healthy (all/most confirm) or systemically broken (none confirm) at a
glance — the exact ambiguity this sprint had to resolve by hand from raw log
line-counts and a manual pubkey-length check. No subscribe/retry logic was
changed; three plain counters (`_sent_by_kind`, `_confirmed_by_kind`,
`_exhausted_by_kind`) were added alongside the existing lifetime totals.

## Tests

`tests/test_x24_8_sub_kind_breakdown.py` (4 tests): breakdown starts empty,
sent is counted by kind, confirmed is counted by kind, and — the core
regression guard — a mix of one successfully-confirmed wallet and two
exhausted wallets of the *same* kind produces a partial breakdown
(`confirmed=1, exhausted=2` out of `sent=3`), not a total-failure signal,
proving the instrumentation can distinguish an isolated-wallet defect from a
whole-tier outage. All pass; full regression (`ws_cascade`/`x24`/`x27`
suites) re-run clean alongside X27.7's existing 7 tests.

## Recommendation (not implemented this sprint)

Correct the two malformed entries the same way the earlier session already
handled `G212UVo8uwfcAKvU2eb4TtWpa1cJzE3DJQRP1fqf2ewPZ`: obtain the correct
32-byte address for the `EF11`/`5JW1` vanity companions (or disable them in
`wt_dust_markers` / comment them out of `SEED_DUST_WALLETS` if no correct
address is available), and consider adding a pubkey-length assertion at
`SubscriptionManager.subscribe()` or at dust-marker load time so a future
malformed entry fails loudly at startup instead of silently retrying forever.
That assertion is a natural follow-up, not implemented here per the
no-speculative-fix constraint.
