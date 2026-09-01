"""Post-commit generic retained-evidence operation projections."""
import json, time

NEXUS_ID = "bd7d7479-1454-5d41-9f68-115550348f3e"

def project_retained_operation_evidence(conn, mint):
    conn.execute("""CREATE TABLE IF NOT EXISTS operation_detector_results (
      operation_id TEXT, detector_id TEXT, mint TEXT, defining_signature TEXT,
      detector_result TEXT, reason_code TEXT, provenance_json TEXT, evaluated_at INTEGER,
      PRIMARY KEY(operation_id,mint,defining_signature))""")
    q=conn.execute("SELECT creator,funder_wallet FROM wt_walkback_queue WHERE mint=?",(mint,)).fetchone()
    r=conn.execute("SELECT signature,transfer_source,transfer_destination,transfer_lamports,fee_payer,signers_json,route_semantics,provenance_digest FROM wt_walkback_transaction_roles WHERE mint=? ORDER BY last_observed_at DESC LIMIT 1",(mint,)).fetchone()
    if not q or not r: return None
    from src.ops.direct_10k_creator_provisioning import detect_direct_10k_creator_provisioning
    e={'mint':mint,'creator':q[0],'direct_funder':q[1],'defining_signature':r[0],'transfer_source':r[1],'transfer_destination':r[2],'transfer_amount_lamports':r[3],'launch_coupled':True}
    d=detect_direct_10k_creator_provisioning(e)
    conn.execute("INSERT OR REPLACE INTO operation_detector_results VALUES(?,?,?,?,?,?,?,?)",(NEXUS_ID,'DIRECT_10K_CREATOR_PROVISIONING',mint,r[0],d['result'],d['reason'],json.dumps({'role_digest':r[7],'route_semantics':r[6]}),int(time.time())))
    conn.commit(); return d
