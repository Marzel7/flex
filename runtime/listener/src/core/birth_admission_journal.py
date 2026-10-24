"""Versioned durable admission in the existing birth fallback file.

Reuses migration journal's bounded publication/locking, not another queue
service/database. Production initialization or transfer is never automatic.
"""
import hashlib
import json
from src.core.migration_signature_journal import (
    MigrationJournal, JournalHold, MAX_RECORD_BYTES, MAX_JOURNAL_BYTES, MAX_RECORDS,
)

FIELDS = {'_birth_retry_schema','mint','signature','traderPublicKey','bondingCurveKey','symbol','name','receive_utc_ns','provenance'}

def _validate_payload(data):
    if not isinstance(data, dict) or set(data) != FIELDS or type(data.get('_birth_retry_schema')) is not int or data['_birth_retry_schema'] != 1 or data['provenance'] != 'PUMPPORTAL_NEW_TOKEN':
        raise JournalHold('INVALID_BIRTH_PROVENANCE')
    for key,cap,required in [('mint',64,True),('signature',128,True),('traderPublicKey',64,False),('bondingCurveKey',64,False),('symbol',1024,False),('name',1024,False)]:
        value = data[key]
        if value is None and not required:
            continue
        if not isinstance(value,str) or len(value)>cap or (required and not value):
            raise JournalHold('INVALID_BIRTH_FIELD')
    if type(data['receive_utc_ns']) is not int or not 0 < data['receive_utc_ns'] < 2**63:
        raise JournalHold('INVALID_BIRTH_TIME')
    if len(json.dumps(data).encode()) > MAX_RECORD_BYTES:
        raise JournalHold('BIRTH_RECORD_CAPACITY')

def birth_id(data):
    _validate_payload(data)
    values = {'source':'PUMPPORTAL_NEW_TOKEN','mint':data['mint'],'supplied_creator':data['traderPublicKey'],'supplied_birth_signature':data['signature']}
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class BirthJournal(MigrationJournal):
    @staticmethod
    def _key(identity):
        if not isinstance(identity,str) or len(identity)!=64 or any(c not in '0123456789abcdef' for c in identity):
            raise JournalHold('INVALID_BIRTH_ID')
        return identity

    def _validate(self,row):
        if not isinstance(row,dict) or set(row)-{'version','id','signature','mint','source','received_at','state','attempts','error_code','ack','payload','legacy_birth','conflicting_payload'}:
            raise JournalHold('INVALID_BIRTH_RECORD')
        if type(row.get('version')) is not int or row['version']!=2:
            raise JournalHold('INVALID_BIRTH_VERSION')
        payload=row.get('payload');_validate_payload(payload)
        if row.get('id')!=birth_id(payload) or row.get('signature')!=payload['signature'] or row.get('mint')!=payload['mint'] or row.get('source')!='PUMPPORTAL_NEW_TOKEN' or row.get('received_at')!=payload['receive_utc_ns']:
            raise JournalHold('BIRTH_IDENTITY_CONFLICT')
        if row.get('state') not in {'RESOLVED','ACKED','QUARANTINED','RETRY_EXHAUSTED'} or type(row.get('attempts')) is not int or not 0<=row['attempts']<=3:
            raise JournalHold('INVALID_BIRTH_STATE')
        if row.get('error_code') is not None and (not isinstance(row['error_code'],str) or len(row['error_code'])>80):
            raise JournalHold('INVALID_BIRTH_ERROR')
        if row.get('ack') is not None:
            ack=row['ack']
            if not isinstance(ack,dict) or set(ack)!={'identity','binding'} or ack['identity']!=row['id'] or not isinstance(ack['binding'],str) or not 1<=len(ack['binding'])<=256:
                raise JournalHold('INVALID_BIRTH_ACK')
        if row['state']=='ACKED' and row.get('ack') is None:
            raise JournalHold('INVALID_BIRTH_ACK')
        if 'conflicting_payload' in row:_validate_payload(row['conflicting_payload'])

    def _record(self,payload):
        _validate_payload(payload)
        return {'version':2,'id':birth_id(payload),'signature':payload['signature'],'mint':payload['mint'],'source':'PUMPPORTAL_NEW_TOKEN','received_at':payload['receive_utc_ns'],'state':'RESOLVED','attempts':0,'error_code':None,'ack':None,'payload':dict(payload)}

    def _read(self):
        if self.tmp.exists():raise JournalHold('INTERRUPTED_PUBLICATION_REQUIRES_REVIEW')
        try:
            with self.path.open('rb') as stream:raw=stream.read(MAX_JOURNAL_BYTES+1)
        except FileNotFoundError:raise JournalHold('MISSING_JOURNAL_AUTHORITY') from None
        if len(raw)>MAX_JOURNAL_BYTES or (raw and not raw.endswith(b'\n')):raise JournalHold('TRUNCATED_OR_OVERSIZE_JOURNAL')
        rows=[];seen=set()
        for line in raw.splitlines():
            if len(line)+1>MAX_RECORD_BYTES:raise JournalHold('RECORD_CAPACITY')
            try:row=json.loads(line)
            except (ValueError,UnicodeError):raise JournalHold('MALFORMED_JOURNAL') from None
            if not isinstance(row,dict):raise JournalHold('INVALID_BIRTH_RECORD')
            if 'version' not in row:
                allowed={'mint','creator','created_at','bonding_curve_pda','create_tx_signature','symbol','name','received_at','last_error','birth_evidence_source'}
                if set(row)-allowed or row.get('birth_evidence_source')!='PUMPPORTAL_NEW_TOKEN' or type(row.get('received_at')) is not int:
                    raise JournalHold('UNQUALIFIED_LEGACY_BIRTH')
                original=row
                payload={'_birth_retry_schema':1,'mint':row.get('mint'),'signature':row.get('create_tx_signature'),'traderPublicKey':row.get('creator'),'bondingCurveKey':row.get('bonding_curve_pda'),'symbol':row.get('symbol'),'name':row.get('name'),'receive_utc_ns':row['received_at']*1_000_000_000,'provenance':'PUMPPORTAL_NEW_TOKEN'}
                row=self._record(payload);row['legacy_birth']=original
            self._validate(row)
            if row['id'] in seen:
                previous=next(r for r in rows if r['id']==row['id'])
                if previous!=row:raise JournalHold('LEGACY_BIRTH_DUPLICATE_CONFLICT')
                continue
            seen.add(row['id']);rows.append(row)
            if len(rows)>MAX_RECORDS:raise JournalHold('JOURNAL_CAPACITY')
        return rows

    def admit_birth(self,payload):
        incoming=self._record(payload)
        def change(rows):
            for row in rows:
                if row['id']==incoming['id']:
                    old={k:v for k,v in row['payload'].items() if k!='receive_utc_ns'}
                    new={k:v for k,v in payload.items() if k!='receive_utc_ns'}
                    if old!=new:
                        if row.get('conflicting_payload') not in (None,payload):raise JournalHold('BIRTH_CONFLICT_CAPACITY')
                        row['conflicting_payload']=dict(payload);row['state']='QUARANTINED';row['error_code']='BIRTH_PAYLOAD_CONFLICT'
                    return dict(row)
            rows.append(incoming);return dict(incoming)
        return self._change(change)
