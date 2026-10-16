# Flex golden baseline protection and future DEV rules

Golden application source is DEV043_BOUNDED_RECOVERY_BASELINE, commit0eb749ecd6acd0ab70ec6d09b9170c583d82374b. This is a bounded recovery foundation with documented exclusions, not complete historical functionality or a Production-qualified deployment.

- Never edit, overwrite, reset, retag, force-push or delete a sealed release. Source changes belong in a new branch and isolated checkout. Do not clear immutable flags as part of ordinary DEV. Any protection removal needs explicit owner authorization and a separately verified duplicate.
- Never delete or rename a release referenced by any service configuration, running process, startup command, rollback manifest or active candidate. Establish references first; unknown ownership blocks cleanup. No cleanup is authorized by this document.
- Persistent runtime deployment sources must be durable versioned candidate release directories, never /private/tmp, disposable Codex worktrees, or the mutable shared checkout. Temporary checkouts are allowed only for isolated tests stopped afterward. Existing services are unchanged by this forward-looking policy.
- Never promote uncommitted/untracked dependencies. Verify exact source/dependency manifests and clean checkout; include templates, assets, configuration and evidence references. Do not treat a Git SHA as loaded-code proof.
- Never replace a working release without scoped tests, real retained-data browser acceptance, source/configuration identity, isolation verification and a concrete preserved rollback. Production/persistent DEV promotion needs separate explicit authorization.
- Never clean a worktree containing unique commits, dirty/untracked source or unpreserved evidence. Verify durable preservation and owner approval first; do not infer disposability from a temporary path. No Git cleanup, prune, reset or worktree deletion is authorized here.
- Golden references are append-only. Do not move the DEV043 tag to newer work. A future accepted golden release receives a new unique name; keep old recovery and rollback references.

## Mandatory future workflow

Golden Baseline → Isolated DEV → Tests → Integration Acceptance → Candidate Release → Controlled Promotion → New Golden Baseline

Every DEV records: parent baseline SHA; changed files; direct dependency closure and exact versions/hashes; configuration/database/queue/network bindings; focused tests and real browser acceptance; known limitations; rollback source/configuration/state authority. Record any deviation from the golden parent and its reason. Qualification is not deployment authorization.

Before promotion, seal a uniquely named durable candidate outside temporary worktrees; retain prior source/configuration and state bindings without copying canonical databases. Perform only the separately authorized target restart and bounded acceptance. On failure restore the preserved previous target configuration/source; never rewind canonical data or queues. Promote the new golden identity only after acceptance; do not silently relabel existing references.

Storage: artifacts and Git transfer files fail closed below256000000bytes each; frozen runtime files remain capped8MiB. No canonical DB clones, external large research copies, provider payload acquisition or new growth architecture. No file may reach500000000bytes. Remote annotated tag preservation is verified; server-side GitHub deletion/update rules have not been asserted. Local filesystem immutable flags and deny-all receive hook enforce ordinary local protection; an owner/admin could explicitly remove protection. Independent local snapshot and archive remain restorable even if a remote ref changes.
