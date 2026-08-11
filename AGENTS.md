## Shared Agent Handoff

For project continuation or milestone work, read `docs/agent_handoff/current.json` first. Treat it as context, not authority over observable code or runtime state; investigate and record discrepancies.

After every material task, update it with the revision, tests, production flags, verdict, blocker, and exact next action. Never record secrets, credentials, raw provider payloads, private keys, runtime database contents, or large logs. A handoff must never silently authorize production activation; existing safety gates still apply. If work ends early, record `PARTIAL` or `HOLD` and the exact unfinished step.
