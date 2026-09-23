// MULTI / EXEC / DISCARD / WATCH / UNWATCH.
// WATCH is a conditional write on write-order ids: EXEC compares each watched key's newest id with the
// one seen at WATCH time. EXEC runs the queued commands one after another, each its own write-order
// step (question-3: what a write-order id covers).
#include "server.hpp"

static void cmd_multi(Client& c, const Argv&, Reply& r) {
    if (c.in_multi) { r.error("ERR MULTI calls can not be nested"); return; }
    c.in_multi = true; c.multi_err = false; c.queued.clear(); r.ok();
}
static void cmd_discard(Client& c, const Argv&, Reply& r) {
    if (!c.in_multi) { r.error("ERR DISCARD without MULTI"); return; }
    c.in_multi = false; c.multi_err = false; c.queued.clear(); c.watches.clear(); r.ok();
}
static void cmd_watch(Client& c, const Argv& a, Reply& r) {
    if (c.in_multi) { r.error("ERR WATCH inside MULTI is not allowed"); return; }
    for (size_t i = 1; i < a.size(); i++) c.watches.push_back(Client::Watch{c.db, a[i], db::version(c.db, a[i])});
    r.ok();
}
static void cmd_unwatch(Client& c, const Argv&, Reply& r) { c.watches.clear(); r.ok(); }
static void cmd_exec(Client& c, const Argv&, Reply& r) {
    if (!c.in_multi) { r.error("ERR EXEC without MULTI"); return; }
    bool aborted = c.multi_err; bool touched = false;
    for (auto& w : c.watches) if (db::version(w.db, w.key) != w.version) { touched = true; break; }
    std::vector<Argv> q; q.swap(c.queued);
    c.in_multi = false; c.multi_err = false; c.watches.clear();
    if (aborted) { r.error("EXECABORT Transaction discarded because of previous errors."); return; }
    if (touched) { r.null_array(); return; }
    r.array(q.size());
    c.in_exec = true;
    // the transaction's writes land one by one (its own reads see them), but every wake is held until the end:
    // a participant parked on any of these keys wakes once, after EXEC, and decides from the final state
    std::vector<std::pair<Region*, std::string>> held; Region::begin_quiet(held);
    for (auto& argv : q) { Reply one; one.proto = r.proto; dispatch_command(c, argv, one); r.out += one.out; }
    Region::end_quiet();
    c.in_exec = false;
}
void register_multi_commands() {
    register_cmd("multi", cmd_multi); register_cmd("exec", cmd_exec); register_cmd("discard", cmd_discard);
    register_cmd("watch", cmd_watch); register_cmd("unwatch", cmd_unwatch);
}
