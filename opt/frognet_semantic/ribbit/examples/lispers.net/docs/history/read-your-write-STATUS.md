# SUPERSEDED
Read-your-own-write is finished and in the package ([READ_YOUR_OWN_WRITE_V1], qualify stage ryw); see CHECKPOINT-BOUNDARY-NEVER-WAITS.md at the top of outputs/.

# [READ_YOUR_OWN_WRITE_V1] -- in progress, NOT shipped
Ruling (John 2026-09-27): put() is complete when this participant's own consequences are observable.
Built: the RAM host reports every cell a hosted operation wrote (frogram::log_writes_to); each held view records the
id it has applied up to (Engine::held_read, lock-free table); remote_call waits for its own views (Engine::await_own).
Red (before): tools/test_read_your_write.py -- 6 of 80 put->get / delete->get pairs missed their own write.
Now: put at an EXISTING prefix length works; the FIRST put at a NEW length hangs: the registration-lengths watcher
receives the new marker, calls materialize_length, whose plain read of registration|iid||/len (seq 12 in
client-trace-hang.log) is sent but never answered, so the watcher never returns to record its watermark and
await_own times out (10 s). The server's socket threads are idle; the frame is not in the server's trace. Being traced.
