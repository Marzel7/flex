# X65.6 — Phase 4: Treasury Independence

## Principle, restated precisely for this integration

`campaign` is computed strictly before, and independently
of, `treasury_resolution.py`'s output (Phase 3, step 2 never
references treasury status). Treasury status only ever appears in step
3's confidence refinement as one optional signal among five — it can
raise a launch from Baseline to Medium/High, it can never move a
launch out of `WATCHTOWER` into `OTHER_CAMPAIGN` or
`UNCLASSIFIED`.

## Treasury as a Discovery table field, not a campaign-membership gate

Per the task's explicit framing, Treasury Resolution's existing status
values (`KNOWN_TREASURY`/`UNKNOWN_TREASURY_CANDIDATE`/`NO_SUBPROV`/
`UNRESOLVED`, from `src/ops/treasury_resolution.py`) are displayed as
a **column in the Discovery launch table** — exactly the same role
Topology, Funding Origin, and Confidence already play as table columns
today — rather than as a further drill-down stage nested under
Campaign.

Presentation labels (Discovery-facing, mapped from the existing
statuses without changing them):

| `treasury_resolution.py` status | Discovery table label |
|---|---|
| `KNOWN_TREASURY` | Confirmed Treasury |
| (a session-evidenced, not-yet-confirmed subprov→treasury relationship — the "Probable" tier documented but not yet exercised by live data in X65.1) | Probable Treasury |
| `UNKNOWN_TREASURY_CANDIDATE`, or a treasury wallet first-observed within a recent window | New Treasury |
| `UNRESOLVED` / `NO_SUBPROV` | Unknown Treasury |

## Sub-grouping within Campaign, not a separate stage

When `campaign = WATCHTOWER` is selected, the
card itself displays a segmented breakdown by Treasury tier (identical
presentation to X65.5 Phase 4/7's design), computed as a simple
`GROUP BY` over the already-selected population's Treasury field — not
a new cascade stage, and not a filter that must be traversed to reach
the launch table. Clicking a Treasury-tier segment (e.g. "New
Treasury") narrows `x60CampaignRows()`'s output by that field
alone, without altering `campaign`'s own value or clearing
any other `TOPO_SELECTION` key — this is a same-stage refinement, not
a cascade advance, matching Phase 6's requirement that "switching
dimensions should never lose access to the underlying evidence."

## Why this satisfies the task's example layout exactly

The task's own Phase 7 example:
```
WATCHTOWER Provisioning
Confirmed Treasury
Probable Treasury
New Treasury
Unknown Treasury
```
is realized as the segmented breakdown described above, nested
directly inside the Campaign card for the
`WATCHTOWER` value — not as four separate top-level
Discovery dimensions, and not as a precondition for a launch's
inclusion in the bucket.
