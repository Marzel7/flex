# Shared Agent Handoff

`current.json` is the compact, machine-readable project handoff. Codex reads it before continuation work and updates it after every material continuation turn, while ChatGPT may use it as current project context.

It is context, not authority: repository and runtime evidence always win. Do not store secrets, credentials, raw provider data, databases, or large logs here. Use `history/` only for meaningful gate or milestone transitions.

## Dedicated transport

The canonical remote transport is the `agent-handoff` branch. It is agent-state transport only and must never be treated as the source of production code. The engineering branch and revision being observed are recorded inside `current.json`.

For every material Codex continuation turn—including `PASS`, `HOLD`, `PARTIAL`, observation-in-progress, execution/time-limit stops, and blocker discovery—Codex must:

1. Verify repository and relevant runtime evidence, then update local `current.json` with the engineering revision, tests, production flags, verdict/status, blockers, and exact next action.
2. JSON-validate it.
3. From the dedicated handoff worktree, publish only `current.json` and this README when protocol versioning requires it, using `agent handoff: <milestone> <status>`.
4. Push `agent-handoff` to `origin`.
5. Fetch/read `origin/agent-handoff`, validate its JSON, and verify it matches the just-written state before finishing the response.

Routine handoff publication must not create commits on the engineering branch. Engineering-branch handoff commits are reserved for intentional protocol changes. Never include unrelated engineering/runtime changes, logs, databases, provider data, audit artifacts, secrets, or credentials in the transport branch.

If local update succeeds but publication fails, preserve the local handoff, record/report `HANDOFF_TRANSPORT_DEGRADED`, and do not imply that ChatGPT has received it. Publication does not require ChatGPT review first.

## ChatGPT read contract

ChatGPT should retrieve the latest Codex state from:

- Repository: `Marzel7/flex`
- Branch: `agent-handoff`
- File: `docs/agent_handoff/current.json`

When code inspection is needed, use the engineering branch and `project.git_head` named inside that file. `project.git_head` is the engineering source/runtime revision; it is never the handoff transport commit. `handoff_transport` describes the separate publication channel without attempting a self-referential transport hash.

This protocol records and transports review context only. It does not authorize production activation, service restarts, configuration changes, provider calls, or any other production-changing work. All existing safety gates remain in force, and production changes require their own explicitly authorized milestone.
# Agent Orchestrator V1

`scripts/agent_orchestrator.py` is a local, fail-closed executor for this handoff. Its persisted `orchestration` object has states `IDLE`, `READY_FOR_CODEX`, `CODEX_RUNNING`, `READY_FOR_REVIEW`, `HUMAN_APPROVAL_REQUIRED`, `BLOCKED`, and `PROGRAMME_COMPLETE`.

An action is executable only when `approved_action.approved` is `true`, has a matching `source_handoff_revision`, and names one of `LOCAL_READ_ONLY`, `LOCAL_IMPLEMENTATION`, `LOCAL_TEST`, or `LOCAL_COMMIT`. Production mutation, provider-work increase, production observation, and architectural decision classes remain human gates. Completion always stops at `READY_FOR_REVIEW`; V1 has no planner callback.
