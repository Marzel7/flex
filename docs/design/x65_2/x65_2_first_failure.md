# X65.2 — Phase 3: Earliest Missing Stage

Applies Phase 2's matrix left-to-right per launch and records the
first stage where evidence turns from `YES` to `NO`. Every launch gets
exactly one earliest failure point.

## Pipeline order (as walked)

```
Program CREATE observed → Birth persisted → CREATE ledger →
Funding captured → Walkback queued → SubProv identified →
Treasury linked → Topology derived → Funding Origin → Operation Attribution
```

## Per-launch earliest failure point

| Mint | Program CREATE | Birth persisted | CREATE ledger | Earliest missing stage |
|---|---|---|---|---|
| B3Fq8SqBtsxsWw... | ✓ | ✓ | ✗ | **CREATE ledger** |
| CmoCuZ9J2YT1QH... | ✓ | ✓ | ✗ | **CREATE ledger** |
| HHcXBLbnuSWdYi... | ✓ | ✓ | ✗ | **CREATE ledger** |
| EQZfBpWpQc5BEU... | ✓ | ✓ | ✗ | **CREATE ledger** |
| DpTtRHY6PSuxxJ... | ✓ | ✓ | ✗ | **CREATE ledger** |
| CvP9vVUCpoDuMd... | ✓ | ✓ | ✗ | **CREATE ledger** |
| 4WfoYERYFw3AQW... | ✓ | ✓ | ✗ | **CREATE ledger** |
| EDNvjVDjKVfRsq... | ✓ | ✓ | ✗ | **CREATE ledger** |
| 71TKvknpvwRcjd... | ✓ | ✓ | ✗ | **CREATE ledger** |
| c5Zye8yFd1AGrS... | ✓ | ✓ | ✗ | **CREATE ledger** |
| 9Mn2t7yX2TmSSM... | ✓ | ✓ | ✗ | **CREATE ledger** |
| FzNgpR11RYACas... | ✓ | ✓ | ✗ | **CREATE ledger** |

**All 12 of 12 launches share the identical earliest missing stage:
CREATE ledger.** Every launch passes both "Program CREATE observed"
and "Birth persisted" (the row exists, attributed to a genuine birth
event, with the correct creator address captured) and then fails at
the very next stage: no `wt_create_event_ledger` row, and — per the
detailed evidence in the historical-placement and matrix phases — no
durable `create_tx_signature` in `token_analysis` either, which is the
proximate technical reason the ledger stage never fires (the ledger
write path requires a validated CREATE signature to key off of, per
the earlier investigation's Phase 5 root-cause tracing).

Every stage downstream of CREATE ledger (Funding captured through
Operation Attribution) is correctly `NO` as a direct, fully-explained
consequence of this single earliest failure — none of them represents
an independent, additional failure requiring separate explanation.

## Why none of the 12 fail earlier (at Program CREATE or Birth)

Both of these first two stages are satisfied by direct, positive
evidence for all 12 (Phase 1/2): `pf_ws_creator` correctly populated
with the real creator wallet, and `analyzed_at` + `migration_signal_source
='birth'` correctly present. There is no launch in this cohort where
the CREATE event itself went entirely unobserved — the failure is
specifically at the point where the birth-time evidence should have
been durably promoted into the CREATE ledger, not at the point of
initial observation.

## Why none of the 12 fail later (at Funding/Walkback/SubProv onward)

"Walkback queued" is in fact `YES` (complete) for all 12 — but this
does not contradict CREATE ledger being the *earliest* failure, since
Walkback is a separate, RPC-capable backstop process that runs
independently of the CREATE-ledger stage and does not require it to
have succeeded. The pipeline as drawn is the *intended* linear
dependency chain; Walkback's real-world independence from CREATE
ledger is itself part of what Phase 4/5 characterize, not a
contradiction of this phase's "first-gap" measurement, which strictly
follows the stage order given in the task.
