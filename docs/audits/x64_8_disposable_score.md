# X64.8 Disposable Creator Score

The score is an evidence confidence score from 0 to 100:

| Evidence | Weight | Persisted source |
|---|---:|---|
| Fresh wallet at launch | 20 | selected creator-birth evidence and CREATE time |
| Single observed launch | 25 | `token_analysis` creator history |
| At most three persisted transactions | 20 | `creator_tx_ledger` |
| No persisted activity after migration | 20 | ledger latest transaction and migration time |
| Earliest ledger signature equals CREATE | 15 | ledger earliest signature and CREATE signature |

Unavailable evidence receives zero points and is explicitly reported. `evidence_coverage_pct` records how much of the 100-point evidence base was evaluable. Creator balance after migration and previous SPL activity are currently excluded because persisted coverage cannot support those claims.

The score is descriptive only. It never assigns WATCHTOWER, changes attribution, or alters candidate/walkback priority.
