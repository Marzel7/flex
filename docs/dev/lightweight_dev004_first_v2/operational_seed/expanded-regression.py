from pathlib import Path
import shutil,sys
O=Path('/private/tmp/dev004-operational-expanded-regression-final');O.mkdir(exist_ok=False)
for name in ['core_v2.sqlite','ops_v2.sqlite']:shutil.copyfile(Path('/private/tmp/dev004-operational-seed-qualified')/name,O/name)
p=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex/docs/dev/lightweight_dev004_first_v2/end_to_end/price_and_candidates.py');sys.argv=[str(p),str(O)];source=p.read_text().replace('DEV004_','DEV004_OPERATIONAL_').replace('pragma max_page_count=2048','pragma max_page_count=3840');exec(compile(source,str(p),'exec'),{'__file__':str(p),'__name__':'__main__'})
