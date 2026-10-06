"""Dry-run-first structural installer for the two Watchtower programs only."""
import argparse, subprocess
from pathlib import Path

ALLOWED=("operation_monitor_worker","watchtower_api")
def stanza(text,name):
 start=text.index(f"[program:{name}]"); tail=text.find("\n[",start+1)
 return text[start:] if tail<0 else text[start:tail]
def remove(text,name):
 block=stanza(text,name); return text.replace(block,"",1)
def main():
 p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);p.add_argument("--root",type=Path,required=True);p.add_argument("--sha",required=True);p.add_argument("--include-dir",type=Path,required=True);p.add_argument("--apply",action="store_true");a=p.parse_args()
 if subprocess.check_output(["git","-C",str(a.root),"rev-parse","HEAD"],text=True).strip()!=a.sha or subprocess.check_output(["git","-C",str(a.root),"status","--porcelain"],text=True): raise SystemExit("DEPLOYMENT_AUTHORITY_INVALID")
 source=a.config.read_text(); frags=(a.root/"config/supervisor/watchtower_final_runtime.conf").read_text()
 if any(source.count(f"[program:{n}]")!=1 for n in ALLOWED): raise SystemExit("EMBEDDED_SCOPE_INVALID")
 candidate="\n".join(remove(remove(source,"operation_monitor_worker"),"watchtower_api").rstrip().splitlines()+["","[include]",f"files = {a.include_dir}/*.conf",""])
 if a.apply:
  a.include_dir.mkdir(parents=True,exist_ok=True); (a.include_dir/"watchtower_final.conf").write_text(frags); a.config.write_text(candidate)
 else: print(candidate)
if __name__=="__main__": main()
