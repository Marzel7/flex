import sqlite3
from pathlib import Path
from scripts.monitor_first_farm_evidence_link import main

def test_silent_when_link_table_is_absent(tmp_path, monkeypatch, capsys):
 db=tmp_path/'db'; c=sqlite3.connect(db); c.executescript('CREATE TABLE wt_farm_launches(funder TEXT); CREATE TABLE wt_confirmed_treasuries(treasury TEXT); CREATE TABLE wt_discovered_subprovs(subprov TEXT);'); c.close()
 monkeypatch.setattr('scripts.monitor_first_farm_evidence_link.DB',db)
 assert main()==0 and capsys.readouterr().out==''
