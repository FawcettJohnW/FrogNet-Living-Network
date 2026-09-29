#!/usr/bin/env python3
"""[CLIENT_COALESCES_V1] John 2026-09-29: "The thing needs to coalesce the same way the Python client does. If there is
a request in flight, the new request waits at the proxy and is served by the single response."
frogram::Session::call: a request identical to one already sent and not yet answered (same method, path and body --
the request the Python proxy hashes for _coalesced_rpc) is not sent; it waits and is served by that one response. The
entry leaves the table BEFORE its waiters are woken, so only requests parked while it was in flight share its answer.
post() (send and forget) does not coalesce. Stats gain `coalesced`.
Usage: apply_coalesce.py <ribbit_cpp dir>   (edits frogram.hpp and frogram.cpp; refuses unless every anchor is found once)"""
import os, sys
d = sys.argv[1]
def patch(rel, pairs):
    p = os.path.join(d, rel); s = open(p).read()
    for old, new in pairs:
        n = s.count(old)
        if n != 1: sys.exit("apply_coalesce: %s: anchor found %d times: %r" % (rel, n, old[:80]))
        s = s.replace(old, new)
    open(p, "w").write(s)
patch("frogram.hpp", [
    ("struct Stats { uint64_t raw = 0, repeat = 0, same = 0, miss = 0,",
     "struct Stats { uint64_t coalesced = 0;   // [CLIENT_COALESCES_V1] calls served by an identical request's one response\n"
     "                uint64_t raw = 0, repeat = 0, same = 0, miss = 0,"),
])
patch("frogram.cpp", [
    ("struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; fnwp::Answer answer; std::string err; };",
     "struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; fnwp::Answer answer; std::string err;\n"
     "                          Session::Impl* co = nullptr; std::string co_key; };   // [CLIENT_COALESCES_V1] its entry, if any"),
    ("    static void finish(const std::shared_ptr<Pending>& p, const fnwp::Answer* a, const std::string& err) {\n"
     "        if (!p) return;\n",
     "    // [CLIENT_COALESCES_V1] requests in flight, by the exact request: an identical call waits here for the one answer\n"
     "    std::mutex co_m; std::map<std::string, std::shared_ptr<Pending>> coalesce;\n"
     "    static void finish(const std::shared_ptr<Pending>& p, const fnwp::Answer* a, const std::string& err) {\n"
     "        if (!p) return;\n"
     "        if (p->co) {   // out of the table first: a request made after this answer is its own request\n"
     "            std::lock_guard<std::mutex> g(p->co->co_m);\n"
     "            auto it = p->co->coalesce.find(p->co_key); if (it != p->co->coalesce.end() && it->second == p) p->co->coalesce.erase(it);\n"
     "        }\n"),
    ("    ++tl_calls;\n    auto p = std::make_shared<Pending>();\n    { std::unique_lock<std::mutex> l(d_->q_m);",
     "    ++tl_calls;\n"
     "    std::string key; key.reserve(method.size() + path.size() + body.size() + 2);\n"
     "    key += method; key += '\\0'; key += path; key += '\\0'; key += body;\n"
     "    std::shared_ptr<Pending> p; bool joined = false;\n"
     "    {   std::lock_guard<std::mutex> g(d_->co_m);\n"
     "        auto it = d_->coalesce.find(key);\n"
     "        if (it != d_->coalesce.end()) { p = it->second; joined = true; }\n"
     "        else { p = std::make_shared<Pending>(); p->co = d_.get(); p->co_key = key; d_->coalesce.emplace(key, p); }\n"
     "    }\n"
     "    if (joined) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.coalesced; }\n"
     "    if (!joined)\n"
     "    { std::unique_lock<std::mutex> l(d_->q_m);"),
    # a call that cannot be sent takes its entry out and wakes whoever joined it, with the same error
    ("      { std::lock_guard<std::mutex> g(d_->pend_m); if (!d_->dead.empty()) throw Unreachable(d_->dead); }\n"
     "      d_->room_cv.wait(l, [&] { return d_->stop || d_->jobs.size() < Impl::QUEUE_MAX; });\n"
     "      if (d_->stop) throw Unreachable(\"session shut down\");\n"
     "      Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p; d_->jobs.push_back(std::move(j)); }",
     "      { std::string dead; { std::lock_guard<std::mutex> g(d_->pend_m); dead = d_->dead; }\n"
     "        if (!dead.empty()) { Impl::finish(p, nullptr, dead); throw Unreachable(dead); } }\n"
     "      d_->room_cv.wait(l, [&] { return d_->stop || d_->jobs.size() < Impl::QUEUE_MAX; });\n"
     "      if (d_->stop) { Impl::finish(p, nullptr, \"session shut down\"); throw Unreachable(\"session shut down\"); }\n"
     "      Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p; d_->jobs.push_back(std::move(j)); }"),
])
print("apply_coalesce: applied")
