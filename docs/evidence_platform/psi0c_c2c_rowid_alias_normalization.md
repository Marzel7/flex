# PSI0C-C2C rowid-alias normalization

PSI0C-C2C qualifies exact serialized-schema normalization for two SQLite `INTEGER PRIMARY KEY` rowid alias cases. The repaired immutable-bundle adapter contract digest is `977043b77ef7a97d23b021bbb4b4150b0f9dc6495421e42352662f0ea7285278`.

The frozen SELECT templates and logical PSI0A-D schemas are unchanged. For `ops_selected_cohort`, the adapter accepts only the exact physical shape where `id` replaces `rowid`, then maps the positive integer `id` to logical `rowid`. For `snapshot_selected_cohort`, it accepts only the exact physical shape where `rowid` is absent and positive integer `snapshot_id` supplies the bound row identity. The already-qualified logical shapes remain accepted.

Rows with conflicting ops identities, missing identities, unexpected fields, non-positive or non-integer identities, or any other schema variant fail before assessment and publication. The fixture-only entry point remains unchanged. Production-derived immutable-local-bundle provenance and false integration/activation authority are preserved.
