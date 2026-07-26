# X65.2 — Phase 1: Historical Placement

Read-only. Determines whether each of the 12 unresolved launches
predates the current CREATE/Birth capture pipeline, or occurred while
it was active, using live database timestamps, log evidence, git
history, and supervisor process-restart records.

## Deployment generation baseline

The live `src/core/pumpfun_curve_listener.py` is at commit `394dbd9`
("feat: X21E Operational Behaviour Intelligence + accumulated X20-X21D
work"), committed **2026-07-14 15:57:14 +0100 (2026-07-14T14:57:14Z)**.
This is the code generation currently running (confirmed live: PID
74440, running `python -u -m src.core.pumpfun_curve_listener`). No
listener-restart carries a version/build marker in its log output —
supervisor process-restart timestamps and git commit history are used
instead to establish generation boundaries.

## Per-launch timestamps

| Mint | CREATE (UTC) | Migration (UTC) | First DB appearance (`analyzed_at`) | `created_at` | `migration_signal_source` | Nearest listener restart | Restart delta |
|---|---|---|---|---|---|---|---|
| B3Fq8SqBtsxsWw... | 2026-07-15T14:48:09Z | 2026-07-15T14:48:10Z | 2026-07-15 14:48:10 | NULL | birth | 2026-07-15 14:44:33 | +216s |
| CmoCuZ9J2YT1QH... | 2026-07-17T11:26:39Z | 2026-07-17T11:26:41Z | 2026-07-17 11:26:41 | NULL | birth | 2026-07-17 11:31:10 | -272s |
| HHcXBLbnuSWdYi... | 2026-07-20T13:33:22Z | 2026-07-20T13:33:23Z | 2026-07-20 13:33:23 | NULL | birth | 2026-07-20 13:06:20 | +1622s |
| EQZfBpWpQc5BEU... | 2026-07-20T00:38:08Z | 2026-07-20T00:38:15Z | 2026-07-20 00:38:16 | NULL | birth | 2026-07-20 00:41:17 | -190s |
| DpTtRHY6PSuxxJ... | 2026-07-18T04:44:06Z | 2026-07-18T04:44:07Z | 2026-07-18 04:44:07 | NULL | birth | 2026-07-18 04:55:32 | -687s |
| CvP9vVUCpoDuMd... | 2026-07-20T14:45:28Z | 2026-07-20T14:45:29Z | 2026-07-20 14:45:29 | NULL | birth | 2026-07-20 15:18:48 | -2000s |
| 4WfoYERYFw3AQW... | 2026-07-17T18:20:09Z | 2026-07-17T18:20:10Z | 2026-07-17 18:20:10 | NULL | birth | 2026-07-17 18:18:17 | +111s |
| EDNvjVDjKVfRsq... | 2026-07-18T10:41:30Z | 2026-07-18T10:41:31Z | 2026-07-18 10:41:31 | NULL | birth | 2026-07-18 10:42:58 | -88s |
| 71TKvknpvwRcjd... | 2026-07-16T15:59:33Z | 2026-07-16T15:59:34Z | 2026-07-16 15:59:34 | NULL | birth | 2026-07-16 15:53:01 | +391s |
| c5Zye8yFd1AGrS... | 2026-07-17T22:55:25Z | 2026-07-17T22:55:27Z | 2026-07-17 22:55:28 | NULL | birth | 2026-07-17 22:34:27 | +1258s |
| 9Mn2t7yX2TmSSM... | 2026-07-21T12:14:43Z | 2026-07-21T12:14:45Z | 2026-07-21 12:14:45 | NULL | birth | 2026-07-21 12:45:02 | -1820s |
| FzNgpR11RYACas... | 2026-07-17T10:05:47Z | 2026-07-17T10:05:48Z | 2026-07-17 10:05:48 | NULL | birth | 2026-07-17 11:31:10 | -5124s |

`Restart delta` = seconds from the launch's CREATE timestamp to the
nearest `watchtower_listener` supervisor spawn event (positive = launch
occurred after the restart; negative = before). **8 of 12 launches
occurred within ±30 minutes of a listener restart; 5 of 12 within ±5
minutes.**

## Listener stability across the window (2026-07-15 → 2026-07-21)

The `watchtower_listener` process is not a long-running, stable
process during this period — it is chronically crash-looping:

- **3,224 supervisor spawn events** for `watchtower_listener` recorded
  between 2026-05-23 (earliest retained supervisor log) and now.
- **2,892 "exit status 1; not expected" events** in the same file.
- Restricting to the 07-15→07-21 window specifically: **905 spawn/exit
  line pairs**.
- Median gap between consecutive restarts: **~377 seconds (~6.3
  minutes)**. **43.7% of all restart gaps are under 5 minutes.**
- No sustained multi-hour stable run was found anywhere in this
  window — the process is in a near-continuous restart cycle
  throughout the entire period these 12 launches occurred in.

This matters directly for historical placement: every restart clears
all in-process state (`_portal_vsol`, `completed_launches`,
`seen_mints`, and any other in-memory dict/set the listener class
holds) with no persisted snapshot/restore across restarts (confirmed:
these are plain Python instance attributes initialized in `__init__`,
not backed by any table). A birth or a subsequent migration-time
lookup that depends on in-memory state populated moments earlier can
silently lose that state if a restart falls in between — independent
of, and in addition to, the code-level `create_tx_signature` clobber
mechanism identified in the prior pass of this investigation.

## Deployment-generation classification

Every one of the 12 launches' CREATE timestamps (2026-07-15T14:48
through 2026-07-21T12:14) falls **strictly after** the current code
generation's deployment commit (394dbd9, 2026-07-14T14:57:14Z) — none
predate it. There is no evidence in this cohort of a launch captured
under an older, materially different capture pipeline; git history
shows no capture-relevant change to `pumpfun_curve_listener.py` since
394dbd9 through the present.

## Classification

| Classification | Launches | Count |
|---|---|---|
| **PRE_CAPTURE_PIPELINE** | none | 0 |
| **CAPTURE_PIPELINE_ACTIVE** | All 12 | 12 |
| **UNKNOWN** | none | 0 |

All 12 launches are classified **CAPTURE_PIPELINE_ACTIVE**: they
occurred under the current, live code generation, with the listener
process nominally running (if unstably) throughout. None can be
attributed to a legacy/pre-modern pipeline. This directly answers the
task's framing question: these are not historical artifacts from
before the current system existed — they are contemporary failures
occurring *during* active operation of the current pipeline, which
narrows Phase 2 onward to distinguishing **which stage** of the active
pipeline the evidence was lost at (either the migration-time clobber
mechanism found previously, the restart-driven in-memory-state-loss
mechanism found in this phase, or both, per launch).
