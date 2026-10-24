"""Critical acceptance regression: an optional lookup must not precede admission.

Intentionally remains failing until the journal lifecycle correction is qualified.
No production DB, provider, source fence or Supervisor action occurs here.
"""
import ast,importlib.util,pathlib,resource
ROOT=pathlib.Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('fenced_admission_journal',ROOT/'runtime/listener/src/core/migration_signature_journal.py');journal=importlib.util.module_from_spec(s);s.loader.exec_module(journal)

def test_signature_is_durable_when_completion_lookup_rejects_transfer(tmp_path):
 resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
 def fence():raise RuntimeError('PAIR_TRANSFER_ONLY')
 scope={'_validate_active_pair':fence,'DB_PATH':'DISPOSABLE_NEVER_OPENED'}
 tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_migration_completed_mint')
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-completion-lookup','exec'),scope)
 work=journal.MigrationJournal(tmp_path/'migration.jsonl',resolve_completed=scope['_migration_completed_mint']);work.initialize()
 try:work.admit('synthetic-transfer-signature','PUMPSWAP_MIGRATION_ACCOUNT',1700000000)
 except RuntimeError as error:assert str(error)=='PAIR_TRANSFER_ONLY'
 assert [r['signature'] for r in work.snapshot()]==['synthetic-transfer-signature'], 'Required signature was not durably admitted before completion lookup failed'
