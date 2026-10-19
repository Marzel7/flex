"""Optional, bounded PumpPortal birth receive telemetry for EB0.1.

This collector is deliberately independent of ingestion: it is disabled by
default, performs no RPC or database work, and drops telemetry rather than
blocking the socket if its bounded writer queue is full.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import hashlib
import time
import itertools
import shutil
from pathlib import Path
from typing import Any, Optional


class PumpPortalBirthAudit:
    # One authoritative listener process; shared across its collector instances.
    _receipt_lock = threading.RLock()
    MAX_RECEIPT_BYTES = 4096
    MAX_RECEIPT_FILES = 4096
    MIN_FREE_BYTES = 2_000_000_000
    def __init__(self, *, enabled: bool, path: str, max_pending: int = 2048, compact_path: Optional[str] = None) -> None:
        self.enabled = bool(enabled)
        self.path = Path(path)
        self.compact_path = Path(compact_path) if compact_path else None
        if self.compact_path and self.enabled:
            raise ValueError("RAW_AND_COMPACT_RETENTION_CONFLICT")
        if self.compact_path and self.compact_path.resolve()==self.path.resolve():
            raise ValueError("COMPACT_AUTHORITY_MUST_DIFFER_FROM_RAW")
        self.last_receipt_error = None
        self._pending: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, int(max_pending)))
        self._dropped = 0
        self._started = False
        self._lock = threading.Lock()

    def record(
        self,
        *, receive_utc_ns: int, receive_monotonic_ns: int, signature: Optional[str],
        mint: Optional[str], creator: Optional[str], parser_utc_ns: int,
        market_cap_sol: Optional[float] = None,
        virtual_sol_reserves: Optional[float] = None,
        bonding_curve: Optional[str] = None,
        raw_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Legacy queued audit, or explicitly enabled compact fsync receipt."""
        if self.compact_path is not None:
            return self._compact_record(mint, creator, signature, receive_utc_ns)
        if not self.enabled:
            return
        # Canonical, immutable PumpPortal-create evidence.  The payload digest is
        # the identity: reconnect delivery of identical evidence is harmless.
        raw = raw_payload or {}
        raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        raw_digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        row = {
            "event_schema_version": 2,
            "parser_version": "pumpportal_create.v1",
            "source": "pumpportal_create",
            "receive_utc_ns": int(receive_utc_ns),
            "receive_monotonic_ns": int(receive_monotonic_ns),
            "parser_utc_ns": int(parser_utc_ns),
            "signature": signature or None,
            "mint": mint or None,
            "creator": creator or None,
            # EB0.3: values already supplied by PumpPortal at the receive
            # boundary. This append-only shadow file captures no RPC-derived
            # state and does not affect token persistence.
            "market_cap_sol": float(market_cap_sol) if market_cap_sol is not None else None,
            "virtual_sol_reserves": float(virtual_sol_reserves) if virtual_sol_reserves is not None else None,
            "bonding_curve": bonding_curve or None,
            "received_at": int(receive_utc_ns // 1_000_000_000),
            "raw_payload": raw,
            "raw_payload_sha256": raw_digest,
        }
        try:
            self._pending.put_nowait(row)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return
        self._ensure_writer()


    @staticmethod
    def _identity(mint, creator, signature):
        values = {"source":"PUMPPORTAL_NEW_TOKEN", "mint":mint, "supplied_creator":creator, "supplied_birth_signature":signature}
        for key,limit in (("mint",44),("supplied_creator",44),("supplied_birth_signature",88)):
            value=values[key]
            if value is not None and (not isinstance(value,str) or len(value)>limit):
                raise ValueError("RECEIPT_FIELD_CAPACITY")
        identity=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        return identity,values

    @staticmethod
    def _validation(mint,creator,signature):
        from src.ops.walkback_create_handoff import identity
        if not mint:return "MISSING_MINT"
        if not creator:return "MISSING_CREATOR"
        if not signature:return "MISSING_SIGNATURE"
        if not identity(mint,range(32,45),32) or not identity(creator,range(32,45),32) or not identity(signature,range(64,89),64):return "MALFORMED_IDENTITY"
        return "IDENTITIES_VALID_UNVERIFIED_CHAIN"

    def _read_receipt(self,path):
        if path.is_symlink():raise ValueError("RECEIPT_SYMLINK")
        with path.open("rb") as stream:raw=stream.read(self.MAX_RECEIPT_BYTES+1)
        if len(raw)>self.MAX_RECEIPT_BYTES:raise ValueError("RECEIPT_FILE_CAPACITY")
        row=json.loads(raw)
        if not isinstance(row,dict) or row.get("schema")!=1:raise ValueError("RECEIPT_SCHEMA")
        allowed={"schema","kind","receipt_identity","source","mint","supplied_creator","supplied_birth_signature","receipt_utc_ns","validation_result","persistence_outcome","accepted_at_utc_ns","canonical_conflict"} if row.get("kind")=="PUMPPORTAL_BIRTH" else {"schema","kind","receipt_identity","mint","migration_signature","handoff_utc_ns","boundary","outcome"}
        if set(row)-allowed:raise ValueError("UNEXPECTED_RECEIPT_FIELDS")
        if row.get("kind")=="PUMPPORTAL_BIRTH":
            ident,_=self._identity(row.get("mint"),row.get("supplied_creator"),row.get("supplied_birth_signature"))
            if row.get("receipt_identity")!=ident:raise ValueError("RECEIPT_IDENTITY")
        elif row.get("kind")!="MIGRATION_HANDOFF":raise ValueError("RECEIPT_KIND")
        return row

    def _store_receipt(self,name,row):
        root=self.compact_path
        if root.is_symlink():raise ValueError("RECEIPT_ROOT_SYMLINK")
        if shutil.disk_usage(root.parent).free<self.MIN_FREE_BYTES:raise ValueError("RECEIPT_DISK_HEADROOM")
        root.mkdir(mode=0o700,parents=False,exist_ok=True)
        target=root/(name+".json");temp=root/(name+".tmp")
        if target.is_symlink() or temp.is_symlink():raise ValueError("RECEIPT_SYMLINK")
        # Include incomplete transient files in the bound. No automatic deletion.
        with os.scandir(root) as entries:count=sum(1 for _ in itertools.islice(entries,self.MAX_RECEIPT_FILES+2))
        if count>self.MAX_RECEIPT_FILES+1 or (count>self.MAX_RECEIPT_FILES and not temp.exists()) or (not target.exists() and not temp.exists() and count>=self.MAX_RECEIPT_FILES):raise ValueError("RECEIPT_CAPACITY")
        encoded=json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        if len(encoded)>self.MAX_RECEIPT_BYTES:raise ValueError("RECEIPT_RECORD_CAPACITY")
        # Recover only this exact validated interrupted write, never scan receipts.
        if temp.exists():
            pending=self._read_receipt(temp)
            if pending.get("receipt_identity")!=row.get("receipt_identity"):raise ValueError("RECEIPT_PENDING_CONFLICT")
            os.replace(temp,target)
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"wb") as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
        directory=os.open(root,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return row

    def _compact_record(self,mint,creator,signature,received):
        try:
            ident,values=self._identity(mint,creator,signature)
            if type(received) is not int or not 0<received<2**63:raise ValueError("RECEIPT_TIME")
            with self._receipt_lock:
                target=self.compact_path/(ident+".json")
                if target.exists():
                    old=self._read_receipt(target)
                    if any(old.get(k)!=v for k,v in values.items()):raise ValueError("RECEIPT_CONFLICT")
                    return ident
                # A restart may find a fully written but not yet renamed record.
                pending=self.compact_path/(ident+".tmp")
                row=self._read_receipt(pending) if pending.exists() else {"schema":1,"kind":"PUMPPORTAL_BIRTH","receipt_identity":ident,**values,"receipt_utc_ns":received,"validation_result":self._validation(mint,creator,signature),"persistence_outcome":"UNCONFIRMED"}
                if any(row.get(k)!=v for k,v in values.items()):raise ValueError("RECEIPT_CONFLICT")
                self._store_receipt(ident,row)
            return ident
        except (OSError,ValueError,TypeError):
            self.last_receipt_error="COMPACT_RECEIPT_UNAVAILABLE";return None

    def record_outcome(self,ident,*,outcome,canonical=None):
        if self.compact_path is None:return False
        try:
            with self._receipt_lock:
                row=self._read_receipt(self.compact_path/(ident+".json"))
                if outcome not in {"COMMITTED","RETRY_PENDING","UNRECOVERABLE","INCOMPLETE"}:raise ValueError("RECEIPT_OUTCOME")
                if outcome=="COMMITTED":
                    if canonical is None:raise ValueError("COMMIT_READBACK_REQUIRED")
                    creators={x for x in canonical[:2] if x}
                    if creators!=({row['supplied_creator']} if row['supplied_creator'] else set()) or canonical[2]!=row['supplied_birth_signature']:
                        row['canonical_conflict']=True;outcome="COMMITTED_CONFLICT"
                    elif row['validation_result']=="IDENTITIES_VALID_UNVERIFIED_CHAIN":
                        row.setdefault('accepted_at_utc_ns',time.time_ns())
                # Duplicate/retry notifications never downgrade a confirmed outcome.
                if row['persistence_outcome']!="COMMITTED" or outcome=="COMMITTED_CONFLICT":row['persistence_outcome']=outcome
                self._store_receipt(ident,row)
            return True
        except (OSError,ValueError,TypeError,KeyError):
            self.last_receipt_error="COMPACT_OUTCOME_UNAVAILABLE";return False

    def note_handoff(self,mint,migration_signature,*,at_utc_ns=None):
        if self.compact_path is None:return False
        try:
            if not isinstance(mint,str) or not 1<=len(mint)<=44 or not isinstance(migration_signature,str) or len(migration_signature)>88:raise ValueError("HANDOFF_IDENTITY")
            ident='migration-'+hashlib.sha256(mint.encode()).hexdigest()
            with self._receipt_lock:
                target=self.compact_path/(ident+'.json')
                if target.exists():return True
                self._store_receipt(ident,{"schema":1,"kind":"MIGRATION_HANDOFF","receipt_identity":ident,"mint":mint,"migration_signature":migration_signature,"handoff_utc_ns":time.time_ns() if at_utc_ns is None else at_utc_ns,"boundary":"BEFORE_OPS_SUBMIT","outcome":"ATTEMPTED"})
            return True
        except (OSError,ValueError,TypeError):
            self.last_receipt_error="COMPACT_HANDOFF_UNAVAILABLE";return False

    def health(self) -> dict[str, int | bool]:
        with self._lock:
            dropped = self._dropped
        return {"enabled": self.enabled, "pending": self._pending.qsize(), "dropped": dropped}

    def _ensure_writer(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._drain, name="pumpportal-birth-audit", daemon=True).start()

    def _drain(self) -> None:
        # A directory is an append-only content-addressed store.  The legacy
        # JSONL path remains supported only when explicitly configured with a
        # suffix, preserving old telemetry tests/deployments.
        content_store = self.path.suffix != ".jsonl"
        (self.path if content_store else self.path.parent).mkdir(parents=True, exist_ok=True)
        while True:
            row = self._pending.get()
            try:
                encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                if content_store:
                    target = self.path / (row["raw_payload_sha256"] + ".json")
                    try:
                        with target.open("x", encoding="utf-8") as fh:
                            fh.write(encoded)
                    except FileExistsError:
                        pass
                else:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(encoded + "\n")
            finally:
                self._pending.task_done()



def summarize_birth_coverage(migrations, receipts, handoffs, *, cohort_complete=False, rpc_proofs=(), walkback=(), deliveries=()):
    """Pure, evidence-bound future-cohort counts; caller supplies bounded reads.

    Denominator is the complete unique canonical migration cohort, never receipt
    count. A missing receipt means unknown delivery provenance, not non-delivery.
    Local receive/accept timestamps are never on-chain CREATE timestamps.
    """
    if not cohort_complete:
        return {"total_unique_migrations":"UNKNOWN","qualified_pp_birth_coverage_percent":"UNKNOWN","rpc_recovery_percent":"UNKNOWN","walkback_completion_percent":"UNKNOWN"}
    def bounded(rows,limit):
        selected=list(itertools.islice(iter(rows),limit+1))
        if len(selected)>limit:raise ValueError("COVERAGE_INPUT_BUDGET")
        return selected
    migrations=bounded(migrations,256);receipts=bounded(receipts,4096);handoffs=bounded(handoffs,4096)
    rpc_proofs=bounded(rpc_proofs,256);walkback=bounded(walkback,256);deliveries=bounded(deliveries,256)
    mints={row['mint'] for row in migrations};n=len(mints)
    markers={row['mint']:row for row in handoffs if row.get('kind')=='MIGRATION_HANDOFF' and row.get('boundary')=='BEFORE_OPS_SUBMIT'}
    pp={mint:[] for mint in mints}
    seen=set()
    for row in receipts:
        if row.get('kind')!='PUMPPORTAL_BIRTH' or row.get('source')!='PUMPPORTAL_NEW_TOKEN' or row.get('mint') not in mints:continue
        ident,_=PumpPortalBirthAudit._identity(row.get('mint'),row.get('supplied_creator'),row.get('supplied_birth_signature'))
        if row.get('receipt_identity')!=ident or ident in seen:continue
        seen.add(ident);pp[row['mint']].append(row)
    qualified=set();incomplete=set();timing_unknown=set()
    for mint,rows in pp.items():
        for row in rows:
            marker=markers.get(mint);accepted=row.get('accepted_at_utc_ns')
            valid=row.get('persistence_outcome')=='COMMITTED' and not row.get('canonical_conflict') and PumpPortalBirthAudit._validation(mint,row.get('supplied_creator'),row.get('supplied_birth_signature'))=='IDENTITIES_VALID_UNVERIFIED_CHAIN'
            if valid and type(accepted) is int and type(row.get('receipt_utc_ns')) is int and accepted>=row['receipt_utc_ns'] and marker and type(marker.get('handoff_utc_ns')) is int and accepted<marker['handoff_utc_ns']:qualified.add(mint)
            elif valid and not marker:timing_unknown.add(mint)
        if rows and mint not in qualified:incomplete.add(mint)
    field_incomplete={mint for mint,rows in pp.items() if rows and not any(PumpPortalBirthAudit._validation(mint,r.get('supplied_creator'),r.get('supplied_birth_signature'))=='IDENTITIES_VALID_UNVERIFIED_CHAIN' for r in rows)}
    conflicts={mint for mint,rows in pp.items() if any(r.get('canonical_conflict') for r in rows)}
    recovered=set()
    for proof in rpc_proofs:
        if proof.source=='STRICT_RPC' and proof.qualification=='DEV074_STRICT_CREATE' and proof.mint in mints:
            proof.encode();recovered.add(proof.mint)
    jobs={row['mint']:row for row in walkback if row.get('mint') in mints}
    full={mint for mint,row in jobs.items() if row.get('walkback_class')=='FULL_WALKBACK' and row.get('status')=='complete' and row.get('completed_at') is not None}
    acknowledged={row['mint'] for row in deliveries if row.get('mint') in mints and row.get('state')=='ACK'}
    delivered={row['mint'] for row in deliveries if row.get('mint') in jobs and jobs[row['mint']].get('create_anchor_source')=='CORE_QUALIFIED_CREATE' and jobs[row['mint']].get('create_anchor_signature')==row.get('signature') and jobs[row['mint']].get('creator')==row.get('creator')}
    counts={"total_unique_migrations":n,"pp_birth_receipt_retained":sum(bool(rows) for rows in pp.values()),"pp_creator_retained":sum(any(r.get('supplied_creator') for r in rows) for rows in pp.values()),"pp_birth_signature_retained":sum(any(r.get('supplied_birth_signature') for r in rows) for rows in pp.values()),"pp_birth_qualified_at_migration":len(qualified),"pp_birth_evidence_incomplete_or_late":len(incomplete),"pp_birth_evidence_incomplete":len(field_incomplete),"pp_birth_canonical_conflicts":len(conflicts),"pp_birth_provenance_unknown":sum(not rows for rows in pp.values()),"pp_handoff_timing_unknown":len(timing_unknown),"rpc_create_recovered":len(recovered),"walkback_eligible":sum(bool(row.get('create_anchor_signature')) and row.get('create_anchor_audit_state')=='VALID' and row.get('walkback_class')=='FULL_WALKBACK' for row in jobs.values()),"walkback_executed":sum(row.get('started_at') is not None for row in jobs.values()),"walkback_completed":len(full),"late_create_handoff_delivered":len(delivered|acknowledged),"late_create_handoff_acknowledged":len(acknowledged)}
    for key,num in [('qualified_pp_birth_coverage_percent',len(qualified)),('rpc_recovery_percent',len(recovered)),('walkback_completion_percent',len(full))]:counts[key]=100*num/n if n else 'UNKNOWN'
    counts['semantics']='Verified retained accepted evidence before recorded OPS handoff initiation; not a PumpPortal delivery-rate claim. RPC recovery may overlap PP receipts. No on-chain receipt-time inference.'
    return counts

def configured_birth_audit() -> PumpPortalBirthAudit:
    enabled = os.environ.get("PUMPPORTAL_CANONICAL_BIRTH_RETENTION_ENABLED", "1").lower() in {"1", "true", "yes"}
    path = os.environ.get(
        "PUMPPORTAL_CANONICAL_BIRTH_RETENTION_PATH",
        str(Path(__file__).resolve().parents[2] / "database" / "evidence_platform" / "production" / "pumpportal_birth_evidence"),
    )
    compact=os.environ.get("PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_ENABLED","0")=="1"
    compact_path=os.environ.get("PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH") if compact else None
    if compact and not compact_path:raise ValueError("EXPLICIT_COMPACT_RECEIPT_PATH_REQUIRED")
    return PumpPortalBirthAudit(enabled=enabled, path=path, compact_path=compact_path)
