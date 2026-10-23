# DEV-only contract

Use the existing listener package with DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED=1 and an explicitly provisioned absolute MIGRATION_JOURNAL_PATH. Production activation is not authorized. The file must already be a retained compatible legacy file or an explicitly initialized empty DEV fixture via MigrationJournal(path).initialize(). Admission never initializes missing authority automatically.

Do not derive the stable path from changing DB_PATH. Do not point at arbitrary legacy or oversized files and assume automatic repair. No automatic journal relocation is implemented. Before future deployment, retain the accepted source composition and document the stable journal's relationship to any existing V1 fallback file.

Record states are retained, including ACKED, QUARANTINED and RETRY_EXHAUSTED. Capacity means stop admission, not cleanup authorization. A leftover .tmp or malformed current file blocks replay for operator review. Do not discard it as a disposable cache.

Journal flock protects file operations only. It does not authorize simultaneous V1/V2 writers or implement a cutover phase owner. Core/OPS readback proves per-event completion under the currently bound pair; it cannot certify a final database generation. Existing provider acquisition, budgets, retries and BIRDEYE credential authority remain unchanged.

Qualification: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest --noconftest tests/dev004/test_signature_first_migration_journal.py -o cache_dir=/private/tmp/dev004-signature-test-cache
