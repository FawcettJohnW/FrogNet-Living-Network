#!/usr/bin/env python3
"""[WIRE_ORDER_TURNS_V1] John 2026-09-29: "The C++ client must be functionally equivalent to the Python client. That is
the contract." -- the Python proxy's [REFERENCES_MOVE_IN_WIRE_ORDER_V1] gate, in frogram::Session.

Python: the reader stamps each reply with an arrival ticket for its instance; a caller applies its reply only when every
earlier ticket of that instance has been applied; replies to other instances never wait; the reader never waits. A
SAME miss is resolved inside its caller's turn, so the instance's later replies wait their turn.
C++ before: every reply was applied on the one reader thread, in arrival order, with no notion of a turn that stays open
across a round trip -- so a SAME miss could not be resolved in order (and the engine's pending_ wait, used for data
markers, blocks the reader, which would deadlock on an answer that arrives on the request socket).
C++ after: the reader stamps each reply of a templated instance with the instance's next ticket. If every earlier ticket
has been applied it applies it at once (the common case: unchanged cost); otherwise the reply is HELD for its instance
and the reader goes on reading. A ticket stays open while its call makes a round trip in its turn (a resend -- the SAME
miss, a REQ_MISS, a template refresh -- carries the ticket, and its reply applies at once); it closes when its answer
is applied, and closing it applies the held replies of that instance in ticket order. A data marker's ticket is stamped
when the marker arrives (wire order) and closes when its payload has landed and been applied -- the engine's blocking
pending_ wait is no longer used. Raw (untemplated) requests move no reference and take no ticket, as in Python.
Usage: apply_wire_order.py <ribbit_cpp dir>   (edits frogram.cpp; refuses unless every anchor is found once)"""
import os, sys
d = sys.argv[1]
def patch(rel, pairs):
    p = os.path.join(d, rel); s = open(p).read()
    for old, new in pairs:
        n = s.count(old)
        if n != 1: sys.exit("apply_wire_order: %s: anchor found %d times: %r" % (rel, n, old[:80]))
        s = s.replace(old, new)
    open(p, "w").write(s)
