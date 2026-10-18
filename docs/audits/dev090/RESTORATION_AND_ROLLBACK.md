# DEV090 — qualification and controlled-release prerequisites

This is isolated source qualification, not authorization to migrate, deploy, restart, activate or clean anything. No operational database was copied or written. Port8100/8145 and Production remain untouched.

## Source authority

The protected parent is DEV043_BOUNDED_RECOVERY_BASELINE `0eb749ecd6acd0ab70ec6d09b9170c583d82374b`. Operational files are attributable current preimages, not golden-worker reconstruction. `source-preimages.json` distinguishes preserved dependencies from installation targets. Creator guard/analyzer/creator worker use unchanged DEV074 layers plus the explicit atomic staging call. DEV051 program identities and signature correlation remain. DEV082 `_get_tx` is reused verbatim.

The configured supervisor directory is `flex-walkback-dev002`, worker sha256 `a45ccc0d…`; the newer installed `flex-walkback` pinned composition is `145a3368…`. Loaded operational bytes were not attested and no service was signalled. The qualified source composition preserves the latter's newer funding/promotion behaviour. The worker diff is only `_get_tx` and the existing-loop maintenance hook. Alternate preimage-specific patches preserve each target's remaining bytes; never overwrite a different target with the whole candidate module. Resolve the intended target and reverify exact preimage, dependencies and relevant acceptance before any future installation. No targeting assumption is deployment approval.

## Transaction and proof contract

`stage_rpc_completion()` runs after DEV074 strict creator/conflict checks, before the same Core connection commits authoritative token fields and queue completion. `stage_birth_completion()` runs in the actual accepted PumpPortal birth Core writer transaction. Trusted newToken provenance travels through the existing birth retry queue and bounded fallback metadata. Old/unlabelled evidence is not relabelled as qualified. The existing before-migration path remains intact and creates no unnecessary proof intent for births without a migration.

Proofs require valid decoded32-byte mint/creator,64-byte CREATE signature, accepted source/qualification, Pump program identity, no Core creator/signature conflict, and an explicit pre-launch `before_signature` boundary. Missing slot/time remain NULL. Upstream strict decoder/program/response correlation controls are preserved and tested; a marker or digest alone is not transaction proof.

Consumer callbacks use the existing DatabaseWriteService shared lane before SQLite transaction acquisition. There is no private writer or new operational worker/service. Its existing per-database infrastructure may initialize lazily. Core reads close before OPS writes; OPS callback returns after commit; exact proof ACK follows in a separate Core transaction. No cross-database atomicity is assumed. Duplicate delivery never resets pending/running/completed matching jobs. Missing/conflicting/historical OPS rows remain unacknowledged. New recovery progress is indexed independently of the historical oldest waiting population.

## Explicit schema and activation — future authorization required

`walkback_create_handoff.initialize()` must run in an explicitly owned Core shared-writer transaction. It creates only control/delivery tables and due/ACK indexes, and adds nullable `birth_persist_queue.wb_birth_source` with no historical backfill or validation scan. Source labels are application-enforced enums with a lifetime cap. No hot path calls initialize, starts DDL or enables the feature.

Initialization creates `enabled=0`. A future controlled release must verify disk/lock headroom and exact schemas; stage compatible producer/consumer/shared helper files at every participating runtime; qualify rollback; then separately authorize a fixed epoch/activation time and `enabled=1` under the Core lane. Do not choose an automatic restart-relative activation cutoff. No historical sweep or retroactive token replay is part of activation.

The tested application never invoked this operational migration. Fixture initialization is not approval to do so.

## Storage limits and residual risk

- Typed proof fields only; canonical digest serialization<=1024bytes; no retained JSON transaction or raw response.
-10000 outstanding including BLOCKED,70000 ACK,100000 lifetime admissions,30-day enrollment window. Counter reservations share the authority transaction. At capacity the transaction fails closed; existing obligations remain.
-16 due intents per existing-worker tick, one OPS update/ACK at a time; five failed automatic deliveries then durable BLOCKED/last_error_code, no silent deletion or provider acquisition. BLOCKED work needs explicit intervention and does not promise progress through permanent outage.
- ACK cleanup is at most16 per tick after7days and only with matching completed OPS creator/signature evidence. No pending/BLOCKED deletion or VACUUM. It is not safe to force-delete/recreate OPS execution history as part of this protocol; no such operation is authorized.
- Stop admission at<2GB free, WAL>=48MiB, rollback journal>=16MiB, or observed main growth>64MiB/database from activation. These conservatively count shared growth; new-record/counter caps bound the feature's logical allocation. They are admission observations, NOT hard VFS quotas. Concurrent writers/pinned readers or an unexpectedly large legacy row can grow WAL between checks. No hard64MiB or500MB sidecar ceiling is claimed. Future operational rollout needs headroom/reader/lock checks and immediate stop on these conditions.
- Existing birth fallback new-provenance records<=4096bytes, source error metadata<=500chars; new-provenance append refuses at2MB under a nonblocking file lock. Legacy paths retain their prior behaviour; no new raw retention. Unknown legacy provenance is not invented. Additional retry-source admissions cap at100000. Existing spool health is a rollout prerequisite.

## Reproduce qualification only

Use the pinned Python/runtime and existing DEV080-corrected DEV048 tooling. Source guards and browser assertions remain unchanged. The permanent flex-dev entry created the worktree; its older frozen tooling differs from the already-qualified DEV080 tool used for acceptance. No global tooling replacement occurred.

1. Checkout the recorded qualified source in an isolated durable worktree; ensure>=3GB free for fixture/source budget plus recovery reserve.
2. Run the frozen preflight using `tools/flex_dev.py` from DEV080 source. Run `tests/dev090_process_crash_driver.py <source> <owned-preflight-state>` through its existing `run_log` executor: seven actual fixture-process exits, guarded in every child. The outer orchestrator alone launches them.
3. Run `flex_dev_guarded.py` on `tests/test_dev090_handoff.py`, `test_dev074_creator_authority.py`, both DEV051 CREATE/signature files, `test_dev079_walkback_v1.py`, `test_dev082_current_walkback.py`, and the mandatory unchanged DEV043 resource tests. Fixtures/logs remain below existing8MiB limits. Fresh receipt paths and exact source hashes are mandatory.
4. Run unchanged frontend30-second acceptance and all mandatory API/browser/tooling gates. The preserved source-pin tests explicitly reverse only the new dispatcher hook plus the previously qualified `_get_tx` before comparing the exact newer installed preimage; original historical manifests/tests are retained in the audit directory. No funding/budget assertion is removed.
5. Full release blockers are separate from focused Walkback acceptance. Do not create an immutable candidate if any mandatory gate fails.

## Rollback

No rollback was executed. Future controlled rollback must first coordinate new intake/qualification: disable new enrollment under the same durable control and preserve all pending/BLOCKED proofs/counters. Disabled enrollment does not cover newly resolved tokens, so do not silently keep admitting a stream while claiming the new contract. Drain with compatible code within approved budgets or explicitly pause for recovery. Restore exact per-target source preimages only after owner/queue compatibility is established. Do not erase proofs, drop tables, reset lifetime counters, restore a canonical DB clone, falsely ACK an existing waiting row, or remove another service's dependency files. Nullable metadata/new tables are backward-compatible inert storage, not permission to delete evidence.
