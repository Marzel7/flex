# OIP v2.2E.2B2X one-request projection design

The only unambiguous local one-request target available for each frozen B2R
member is its PumpPortal `subscribeMigration` transaction signature.  The
projection binds the existing B2R manifest member to the local census record by
event ID, mint, subscription name, and signature.  The adapter then performs
one injected `getTransaction(signature, jsonParsed)` call.

This design is intentionally local-qualified only: it has no endpoint, HTTP
client, credentials, provider call, database, queue, service, or configuration
dependency.  Fake transport tests prove one request object per permitted mint
and prove that unknown input cannot create a request.

Semantic limit: that one migration transaction can prove migration lineage and
the mint's presence in the response, but cannot prove a creator-funding edge.
It therefore returns `evidence_observed=false` even when the response is a
valid migration transaction.  B2N's all-20 creator-funding success criterion
cannot be reached with this single-request shape.  No B2W provider run may be
started from this design without a human decision to either amend the objective
to migration-lineage qualification or authorize a different reviewed evidence
contract and request budget.
