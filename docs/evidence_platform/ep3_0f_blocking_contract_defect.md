# EP3.0F blocking contract defect

Status: **RESOLVED BY EP3.0G**

## Summary

The bounded WATCHTOWER shadow acquisition completed its network phase and
durably queued 207 acquisition envelopes. Ingestion then rolled back without
writing the batch because distinct acquisition observations can share identical
provider response bytes, while the Evidence database currently requires
`evidence_envelopes.evidence_digest` to be globally unique.

No second acquisition has been attempted. The 207 responses remain recoverable
from the shadow intake retry queue and content-addressed artifact store.

## Reproduction

The writer rejected the batch with:

```text
sqlite3.IntegrityError: Evidence digest belongs to a different envelope
```

The durable retry queue contains:

- 207 acquisition envelopes;
- 205 distinct response/artifact digests;
- two response digests each referenced by two distinct envelopes.

The database transaction was atomic and rolled back. Consequently, the failed
batch did not partially append envelopes or receipts.

## Contract conflict

The EP1.3B raw-artifact contract states that raw artifacts are content-addressed
by the SHA-256 digest of exact provider bytes. Identical provider bodies must
therefore share an artifact digest. This is expected for responses such as
historically unavailable transactions or repeated empty results.

The acquisition envelope has a separate deterministic identity derived from
acquisition identity, provider, retry count and response digest. Distinct
requests may therefore correctly have distinct envelope IDs while referencing
the same immutable artifact.

The current implementation conflicts with that model in two places:

- `src/evidence/mirror.py` assigns the raw artifact digest to
  `evidence_digest`;
- `src/evidence/schema.sql` declares `evidence_digest TEXT NOT NULL UNIQUE` and
  uses it as the foreign-key target for normalized raw-artifact provenance.

`src/evidence/database.py` consequently rejects a second envelope whenever its
exact provider bytes match an earlier envelope, even when request identity and
provenance differ.

## Why execution stopped

Resolving the collision requires choosing the permanent identity relationship
between acquisition observations, evidence envelopes and content-addressed raw
artifacts. Changing that relationship without approval would alter the frozen
Evidence identity contract and database schema.

EP3.0F therefore stopped before:

- changing the Evidence schema;
- rewriting queued envelopes;
- replaying the queue;
- making any additional RPC request;
- running normalization or Primitive generation;
- committing or pushing.

## Safe recovery state

The completed acquisitions are retained under the EP3.0D shadow corpus:

```text
database/evidence_platform/watchtower_shadow_ep3_0d/
```

Recovery does not require another RPC call. After the identity contract is
clarified, the durable queue can be drained, normalized and replayed locally.
RPC accounting can be reconstructed from the queued provider, retry and method
metadata.

## Decision required

Choose and formalize one of these approaches before EP3.0F resumes:

1. Treat `evidence_digest` as an acquisition-observation digest and retain the
   exact-byte digest solely as `artifact_digest`. This preserves distinct
   envelopes that reference the same content-addressed artifact.
2. Permit multiple envelopes to share an `evidence_digest` and change normalized
   fact provenance to reference the artifact store independently of envelope
   uniqueness.
3. Define an approved alternative that preserves distinct request provenance,
   exact raw bytes, deterministic replay and append-only identity.

EP3.0G approved and implemented option 1. Acquisition observations now use an
observation digest, while exact provider bytes remain addressed exclusively by
their artifact digest. The retained queue was recovered without RPC.
