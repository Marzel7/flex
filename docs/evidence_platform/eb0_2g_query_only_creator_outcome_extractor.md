# EB0.2G query-only creator historical outcome extractor

EB0.2G qualifies a dependency-injected SQLite extractor using only frozen or
ephemeral fixtures. There is no default database path and no production
compatibility claim.

The source must contain exactly four normalized tables: immutable cohort mints,
canonical EB0.1 observations, provenance-qualified creator identity facts, and
provenance-qualified observation-window facts. The extraction policy fixes the
cohort event, outcome kind, horizon, and optional market threshold before any
query. Raw `earliest_tx_creator`, legacy `market_cap_highest`, and historical
performance aggregate tables are not accepted inputs.

SQLite is opened with `mode=ro`; `PRAGMA query_only=ON` is enabled and verified.
Exact table/column allow-lists, a 250 ms connection timeout, active progress-
handler deadlines no greater than 30 seconds, a 5,000-mint ceiling, and a
32-policy ceiling fail closed. Queries select only the immutable cohort.

Every selected mint is either qualified or assigned an explicit exclusion
reason. Qualified rows flow through EB0.2C adapters, EB0.2D manifests, and
EB0.2E corpora. Results expose selected/qualified/excluded, UNKNOWN, conflict,
and eligible-denominator counts plus input/result digests. They contain no
rates, profiles, ranks, scores, policy decisions, profitability, cashflow, or
operator attribution. Output publication and live execution remain separate
milestones.
