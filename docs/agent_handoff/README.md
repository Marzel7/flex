# Shared Agent Handoff

`current.json` is the compact, machine-readable project handoff. Codex reads it before continuation work and updates it after material milestones, while ChatGPT may use it as current project context.

It is context, not authority: repository and runtime evidence always win. Do not store secrets, credentials, raw provider data, databases, or large logs here. Use `history/` only for meaningful gate or milestone transitions.

## Review checkpoint protocol

When a material milestone or checkpoint requires ChatGPT review, Codex must:

1. Verify the repository and relevant runtime state, then update `current.json` with the revision, tests, production flags, verdict/status, blockers, and exact next action.
2. JSON-validate `current.json` and inspect the staged diff.
3. Stage only the handoff files intentionally changed: `AGENTS.md`, this `README.md`, `current.json`, and any intentional `history/` snapshot.
4. Create a narrow commit titled `Update agent handoff` and push the current branch to GitHub.

Never include unrelated working-tree changes in a handoff commit. Do not commit or push trivial intermediate checks that do not need ChatGPT review.

This protocol records and transports review context only. It does not authorize production activation, service restarts, configuration changes, provider calls, or any other production-changing work. All existing safety gates remain in force, and production changes require their own explicitly authorized milestone.
