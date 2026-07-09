# WATCHTOWER: Detection vs Prediction

**Date:** 2026-06-23
**Status:** Confirmed — operational model

---

## The core finding

WATCHTOWER token launches **can be detected in real time** by watching known sub-provisioners
(subprovs) on WebSocket. But **predicting the creator wallet in advance is structurally
impossible** — regardless of how much operator history we hold.

These are different problems with different answers.

---

## Why prediction is impossible

A WATCHTOWER creator wallet is **single-use and fresh**. It is funded at most seconds
before the CREATE transaction. It has:

- No prior transaction history
- No on-chain fingerprint before the funding event
- No pattern that distinguishes it from any other new wallet until the wrap-close lands

The only moment the creator is knowable is when the subprov's wrap-close completes and
`closeAccount.destination` points at it — which is the same moment (or sub-second before)
the CREATE tx fires. There is no lead time to act on.

Measured across 44 verified launches:
- **81% INSTANT** — birth→launch median **1 second**. Impossible to intercept pre-launch.
- **18% STAGED** — ≥60s gap. Theoretically catchable, but the ARMED machinery that tried
  to exploit this was dominated by buy-swarm false positives.

---

## Why real-time detection IS possible

The subprov wallets are known and stable. They:

- Appear repeatedly across many launches
- Can be identified by their wrap-close fan-out behavior
- Can be subscribed to via Helius WebSocket

When a subprov fires a wrap-close, the `closeAccount.destination` field reveals the creator
wallet at the same instant it receives funding. The CREATE tx follows within ~1 second.
Detection latency = time from WS notification to processing the tx — typically <2s.

**The mechanism is deterministic:**
```
treasury → subprov (known, webhooked) → wrap-close → creator = closeAccount.destination → CREATE
```

Watching the subprov WS gives the creator address at funding time, which is effectively
the same as CREATE time for INSTANT launches.

---

## The only real lever: subscription coverage

The cascade can only detect launches from subprovs it is subscribed to. As of the
measurement pass (322 real launches over 7-30d):

| Metric | Value |
|--------|-------|
| Real-time detections (wt_watchtower_launches) | 9 / 322 (2.8%) |
| Launch funders with any cascade session | 21 / 218 (10%) |
| Webhooked treasuries | 12 |
| Discovered subprovs with treasury_known, not subscribed | 33 |

The 2.8% baseline is not a classification failure — it is a subscription gap. The 33
known-but-unsubscribed subprovs are the immediate fix.

---

## What this means operationally

| Approach | Verdict |
|----------|---------|
| Pre-launch prediction of the creator wallet | **Impossible** — fresh wallet, no signal before wrap-close |
| Pre-launch ARMED countdown | **Marginal** — only serves 18% staged launches; swamped by buy-swarm FP |
| Real-time detection via WS on known subprovs | **Works** — cascade architecture is correct |
| Expanding subscription coverage | **#1 lever** — 33 known subprovs unsubscribed today |

The endgame is **real-time attribution**: treasury → subprov → creator → launch fires
through the WS cascade the instant the CREATE lands. Not prediction — recognition at
the moment it happens.

---

## Related

- [`SUBPROV_DISTRIBUTION_AND_WS_COVERAGE.md`](SUBPROV_DISTRIBUTION_AND_WS_COVERAGE.md) — measurement pass, buy-swarm audit, subscription gap numbers
- Memory: `[[staged-vs-instant-reframe]]` — the 81/18 split and the shift from prediction to attribution
- Memory: `[[buy-swarm-vs-creator]]` — why the ARMED false-positive rate was so high
- Memory: `[[single-token-creator-filter]]` — creator is always fresh single-use (1 token)
- Memory: `[[watchtower-wrap-close-pattern]]` — the mechanism the WS cascade detects
