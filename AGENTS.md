## Shared Agent Handoff

For project continuation or milestone work, read `docs/agent_handoff/current.json` first. Treat it as context, not authority over observable code or runtime state; investigate and record discrepancies.

After every material task, update it with the revision, tests, production flags, verdict, blocker, and exact next action. If the result is a material milestone or checkpoint that requires ChatGPT review, commit only the handoff files in a narrow `Update agent handoff` commit and push the current branch to GitHub. Handoff files are `AGENTS.md`, `docs/agent_handoff/README.md`, `docs/agent_handoff/current.json`, and any intentionally updated file under `docs/agent_handoff/history/`. Inspect the staged diff before committing; never stage or include unrelated working-tree changes. Do not commit or push for trivial intermediate checks.

Never record secrets, credentials, raw provider payloads, private keys, runtime database contents, or large logs. A handoff commit or push must never silently authorize production activation; existing safety gates still apply, and production-changing work still requires an explicitly authorized milestone. If work ends early, record `PARTIAL` or `HOLD` and the exact unfinished step.
