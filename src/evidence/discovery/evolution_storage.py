"""Append-only persistence for immutable operational evolution graphs."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any,Mapping

from ..contracts import canonical_json_bytes
from .evolution_intelligence import EvolutionSnapshot


SCHEMA_PATH=Path(__file__).with_name("evolution_schema.sql")


class OperationalEvolutionStore:
    def __init__(self,path:Path)->None:self.path=Path(path);self.connection:sqlite3.Connection|None=None

    def open(self)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(self.path,isolation_level=None);self.connection.row_factory=sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL");self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text())

    def close(self)->None:
        if self.connection is not None:self.connection.close();self.connection=None

    def _conn(self)->sqlite3.Connection:
        if self.connection is None:raise RuntimeError("Operational evolution store is not open")
        return self.connection

    @staticmethod
    def _payload(value:Mapping[str,Any])->tuple[str,str]:
        payload=canonical_json_bytes(value).decode().rstrip("\n")
        return payload,hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _insert(connection,table,id_column,identity,values,digest)->bool:
        cursor=connection.execute(f"INSERT OR IGNORE INTO {table} VALUES({','.join('?' for _ in values)})",values)
        if cursor.rowcount:return True
        row=connection.execute(f"SELECT payload_digest FROM {table} WHERE {id_column}=?",
                               (identity,)).fetchone()
        if row is None or row[0]!=digest:raise sqlite3.IntegrityError(f"{table} identity collision")
        return False

    def append(self,snapshot:EvolutionSnapshot)->dict[str,int]:
        connection=self._conn();connection.execute("BEGIN IMMEDIATE")
        try:
            value=snapshot.to_dict();payload,digest=self._payload(value)
            inserted=self._insert(connection,"operational_evolution_snapshots","evolution_snapshot_id",
                snapshot.evolution_snapshot_id,(snapshot.evolution_snapshot_id,snapshot.evolution_version,
                snapshot.previous_landscape_snapshot_id,snapshot.current_landscape_snapshot_id,
                snapshot.change_snapshot_id,payload,digest),digest)
            records=0
            for record_type,items in (("EvolutionNode",snapshot.nodes),("EvolutionEdge",snapshot.edges),
                                      ("EvolutionEvent",snapshot.events)):
                for item in items:
                    record=item.to_dict();intrinsic=record.get("node_id",record.get("edge_id",record.get("event_id")))
                    record_id=hashlib.sha256(canonical_json_bytes(
                        [snapshot.evolution_snapshot_id,record_type,intrinsic])).hexdigest()
                    record_payload,record_digest=self._payload(record)
                    records+=self._insert(connection,"operational_evolution_records","record_id",record_id,
                        (record_id,snapshot.evolution_snapshot_id,record_type,record_payload,record_digest),
                        record_digest)
            connection.commit()
        except BaseException:connection.rollback();raise
        return {"inserted_snapshots":int(inserted),"duplicate_snapshots":int(not inserted),
                "inserted_records":records}

    def health(self)->dict[str,Any]:
        connection=self._conn()
        return {"status":"HEALTHY","snapshots":int(connection.execute(
            "SELECT COUNT(*) FROM operational_evolution_snapshots").fetchone()[0]),
            "records":int(connection.execute(
            "SELECT COUNT(*) FROM operational_evolution_records").fetchone()[0]),
            "authoritative":False}
