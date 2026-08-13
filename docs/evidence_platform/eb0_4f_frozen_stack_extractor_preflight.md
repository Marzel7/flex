# EB0.4F Frozen Stack Closure and Extractor Preflight

## Verdict

`FROZEN_CONTRACT_STACK_COMPLETE_EXTRACTOR_BOUNDARY_DEFINED`

EB0.4A-E form a complete frozen, deterministic stack from normalized operational evidence through non-authoritative facts, nominations, immutable manifests and per-primary-role corpora. This is not a production compatibility or activation claim.

## Minimum fixture-only source

The future EB0.4G source is one explicitly injected SQLite path containing exactly:

- `operation_cohort(position, operation_id)`: immutable unique ordered platform-operation cohort;
- `normalized_operation_runtime(...)`: the exact EB0.4C scalar fields, with edge/mechanism/temporal arrays stored as canonical JSON arrays of strings;
- `nomination_candidates(group_id, position, operation_id, nomination_state)`: explicit immutable candidate membership and only `PROPOSED` or `SUPPORTED` authority.

Candidate membership is input evidence, not a clustering, scoring or identity inference. Every candidate group must contain at least two distinct cohort operations and a single authority state. Every runtime row must bind `identity_basis=PLATFORM_OPERATION_ID` and use the exact EB0.4C schema/version.

## Query boundary

- dependency-injected fixture path only; no default or production path;
- SQLite `mode=ro` and verified `PRAGMA query_only=ON`;
- exact table and column allow-lists, no views/triggers or extra schema objects;
- active SQLite progress-handler deadline no greater than 30 seconds;
- ceilings: 5,000 cohort operations, 10,000 normalized evidence rows, 5,000 candidate groups and 50,000 membership rows;
- queries constrained to the immutable selected cohort;
- complete selected, qualified, excluded, group, fact, nomination, quality, completeness and conflict accounting;
- deterministic EB0.4C-D-E outputs and digests;
- fail closed on missing/duplicate/noncontiguous cohort positions, orphan evidence/membership, ambiguous group order or authority, schema drift, deadline/ceiling breach, adapter/contract rejection or replay mismatch.

## Prohibited interpretation

The extractor must not discover wallet or real-world operator identity, infer ownership, merge operations into a canonical operator, create profiles, rates, rankings or scores, activate policy, or infer profitability/cashflow. Production/runtime compatibility, live census, provider access and activation remain separately gated.
