# DEV004 Non-P3R Discovery and Review Qualification

DEV004_NON_P3R_DISCOVERY_STATUS: PARTIAL — 19/19 functional controls PASS; full gate HOLD on enforceable ongoing execution/storage bounds.

Inspection revision d759341e1a3b62968b77df9755e2be9531271e3c; unchanged executable candidate 506fe25c941eadf379ca98e4f0d42c1a2486dc9c. FLEX_STABLE_V2 resolves to 2d825bbcf27f802bd0bfcef90cedeba43cc968eb. Relevant accepted freshness, roles, creator graph, Walkback recovery, startup and latest Birdeye corrections remain ancestral. The four exercised/reviewed owner source files are byte-identical to installed Walkback package files (hashes in result.json). No production source or process changed.

## ACCEPTED_DISCOVERY_OWNER

`provisioning_edges.capture_provisioning_relationship` captures operation-neutral Treasury→SubProvider→Creator facts/sessions. `treasury_topology_classifier.surface_runtime_topology_candidate` uses its existing classifier, default five-chain and amount-consistency rules, valid CREATE anchors, transaction roles and exclusions, to insert/update PENDING_REVIEW only. `EmergingOperatorService._discovery_profiles`, `_compose` and public `list(debug=True)` project/publish X78.4 review evidence. No new detector, threshold, writer, review state or admission semantics were implemented. Tests explicitly invoke these accepted callable interfaces; automatic daemon scheduling/first-root hook invocation is not claimed. Earlier complete Walkback acquisition qualification is reused rather than replayed here.

## FRESH_DISCOVERY_RESULT

Fresh schema-only Core/OPS fixtures were initialized from accepted retained DEV fixture schema metadata (not production data), with the accepted provisioning_edges initializer adding its own schema. No historical token rows copied. Five new synthetic Treasury→SubProvider→Creator→Launch routes were captured through the actual writer. Synthetic durable queue CREATE anchors, role projections and selected-edge records supplied the post-acquisition evidence boundary; they are test inputs, not claims of historical/provider observations. No hand-written review row was seeded. Actual topology owner returned action=inserted, distinct_mints/subprovs/creators=5, amount consistency=1.0. All five wrap signatures, mints, roles and provenance survived in review/evidence records.

## REVIEW_PUBLICATION

Actual service public list contains one `potential_operations_reconciled` card with deterministic family identity, matching the real `_compose` result. It has DISCOVERY_ONLY scope, registry_eligible=false and provisional governance. Treasury status remains PENDING_REVIEW; no confirmed membership/attribution was inferred. Identity is stable across re-instantiation and duplicate publication. The separate accepted funding-structure Analyst Queue was source/hash verified, not rerun or relabeled as this X78.4 publication; it retains its STRONG_AND_ATTRIBUTABLE input rule and maximum 20 output. Its full Core corpus generator was not executed because that would be a population scan outside this bounded case.

## RESTART_AND_DUPLICATE_SAFETY

After closing/reopening OPS and creating a new service, the same family is published. Re-running the actual topology owner produces one review row. Replaying the provisioning writer leaves 10 edge rows and five unique sessions, with no duplicate identity or review card. Existing observation_count accumulation is expected evidence bookkeeping, not duplicate membership. Publication is read-only and regenerable; no new persistent assessment history was invented. This is reopen/re-instantiation, not a worker crash-injection or production restart claim.

## CONFLICT_HANDLING

A retained ALTERNATIVE competing-parent edge remains unchanged with its rejection reason, does not become a selected descendant and does not create an alternative-parent X78.4 profile. Insufficient evidence yields no qualified review candidate. Known-infrastructure exclusion rejects the wallet. Existing REJECTED human review is not overwritten and is no longer surfaced by the X78.4 branch. No untested claim is made about arbitrary contradictory SELECTED parent sets.

## KNOWN_OPERATION_SEPARATION

