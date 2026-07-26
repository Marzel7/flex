# X64.8 Performance Validation

The implementation adds batched indexed reads and no RPC:

- two creator launch-history lookups over existing `token_analysis` creator indexes;
- two grouped ledger lookups over `idx_creator_tx_ledger`;
- in-memory classification over the already bounded Discovery population.

The 1,000-creator regression fixture completes below one second. No additional index is required. Results remain inside the existing five-minute stale-while-revalidate Operational Intelligence cache, so repeated page loads do not recompute identity.

The full cold Operational Intelligence build remains dominated by pre-existing topology/behavior construction. Identity does not add a request to Discovery and does not delay the HTML render path.

## Live 24-hour validation

- Population: 625 launches
- Deployed cached endpoint: 18 ms
- Discovery HTML: 60 ms
- Fresh: 40 (6.4%); selecting it reduces the universe 93.6%
- Single-use: 78 (12.5%); reduction 87.5%
- Repeat: 493 (78.9%); reduction 21.1%
- Returning: 4 (0.6%); reduction 99.4%
- Dormant / Reactivated: 2 (0.3%); reduction 99.7%
- Unknown: 8 (1.3%); reduction 98.7%

Within 88 Quick Birth -> Migration launches, 40 are Fresh and 48 Repeat. Selecting Fresh therefore reduces that behavior cohort by 54.5% before topology analysis.
