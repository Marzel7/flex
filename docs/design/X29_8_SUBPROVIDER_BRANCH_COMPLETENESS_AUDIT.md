# X29.8 — Subprovider Branch Completeness Audit

Investigation only, per the brief. No code changed. Investigation subject: confirmed subprovider `ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq`. All exact counts below are from exhaustively paged `getSignaturesForAddress` calls (full transaction history, not a window) and direct SQL lookups against the live `wt_ops_v2.db`/`flex_complete_database.db`; all percentage/extrapolated figures are explicitly labeled as such and are never presented as exact.

## Method and cost discipline

`getSignaturesForAddress` was paged to exhaustion (5 calls, 1000-signature pages, last page partial) rather than a bounded window, per the brief's "sufficiently representative window" instruction interpreted as "the wallet's entire history," since ANen's real operational span is unknown in advance. A systematic 1-in-40 sample (107 transactions, evenly spaced across the full signature list, not just the burst) was then fetched via `getTransaction` for mechanism classification and destination extraction — full-population fetch (4,263 transactions) was not performed, as that would cost 4,263 credits for a single wallet's investigation; the sample size (107, ~2.5% coverage) is large enough for reliable proportional estimates while respecting RPC budget discipline. Every extrapolated figure below is explicitly marked "estimated" and is a linear scale-up from the sample rate — never presented as an exact count.

## Exact facts (no extrapolation)

