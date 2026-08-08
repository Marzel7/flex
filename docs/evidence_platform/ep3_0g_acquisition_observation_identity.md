# EP3.0G — Acquisition Observation Identity Amendment

Status: **IMPLEMENTED**

## Canonical model

An acquisition observation records that one request was performed. Its
`evidence_digest` is the SHA-256 digest of canonical observation identity:

- acquisition and correlation IDs;
- purpose, creator and launch context;
- provider and RPC method;
- request digest;
- acquisition timestamp, retry and cache state;
- response status;
- immutable artifact digest.

The artifact digest remains the SHA-256 digest of exact provider bytes. Many
observations may reference one artifact; one observation references exactly one
artifact.

## Persistence

Schema version 4 introduces `immutable_artifacts`, keyed solely by
`artifact_digest`. `artifact_references` links envelopes to artifacts, and
normalized Evidence references the immutable artifact rather than treating an
envelope digest as content identity.

Existing artifacts and normalized facts are not rewritten. Existing databases
are migrated transactionally by registering retained artifact digests and
changing the normalized Evidence foreign-key target. Immutable triggers are
restored after migration.

New acquisition provenance retains the sanitized request and acquisition
metadata needed to replay an observation without guessing its request context.

## EP3.0F recovery

The 207 retained queue messages were amended atomically in place. Only their
observation identity changed. Exact provider bytes were not rewritten or
duplicated. The existing writer, normalizer and Primitive Engine then consumed
the queue locally.

No acquisition method was invoked during recovery and no RPC was issued.

The amended observation subset replayed 207 observations into 13,037 identical
normalized facts: zero missing and zero unexpected facts. The Primitive Engine
then regenerated the current 85,932-observation projection with 85,932
idempotent duplicates and zero inserts.

The historical shadow database contains 57 earlier aggregate Primitive
observations in addition to that current projection. A whole-table rebuild does
not recreate those historical incremental snapshots because replay starts from
the final Evidence state. EP3.0G records that distinction explicitly; it does
not alter or delete the historical observations and makes no Primitive semantic
change.

## Compatibility

- production consumers remain disconnected;
- artifacts remain content-addressed and immutable;
- normalized Evidence identity continues to include raw artifact identity;
- Primitive Contract v1 is unchanged;
- Operation Runtime is unchanged;
- detector, governance and canonical identity behavior are unchanged.
