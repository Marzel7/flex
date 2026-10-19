"""Append-only persistence for immutable operational change observations."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any,Mapping,Sequence

from ..contracts import canonical_json_bytes
from .change_intelligence import ChangeSnapshot,OperationalLandscapeSnapshot


SCHEMA_PATH=Path(__file__).with_name("change_schema.sql")


class OperationalChangeStore:
    def __init__(self,path:Path)->None:self.path=Path(path);self.connection:sqlite3.Connection|None=None

    def open(self)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(self.path,isolation_level=None);self.connection.row_factory=sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL");self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA_PATH.read_text())

    def close(self)->None:
        if self.connection is not None:self.connection.close();self.connection=None

    def _conn(self)->sqlite3.Connection:
        if self.connection is None:raise RuntimeError("Operational change store is not open")
        return self.connection

    @staticmethod
    def _payload(value:Mapping[str,Any])->tuple[str,str]:
        payload=canonical_json_bytes(value).decode().rstrip("\n")
        return payload,hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _insert_immutable(connection:sqlite3.Connection,table:str,id_column:str,
                          identity:str,values:Sequence[Any],digest:str)->bool:
        placeholders=",".join("?" for _ in values)
        cursor=connection.execute(f"INSERT OR IGNORE INTO {table} VALUES({placeholders})",values)
        if cursor.rowcount:return True
        row=connection.execute(f"SELECT payload_digest FROM {table} WHERE {id_column}=?",
                               (identity,)).fetchone()
        if row is None or row[0]!=digest:raise sqlite3.IntegrityError(f"{table} identity collision")
        return False

    def append(self,previous:OperationalLandscapeSnapshot,current:OperationalLandscapeSnapshot,
               change:ChangeSnapshot)->dict[str,int]:
        connection=self._conn();connection.execute("BEGIN IMMEDIATE")
        inserted_snapshots=duplicate_snapshots=inserted_changes=duplicate_changes=records=0
        try:
            for snapshot in (previous,current):
                value=snapshot.identity_payload();payload,digest=self._payload(value)
                inserted=self._insert_immutable(connection,"operational_landscape_snapshots",
                    "snapshot_id",snapshot.snapshot_id,(snapshot.snapshot_id,snapshot.snapshot_version,
                    snapshot.observation_boundary,snapshot.input_digest,payload,digest),digest)
                inserted_snapshots+=inserted;duplicate_snapshots+=not inserted
            value=change.to_dict();payload,digest=self._payload(value)
            inserted=self._insert_immutable(connection,"operational_change_snapshots",
                "change_snapshot_id",change.change_snapshot_id,(change.change_snapshot_id,
                change.change_version,change.previous_snapshot_id,change.current_snapshot_id,payload,digest),digest)
            inserted_changes+=inserted;duplicate_changes+=not inserted
            record_groups=(("MotifDelta",change.motif_deltas),("NeighbourhoodDelta",change.neighbourhood_deltas),
                ("RelationshipDelta",change.relationship_deltas),("TrendObservation",change.trend_observations))
            for record_type,items in record_groups:
                for item in items:
                    record=item.to_dict();intrinsic_id=record.get("delta_id",record.get("observation_id"))
                    record_id=hashlib.sha256(canonical_json_bytes(
                        [change.change_snapshot_id,record_type,intrinsic_id])).hexdigest()
                    record_payload,record_digest=self._payload(record)
                    records+=self._insert_immutable(connection,"operational_change_records","record_id",
                        record_id,(record_id,change.change_snapshot_id,record_type,record_payload,record_digest),
                        record_digest)
            connection.commit()
        except BaseException:connection.rollback();raise
        return {"inserted_snapshots":inserted_snapshots,"duplicate_snapshots":duplicate_snapshots,
            "inserted_changes":inserted_changes,"duplicate_changes":duplicate_changes,
            "inserted_records":records}

    def health(self)->dict[str,Any]:
        connection=self._conn()
        return {"status":"HEALTHY","snapshots":int(connection.execute(
            "SELECT COUNT(*) FROM operational_landscape_snapshots").fetchone()[0]),
            "changes":int(connection.execute("SELECT COUNT(*) FROM operational_change_snapshots").fetchone()[0]),
            "records":int(connection.execute("SELECT COUNT(*) FROM operational_change_records").fetchone()[0]),
            "authoritative":False}
