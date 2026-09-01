"""Read-only retained-evidence Spam Pattern A/B projection."""
from __future__ import annotations

import hashlib
import json

# External/RPC confirmations asserted by the operator.  No raw RPC payload is retained.
CONFIRMED_SPAM = frozenset({
    "2g31Y53tXbmxGxCbtBbqBY8icsr3evRSoD5Fc7RTKefc","4CUCgqzhPmV4a3UJgj5qahFoEijWqn64BB4Rn9AWikqc",
    "5unn5ZMgNTTKkqS8Z6iKD1cEJSiyjwtT5V8vcXjSz3Dj","8rZBaU8Fc7EsJbY9JGTbrTNeWHVp5ZjdmBp5o1pz2zfS",
    "9Ui561sNUuqsiEhMNHXq9Vvpb9fMTErwQEoAuxxpause","G1NmYMkCBNabqikMrWrtr2tvujgcvDRvSkk1N24vxfUa",
    "Gyq2cXxtvje66ERoLtsQKpqfpkM2JuWAEVJNpBSjMSSq","H4Eq5Gj9Fgic2fMiRNn4TTpY9AmihpS2gJPmb37YeWRE",
    "7TGBqwvNmhWtENjdRxT4hdAVpZmw3eMsom5G9UQFDK2w","C1R5wCUXgU3qcXQmqJZsD7p6yFe9szpMvzk4r5zVH4HG",
    "CNsE6EicBpsvQTtPAv35nLSSyFYgoip2dm6bbqi4bBC5","8FwArsQAXKTa1FrRmZj37TNBBkLey18nZYUK7Vrtf4jn",
    "9m7UEAppvB1nU5nYn2j25cfGY94dmcWEALecnfKHEr1T","3ia7aDtzMjFgj72TTJNAz7yhLCgjuFSv46FieRHwaqAZ",
    "GsT4A2gd8jLRUVsLP5TE8dgKTSYy8hFzSESUB4XdQgoz","FYW3ARvUdVH8eJ2NZDM3sZHeLHRcPCngaPwCmNDZyV61",
    "FUpeoQG8ViRLZrc5udzFUNR7x22WkbLJAE9oV4S2GmJA","Dn8jZKiu6mzj2BsJTNoZaXHJZtj5Y2oLYz4Bw5K5KVtm",
    "6y84CxtWjKaPN87yZx234rFx4EBJeYgtj8Tcsn97AUir","AShPU2pskZzRJmMi7PNkmnUAggvL3ZkcDg8YDsbdEses",
    "By3TXiU5kRy9f5UCk2THmoLh2siNWqws3cFMLHrXduq3","FpREdZ2bX6oS6BgW4TKQQoSDNawPT3oSHDkPow7RAy51",
})

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def project(conn):
    rows=conn.execute("""WITH farm AS (SELECT DISTINCT funder f FROM wt_farm_launches), walk AS (SELECT DISTINCT candidate_parent f FROM wt_walkback_edge_candidates), eligible AS (SELECT f FROM farm UNION SELECT f FROM walk), genuine AS (SELECT DISTINCT fl.funder f FROM wt_farm_launches fl JOIN wt_walkback_edge_candidates e ON e.mint=fl.mint AND e.candidate_parent=fl.funder WHERE e.selection_status='SELECTED'), p AS (SELECT candidate_parent f,COUNT(*) n,COUNT(DISTINCT mint)m,COUNT(DISTINCT wallet)w,SUM(selection_status='SELECTED')s,SUM(mechanism='PLAIN_XFER')x,SUM(COALESCE(amount_lamports,0)<>0)z,SUM(hop_depth<=1)h,SUM(hop_depth=2)d2 FROM wt_walkback_edge_candidates GROUP BY candidate_parent) SELECT e.f,COALESCE(p.n,0)n,COALESCE(p.m,0)m,COALESCE(p.w,0)w,COALESCE(p.s,0)s,COALESCE(p.x,0)x,COALESCE(p.z,0)z,COALESCE(p.h,0)h,COALESCE(p.d2,0)d2,CASE WHEN g.f IS NULL THEN 0 ELSE 1 END genuine FROM eligible e LEFT JOIN p ON p.f=e.f LEFT JOIN genuine g ON g.f=e.f ORDER BY e.f""").fetchall()
    out=[]
    for f,n,m,w,s,x,z,h,d2,g in rows:
        a=n>0 and n==m and w==m and s==n and x==n and z==0 and h==n
        b=m>=4 and n==m and w==m and s==n and x==n and z==0 and h==n-1 and d2==1
        label="SPAM_CONFIRMED" if f in CONFIRMED_SPAM else ("SPAM_PATTERN_A_CANDIDATE" if a else ("SPAM_PATTERN_B_CANDIDATE" if b else ("GENUINE_CONTROL" if g else "UNKNOWN")))
        out.append({"funder":f,"label":label,"n":n,"m":m,"w":w,"s":s,"x":x,"z":z,"h":h,"d2":d2,"genuine":bool(g)})
    return out
