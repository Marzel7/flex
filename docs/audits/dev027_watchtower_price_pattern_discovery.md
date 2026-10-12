# DEV027 — Watchtower price pattern discovery

## Executive summary

This DEV-only, provider-free analysis joins the retained DEV014 lifecycle cohort, DEV019 opening facts, and DEV020 pre-peak journal by mint. The data shows no practically useful opening-market-cap predictor of observed peak multiple. The clearest ordered relationship is nonlinear: retained pre-peak drawdowns of 70% or more have a sharply lower median eventual observed peak multiple than shallower drawdown groups. It is an exploratory risk state, not a validated executable signal.

Post-peak records show near-total observed collapse across every peak-multiple group. That is an important lifecycle description, but it happens after the peak and cannot support an entry signal or a realizable exit rule without ordered post-peak coverage and fills. Existing high-resolution crash evidence contains 11 valid 30-second red-candle observations, all partial-window; only one has a retained exact opening-and-observed-peak join.

## Dataset and completeness

| Retained join | Count | Qualification boundary |
| --- | ---: | --- |
| DEV014 completed lifecycle journals | 549 | Observed peak exists for each; not verified lifetime ATH |
| DEV020 qualified opening-to-peak pairs | 539 | 415 contiguous, 111 sparse, 13 insufficient pre-peak paths |
| Pre-peak drawdown paths | 526 | Max high-to-later-low inside the retained opening-to-peak window |
| Opening-linked post-peak low/latest records | 528 | Observed, not continuous post-peak paths |
| Valid 30-second crash-candle observations | 11 | All `PARTIAL_30S_COVERAGE`; zero complete windows |

All joins use canonical mint identity. Openings retain their baseline method and timestamp; peaks are observed-window maxima. Missing observations are not filled. The analysis does not copy source databases or retain raw provider payloads.

## Opening market cap

Opening MC has little relationship with eventual observed peak multiple: log-opening versus log-peak-multiple Pearson r = -0.071 (n=539). It has a modest relationship with absolute observed peak MC (r = 0.210), which is expected mechanically because larger initial values contribute to larger absolute values. Opening MC is similarly weak against peak timing (r = 0.079, n=535) and pre-peak drawdown (r = 0.074, n=526).

Data-driven opening-MC quintiles are $98.7k, $123.5k, $137.6k, and $157.6k. Median observed peak multiples by quintile are 1.893x, 1.949x, 1.738x, 1.904x, and 1.696x. Their interquartile ranges overlap substantially, so no opening-MC band consistently outperforms.

## Peak timing and pre-peak drawdowns

| Maximum observed pre-peak drawdown | n | Median observed peak multiple | Reached 2x opening | Median observed recovery time |
| --- | ---: | ---: | ---: | ---: |
| 10–30% | 51 | 1.459x | 23.5% | 240s |
| 30–50% | 190 | 1.903x | 46.8% | 420s |
| 50–70% | 196 | 2.153x | 55.1% | 540s |
| 70%+ | 88 | 0.095x | 35.2% | 600s |

The 70%+ group is the strongest non-linear finding: its median observed peak is below opening, unlike the shallower groups. The metric is chronologically constructed from a preceding high to a later low, but sparse windows, selection on terminal outcomes, and heterogeneity mean it is not a standalone live stop rule. It does justify a prospective, real-time risk-state study rather than retrospective profit claims.

Median opening-to-peak time is broadly similar across opening quintiles (19–30 minutes). Faster versus slower peak timing did not produce a stable, independent relationship after accounting for the wide dispersion of peak multiples.

## Post-peak lifecycle

| Observed peak-multiple stratum | n | Median post-peak drawdown | Median post-peak low / opening | Median peak-to-low time |
| --- | ---: | ---: | ---: | ---: |
| Below 1x | 69 | 40.54% | 0.0139x | 22.3h |
| 1–2x | 218 | 99.02% | 0.0143x | 16.9h |
| 2–5x | 183 | 99.43% | 0.0152x | 14.0h |
| 5x+ | 58 | 99.77% | 0.0167x | 21.0h |

Observed post-peak collapse is severe in every positive-peak stratum. The small difference in opening-normalized troughs does not show a reliable absolute-MC floor. These are cumulative peak-to-trough observations, never single-candle crash claims.

The retained 30-second research supports only an observed-partial crash event: `6LdjC13zbAxmruKrqsAQ4znUrjNe7gXXVeCHDBMYpump` fell from $191,574.84 to $2,166.59 (98.869%) 658 seconds after a retained $274,476.61 peak. Its pre-crash MC was 1.1769x its $162,775.94 opening. This confirms that a catastrophic red candle can begin above opening, but one partial event cannot establish a cluster or a predictor.

## Time and lifecycle groups

UTC-hour median peak multiples range from 1.333x to 2.443x, but hourly groups contain only 8–35 tokens; weekday medians range 1.719x–2.145x. Neither is robust after cohort-composition and multiple-comparison concerns. Calendar timing is descriptive only.

Simple interpretable lifecycle strata are more useful than unstable unsupervised clusters:

1. **Did not clear opening:** 69 tokens, peak below opening.
2. **Modest observed peak:** 218 tokens, 1–2x opening.
3. **Breakout:** 183 tokens, 2–5x opening.
4. **Large breakout:** 58 tokens, 5x+ opening.

These strata are dominated by observed peak multiple and all show heavy later observed decline. They describe outcome groups, not tradable classifications available at launch.

## Ranked discoveries and signal boundary

1. **70%+ observed pre-peak drawdown deterioration** — strongest effect, n=88, time-order-aware; candidate prospective risk-state signal, not validated strategy.
2. **No meaningful opening-MC peak-multiple predictor** — robust negative result across n=539; discourages simple opening-band filters.
3. **Near-universal post-peak observed collapse** — n=528; strong descriptive fact but future-dependent and non-actionable for entry.
4. **Post-peak troughs normalize near 1–2% of opening across positive-peak groups** — descriptive only; requires continuous paths before any exit interpretation.
5. **Catastrophic candles can start above opening** — one exact ordered partial example; hypothesis-generating only.

Potential trading research signals, ordered by support:

1. Prospectively measure a 70% real-time pre-peak drawdown state and evaluate subsequent path using complete ordered observations.
2. Treat observed rapid post-peak decline as a risk-management research trigger, not an exit recommendation, until ordered post-peak and fill data exist.
3. Use opening MC as a normalization variable, not a primary entry screen.

## Confidence, limitations, and minimum valuable evidence

The opening-MC null result has the strongest sample support. The 70% deterioration is medium confidence: sample n=88 and chronology support it, but sparse coverage and selection prevent predictive claims. Post-peak collapse is high-confidence descriptive evidence but low-confidence trading evidence. Hour/day patterns and crash-candle clustering are low confidence.

The highest-value missing evidence is a compact, ordered post-peak OHLC path with coverage boundaries and execution-quality data for a frozen, unselected sample. This would test exit ordering and catastrophic-candle incidence. Second is prospective complete 1-minute pre-peak coverage to evaluate the 70% state without survivorship. Third is fills, liquidity, and costs; without them no executable profitability calculation is valid. No acquisition is proposed or initiated by DEV027.

`RUNTIME_DEPLOYMENT_REQUIRED=false`: this milestone is read-only analysis and changes no executable runtime behaviour.