The accepted classifier returns already_confirmed for a wallet present in wt_confirmed_treasuries, rather than rediscovering it. Review-only publication retains no canonical membership authority. Synthetic confirmation is a separate test input installed only after the fresh case; it is not a promotion performed by discovery. Prior 100 creator classifications, 616 Watchtower routes, 26 Deep cases, 11 frozen comparisons, freshness and Walkback/Birdeye recovery evidence remain unchanged and were not repeated.

## STORAGE_AND_EXECUTION_BOUNDS

DEV qualification is bounded: five routes plus one alternative edge, two schema-only databases; final Core 335872 bytes and OPS 806912 bytes. RLIMIT_FSIZE=16000000 and SQLite max_page_count=3840 enforce disposable limits. Network connect/bind, subprocesses, unmanaged threads, credential-file reads and database paths outside declared fixtures are blocked by the retained isolation harness. 23 observed SQLite opens were allowed fixture paths, zero undeclared attempts; blocked runtime activity=[]; no raw provider payloads retained or provider transport invoked. No new runtime growth path introduced. No snapshot file was published to the global cache.

Parent-specific selected-edge lookup uses SEARCH via ix_wwec_parent. However `_discovery_profiles` also runs whole-population `SELECT DISTINCT candidate_parent,mint ... WHERE selection_status=SELECTED`; EXPLAIN shows SCAN using ix_wwec_parent and USE TEMP B-TREE FOR DISTINCT. It also iterates review/candidate/provisioning/session populations, and the topology classifier fetches all qualifying chains for a root without a hard root-size ceiling. `_list_uncached` computes before applying visible list limits. Existing snapshot builder invokes that full computation; scheduling isolates request latency, not total work. Analyst LIMIT 20 limits output, not generation/sort/history. No accepted hard total candidate/snapshot/input-byte or VM-work bound for these existing owners was demonstrated. Small fixtures, test-only resource limits, indexes and atomic snapshot replacement are not proof of bounded sustained V2 execution or files.

Therefore requirement 4 (bounded per-cycle work/no unbounded population scans/candidate growth) cannot receive PASS. No unbounded source generator or large historical scan was executed; EXPLAIN is metadata-only. No production storage controls were changed or invented.

## PROVIDER_FREE_TEST_RESULTS

19/19 controls pass; exact assertions, publication output, source hashes and plans are in result.json and qualify.py. Command used accepted Python `/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python` with PYTHONDONTWRITEBYTECODE=1, runtime/walkback package root, and disposable `/private/tmp/dev004-nonp3r-3jqu0b8h` paths. Script uses retained accepted isolation setup; copies are saved alongside it. Schema preparation copies only sqlite_master table/index/view definitions from `/private/tmp/dev004-compact-creator-qualified/classification_closure/{core,ops}_v2.sqlite`; provisioning schema comes from its accepted initializer. Initial baseline run passed 14 controls; only the five added duplicate/conflict/publication checks justified the final rerun. No unrelated matrix or production test.

## REMAINING_CUTOVER_GATES

M1 functional non-P3R review publication is established for this accepted callable path. M1/M4 execution and ongoing storage bounds remain unqualified as stated above; this is not a missing discovery engine or P3R admission policy blocker. Final source/config/secondary-store manifest, final state/queue/handoff reconciliation and paired cutover/rollback remain separate existing gates. No deployment authorization.

NEXT_ACTION: Authorize only a bounded DEV correction to the existing non-P3R projection/snapshot execution boundary: explicit input/work/output caps with fail-closed behavior preserving the last good publication, then rerun these focused controls. Do not alter discovery predicates, introduce a scheduler or copy historical bulk. Until such enforcement is qualified, operational cutover remains HOLD.

RUNTIME_DEPLOYMENT_REQUIRED=false — provider-free qualification/test artifacts only; no executable runtime change. V1/production unchanged, no service actions, providers, live WebSockets, deletion or cutover.
