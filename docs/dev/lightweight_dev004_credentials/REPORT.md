# DEV004 Birdeye Credential Authority Correction

DEV004_BIRDEYE_CREDENTIAL_STATUS: QUALIFIED_NOT_DEPLOYED. All tested acquisition paths select BIRDEYE exclusively; missing/blank approved credentials, legacy-only configuration and conflicting explicit overrides fail before transport. Production deployment was not authorized or attempted. Overall DEV004 minimum-seed acceptance remains HOLD for its independent unresolved dependencies.

## Exact scope

Dedicated isolated branch `codex/dev004-birdeye-authority`, source parent `fde478b7ef2b77b983b58d41f46d0de451d22dd3`. Immutable FLEX_STABLE_V2 remains `2d825bbcf27f802bd0bfcef90cedeba43cc968eb`. This continues DEV004; no new DEV or schema audit.

One small credential helper is supplied identically in canonical source, the three frozen runtime packages and the two existing monitor integration overlays. It selects only the BIRDEYE name, rejects blank normalized values and returns explicit non-secret configuration errors. Canonical/frozen paths retain their existing same-named BIRDEYE dotenv source when the environment setting is absent. An explicitly blank environment BIRDEYE fails closed without file fallback. Legacy names are never consulted. The integration overlays retain their existing environment-only selection, label guard and public mapping-based resolver API.

Existing constructor signatures are retained. An explicit credential is accepted only if it exactly matches the approved BIRDEYE value; an override cannot bypass authority. No missing credential is sent as an empty header. The existing pilot's environment factory and explicit constructor receive the same validation.

Token-price synchronous and asynchronous transports now attach the approved X-API-KEY and Solana chain headers. Their endpoint, request parameters, timeout, response normalization and source ordering remain unchanged. Configuration resolution occurs before their generic transport-error catch, so missing credentials are an explicit configuration error rather than a silent price miss.

No production source, configuration or credentials were edited. The installed main-workers binding still matches the SHA256 retained in the earlier DEV004 read-only audit; it has not received this correction. Do not deploy the entire parent tree: it contains separately qualified DEV work. Any future installation must use the narrow credential diff, preserve each target's accepted overlays and pass its own authorized preflight.

## Relevant acquisition paths

| Path | Authority and qualification |
| --- | --- |
| Token price fallback | Actual sync `_fetch_birdeye_sync` and async `BirdeyeClient.get_price` tested in canonical plus all three frozen price-service copies; same `/defi/token_price` endpoint and 1s timeout |
| Candles and market-cap OHLCV | Five binding variants tested: canonical, frozen listener/creator-resolution, integration monitor-bridge/monitor-worker; same `/defi/v3/ohlcv` builder and 45s timeout |
| Opening market cap | Unchanged installed-consumer class definition exercised with the corrected binding and synthetic first-full-post-migration-second response; same 1s/two-second window builder |
| Monitor/lifecycle polling | Unchanged monitor transport class definition exercised through its default binding factory; same 15m request and parsing |
| Terminal ATH/lifecycle finalizer | Unchanged finalizer constructor/default binding exercised with DB imports excluded; binding authorization tested without executing database freeze/persistence or historical price recalculation |
| Current market-cap overlay | Existing monitor-worker integration's token-overview builder and corrected binding tested; same `/defi/token_overview` endpoint |
| Early-buyer provider pilot | Existing frozen creator-resolution pilot's actual environment factory and explicit constructor tested with injected opener; endpoint, 30s timeout and retry ownership unchanged |
| Durable provider registry | All five factories tested; request dispatch resolves the same approved credential |

Tracked acquisition-source discovery covered canonical/runtime/integration source and scripts. The additional script auth-header match targets streaming.bitquery.io, not Birdeye, and was left unchanged. Historical documentation/embedded audit programs are not asserted to be runnable production acquisition paths. Source path identities are retained in `source_identity.json`.

## Provider-free acceptance

86 passed, zero failures/skips: 81 focused credential cases plus five existing price-service singleton/connection-release regressions. Tests start from `env -i`, use synthetic environment values and synthetic or absent dotenv contents, deny socket/urllib/requests network access and SQLite access before collection, and stub the database/reaper import. No actual credentials or configuration values were inspected, exposed or logged. All transport records contain synthetic keys only.

Coverage includes simultaneous approved/legacy settings; legacy-only; missing/empty/whitespace/quoted-empty values; same-named synthetic dotenv; explicit override rejection; preserved integration alias guards; actual outgoing auth headers; supported candle intervals; opening/monitor constructor wiring; lifecycle default binding; registry and pilot paths; error/log secrecy; and single-attempt 429 behavior. The previous broad-test harness initially lacked the DB lock stub needed by one existing regression; supplying that synthetic lock corrected the harness, with no runtime-code change for that issue.

The consumer class-definition tests execute unchanged class code with provider bindings injected and service/database imports excluded. They qualify credential propagation, not complete lifecycle/price equivalence or V2 seed acceptance. No stopped service was activated. All seven broader DEV004 Birdeye preservation gates remain required for V2 acceptance.

Run from this DEV worktree:

`env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /Users/kevinkeaveney/anaconda3/envs/algotrader/bin/python docs/dev/lightweight_dev004_credentials/qualify.py`

## Preserved controls and storage

AST comparison verifies every changed source definition is credential-related. Existing request builders, transport `__call__` bodies, Helius binding, timeout/version constants and all other pre-existing function bodies remain equivalent. Monitor queue/budget modules, rate limits, pagination, retry owners, acquisition eligibility, valuation/calculation code, detection, attribution, WebSocket intake and Walkback sources were not changed. The price-source order and response normalization were not rewritten. No provider request was made.

This correction creates no runtime data files, database schema, background service, logger or persistent credential store; new unbounded storage paths=0. Runtime raw-retention state is unchanged. Qualification uses synthetic in-memory transports and bounded source/results files, with no production database fixture or clone. Every new/changed candidate file is below500000000bytes. Existing runtime storage-producing code is outside this credential delta.

## Decision and next action

Credential-selection acceptance: PASS in DEV, QUALIFIED_NOT_DEPLOYED. No exact credential-selection blocker remains in the tested candidate scope. Runtime deployment gate: QUALIFIED_NOT_DEPLOYED; the production mismatch remains until separately authorized installation.

Preserve the existing DEV004 seed HOLD. Next action is a separately authorized narrow credential deployment preflight against the actual runtime composition, while continuing the unresolved reference/role/history/recovery and V1/V2 functional-equivalence work. No production installation, configuration change, provider request or service action is authorized by this report.
