# OIP v2.2E.2B2Y creator-funding evidence contract — B2BS amendment

## Status

This is a safe-local amended design and implementation. It authorizes no
provider request, execution, production access, deployment, restart,
configuration change, Evidence Mirror, or Cohort Mode activation. The failed
B2Z run and its sealed artifacts remain immutable.

## Frozen cohort

The cohort remains the 20-member B2R manifest with digest
`82bbda32d25a9951a8d8475528d7db3a92b675aae90ce2d55e13391a6b69eedc`.
Its migration signatures remain request targets and do not alter identity.

## Amended deterministic sequence

Each member uses at most three sequential physical requests:

1. `getTransaction(migration_signature, jsonParsed)` resolves and validates
   the unique creator and migration time.
2. One Helius Enhanced Address Transactions request for the creator with
   `limit=100`, no cursor or pagination. Scan that one provider-ordered page
   for the first transaction strictly before migration containing a positive
   `nativeTransfers` SOL transfer from another account to the creator.
3. Only after step 2 identifies that transfer-aware candidate,
   `getTransaction(candidate_signature, jsonParsed)` verifies an exact parsed
   System Program transfer with matching source, destination and lamports.

The first chronological pre-migration signature is no longer presumed to be
funding. No transfer-aware candidate stops after request 2. Any mismatch in
request 3 stops after request 3. The contract remains a bounded sentinel: a
single 100-item page is not claimed to cover all historical funding.

## Credential-free bounded projections

Every response produces only a bounded projection: request number, response
kind, signature, block time, resolved creator, transfer source/destination,
lamports and lineage-valid flag. Raw provider responses, descriptions,
credentials, endpoint URLs, account arrays and unrelated transfers are not
retained. A later execution boundary must durably append these projections
alongside physical-attempt metadata before any re-execution is authorized.

## Safety and budget

There is no retry, failover, pagination, replacement, concurrency, cache,
background work or supplementary lookup. The current design is still at most
60 physical requests for 20 members, but a future provider execution requires
a new immutable preflight, run ID, empty ledger and explicit human approval.

## Local implementation

`src/acquisition/b2y_creator_funding_contract.py` depends only on an injected
transport. Fake-transport tests prove transfer-aware selection, exact proof
matching, two-request stop, bounded projections and the fixed page limit.
