# DEV032 — Watchtower catastrophic crash market-cap relationship

**Status:** COMPLETE — exploratory read-only analysis.  **Environment:** DEV.
**Provider calls:** 0.  **Runtime deployment required:** false; this analysis does
not change executable behaviour, retained evidence, or runtime configuration.

## Evidence boundary

This analysis reconciles the two compatible-baseline DEV028 events from
`/private/tmp/dev028-watchtower-sharp-exit/corrected-continuation-{manifest,journal}.json`
and the two DEV031 events from the exact frozen manifest
`docs/audits/dev031_crash_level_validation_manifest_20261013.v1.json` and compact
journal `/private/tmp/dev031-crash-level/journal.json`.  Each event is a distinct
mint and request identity.  All were normalized as Birdeye v3, 30-second MCAP
events and have `PARTIAL_30S_COVERAGE`; a detected candle is an observed event,
not proof that it was the only or first possible event in every unobserved gap.

XMoney (`7xXQV...pump`) is excluded: its `FIRST_RETAINED_MONITOR_CANDLE`
baseline was classified incompatible in
`docs/audits/dev028_opening_baseline_compatibility_correction_20261012.v1.json`.
The earlier DEV014 event is also excluded because no compatible opening-ratio
evidence survives.  No values below are inferred from rounded summaries.

## Four independent observed crash events

| Mint | Opening MC / timestamp / baseline | Observed peak MC / timestamp | Crash candle start | Pre → post MC | Drop | Pre/opening | Pre/peak | Peak→crash | Coverage / provenance |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `3JKhx3e5pPbDkwUP7oP4JFXGwk23z2NtrHWVKPVYpump` | $137,777.67 / 1787021760 / DEV031 qualified opening | $188,774.00 / 1787025600 | 1787026410 | $142,629.09 → $1,507.62 | 98.9430% | 1.035212× | 0.755555× | 810 s | Partial / DEV031 compact normalized evidence |
| `B4PBamVhXRq3Lp4rGTtgwR8youh8C5rP3ShVZUmPpump` | $117,356.33 / 1787754540 / `FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC` | $129,564.03 / 1787754600 | 1787755050 | $82,448.11 → $2,055.17 | 97.5073% | 0.702545× | 0.636350× | 450 s | Partial / DEV028 compact normalized evidence |
| `ALryq6MuYTt6nXEsXFTUFXbdburCPpe7uhi941Pmpump` | $75,185.96 / 1787901600 / DEV031 qualified opening | $210,741.86 / 1787904900 | 1787905230 | $150,696.93 → $2,690.55 | 98.2146% | 2.004323× | 0.715078× | 330 s | Partial / DEV031 compact normalized evidence |
| `Awzaq2s75uJAmPJBWHc9PwS7H7LfCiAp1KRJxh3kpump` | $135,871.84 / 1788179760 / `FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC` | $215,101.95 / 1788181200 | 1788182070 | $137,167.17 → $2,735.24 | 98.0059% | 1.009533× | 0.637684× | 870 s | Partial / DEV028 compact normalized evidence |

## Absolute pre-crash MC

Pre-crash MC ranges from **$82,448.11** to **$150,696.93**.  The median is
**$139,898.13**, with linear-interpolated Q1/Q3 of **$123,487.40 / $144,646.05**.
The population SD is **$26,869.83** and coefficient of variation (CV) is
**20.95%**.  The $68,248.82 range equals **48.78%** of the median.

| Band | Events |
|---|---:|
| $100K–$125K | 0 |
| $125K–$150K | 2 |
| $150K–$175K | 1 |
| Outside (one is below $100K) | 1 |

Thus the apparent $140K–$150K clustering does **not** persist across all four:
one compatible event begins at $82.45K.  Three values lie from $125K to $175K,
which is an observation rather than a validated threshold.

## Normalized comparisons

All dispersion values use the four events and population SD/CV, not an inferential
model.

| Relationship | Median | Q1–Q3 | Range | CV | Interpretation |
|---|---:|---:|---:|---:|---|
| Pre-crash MC | $139,898.13 | $123,487.40–$144,646.05 | $82,448.11–$150,696.93 | 20.95% | Moderately dispersed; no common absolute level established. |
| Pre-crash / opening | 1.022373× | 0.932786×–1.277490× | 0.702545×–2.004323× | 41.18% | The widest candidate relationship; opening is not a stable observed crash level. |
| Pre-crash / peak | 0.676381× | 0.637351×–0.725197× | 0.636350×–0.755555× | **7.46%** | Lowest variability among the requested level relationships. |
| Peak → crash | 630 s | 420–825 s | 330–870 s | 37.39% | Not a stable timing rule. |

The observed crash magnitudes themselves are narrow (97.5073%–98.9430%, CV
0.53%), as expected from the ≥85% event definition; this is not an independent
entry/exit-level relationship.

## Signature and controls

The compatible crashes span low-, near-, and high-multiple launches (0.70×,
1.01×, 1.04× and 2.00× opening), so “crash near opening” is not supported.  The
peak-relative values form two close observations near 0.636×, then 0.715× and
0.756×.  With n=4, partial coverage, and event-selected data, this does not
justify either a fitted exit threshold or two crash-behaviour clusters.

`INSUFFICIENT_RETAINED_CHRONOLOGY`: the DEV031 compact non-crash records retain
coverage/missing-bucket summaries, not ordered candle values or first entry into
the candidate MC bands.  Consequently no retained record can establish how many
non-crash tokens visited the same absolute or normalized ranges, whether a later
crash was observed after that visit, or whether missing coverage intervened.  A
partial/missing window is not counted as crash-free.

## Most valuable missing evidence

1. **Complete chronological 30-second post-peak coverage for the same frozen
   windows, including non-crash controls.** This directly resolves both the
   first-crossing and counterfactual-control gaps, and has higher value than more
   event-selected examples alone.
2. More independent compatible-baseline crash events with the same complete
   coverage contract.
3. Liquidity and executable-exit observations at the candidate level; these are
   necessary before any practical trading claim.

**Verdict:** retained evidence makes peak-relative pre-crash MC the strongest
*exploratory* candidate (0.636×–0.756× observed), but it does not establish a
repeatable or predictive crash signal.  No further acquisition is proposed or
authorized by this analysis.
