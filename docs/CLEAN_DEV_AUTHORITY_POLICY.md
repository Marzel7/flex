# Clean DEV authority policy

A working tree is never source authority; a remote-verified Git commit is.

- Every DEV names a committed `BASE_SHA`; substantial work uses a dedicated branch and isolated worktree.
- The original dirty workspace is never a runtime or release authority.
- A qualification PASS requires every feature-owned source file and test to be tracked, followed immediately by one engineering commit, push, and remote verification.
- The next DEV starts only from that remote-verified engineering SHA. Deployments name an exact engineering SHA and running source must exact-match it.
- Critical runtime source may not exist only as untracked files. Cross-lineage composition requires explicit path/blob provenance.
- If a clean commit cannot be made, record HOLD and do not start the next DEV. `QUALIFIED_BUT_UNCOMMITTED` is STOP.
- PASS handoffs record `BASE_SHA`, `ENGINEERING_SHA`, `ENGINEERING_COMMIT_REMOTE_VERIFIED`, and, when deployed, `RUNTIME_SOURCE_SHA`. Read-only gates use `ENGINEERING_SHA=NOT_APPLICABLE_READ_ONLY`.
