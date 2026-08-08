"""Append-only persistence for objective motif intelligence and rankings."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from ..contracts import canonical_json_bytes
from .intelligence import MotifIntelligence


SCHEMA_PATH=Path(__file__).with_name("intelligence_schema.sql")


class MotifIntelligenceStore:
    def __init__(self,path:Path)->None:
        self.path=Path(path); self.connection:sqlite3.Connection|None=None

    def open(self)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(self.path,isolation_level=None)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text())

    def close(self)->None:
        if self.connection is not None:self.connection.close();self.connection=None

    def _conn(self)->sqlite3.Connection:
        if self.connection is None:raise RuntimeError("Motif intelligence store is not open")
        return self.connection

    def append(self,profiles:Sequence[MotifIntelligence])->dict[str,int|str]:
        inserted=duplicates=0;connection=self._conn();connection.execute("BEGIN IMMEDIATE")
        try:
            for profile in profiles:
                payload=canonical_json_bytes(replace(profile,rank=None).to_dict()).decode().rstrip("\n")
                digest=hashlib.sha256(payload.encode()).hexdigest()
                cursor=connection.execute("INSERT OR IGNORE INTO motif_intelligence VALUES(?,?,?,?,?,?,?)",
                    (profile.intelligence_id,profile.motif_id,profile.intelligence_version,
                     profile.replay_version,profile.input_digest,payload,digest))
                if cursor.rowcount:inserted+=1
                else:
                    row=connection.execute("SELECT payload_digest FROM motif_intelligence "
                        "WHERE intelligence_id=?",(profile.intelligence_id,)).fetchone()
                    if row is None or row[0]!=digest:raise sqlite3.IntegrityError(
                        "motif intelligence identity collision")
                    duplicates+=1
                for reference_type,values in (("Evidence",profile.supporting_evidence_ids),
                                               ("Primitive",profile.supporting_primitive_ids)):
                    for value in values:connection.execute(
                        "INSERT OR IGNORE INTO motif_intelligence_references VALUES(?,?,?)",
                        (profile.intelligence_id,reference_type,value))
            ordered=[item.intelligence_id for item in sorted(profiles,key=lambda value:value.rank or 0)]
            ranking_body=[profiles[0].intelligence_version if profiles else "1.0.0",ordered]
            ranking_id=hashlib.sha256(canonical_json_bytes(["MotifRanking",ranking_body])).hexdigest()
            ranking_json=canonical_json_bytes(ordered).decode().rstrip("\n")
            ranking_digest=hashlib.sha256(ranking_json.encode()).hexdigest()
            connection.execute("INSERT OR IGNORE INTO motif_rankings VALUES(?,?,?,?)",
                (ranking_id,ranking_body[0],ranking_json,ranking_digest))
            connection.commit()
        except BaseException:
            connection.rollback();raise
        return {"inserted":inserted,"duplicates":duplicates,"ranking_id":ranking_id}

    def health(self)->dict[str,object]:
        connection=self._conn()
        profiles=int(connection.execute("SELECT COUNT(*) FROM motif_intelligence").fetchone()[0])
        rankings=int(connection.execute("SELECT COUNT(*) FROM motif_rankings").fetchone()[0])
        return {"status":"HEALTHY","motifs":profiles,"intelligence_generated":profiles,
                "rankings":rankings,"authoritative":False}
