# Gate-aware Helius recovery compatibility

This is a DEV-only correction of the existing renderer and existing fresh compact
matched-pair recovery script. It installs nothing and makes no provider requests.
The ten runtime files qualified in e7e72513 remain byte-identical.

The renderer pins all ten role-specific payload files, including the exact new
listener, and preserves both terminal-aware and CREATE contracts. Every role
must carry the opt-in gate flag and the same explicit denial-state path beside
the existing stable migration journal. Missing flag, unknown state, wrong physical
path, source mismatch or generation mismatch rejects configuration publication.
The gate is never initialized or granted a restoration probe by the renderer.
Gate-unaware source/flag0 is forbidden after the stable state exists.

First activation and changed paired bindings require all three affected services
to be STOPPED before configuration publication. Read-only status checks precede
publication; no reread/update/start is called on a failed precondition. Ordinary
idempotent rendering of an already matched gate-aware configuration preserves its
flags, state and counters. Unrelated live Supervisor sections must match the
hash-verified frozen configuration preimage; drift is not silently overwritten.
The preimage is an additional immutable backup reference, not replacement of old
DEV004 rollback evidence. Secret configuration contents are kept local.

## Fresh-pair control transfer

Before fencing, validate the active matched pair and all source hashes against
one frozen destination manifest. After all affected services have stopped, freeze
one <=16 KiB recovery receipt containing the latest validated denial state and
source/destination authority identities. Existing control file publication remains
the accepted checksum/PENDING/READY protocol.

After the qualified matched destination is ACTIVE, change **only** the gate
record's generation. Denial, request reservations, outcomes, method counters,
backoff and probe deadlines remain identical. Independent readback precedes
receipt acknowledgement and service configuration publication. A crash before the
move leaves wrong-generation state and blocks paid acquisition. A crash after
the move is recognized by exact destination-state readback and is idempotent.
Missing/corrupt state, a changed source, a mixed pair or an unfrozen destination
fails closed. There is no automatic recreation, allowance reset, provider probe,
queue replay or mutation of canonical evidence.

The existing fresh-pair procedure continues to copy only its qualified compact
pair. Tests use tiny synthetic stores; no production or V1 database is copied.
The explicit execution argument remains mandatory. No new coordinator, service,
provider endpoint or retention mechanism is introduced.

## Qualification and deployment boundary

83 provider-free controls pass: 29 focused recovery tests, 13 accepted terminal
recovery controls and 41 accepted CREATE integration controls. Existing Helius
containment, reconnect and migration-completion evidence is reused unchanged.
Tests cover all roles, stopped first activation, restart, fresh-pair transfer,
protected terminal/pending records, source/state/generation refusal and atomic
configuration publication failures before/after rename and directory sync.

Deployment still requires separate authorization. A frozen local bundle binds
source commit, source hashes, the exact existing Supervisor configuration hash,
nonsecret configuration overlay, stable journals, matched authority and a staged
immutable configuration preimage. That preimage must be installed and verified
before activation; no production preimage is overwritten during qualification.
A compatible fallback uses this gate-aware source/configuration and retains denial;
it is never the old gate-unaware listener or flag0. Recovery does not authorize
provider restoration or reset any request counters.

Live journal occupancy is a finite safety bound, not an unlimited-buffer claim.
Raw free slots and independently verified ACK capacity must be reported separately
from protected records eligible for the existing verified OPS terminal transfer.
Only the accepted <=20-record transfer lifecycle may create further slots. No
protected record is discarded by this correction, and no live transfer is run here.
