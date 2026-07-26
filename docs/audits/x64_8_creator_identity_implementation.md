# X64.8 Creator Identity Implementation

Implementation lives in `src/ops/creator_identity.py` and enriches the existing flat per-mint Operational Intelligence record.

Added fields:

- `creator_identity`
- `creator_identity_label`
- `creator_launch_count`
- `prior_launch_gap_seconds`
- `disposable_creator_score`

The API adds `creator_identity_summary` and `disposable_creator_score_distribution`, accepts an optional `creator_identity` query filter, and returns identity fields with `include_records=1`. It remains read-only and cached by the existing Operational Intelligence stale-while-revalidate cache.

`REPEAT_CREATOR` was removed from additive Behaviour output and is now represented by the mutually exclusive Creator Identity dimension. No topology, funding, attribution, scoring, registry, Operation, or Operator logic consumes this classification.