patch("frogram.cpp", [
    ("struct Session::InFlight { std::shared_ptr<fnwp::ClientEngine::Ctx> ctx; std::shared_ptr<Pending> waiter; };",
     "struct Session::InFlight { std::shared_ptr<fnwp::ClientEngine::Ctx> ctx; std::shared_ptr<Pending> waiter;\n"
     "                           uint64_t ticket = 0; bool in_turn = false; };   // [WIRE_ORDER_TURNS_V1]"),
    ("    struct Job { std::string method, path, body; std::shared_ptr<Pending> waiter; fnwp::ClientEngine::Out resend; bool is_resend = false; };",
     "    struct Job { std::string method, path, body; std::shared_ptr<Pending> waiter; fnwp::ClientEngine::Out resend; bool is_resend = false;\n"
     "                 uint64_t ticket = 0; bool in_turn = false; };   // [WIRE_ORDER_TURNS_V1] a resend made in its call's turn\n"
     "    // [WIRE_ORDER_TURNS_V1] per templated instance: tickets stamped (arrival order) and applied; replies held for their turn\n"
     "    std::mutex gate_m; std::map<fnwp::detail::IKey, uint64_t> issued, applied;\n"
     "    std::map<fnwp::detail::IKey, std::map<uint64_t, std::pair<Session::InFlight, std::string>>> held;"),
    ("          else { seq = d_->seq++; d_->inflight[seq] = Impl::InFlight{o.ctx, j.waiter}; } }",
     "          else { seq = d_->seq++; d_->inflight[seq] = Impl::InFlight{o.ctx, j.waiter, j.ticket, j.in_turn}; } }"),
    # the marker: its ticket is stamped on arrival; it is applied (in turn) when its payload lands
    ("                d_->eng->defer(inf.ctx);\n"
     "                std::lock_guard<std::mutex> g(d_->land_m);\n"
     "                d_->landers.emplace_back([this, inf, reply, seq] {\n"
     "                    try { apply_reply(inf, fnwp::ClientEngine::reply_from_marker(reply, d_->landed.take(seq))); }",
     "                stamp(inf);                               // [WIRE_ORDER_TURNS_V1] its place is its arrival\n"
     "                std::lock_guard<std::mutex> g(d_->land_m);\n"
     "                d_->landers.emplace_back([this, inf, reply, seq] {\n"
     "                    try { ready(inf, fnwp::ClientEngine::reply_from_marker(reply, d_->landed.take(seq))); }"),
    ("            apply_reply(inf, reply);\n        }\n    } catch (const std::exception& e) { why = e.what(); }",
     "            stamp(inf); ready(inf, reply);               // [WIRE_ORDER_TURNS_V1] in its turn, or held for it\n        }\n    } catch (const std::exception& e) { why = e.what(); }"),
    # a dead session also fails the replies held for their turn
    ("    for (auto& kv : lost) Impl::finish(kv.second.waiter, nullptr, d_->dead);",
     "    for (auto& kv : lost) Impl::finish(kv.second.waiter, nullptr, d_->dead);\n"
     "    { std::map<fnwp::detail::IKey, std::map<uint64_t, std::pair<InFlight, std::string>>> h;\n"
     "      { std::lock_guard<std::mutex> g(d_->gate_m); h.swap(d_->held); }\n"
     "      for (auto& i : h) for (auto& t : i.second) Impl::finish(t.second.first.waiter, nullptr, d_->dead); }"),
    # apply: the turn closes when the answer is applied; a resend keeps it open and carries it
    ("void Session::apply_reply(const InFlight& inf, const std::string& reply) {\n"
     "    try {\n"
     "        fnwp::ClientEngine::Step s = d_->eng->complete(inf.ctx, reply);",
     "// [WIRE_ORDER_TURNS_V1] the turn machinery: stamp on arrival; apply in turn or hold; close the turn, apply the held\n"
     "void Session::stamp(InFlight& inf) {\n"
     "    if (inf.in_turn || !inf.ctx || inf.ctx->raw) return;          // raw moves no reference: no turn (as in Python)\n"
     "    std::lock_guard<std::mutex> g(d_->gate_m); inf.ticket = ++d_->issued[inf.ctx->ik];\n"
     "}\n"
     "void Session::ready(const InFlight& inf, const std::string& reply) {\n"
     "    if (inf.ticket && !inf.in_turn) {\n"
     "        std::lock_guard<std::mutex> g(d_->gate_m);\n"
     "        if (d_->applied[inf.ctx->ik] != inf.ticket - 1) { d_->held[inf.ctx->ik].emplace(inf.ticket, std::make_pair(inf, reply)); return; }\n"
     "    }\n"
     "    apply_reply(inf, reply);\n"
     "}\n"
     "void Session::turn_done(const InFlight& inf) {\n"
     "    if (!inf.ticket) return;\n"
     "    std::optional<std::pair<InFlight, std::string>> next;\n"
     "    { std::lock_guard<std::mutex> g(d_->gate_m);\n"
     "      uint64_t a = ++d_->applied[inf.ctx->ik];\n"
     "      auto h = d_->held.find(inf.ctx->ik);\n"
     "      if (h != d_->held.end()) { auto t = h->second.find(a + 1); if (t != h->second.end()) { next = std::move(t->second); h->second.erase(t); } } }\n"
     "    if (next) apply_reply(next->first, next->second);                // the next ticket's turn\n"
     "}\n"
     "void Session::apply_reply(const InFlight& inf, const std::string& reply) {\n"
     "    try {\n"
     "        fnwp::ClientEngine::Step s = d_->eng->complete(inf.ctx, reply);"),
    ("            Impl::finish(inf.waiter, &s.answer, \"\");\n"
     "            return;\n"
     "        }\n"
     "        Impl::Job j; j.is_resend = true; j.resend = s.resend; j.waiter = inf.waiter;",
     "            Impl::finish(inf.waiter, &s.answer, \"\");\n"
     "            turn_done(inf);\n"
     "            return;\n"
     "        }\n"
     "        Impl::Job j; j.is_resend = true; j.resend = s.resend; j.waiter = inf.waiter;\n"
     "        j.ticket = inf.ticket; j.in_turn = inf.ticket != 0;          // the round trip happens in this call's turn"),
    ("        Impl::finish(inf.waiter, nullptr, std::string(\"frogram: \") + e.what());\n    }\n}\nvoid Session::read_loop",
     "        Impl::finish(inf.waiter, nullptr, std::string(\"frogram: \") + e.what());\n        turn_done(inf);\n    }\n}\nvoid Session::read_loop"),
])
patch("frogram.hpp", [
    ("    struct InFlight; void apply_reply(const InFlight& inf, const std::string& reply);",
     "    struct InFlight; void apply_reply(const InFlight& inf, const std::string& reply);\n"
     "    void stamp(InFlight& inf); void ready(const InFlight& inf, const std::string& reply); void turn_done(const InFlight& inf);   // [WIRE_ORDER_TURNS_V1]"),
])
print("apply_wire_order: applied")
