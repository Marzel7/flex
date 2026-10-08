## Shared Agent Handoff

For project continuation or milestone work, read `docs/agent_handoff/current.json` first. Treat it as context, not authority over observable code or runtime state; investigate and record discrepancies.

After every material continuation turn, update it with the engineering revision, tests, production flags, verdict, blocker, and exact next action. This includes `PASS`, `HOLD`, `PARTIAL`, an observation still in progress, an execution/time-limit stop, or blocker discovery. JSON-validate the update, publish it to `origin/agent-handoff` from the dedicated handoff worktree, and verify that the remote file matches before finishing the response. Publication does not require prior ChatGPT review. If publication fails, preserve the local handoff, report `HANDOFF_TRANSPORT_DEGRADED`, and do not claim that ChatGPT can see the update.

Routine handoff state commits belong only on `agent-handoff`, using `agent handoff: <milestone> <status>`. Do not put routine handoff commits on the engineering branch. An engineering-branch handoff commit is allowed only when the handoff protocol itself is intentionally changed. The transport branch may contain only `docs/agent_handoff/current.json` and, when protocol versioning requires it, `docs/agent_handoff/README.md`; never stage or publish unrelated working-tree, engineering, runtime, log, database, or audit changes. The `project.git_head` field records the actual engineering source/runtime revision, never the transport commit.

Never record secrets, credentials, raw provider payloads, private keys, runtime database contents, or large logs. A handoff commit or push must never silently authorize production activation; existing safety gates still apply, and production-changing work still requires an explicitly authorized milestone. The handoff is context, not authority: verify it against observable repository and runtime state before acting. If work ends early, record `PARTIAL` or `HOLD` and the exact unfinished step.

## Development execution discipline V1

This is the single authoritative protocol for Codex and Claude development work.
It supplements, and never relaxes, the handoff and production-safety rules above.

1. **Keep the objective continuous.** Record the original user objective,
   definition of done, authoritative starting commit or handoff, completed
   evidence, and exact remaining action. A newly discovered issue must not
   silently replace the objective.
2. **Reuse evidence first.** Before investigation or execution, check relevant
   commits, handoffs, tests, and qualification records. Repeat prior work only
   for a demonstrated evidence gap.
3. **Take the shortest safe path.** Do straightforward work directly; split it
   into investigation, authorization, and implementation stages only at a real
   safety boundary.
4. **Control scope.** Classify discoveries as `BLOCKING` (prevents the stated
   objective), `DEFERRED` (relevant but not needed now), or `UNRELATED`.
   Only `BLOCKING` findings interrupt the current objective.
5. **Preserve verified states.** Use dedicated branches and isolated worktrees,
   record exact SHAs and qualification evidence, and freeze milestones without
   overstating runtime or deployment verification. Never silently modify a
   known-working component.
6. **Finish by outcome.** Tests, commits, and handoffs are evidence; report
   whether the requested functionality itself is achieved.
7. **Avoid circular recovery.** After two interventions without measurable
   progress toward the original objective, stop and reassess that objective and
   its known-good evidence. Do not start another diagnostic cycle automatically.
8. **Keep existing safety controls.** Development never implicitly authorizes
   production changes. Retain controlled DB/queue mutation, bounded provider
   use, the 500 MB per-file limit, compact evidence retention, authoritative
   paid BIRDEYE credential handling, and the rule that DEV is not production.

## Development execution discipline V2

V2 is mandatory for every new implementation DEV and supplements V1. The
authoritative register is `docs/dev_register.json`; the deterministic offline
guard is `scripts/validate_dev_discipline.py`. Neither command authorizes a
runtime action or replaces the existing Supervisor isolation safeguard.

1. **Admit before editing.** Assign a unique DEV ID, objective, clean isolated
   `codex/` branch/worktree, approved starting SHA, named source/configuration
   authority, definition of done, acceptance criteria, and dependencies. Run
   the admission guard first. Missing, ambiguous, protected-runtime, or dirty
   starting authority fails closed. Never develop inside a running service
   checkout.
2. **Maintain the register.** Add only observed current DEVs; mark unknown
   history as unknown rather than reconstructing it. Record branch/worktree,
   start/current SHA, status, next action, dependencies, qualification evidence,
   and promotion state by reference to handoffs. At most two implementation
   DEVs may be active. A second DEV may not touch the same production service or
   shared mutable state. Only one live integration transition may be active.
3. **Own isolation.** Each DEV owns its worktree and temporary test paths.
   Tests must use distinct `/private/tmp` paths, retain the 500 MB per-file
   prohibition, and fail before touching protected runtime paths, DB/queue
   authorities, or live Supervisor endpoints. Reuse the established
   Supervisor parser-only isolation guard; do not create a second mechanism.
4. **Close handoffs deliberately.** Before reporting a DEV complete, require a
   resolvable committed SHA, clean worktree, relevant regression result, remote
   verification, limitations, and an exact next action. A failed handoff push
   is `HANDOFF_TRANSPORT_DEGRADED`, not permission to discard local work.
5. **Admit integration explicitly.** Before combining or promoting DEVs,
   validate source/dependency authority, launcher-referenced tracked files,
   required capabilities and producer/consumer wiring, actual active Supervisor
   authority, parser-only isolation, and byte-exact rollback. A missing or
   disabled component is never operational without documented proven-equivalent
   evidence. Live mutation still requires separate explicit approval.
