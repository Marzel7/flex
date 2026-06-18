# Migration Detection Coverage Gap — pump.fun → PumpSwap

**Status:** Root cause confirmed on-chain. Fix proposed, not yet implemented.
**Date:** 2026-06-18
**Severity:** HIGH — migrations are silently missed, breaking the entire downstream WATCHTOWER attribution chain.

---

## TL;DR

Our migration listener subscribes **only** to the pump.fun migration authority account
`39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg` (via Helius `logsSubscribe mentions:[...]`).
**Not every migration touches that account.** Some tokens graduate **directly to a PumpSwap
pool (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`) without touching the migration authority** —
and those migrations are **never detected**. The token never enters `token_analysis`, so it is
invisible to every downstream system (WATCHTOWER attribution, /ops/tokens, the launch backfill).

This is a **coverage gap** (we watch the wrong/incomplete signal), **not a reliability gap**
(the WS was up and catching other migrations the whole time).

---

## How it was found

Two WATCHTOWER-lineage tokens were reported missing from `/ops/tokens` despite an active
network. Tracing them revealed they were absent from `token_analysis` **entirely** — not just
unattributed, but never recorded at any lifecycle stage.

| Token | Create | Status |
|-------|--------|--------|
| `AeFSni25bQLtEJrXtRLSvFnWz4N7F4XtUdWWv3mppump` | 16:03 UTC | migrated, **never in token_analysis** |
| `Hepc74vYP9HUBJ25yFJe63XEMxx9TpWAzChzxUe4pump`  | 02:53 UTC | migrated, **never in token_analysis** |

We initially chased several wrong theories (PumpPortal WS drops, keepalive, births webhook
deleted). Each was a real observation but **not the cause of these misses.** The decisive test
was checking what the actual migration transaction touches.

---

## The evidence (on-chain, verified)

Both tokens' migration transactions were fetched and decoded:

```
AeFSni25…pump  migration tx 9JHasFFrK7TERT8f5Spv…
  touches MIGRATION_ACCOUNT (39azUYFW…)?  FALSE   ← the ONLY thing our listener subscribes to
  touches PumpSwap (pAMMBay…)?            TRUE
  pool-creation / migrate?                TRUE

Hepc74…pump    migration tx o7tkWHXef3TQFtrBBE1G…
  touches MIGRATION_ACCOUNT (39azUYFW…)?  FALSE
  touches PumpSwap (pAMMBay…)?            TRUE
  pool-creation / migrate?                TRUE
```

Both have the **identical signature**: they migrated to a PumpSwap pool **without touching the
migration authority account** we subscribe to. Meanwhile the listener was up and actively logging
`🚨 Migration detected via migration account` for *other* tokens — so the listener was healthy;
it simply never received an event for these because the event never mentioned `39azUYFW…`.

---

## Current architecture (where the gap lives)

`src/core/pumpfun_curve_listener.py`:

- `PUMPFUN_MIGRATION_ACCOUNT = "39azUYFW…"` (line 546)
- `PUMPSWAP_PROGRAM = "pAMMBay…"` (line 545)
- `listen_pumpswap_websocket()` subscribes via:
  ```
  logsSubscribe  { "mentions": [PUMPFUN_MIGRATION_ACCOUNT] }   (line ~9341)
  ```

So **detection is gated entirely on a migration mentioning `39azUYFW…`.** Migrations that route
to PumpSwap pool creation by another path are outside that filter.

### The downstream blast radius

A migration that isn't detected here cascades into every dependent system:

```
migration not detected (no 39azUYFW mention)
  └─ token never inserted into token_analysis
       └─ creator never resolved (no row to resolve against)
            └─ WATCHTOWER funding-check never runs → no attribution
                 └─ invisible on /ops/tokens
                 └─ invisible to the post-migration WATCHTOWER backfill
                      (the backfill walks token_analysis — a token absent from it
                       can never be recovered, even working backward)
```

This is why `/ops/tokens` showed no WATCHTOWER tokens for long stretches despite an active
network, and why the post-migration backfill could not recover these specific misses.

---

## Why our earlier theories were wrong (recorded so we don't re-chase them)

| Theory | Real? | Was it the cause? |
|--------|-------|-------------------|
| PumpPortal WS drops (45 reconnects, 21 stalls) | Yes | No — PumpPortal handles *pre-migration creator pre-resolution*, not migration capture |
| Births webhook (`3de9e71a`) deleted (404) | Yes | No — births fall back to other resolution; not why these migrations were missed |
| `ping_interval=None` keepalive bug | Yes | No — the WS was up and catching other migrations |
| Reconciliation sweep needed | — | Would mask it, but the real fix is correct subscription coverage |

The genuine, verified cause is the **subscription coverage gap** above.

---

## The fix

Subscribe to the **destination every migration lands at** — PumpSwap pool creation — rather than
(or in addition to) the migration authority account. Every pump.fun graduation creates a PumpSwap
pool, so a subscription keyed on the PumpSwap program's pool-creation event catches **all**
migrations regardless of which authority/route they took.

**Approach:** `logsSubscribe { "mentions": [PUMPSWAP_PROGRAM] }` (or an `accountSubscribe`/program
subscription on `pAMMBay…`), filtered to **pool-creation** events only — NOT every PumpSwap swap,
which would flood. The existing `handle_migration` / `_process_migration_with_mint` path can be
reused once the event is received.

**Open items before implementing:**
1. Confirm the exact pool-creation log signature on the PumpSwap program so the filter is precise
   (must distinguish a *new pool / migration* from ordinary swaps on existing pools).
2. Decide: replace the `39azUYFW…` subscription, or run both and dedupe (belt-and-suspenders).
   Running both + dedupe on mint/sig is safest — keeps the currently-working path while closing
   the gap.
3. Backfill the already-missed tokens (AeFSni25, Hepc74, and any others) once detection is fixed —
   they need a one-shot insert into `token_analysis` from their migration tx so the rest of the
   pipeline can pick them up.

---

## Verification plan (once implemented)

1. After adding the PumpSwap-pool-creation subscription, confirm the listener logs a migration for
   a token that does **not** touch `39azUYFW…` (re-test against AeFSni25/Hepc74's tx pattern).
2. Compare migration capture rate before/after — it should rise to match the true on-chain
   graduation rate (the prior ~3/hr was suspiciously low).
3. Confirm previously-missed tokens now flow: token_analysis insert → creator resolution →
   WATCHTOWER attribution → /ops/tokens.

---

## Key takeaway

**We were watching the migration *authority*, but migrations land at the *pool*.** The authority
account is one path, not the universal one. The robust signal is the PumpSwap pool creation —
the single point every migration must pass through.
