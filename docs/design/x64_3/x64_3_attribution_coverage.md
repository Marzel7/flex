# X64.3 — Attribution Coverage Audit: Master Report

Read-only, zero-RPC, zero-modification audit of why
[X64.2](../x64_2/x64_2_treasury_emergence.md) did not surface two launches
the user identifies as already-confirmed WATCHTOWER. Companion documents:
[coverage_table.md](coverage_table.md) (Phase 1 — full table search),
[lookup_path_analysis.md](lookup_path_analysis.md) (Phase 2 — why X64.2
missed them), [false_negative_matrix.md](false_negative_matrix.md)
(Phases 3-5 — coverage matrix, full recheck, false-negative count).

## The two cases as supplied

- **Case 1**: creator `7nxHcmxbaM4FC2SxdABWzEWhxtsSU8WX7JXGZdaAwizS`, mint
  `HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump`, user-supplied chain
  `Creator ← 7WbkFQAbQt8toHLHFxjdYZp6XSHr4hTLjPZSCuDYkiDj (PROV) ←
  HXMUxU94Zs2hGHW6r4odBiCTMxkzjV7YGJHAMYdTPFRY (SUB PROV) ←
  4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ (TREASURY)`.
- **Case 2**: creator `71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS`, mint
  `CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`, claimed treasury
  `5nTJWTSozPMWR7im9aBCeDE7y22K7ePW3TDToTpP9bGo`.

## Phase 1 — Where the confirmed knowledge lives (summary)

Full detail in `coverage_table.md`. Headline finding, verified directly:

- **Case 1's treasury (`4231KLYi…`) has NO row in `wt_confirmed_treasuries`
  at all**, and the intermediate wallet the user supplied (`7WbkFQAb…`)
  does not appear in any table, any column, in either database. `4231KLYi…`
  IS a real, active actor in `watchtower_events` (15 rows,
  `SUBPROV_SESSION_OPENED_WS`/`WRAP_CLOSE_FANOUT_DETECTED` events), but
  every one of those events involves different downstream wallets — none
  connects to this creator, this mint, or `HXMUxU94…` (the hop1 wallet
  this system's own walkback already recorded for this creator).
- **Case 2's treasury (`5nTJWTSoz…`) IS genuinely present in
  `wt_confirmed_treasuries`** (`confidence=HIGH`,
  `confirmed_at=1784589622`, 2026-07-20T23:20:22 UTC) — this part of the
  user's claim is a verified database fact. But its own recorded
  downstream activity in `watchtower_events` names three specific
  subprov wallets (`4MEbMFxWs…`, `5xBzetUS2Bs2…`, `G2fr9ikcVgtm…`), and
  **`DCyQJVfAL37…` (this creator's known hop1 wallet) is not among
  them.** No table anywhere links this treasury to this creator or mint.

## Phase 2 — Why X64.2 missed them (summary)

Full detail in `lookup_path_analysis.md`. Two distinct causes:

1. **A genuine scope gap**: X64.2's entire infrastructure-lookup phase
   queried `database/wt_ops_v2.db` exclusively. It never touched
   `database/flex_complete_database.db` (where `wt_candidate_scores`,
   `wt_detected_creates`, `wt_creator_launches`, and `token_analysis`
   live), and never queried `watchtower_events` in either database. This
   is real and should be corrected in any future audit of this shape.
2. **A join-direction gap, specific to treasury lookups**: X64.2's
   `wt_confirmed_treasuries` join only asked "is this launch's OWN hop1
   wallet itself a confirmed treasury" — never "does some OTHER
   already-confirmed treasury's recorded downstream lineage include this
   launch." The second question is the one that could, in principle,
   connect a launch to a treasury discovered through a different path.

**Critically, this audit tested whether fixing both gaps would actually
have surfaced either case — and it does not.** Re-running an expanded,
corrected lookup (Phase 4) against all 18 launches, using every table
located in Phase 1 across both databases, still found **zero** database
rows connecting either case's creator/mint to its claimed treasury. The
specific linking evidence for both cases (the `7WbkFQAb…` hop for Case 1;
any `DCyQJVfAL37…`↔`5nTJWTSoz…` link for Case 2) simply does not exist in
either database. X64.2's scope gap is a real defect worth fixing, but it
is not, on its own, sufficient to explain why these two launches read as
unattributed — the missing evidence itself is the larger factor.

## Phase 3/4/5 — Coverage matrix, recheck, false-negative count (summary)

Full detail in `false_negative_matrix.md`. The corrected, wider lookup
(creator-keyed searches, both databases, `watchtower_events`, a
treasury-reverse join) was run against all 18 launches from X64.1/X64.2.
**Result: still 0 of 18 confirmed WATCHTOWER from stored evidence.**
**0 false negatives were recovered** by the corrected lookup — not
because the lookup wasn't worth fixing, but because the specific evidence
needed to confirm either of the user's two cases is not present in either
database at all.

One correction made during this audit to its own earlier draft: an
initial pass of `coverage_table.md` mistakenly described
`CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump`'s live
`wt_walkback_queue` row as already showing `subprov=DCyQJVfAL37…`/
`outcome=LINEAGE_GAP` — that was the *test-fixture* output from the
earlier X64 implementation's regression test, not this row's actual live
database state. Directly re-queried and corrected: the live row still
shows `NO_ATTRIBUTION_FOUND`/`subprov=NULL`, unchanged since its original
completion — the X64 code fix only affects *future* walkback runs and,
per its own explicit constraint, never retroactively modified historical
rows.

## Phase 6 — Root cause

Two separate, independently-confirmed root causes, neither of which is
"a filtering bug" or "wrong database connection string" in the sense of
an outright defect that silently drops correct matches:

1. **X64.2's query scope was narrower than the full set of tables holding
   WATCHTOWER-relevant knowledge** — it queried one database
   (`wt_ops_v2.db`) and four specific tables within it, when at least
   three more tables (`watchtower_events` in the same DB;
   `wt_candidate_scores`, `wt_creator_launches`,
   `token_analysis.watchtower_related` in the live DB) also hold relevant
   signal. This is an **incomplete-join / narrow-scope** cause, confirmed
   by direct comparison of what X64.2 actually ran against what tables
   exist.
2. **The specific evidence needed to confirm either of the two supplied
   cases does not exist in either database**, independent of query
   scope. This is not a lookup defect at all — it is a genuine **data
   gap**: the intermediate hop (`7WbkFQAb…`) for Case 1, and any
   linking record between `DCyQJVfAL37…` and `5nTJWTSoz…` for Case 2,
   were never persisted anywhere this audit could query. Per the earlier
   X64/X64.2 audits' own finding, this is consistent with the broader,
   already-documented pattern: `wt_walkback_edge_candidates` (the table
   that would record exactly this kind of upstream-hop evidence) has
   zero rows for every one of the 18 disposable wallets in this dataset,
   including both `HXMUxU94…` and `DCyQJVfAL37…`. The walkback pipeline
   never walked far enough upstream to record either connection, for the
   same structural reason already identified in X64: the `FULL_WALKBACK`
   branch's hop2 search returned nothing for these wallets, and no
   deeper walk was ever triggered.

Neither cause is "stale snapshot," "multiple databases silently
diverging," or "a filtering bug that discards correct rows" — both
databases were queried live, at current state, and no query in this audit
excluded a row that should have matched. The honest characterization is:
**X64.2's scope was narrower than it should have been (fixable), and
separately, the specific evidence needed for these two cases has never
been captured by any upstream process (not fixable by a smarter query —
requires either a deeper walkback or an out-of-band confirmation being
written back into the database).**

---

## Executive Summary

**1. Where were the two confirmed WATCHTOWER launches stored?**
Neither is stored as a *confirmed WATCHTOWER launch* anywhere in either
database. What IS stored: Case 1's hop1 wallet (`HXMUxU94…`) is on record
in `wt_walkback_queue` as this creator's disposable funder (unresolved
beyond that). Case 1's claimed treasury (`4231KLYi…`) has extensive,
genuine activity in `watchtower_events` — but connected to entirely
different downstream wallets, not this creator. Case 2's claimed treasury
(`5nTJWTSoz…`) IS a genuine `wt_confirmed_treasuries` row with its own
recorded downstream subprovs in `watchtower_events` — but none of those
downstream subprovs is this creator's hop1 wallet (`DCyQJVfAL37…`).

**2. Why did X64.2 fail to find them?**
Two compounding reasons: (a) X64.2's lookup scope never included
`watchtower_events` or the live database's tables, a genuine and now-
documented gap; (b) even correcting that gap, per Phase 4's exhaustive
recheck, the specific evidence linking either case's creator/mint to its
claimed treasury is not recorded in any table in either database — no
lookup, however complete, could have found it from what's currently
stored.

**3. How many of the remaining 16 launches are still genuinely unresolved
after using every existing attribution source?**
**All 16** (plus both of the cases under discussion — 18 of 18 total)
remain unresolved from stored database evidence, per Phase 4's table.

**4. Did any additional WATCHTOWER launches emerge from the corrected
lookup?**
**No.** The corrected, wider lookup (creator-keyed, cross-database,
including `watchtower_events` and a reverse treasury→downstream join)
was run against all 18 launches and surfaced zero additional confirmed
connections beyond what was already known.

**5. Should the X64.2 conclusions be revised?**

**Partially — the scope-gap finding is new and should be added; the
substantive conclusion is not overturned by database evidence, but must
now explicitly flag two known external confirmations the database does
not (yet) support.**

Corrected headline:

```
18 X64 provisioning leads
        ↓
0 confirmed WATCHTOWER from stored database evidence
        ↓
2 launches independently asserted as confirmed WATCHTOWER by the analyst
  (Case 1: 7nxHcmxb…/HHcXBLbn…; Case 2: 71ftvekA…/CvP9vVUC…) —
  NOT currently traceable to their claimed treasuries via any stored
  lineage in either database
        ↓
0 additional historical false negatives recovered by a corrected,
  wider lookup (both databases, watchtower_events, reverse treasury join)
        ↓
Remaining unresolved candidates: 18 (all), pending either (a) the
specific missing upstream evidence being captured via a deeper walkback/
RPC investigation, or (b) the analyst's external confirmations being
written back into wt_confirmed_treasuries / wt_discovered_subprovs with
their supporting evidence, at which point they would correctly surface
in a subsequent audit
```

X64.2's own Low-confidence "no shared treasury evidence" verdict for the
18-launch cohort as a whole is **not falsified** by this audit — it
remains true that no stored evidence connects most of the cohort to any
treasury. What changes is that this audit now knows the database is
**silent, not negative**, on the two specific launches the user has
independently verified — and that X64.2's own search process had a real,
fixable scope gap that should be corrected before it (or any similar
audit) is trusted as exhaustive again.
