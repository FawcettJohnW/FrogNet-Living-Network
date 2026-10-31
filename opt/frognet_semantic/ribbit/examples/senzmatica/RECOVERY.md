# Reconstructed source checkpoint

The original archive/commits were lost in a workspace rollback. This recovery
reconstructs source from the conversation's recorded tool calls and validates it
again. It is not a byte-identical replacement for the missing archive.

Original backend: 1bdf0dcea6d59d5b6651d14e076cff6dfdb512e4, kept unchanged.
API++ c74fc65 survived and is incorporated into this branch.
Separate conversion and MQTT-ordering-repair checkouts will be used. No older
reported PASS is reused as fresh evidence. Current evidence is under evidence/.
Recovery state/dependencies are in the sibling recovery-state and baseline-tools.
Persist source checkpoints to Library, not just local git.

Fresh reconstruction completed: original 7, RAM 13, optional repaired mixed 12,
JNI 18, API++ 75; all three semantic mutants caught and restored 13-check gate
passed. See evidence/ for fresh logs. No missing old commit IDs are presented as
recovered. Final source archive is saved to Library with a persistent identifier.
