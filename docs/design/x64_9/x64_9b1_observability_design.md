# X64.9B1 — Phase 2: Observability Design

## Chosen approach: aggregate counter + age buckets (option 1 of the preferred list)

Rejected option 2 (sampled duplicate events) and option 3 (per-event
logging gated on negligible volume) because: the whole premise of this
measurement is that we do **not** yet know the true volume (the 0/48
offline sample is not trusted as representative — see the path audit
and X64.9B's original finding), so a design that assumes volume is
low enough to log individually would be reasoning in a circle. An
aggregate-counter design is bounded by construction regardless of
actual duplicate volume, which is the right property when the volume
itself is the unknown being measured.

## Schema: `wt_subprov_sig_dedupe_stats`

One row per `(subprov_wallet, age_bucket)` pair, aggregated —
**not** one row per duplicate event. This bounds total row count to
`(distinct subprov wallets ever duplicate-observed) × 10 age buckets`,
which is small and stable regardless of how many total duplicate
events occur (a wallet hit 500 times in the same bucket still
contributes exactly one row, with `duplicate_count` incremented).

```sql
CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_stats (
    subprov_wallet      TEXT NOT NULL,
    age_bucket          TEXT NOT NULL,
    duplicate_count     INTEGER NOT NULL DEFAULT 0,
    max_duplicate_age_s INTEGER,
    first_observed_at   INTEGER,
    last_observed_at    INTEGER,
    source_ws           INTEGER NOT NULL DEFAULT 0,
    source_catchup      INTEGER NOT NULL DEFAULT 0,
    source_retry        INTEGER NOT NULL DEFAULT 0,
    source_hot_burst    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subprov_wallet, age_bucket)
);
CREATE INDEX IF NOT EXISTS ix_subprov_sig_dedupe_stats_bucket
    ON wt_subprov_sig_dedupe_stats(age_bucket);
```

Per-source counts are stored as four explicit columns (`source_ws`,
`source_catchup`, `source_retry`, `source_hot_burst`) rather than a
free-text `source` in the primary key, deliberately — the four sources
are a small, fixed, known set (see Phase 1's audit), so fixed columns
keep the row count bounded by `(wallet × bucket)` alone rather than
`(wallet × bucket × source)`, while still fully preserving
per-source breakdown ("duplicate count by source, where identifiable").

A second, single-row table holds the global rollup fields the task
explicitly requires that don't naturally fit the per-wallet-per-bucket
grain (overall max age, overall first/last observed):

```sql
CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_summary (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    total_checked         INTEGER NOT NULL DEFAULT 0,
    total_duplicates      INTEGER NOT NULL DEFAULT 0,
    max_duplicate_age_s   INTEGER,
    first_duplicate_at    INTEGER,
    last_duplicate_at     INTEGER,
    updated_at            INTEGER NOT NULL
);
```

`id = 1` is a deliberate single-row constraint (`CHECK (id = 1)`) —
this table only ever has exactly one row, upserted via `INSERT ...
ON CONFLICT(id) DO UPDATE`. `total_checked` (every signature that
reaches the dedupe check, whether skipped or not) is required by the
task's own measurement-contract objective ("enough incoming signatures
to make a zero-duplicate result meaningful") — without a denominator,
"0 duplicates" is meaningless; "0 duplicates out of 2.3M checked" is
evidence.

## Age buckets (exact set required by the task)

```python
_AGE_BUCKETS = [
    ("<5m",     300),
    ("5m-30m",  1800),
    ("30m-2h",  7200),
    ("2h-12h",  43200),
    ("12h-24h", 86400),
    ("1d-3d",   259200),
    ("3d-7d",   604800),
    ("7d-14d",  1209600),
    ("14d-30d", 2592000),
    (">30d",    None),
]

def _age_bucket(age_s: int) -> str:
    for label, upper in _AGE_BUCKETS:
        if upper is None or age_s < upper:
            return label
    return ">30d"  # unreachable given the None sentinel above, kept for clarity
```

## What is retained, mapped to the task's explicit list

| Required field | Where stored |
|---|---|
| Total incoming signatures checked | `wt_subprov_sig_dedupe_summary.total_checked` |
| Total signatures skipped (duplicates) | `wt_subprov_sig_dedupe_summary.total_duplicates` (global) + `SUM(duplicate_count)` across `wt_subprov_sig_dedupe_stats` (per wallet/bucket) |
| Original DONE timestamp | Not stored directly per-event (would require unbounded per-event storage) — instead, its *effect* (the computed age) is captured via the bucket assignment; see "what is deliberately not stored" below |
| Duplicate observation timestamp | Captured in aggregate via `first_observed_at`/`last_observed_at` per wallet/bucket and `first_duplicate_at`/`last_duplicate_at` globally — not per-individual-event |
| Duplicate age | `age_bucket` (categorical) + `max_duplicate_age_s` (both per-wallet/bucket and global) |
| Subprov wallet | `wt_subprov_sig_dedupe_stats.subprov_wallet` |
| Signature | **Deliberately not stored** — see below |
| Delivery source | `source_ws`/`source_catchup`/`source_retry`/`source_hot_burst` columns |
| Process generation/restart context | See "process-restart context" below |

### What is deliberately not stored, and why

- **Individual signatures**: storing every duplicate signature would
  require either an unbounded per-event table (explicitly disallowed
  by the task: "avoid creating another unbounded event table") or a
  bounded-but-lossy sample (option 2, rejected above for reasoning
  circularity). The aggregate design captures everything the retention
  decision actually needs (frequency, age distribution, source
  breakdown) without needing individual signatures. If deeper
  forensic investigation of a *specific* duplicate is ever needed, the
  existing `listener.log`/`ws_cascade` log lines already capture
  per-signature detail at the time it happens (not persisted
  structured data, but available for a live `tail`/`grep` during the
  measurement window if a specific case needs investigation).
- **Original DONE timestamp as an explicit column**: the age-bucket
  categorization is the derived product of `(observed_at -
  original_done_at)` — storing the bucket (plus `max_duplicate_age_s`
  for the single most extreme case) captures the distribution shape
  without needing to retain every individual timestamp pair.

### Process-restart / generation context

Rather than adding a new column, this is captured for free by the
persistence itself: because `wt_subprov_sig_dedupe_stats`/`_summary`
are durable (unlike `_subprov_sig_metrics`), any restart boundary is
visible by cross-referencing `last_observed_at`/`last_duplicate_at`
against `logs/supervisor/supervisord.log`'s restart timestamps
(already the established practice in this project's own operational
investigations, e.g. X64.7C's log-correlation approach) — a dedicated
"restart generation ID" column is not needed, since the measurement
contract (Phase 7) explicitly wants observation *across* multiple
restarts, and the durable table's `updated_at`/`last_observed_at`
fields combined with existing supervisord logs already provide that
correlation without new schema surface.

## Bounded growth, by construction

- `wt_subprov_sig_dedupe_stats`: bounded by `(distinct subprov wallets
  that have ever produced ≥1 duplicate) × 10`. Given this project's own
  data (per X64.8/X64.9, `wt_active_subprov_sessions` currently shows
  on the order of hundreds of thousands of historical subprov wallets
  total, and only a small fraction would ever be expected to produce a
  duplicate at all, let alone across all 10 buckets), this table's
  realistic upper bound is orders of magnitude smaller than the
  2.3M-row `wt_subprov_sig_retry` table this measurement exists to
  inform decisions about.
- `wt_subprov_sig_dedupe_summary`: exactly 1 row, always.

## Rejected alternative: a raw per-duplicate-event log table

Explicitly rejected per the task's own constraint ("avoid an unbounded
raw-event log") and Phase 1's own finding that the true duplicate rate
is unknown — an unbounded table's growth rate would itself be
unmeasured risk, working against the goal of *safely* measuring before
acting.
