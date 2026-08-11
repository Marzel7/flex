# Shared Agent Handoff

`current.json` is the compact, machine-readable project handoff. Codex reads it before continuation work and updates it after material milestones, while ChatGPT may use it as current project context.

It is context, not authority: repository and runtime evidence always win. Do not store secrets, credentials, raw provider data, databases, or large logs here. Use `history/` only for meaningful gate or milestone transitions.
