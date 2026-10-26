# Helius denial containment — qualification only

No production activation, provider traffic, schema change, historical replay or
budget increase is authorized by this candidate. Runtime default is disabled.
The separately qualified PumpSwap reconnect correction is included; the installed
listener preimage remains SHA256
`747b2d497e707977de6f9e50ba9c81f9d864ee07c837abf57117be5d7bb44f9a`.

## Authority and durable control

`FLEX_HELIUS_DENIAL_GATE_ENABLED=1` opts in coherently for the listener, creator
resolver and Walkback. All require the same explicit
`HELIUS_DENIAL_GATE_PATH`, exactly `helius-denial-v1.json` beside the existing
stable `MIGRATION_JOURNAL_PATH`. The matched active Core/OPS generation is
validated by the existing paired-binding guard; there is no V1 fallback.
The identical small module is packaged into the three existing independent
runtime roots. This adds local control metadata, not a service, database, archive
or event buffer.

Initialization is explicit and never automatic. For the current independently
reported exhausted-credit incident, initialize **PAYMENT_DENIED**, not OPEN.
OPEN initialization is only for an independently verified healthy starting
provider authority. Missing, malformed, mismatched-generation or publication-
ambiguous state cannot supply fresh dispatch permission. A missing flag cannot
hide an existing stable control file. Older source that ignores the gate is not
an acceptable activated fallback.

The control JSON is at most 4,096 bytes. A stable nonblocking flock serializes
updates. A synchronized PENDING marker precedes temporary-file publication;
file fsync, atomic replace, directory fsync and a synchronized READY/checksum
marker complete publication. Interrupted replacement/fsync requires independent
recovery authority; it never silently initializes another OPEN state. Capacity
is one control file, one <=128-byte marker/lock, and at most one <=4,096-byte
staging file. Admission never waits for a file lock or provider while holding a
canonical DB transaction created by this gate.

## Denial semantics and recovery

* HTTP 402: payment denial; no automatic probes. This is not a claim that every
  payment denial proves a particular credit balance.
* HTTP 401/403: authentication/access denial; no automatic probes.
* HTTP 429: ordinary rate-limit pause with a bounded retry-after policy. It is
  never labelled confirmed credit exhaustion merely from the status.
* Three failed transport/5xx outcomes: outage pause, at least 300 seconds, then
  one serialized recovery probe. Missing probe acknowledgement retains the
  durable cooldown/lease and reservation count.
* A confirmed payment/auth denial can only move to a probe through explicit
  restoration authority bound to the exact current state digest. No loop invokes
  that operation. Counters are not reset.
* Successful probe recovery is paced at one reservation per two seconds for five
  minutes. Active HTTP paths may wait at most six seconds outside file locks for
  pacing/short lock contention; otherwise they defer. Normal healthy acquisition
  retains its existing per-component scheduling and request budgets.

A reservation is not proof of external HTTP execution. Requests already
reserved/in flight before a denial are not retroactively cancellable or free.
There is no exactly-once external execution claim. Known JSON-RPC credit-denial
codes inside HTTP 200/429 have not been independently established from retained
provider evidence and are not invented. Therefore current-incident activation
must start from operator-confirmed denial; do not probe the exhausted account to
learn its balance.

## Integrated boundaries

* Listener critical/background HTTP dispatcher and direct vault adapter.
* Direct pool and post-migration-pool transports.
* Existing post-migration/CREATE analyzer HTTP paths in listener and resolver.
* Walkback's existing synchronous RPC adapter.
* PumpSwap connection setup and actual subscription confirmation.

All use explicit adapters; no global monkeypatch of networking libraries.
PumpPortal's connection, birth persistence and receipt identities are unchanged.
Migration notification admission commits before the gate check. While denied,
blocked notifications retain supplied subjects and journal identities without
beginning new attempts. Resolver/Walkback defer before claims. Mid-flight
resolver denial is typed acquisition deferral, not missing CREATE evidence.
Walkback retains a started claim's existing attempt count but defers it without
negative attribution. No retry ceilings are increased or old exhausted work
replayed. Genuine paired-authority errors still propagate fail-closed.

Existing journals retain finite capacities: this correction does not promise
indefinite buffering of an arbitrary outage. Full journal/disk refusal remains
fail-closed with no acknowledgement. Existing qualified terminal capacity owners
remain required; no pending/exhausted/protected evidence is deleted.

## Accounting and deployment boundary

The gate stores provider-specific method reservation counts and bounded HTTP/
transport outcome counters. These are not Helius billing credits. Blocked work
is not counted as an external request, failed external execution remains
ambiguous, and legacy recorder summary rows must not be added to gate counts as
if they were distinct HTTP requests.

Qualification uses only disposable fixtures and simulated HTTP/WebSockets.
Before deployment, separately freeze exact multi-role source hashes, explicit
state initialization authority, flags/path/generation, gate-aware compatible
fallback and recovery configuration. In particular the existing recovery
renderer's listener hash pins cannot simply be reused for this changed listener.
A fresh matched-pair generation change must preserve denial/counters and obtain
explicit authority; a generation mismatch blocks acquisition rather than
resetting state. No source/configuration is installed here.

## Retained qualification result

Final focused run: **62 passed, zero failed** (40 denial/authority/admission
controls and 22 reconnect controls). The directly affected full regression run
also passed the existing **530 migration completion/retry controls**; those are
reused after the final control-publication hardening because their default-off
journal/canonical code paths did not change. Total: 592 qualified controls.
Largest final fixture: 655,360 bytes; final fixture allocation: 1,650,355 bytes.
The observed final disk headroom was 3,017,310,208 bytes, above the established
2,067,108,864-byte floor plus reserve. These are measured DEV samples, not a
production storage-growth guarantee.

Verdict: **QUALIFIED_NOT_DEPLOYED**. No production state file is initialized.
Prospective gate reservation/outcome counters do not backfill historical usage
or reset the existing provider budgets. No billing rate or previously unknown
credit-denial response code is manufactured from these tests.
