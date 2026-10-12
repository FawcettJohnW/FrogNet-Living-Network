"""apply_session_hook.py RAM_SERVER_CPP -- [SESSION_IS_LIVENESS_V1] platform patch, applied at build like the Redis
host's apply_*.py (the vendored v0.63 stays untouched).

A region learns WHICH session a request came on (rina_session_id(): a number unique for the life of the process) and
WHEN a session ends (rina_session_down(id), called once, after its socket closes). That is the whole of liveness:
a participant is alive while its one permanent connection is up. No pings, no timed re-asks, no freshness windows."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:70], s.count(a)); s = s.replace(a, b)
rep('#include "lisp_engine.hpp"\n', '#include "lisp_engine.hpp"\n'
    '// [SESSION_IS_LIVENESS_V1] the session a request arrived on, and the region\'s hook for a session ending\n'
    'static thread_local uint64_t tl_session_id = 0;\n'
    'uint64_t rina_session_id() { return tl_session_id; }\n'
    'void rina_session_down(uint64_t id);\n')
rep('        Reply r; if (api(w.method, w.path, w.body, false, r)) { s->finish_and_send(seq, w, r); return; }\n'
    '        std::thread([=] { Reply rr; api(w.method, w.path, w.body, true, rr); s->finish_and_send(seq, w, rr); }).detach();',
    '        tl_session_id = reinterpret_cast<uint64_t>(s.get());\n'
    '        Reply r; if (api(w.method, w.path, w.body, false, r)) { s->finish_and_send(seq, w, r); return; }\n'
    '        std::thread([=] { tl_session_id = reinterpret_cast<uint64_t>(s.get()); Reply rr; api(w.method, w.path, w.body, true, rr); s->finish_and_send(seq, w, rr); }).detach();')
rep('    if (api(method, path, body, false, r)) { s->reply(seq, wire_reply(h, http_req, have_old ? &old : nullptr, r)); return; }\n'
    '    std::thread([=] { Reply rr;',
    '    tl_session_id = reinterpret_cast<uint64_t>(s.get());\n'
    '    if (api(method, path, body, false, r)) { s->reply(seq, wire_reply(h, http_req, have_old ? &old : nullptr, r)); return; }\n'
    '    std::thread([=] { tl_session_id = reinterpret_cast<uint64_t>(s.get()); Reply rr;')
rep('    { std::lock_guard<std::mutex> g(pend_m); live.erase(token); }\n    s->landed.close();',
    '    { std::lock_guard<std::mutex> g(pend_m); live.erase(token); }\n'
    '    rina_session_down(reinterpret_cast<uint64_t>(s.get()));   // [SESSION_IS_LIVENESS_V1] before s can be freed and its address reused\n'
    '    s->landed.close();')
# [RINA_RESIDENT_V1] the region works on the memory itself, in this process: the same ResidentMemory the lisp
# region uses, and a held read (MEM.wait) for an operation that waits for its answer on its own request thread.
rep('Reply lisp_op(const std::string& body) {',
    'frogram::MemoryApi& rina_resident_memory_i() { static ResidentMemory m; return m; }\n'
    'std::vector<frogram::Cell> rina_resident_wait_i(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {\n'
    '    auto eval = [&] { return MEM.match(s, v, i, after, 0, wall()); };\n'
    '    std::vector<ramm::Cell> rows = eval();\n'
    '    if (rows.empty() && wait_s > 0) {\n'
    '        auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait_s);\n'
    '        rows = MEM.wait(s, v, i, deadline, eval, [](const std::vector<ramm::Cell>& r) { return !r.empty(); });\n'
    '    }\n'
    '    std::vector<frogram::Cell> out;\n'
    '    for (auto& c : rows) out.push_back(frogram::Cell{c.id, c.service, c.variable, c.instance, Json::parse(c.bag), c.updated});\n'
    '    return out;\n'
    '}\n'
    '// [RAW_GET_V1] one cell as stored: its bag text, or nothing\n'
    'bool rina_resident_get_raw_i(const std::string& s, const std::string& v, const std::string& i, std::string& bag) {\n'
    '    for (auto& c : MEM.match(s, v, i, -1, 0, wall())) if (c.instance == i) { bag = std::move(c.bag); return true; }\n'
    '    return false;\n'
    '}\n'
    '// [SDU_BY_REFERENCE_V1] one cell as stored, with its id (a reference names a cell AND the version it meant)\n'
    'bool rina_resident_get_raw_id_i(const std::string& s, const std::string& v, const std::string& i, std::string& bag, uint64_t& id) {\n'
    '    for (auto& c : MEM.match(s, v, i, -1, 0, wall())) if (c.instance == i) { bag = std::move(c.bag); id = c.id; return true; }\n'
    '    return false;\n'
    '}\n'
    '// [RAW_HELD_READ_V1] the same held read, the cells as the memory holds them: id and the bag text, nothing parsed\n'
    'std::vector<std::pair<uint64_t, std::string>> rina_resident_wait_raw_i(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) {\n'
    '    auto eval = [&] { return MEM.match(s, v, i, after, 0, wall()); };\n'
    '    std::vector<ramm::Cell> rows = eval();\n'
    '    if (rows.empty() && wait_s > 0) {\n'
    '        auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(wait_s);\n'
    '        rows = MEM.wait(s, v, i, deadline, eval, [](const std::vector<ramm::Cell>& r) { return !r.empty(); });\n'
    '    }\n'
    '    std::vector<std::pair<uint64_t, std::string>> out; out.reserve(rows.size());\n'
    '    for (auto& c : rows) out.emplace_back(c.id, std::move(c.bag));\n'
    '    return out;\n'
    '}\n'
    'Reply lisp_op(const std::string& body) {')
# [SESSION_FREES_ITS_SOCKETS_V1] the data-socket sender captured the session by shared_ptr, and the session owns the
# sender: a cycle, so no session was ever freed, and its return and data sockets (shut down, never closed) stayed open --
# two descriptors per session, for every rlite-ctl call and every program. The host reached its open-file limit (1,024
# on John's AI-Host, in test 0024) and then could complete no new session ("the far end closed the connection").
# The sender now holds the session weakly.
rep('s->dsend.reset(new fnwp::dataplane::Sender([s](const std::string& rec) { std::lock_guard<std::mutex> g(s->data_m); send_all(s->data, rec); }));',
    '{ std::weak_ptr<Session> ws = s;\n'
    '          s->dsend.reset(new fnwp::dataplane::Sender([ws](const std::string& rec) { if (auto sp = ws.lock()) { std::lock_guard<std::mutex> g(sp->data_m); send_all(sp->data, rec); } })); }')
rep('struct Session {\n    int req = -1, ret = -1, data = -1;',
    'struct Session {\n    int req = -1, ret = -1, data = -1;\n'
    '    ~Session() { if (ret >= 0) FR_CLOSE(ret); if (data >= 0) FR_CLOSE(data); }   /* [SESSION_FREES_ITS_SOCKETS_V1] shut down at session end, closed when the last holder lets go */\n   ')
# the region links against these from outside the server's anonymous namespace
rep('}  // namespace\n\n// Bring the server up',
    '}  // namespace\n'
    'frogram::MemoryApi& rina_resident_memory() { return rina_resident_memory_i(); }\n'
    'std::vector<frogram::Cell> rina_resident_wait(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) { return rina_resident_wait_i(s, v, i, after, wait_s); }\n'
    'bool rina_resident_get_raw(const std::string& s, const std::string& v, const std::string& i, std::string& bag) { return rina_resident_get_raw_i(s, v, i, bag); }\n'
    'bool rina_resident_get_raw_id(const std::string& s, const std::string& v, const std::string& i, std::string& bag, uint64_t& id) { return rina_resident_get_raw_id_i(s, v, i, bag, id); }\n'
    'std::vector<std::pair<uint64_t, std::string>> rina_resident_wait_raw(const std::string& s, const std::string& v, const std::string& i, int64_t after, double wait_s) { return rina_resident_wait_raw_i(s, v, i, after, wait_s); }\n'
    '\n// Bring the server up')
open(p, 'w').write(s)
print("session hook applied")
