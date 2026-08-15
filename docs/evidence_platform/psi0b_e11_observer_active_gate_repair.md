# PSI0B-E11 observer active-gate repair

PSI0B-E11 corrects the narrow false-positive boundary identified by E10.
`*.write.lock.owner.*.tmp` files are atomic diagnostic owner-metadata
publications, not authoritative lease evidence. Their canonical path, mtime and
size components remain recorded at every checkpoint, but creation, replacement
or cleanup cannot alone stop prestart or active health. Malformed component
telemetry still fails closed, and the separate authoritative-owner gate remains
unchanged.

The E8 provenance chain now remains open after prestart authorization
consumption and through execution. Each active checkpoint and its named PASS or
STOP decision is appended and fsynced before the corresponding production
source can open. Prestart failure still consumes no authorization. Active or
execution failure seals the replayable attempt as failed; successful completion
seals the same chain only after the executor returns.

All Supervisor, PID, descriptor, critical-event, serializer, lock-error, queue,
database/WAL, feed, ingestion and authoritative lease gates remain unchanged.
The repair grants no extraction, integration or activation authority.

Telemetry observer contract digest:
`f387dd7ae7f2fdb69e19eaf0e3d2b13188708dbad44b952ef2fb107aeea4b94c`.

Observer provenance contract digest:
`c4c7b57b6859726cd34e30fb2dc96f896faca1f78cbf75e41ff2c739e353fe77`.
