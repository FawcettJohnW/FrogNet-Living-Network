#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[NOBODY_WAITS_V1] frogram::Session::call_async: the same call as call_bytes -- same engine, same semantic wire, same
permanent and data sockets, same seq-tagged replies -- that returns at once. The frame is prepared and put on the
request socket by the CALLER's thread (no hand-off to the sender thread); the answer is handed to done() on the thread
that reads it off the return socket (no hand-off back). Nothing waits.

John 2026-10-14: "There's nothing requiring us to wait, is there?" There is not: the session already matches replies to
requests by seq; only its blocking surface made callers wait.

The sender thread stays for what it already does (an engine resend; Session::post). Frames from both leave in the order
their request references were committed: preparing and sending is one step under one lock (out_m) on both paths.

Usage: apply_call_async.py <ribbit_cpp dir>   (edits frogram.hpp and frogram.cpp; refuses unless every anchor is found once)"""
import os, sys
d = sys.argv[1]
def patch(rel, pairs):
    p = os.path.join(d, rel); s = open(p).read()
    for old, new in pairs:
        if s.count(old) != 1: sys.exit("apply_call_async: anchor in %s found %d times: %r" % (rel, s.count(old), old[:70]))
        s = s.replace(old, new)
    open(p, "w").write(s)

patch("frogram.hpp", [
("#include <condition_variable>\n", "#include <condition_variable>\n#include <functional>\n"),
('''    void post(const std::string& method, const std::string& path, const std::string& body = "");''',
'''    void post(const std::string& method, const std::string& path, const std::string& body = "");
    // [NOBODY_WAITS_V1] the call, returning at once: the frame leaves on this thread; done(status, body, err) runs on the
    // thread that reads the answer (err non-empty: the session is gone, or the call could not be made -- status 0)
    using Done = std::function<void(int status, std::string body, std::string err)>;
    void call_async(const std::string& method, const std::string& path, const std::string& body, Done done);'''),
])

patch("frogram.cpp", [
# the pending call may carry a completion instead of a waiter
('''struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; fnwp::Answer answer; std::string err;''',
 '''struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; fnwp::Answer answer; std::string err;
                          Session::Done cb;                                       // [NOBODY_WAITS_V1] instead of a waiter'''),
('''        { std::lock_guard<std::mutex> g(p->m); if (a) p->answer = *a; p->err = err; p->done = true; } p->cv.notify_all();''',
 '''        Session::Done cb;
        { std::lock_guard<std::mutex> g(p->m); if (p->done) return; if (a) p->answer = *a; p->err = err; p->done = true; cb = std::move(p->cb); }
        if (cb) { cb(a ? a->status : 0, a ? a->body : std::string(), err); return; }   // [NOBODY_WAITS_V1]
        p->cv.notify_all();'''),
# one lock orders prepare-and-send on both paths
('''    std::mutex st_m; Stats st; std::thread sender, reader, data_reader;''',
 '''    std::mutex st_m; Stats st; std::thread sender, reader, data_reader;
    std::mutex out_m;                                    // [NOBODY_WAITS_V1] prepare and send are one step, on any thread'''),
('''        d_->room_cv.notify_one();
        fnwp::ClientEngine::Out o;
        try { o = j.is_resend ? j.resend : d_->eng->prepare(j.method, j.path, j.body); }
        catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); if (!j.waiter) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posts_refused; } continue; }
        std::string why;''',
 '''        d_->room_cv.notify_one();
        std::string why = send_job(&j);
        if (!why.empty()) Impl::finish(j.waiter, nullptr, why);
    }
}
// [NOBODY_WAITS_V1] prepare one frame and put it on the wire, under out_m; returns why it could not ("" when sent)
std::string Session::send_job(void* job) {
    Impl::Job& j = *static_cast<Impl::Job*>(job);
    std::lock_guard<std::mutex> out(d_->out_m);
    {
        fnwp::ClientEngine::Out o;
        try { o = j.is_resend ? j.resend : d_->eng->prepare(j.method, j.path, j.body); }
        catch (const std::exception& e) { if (!j.waiter) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posts_refused; } return std::string("frogram: ") + e.what(); }
        std::string why;'''),
('''        if (uint8_t(o.frame[4]) == semwire::OP_REQ_RAW && o.frame.size() > d_->ceil.whole_max()) {
            try { frame = fnwp::ClientEngine::request_marker(o.frame, payload); via_data = true; }
            catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); continue; }
        }''',
 '''        if (uint8_t(o.frame[4]) == semwire::OP_REQ_RAW && o.frame.size() > d_->ceil.whole_max()) {
            try { frame = fnwp::ClientEngine::request_marker(o.frame, payload); via_data = true; }
            catch (const std::exception& e) { return std::string("frogram: ") + e.what(); }
        }'''),
('''            } catch (const std::exception& e) { why = e.what(); }
        }
        if (!why.empty()) Impl::finish(j.waiter, nullptr, why);   // the connection is gone
    }
}''',
 '''            } catch (const std::exception& e) { why = e.what(); }
        }
        if (!why.empty() && seq_sent) { std::lock_guard<std::mutex> g(d_->pend_m); d_->inflight.erase(seq); }   // the connection is gone
        return why;
    }
}
void Session::call_async(const std::string& method, const std::string& path, const std::string& body, Done done) {
    auto p = std::make_shared<Pending>(); p->cb = std::move(done);
    Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p;
    std::string why = send_job(&j);
    if (!why.empty()) Impl::finish(p, nullptr, why);     // outside out_m: done() may call again
}'''),
('''          else { seq = d_->seq++; d_->inflight[seq] = Impl::InFlight{o.ctx, j.waiter, j.ticket, j.in_turn}; } }''',
 '''          else { seq = d_->seq++; d_->inflight[seq] = Impl::InFlight{o.ctx, j.waiter, j.ticket, j.in_turn}; seq_sent = true; } }'''),
('''        uint32_t seq = 0;
        { std::lock_guard<std::mutex> g(d_->pend_m);''',
 '''        uint32_t seq = 0; bool seq_sent = false;
        { std::lock_guard<std::mutex> g(d_->pend_m);'''),
])
patch("frogram.hpp", [
('''    struct Pending; struct Impl;''',
 '''    struct Pending; struct Impl;
    std::string send_job(void* job);                     // [NOBODY_WAITS_V1] an Impl::Job: prepare and send it'''),
])
print("apply_call_async: Session::call_async added")