- **Total ANen transactions, all-time**: **4,315** (4,263 non-error, 52 errored). History is exhausted — the last page returned fewer than the requested 1,000, confirming no earlier activity exists.
- **Time span of that history**: blockTime 1,784,048,314 → 1,784,051,124 — a **2,810-second (~47-minute)** window, not the ~144 seconds observed in X29.7.1's narrower sample. The 350+/144s figure from the prior audit undercounted the true window by only sampling near the end of ANen's activity.
- **Persisted records referencing ANen as a subprovider**: exactly **1** row in `wt_watchtower_launches` (`subprov_wallet=ANen`), exactly **1** row in `wt_provisioning_edges` (`from_wallet=ANen`, `edge_type=SUBPROV_TO_CREATOR`) — the same single HTR9U7 launch already traced in X29.7.1.
- **Of the 40 non-treasury wrap-close-outbound destination wallets found in the 107-transaction sample, exactly 0 appear anywhere as a creator** in `wt_watchtower_launches`, `creator_funders`, or `creator_sol_flows` in either database. (HTR9U7 itself did not appear in this particular sample — consistent with it being 1 of an estimated 1,600+ such transactions, well below the ~2.5% sample's expected hit rate for one specific wallet.)

## Mechanism classification (from the 107-transaction sample, existing taxonomy only)

No new mechanism types were required — every sampled transaction matched either `WSOL_WRAP_CLOSE` (transfer→createIdempotent→transfer→syncNative→closeAccount) or `PLAIN_TRANSFER` (a bare system `transfer`). Zero `SEEDED_ACCOUNT_CLOSE` or unclassifiable instruction patterns appeared in this sample.

| Mechanism | Sample count | Sample % | Estimated full-population count |
|---|---|---|---|
| WSOL_WRAP_CLOSE | 67 | 62.6% | ~2,669 |
| PLAIN_TRANSFER | 40 | 37.4% | ~1,594 |
| SEEDED_ACCOUNT_CLOSE | 0 | 0% | not observed |
| OTHER | 0 | 0% | not observed |

Direction matters and was not distinguished in X29.7.1's smaller sample:

| Mechanism × direction | Sample count | Estimated full-population count |
|---|---|---|
| WSOL_WRAP_CLOSE, ANen sending (outbound — real fan-out candidates) | 41 | ~1,633 |
| WSOL_WRAP_CLOSE, ANen receiving (inbound — not fan-out) | 26 | ~1,036 |
| PLAIN_TRANSFER, ANen sending (outbound) | 15 | ~598 |
| PLAIN_TRANSFER, ANen receiving (inbound) | 25 | ~996 |

**Critical correction to X29.7.1's framing**: not every wrap-close-outbound transaction is a creator-funding fan-out. Of the 41 sampled outbound wrap-close destinations, exactly **one** (809.29 SOL) went to `9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4` — the confirmed treasury itself — a bulk consolidation/sweep, structurally identical in mechanism but operationally a treasury remittance, not a creator-provisioning event. The other 40 went to distinct wallets each receiving 0.01–5.08 SOL, consistent with per-launch creator funding.

## Branch completeness

| | Observed (sample-scaled estimate) | Persisted | Completeness |
|---|---|---|---|
| Wrap-close creator-funding branches (outbound, excluding the treasury sweep) | ~1,633 (estimated) | 1 (exact) | **~0.06%** (estimated) |
| Plain-transfer outbound branches | ~598 (estimated) | 0 (exact, none of the sampled 15 plain-outbound destinations were checked against `wt_provisioning_edges`'s `TREASURY_TO_SUBPROV`/`SUBPROV_TO_CREATOR` types, since neither edge type models a plain-transfer mechanism at all — see below) | **0%** by construction |
| Overall (excluding inbound transfers, which are not branches by definition) | ~2,231 estimated outbound transactions of interest | 1 | **~0.04%** (estimated) |

Do not read "~0.06%" as a precise figure — it is a linear extrapolation from a 2.5% sample and the true rate could plausibly be anywhere from ~0.02% to ~0.15% depending on sample variance. What is not in doubt, because it rests on the exact figures (4,263 real transactions vs. 1 persisted record), is the **order of magnitude**: the persisted corpus captures a tiny fraction — at most a few percent, almost certainly far less than 1% — of this subprovider's real operational activity.

## Missing branch analysis — where does the evidence disappear

Tracing the 7 stages for a *typical* sampled wrap-close-outbound transaction (e.g. destination `FVT3TM1FvwdKKgCGxKYCp57gUcCJrn6FRsuZ34rm6vqz`, 0.847 SOL):

1. **Was it observed?** Cannot be determined from persisted data alone — there is no "observed but discarded" log for this transaction; it simply does not appear in any table this investigation checked. If a WS listener saw it and dropped it, that decision left no trace.
2. **Was it parsed?** Same — no evidence either way.
3. **Was funding mechanism correctly identified?** N/A — never reached a point where mechanism would be recorded.
4. **Was a creator identified?** No — `FVT3...` appears nowhere as a creator in either database.
5. **Was a launch persisted?** No.
6. **Was a provisioning edge written?** No.
7. **Where did it disappear?** The evidence trail goes cold **before step 1** in the sense that no upstream log of "we saw this signature and chose not to act on it" exists anywhere in the schema. This audit **cannot distinguish**, from persisted data alone, between: (a) the live WS listener never received this transaction at all (a subscription/throughput gap), (b) the listener received it but the parser rejected it for a reason not logged, or (c) a backfill/walkback process was never run against this subprovider's full history. All three remain live hypotheses; none can be ruled in or out without runtime instrumentation (out of scope — this is a read-only audit).

This same "cannot determine exactly where" finding applies uniformly across the sampled missing branches — there is no distinguishing evidence pointing to one specific pipeline stage over another. **The honest conclusion is: the exact root-cause stage is unproven from persisted evidence; only the outcome (near-total data loss for this subprovider) is proven.**

## Throughput assessment

The 2,810-second real span (not the 144-second span X29.7.1 sampled) implies roughly 4,263 transactions ÷ 2,810 seconds ≈ **1.5 transactions/second sustained over 47 minutes** — not a single instantaneous burst, but a long, dense, continuous operating period. This is either:
- **normal high-volume WATCHTOWER operating behaviour** for this particular subprovider (i.e., this is simply what a very active subprovider's real signature looks like), or
- **throughput beyond what a single-consumer WS listener or a bounded walkback pass can keep up with**, given the codebase's own documented per-hop RPC caps (`walkback_worker.py`'s "2 getSignaturesForAddress + up to 10 getTransaction = 12cr max" bounded walk, memory: `walkback-queue-design`).

Both are plausible and not mutually exclusive; this audit cannot distinguish between them without live-runtime instrumentation, which is out of scope for a read-only investigation. What the evidence does rule out is "incomplete backfill of old, dormant activity" as the *sole* explanation — the transaction rate itself (~1.5/sec sustained) is high enough that even a live, working listener could plausibly fail to keep pace in real time, independent of any backfill gap.

## Treasury-independent discovery test (updated from X29.7.1)

Using only ANen's persisted branches, ignoring the treasury entirely: **no, Discovery still cannot recognise this wallet as an operational anchor.** The single persisted `SUBPROV_TO_CREATOR` edge is indistinguishable from a one-off funder. This audit adds precision to X29.7.1's qualitative finding: it is not merely "some" evidence missing — it is **on the order of 1,000+ estimated creator-funding events for this one subprovider that never reached persistence**, out of which exactly one is currently visible. The gap is not a rounding error or a minor backfill lag; it is close to total.

## Generalisation — is this ANen-specific?

This audit cannot prove generalisation directly (that would require repeating this same exhaustive trace for other subproviders, out of scope here), but it can report the structural reason to expect it is **not** ANen-specific: nothing in the detection/persistence code path this and the prior audit examined (`creator_extraction_method=CLOSE_ACCOUNT_DESTINATION`, the single-edge-per-launch model in `wt_provisioning_edges`, the bounded-RPC walkback design) is wallet-specific. The same extraction logic and the same per-hop RPC/throughput bounds apply uniformly to every subprovider the platform tracks. A subprovider this active (1.5 tx/sec sustained) is an extreme case, but the mechanism producing the gap — a fixed-cost, bounded-RPC pipeline meeting a variable, sometimes very high transaction rate — would degrade proportionally for any other subprovider whose activity approaches or exceeds whatever the pipeline's real (currently unmeasured) sustained throughput ceiling is. Confirming the exact ceiling, and whether other known subproviders exceed it, would require a follow-up sprint scoped to measuring live listener/walkback throughput directly — not this read-only historical trace.

## Summary answers (per the brief's exact deliverable list)

- **Every observed operational branch**: not enumerated exhaustively (4,263 real transactions; a systematic 107-transaction sample was classified instead, per RPC-budget discipline — see Method section for why exhaustive enumeration was not performed).
- **Funding mechanism per branch**: WSOL_WRAP_CLOSE (62.6% of sample) and PLAIN_TRANSFER (37.4% of sample); no SEEDED_ACCOUNT_CLOSE or other mechanism observed in this sample.
- **Persistence status**: 1 of ~1,633 estimated outbound wrap-close creator-funding branches persisted; 0 of the sampled plain-transfer outbound branches persisted (and `wt_provisioning_edges` has no edge type that could represent a plain-transfer branch at all).
- **Completeness per mechanism**: WSOL_WRAP_CLOSE ~0.06% (estimated); PLAIN_TRANSFER 0% (by construction — no matching edge type exists in the schema).
- **Overall branch completeness**: on the order of a fraction of 1% — an order-of-magnitude finding, not a precise percentage.
- **Exact divergence point for missing branches**: **cannot be pinned to one specific stage from persisted evidence alone.** No log of "observed but rejected" exists; this is itself a finding (the absence of an audit trail for rejected/unprocessed transactions is a gap independent of the missing branches themselves).
- **Root cause classification**: most consistent with either (a) live-detection throughput being unable to keep pace with a sustained ~1.5 tx/sec subprovider, or (b) this subprovider's historical activity never having been walked/backfilled at all — both plausible, neither provable from this read-only trace, and not mutually exclusive.
- **Can Discovery currently recognise operations from subprovider activity alone?** No — confirmed with more precision than X29.7.1: the persisted evidence represents roughly 0.06% (estimated) of this subprovider's real fan-out, nowhere near sufficient for treasury-independent recognition.
