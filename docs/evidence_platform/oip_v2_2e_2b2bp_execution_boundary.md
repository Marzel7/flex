# OIP v2.2E.2B2BP isolated B2Z execution boundary

This safe-local milestone implements but does not execute the reviewed B2Y
contract. `HeliusJsonRpcTransport` is inert at construction and accepts an
explicit HTTPS endpoint; it performs no environment lookup and has no retry,
failover, pagination, cache, database, queue, service, or concurrency path.

`B2ZRunner` accepts only the frozen 20-member B2R manifest and matching B2X
migration-signature projection. It performs the fixed sequence
`getTransaction(migration)`, `getSignaturesForAddress(creator, limit=1000)`,
and, only when a strictly pre-migration candidate exists,
`getTransaction(candidate)`. It stops the whole run on the first request error,
missing candidate, malformed lineage, ambiguous creator, or absent funding
edge. The hard physical ceiling is 60 and the transport counter must reconcile
one-for-one with append-only ledger rows.

Every physical attempt is written to an empty-start JSONL ledger with run ID,
manifest digest, member and physical ordinals, RPC method/target, wall and
monotonic timing, outcome, and error class. A stopped run deliberately leaves
its partial ledger intact and cannot be resumed as a fresh run.

Funding evidence is intentionally strict: the selected parsed transaction must
contain a System Program `transfer` or `transferWithSeed` instruction with a
positive integer `lamports` value whose destination is exactly the creator
resolved from the frozen migration transaction. SPL-token transfers, zero SOL,
or transfers to another destination do not qualify.

B2BP authorizes no provider request. Real B2Z execution remains a separate
human gate naming Helius RPC, the frozen digest, the 60-request maximum, the
empty ledger path/run ID, and the first-non-success stop rule.
