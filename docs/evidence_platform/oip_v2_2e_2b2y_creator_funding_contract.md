# OIP v2.2E.2B2Y creator-funding evidence contract

## Status

This is a locally qualified design.  It authorizes no provider request,
production access, deployment, service restart, configuration change, Evidence
Mirror activation, or Cohort Mode activation.  A later execution authorization
must name Helius RPC, this B2Y contract, and the 60-request maximum.

## Frozen cohort and inputs

The only cohort is the existing 20-member B2R manifest with digest
`82bbda32d25a9951a8d8475528d7db3a92b675aae90ce2d55e13391a6b69eedc`.
Each member's existing local PumpPortal migration census record provides its
already observed migration signature.  The signature is a request target, not
a replacement cohort identity.  Any missing/duplicate/mismatched event, mint,
or signature stops before request 1 for that run.

## Exact endpoint sequence and budget

Each member may consume **at most three physical Helius RPC requests**, in this
fixed order:

1. `getTransaction(migration_signature, jsonParsed)` to verify the frozen mint
   and resolve the migration transaction's signer/creator candidate.
2. `getSignaturesForAddress(creator, limit=1000)` exactly once, with no cursor,
   pagination, cache population, or background work.  Select only the first
   returned signature with provider block time strictly earlier than the
   migration block time.
3. `getTransaction(selected_pre_migration_signature, jsonParsed)` exactly once
   to evaluate the selected candidate for a creator-funding edge.

The total maximum is **60 physical requests** (20 × 3).  There is no retry,
failover, pagination, backfill, replacement, concurrency, cache read/write,
or supplementary lookup.  Stop the entire run at the first non-success,
malformed response, ambiguous creator, missing candidate, counter mismatch,
unexpected outbound attempt, or production impact.  A member with no candidate
stops after two requests; it does not spend the third.

## Evidence semantics

`SUCCESS` requires the third response to contain a parsed, pre-migration
transaction that evidences an inbound SOL funding transfer to the creator
resolved in step 1.  A signature page or migration transaction alone is never
creator-funding evidence.  The execution adapter must therefore preserve the
three response projections, frozen mint/signature lineage, creator, provider
slot/time when supplied, and append-only attempt ledger.

This is a bounded sentinel qualification, not a claim that one history page
finds all creator funding.  Any result other than all 20 validated evidence
edges is `HOLD_PROVIDER_QUALIFICATION`.

## Local qualification

`src/acquisition/b2y_creator_funding_contract.py` uses an injected fake
transport.  Tests prove the exact three-call ordered sequence and that no
candidate stops after two calls.  The module has no HTTP, provider endpoint,
credential, environment, database, queue, or service dependency.
