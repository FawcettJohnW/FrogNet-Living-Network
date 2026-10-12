"""apply_client_landing_lockless.py FROGRAM_CPP -- [CLIENT_LANDING_LOCKLESS_V1] no lander threads, no land_m, no dat_m.

Before: every reply that came as a marker (its payload on the data socket) got its own thread, kept in a vector under
a mutex (land_m), which blocked in Landed::take() until the payload landed and then handed the full reply on. The data
socket's single sender also took a mutex (dat_m) that nothing else ever took.

Now: the data reader hands each landed payload straight to the applier (kind 4, by sequence number); the marker reached
the applier as kind 1 in its wire place. Whichever of the two the applier sees second completes the reply -- payloads can
land before their marker is read (two sockets, two readers). The data socket keeps its one writer and loses the lock.
Applied at build after apply_client_coalesce_lockless.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

rep('''    fnwp::dataplane::Landed landed; std::mutex dat_m;
    std::mutex land_m; std::vector<std::thread> landers;   // threads applying marker replies as their payloads land''',
    '''    fnwp::dataplane::Landed landed;                      // (request-side Landed bookkeeping stays; the client no longer take()s from it)
    // [CLIENT_LANDING_LOCKLESS_V1] the applier pairs a marker (its wire place) with its payload (its landing), either order
    std::map<uint32_t, std::string> marker_reply, early_payload;''')
rep('''    d_->dsend.reset(new fnwp::dataplane::Sender([this](const std::string& rec) { std::lock_guard<std::mutex> g(d_->dat_m); send_all(dat_, rec); }));''',
    '''    d_->dsend.reset(new fnwp::dataplane::Sender([this](const std::string& rec) { send_all(dat_, rec); }));   // its one writer: no lock''')
rep('''    { std::lock_guard<std::mutex> g(d_->land_m); for (auto& t : d_->landers) if (t.joinable()) t.join(); }''',
    '''    // [CLIENT_LANDING_LOCKLESS_V1] no lander threads to join''')
rep('''                d_->landed.put(done->first, std::move(done->second));
                continue;''',
    '''                d_->to_applier(4, Session::InFlight{}, std::move(done->second), done->first);   // [CLIENT_LANDING_LOCKLESS_V1]
                continue;''')
rep('''                d_->to_applier(1, inf, reply, seq);      // [CLIENT_APPLIER_V1] its place is its arrival
                std::lock_guard<std::mutex> g(d_->land_m);
                d_->landers.emplace_back([this, inf, reply, seq] {
                    try { d_->to_applier(2, inf, fnwp::ClientEngine::reply_from_marker(reply, d_->landed.take(seq)), seq); }
                    catch (const std::exception& e) { Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what()); }
                });
                continue;''',
    '''                d_->to_applier(1, inf, reply, seq);      // [CLIENT_APPLIER_V1] its place is its arrival; the payload comes as kind 4
                continue;''')
# the applier: kind 1 keeps the marker reply; kind 4 brings the payload; the second of the two completes it as kind 2 did
rep('''            else if (n->kind == 1) { stamp(n->inf); d_->eng->defer(n->inf.ctx); if (n->inf.ctx && !n->inf.ctx->raw) d_->landing.insert(n->inf.ctx->ik);
                                     d_->marked[n->seq] = n->inf; }   // the stamped call: its landed reply is applied as this one''',
    '''            else if (n->kind == 1) { stamp(n->inf); d_->eng->defer(n->inf.ctx); if (n->inf.ctx && !n->inf.ctx->raw) d_->landing.insert(n->inf.ctx->ik);
                                     d_->marked[n->seq] = n->inf;     // the stamped call: its landed reply is applied as this one
                                     auto e = d_->early_payload.find(n->seq);   // [CLIENT_LANDING_LOCKLESS_V1] payload already here
                                     if (e == d_->early_payload.end()) d_->marker_reply[n->seq] = n->reply;
                                     else { std::string full = fnwp::ClientEngine::reply_from_marker(n->reply, e->second); d_->early_payload.erase(e);
                                            InFlight inf = d_->marked[n->seq]; d_->marked.erase(n->seq); ready(inf, full); } }
            else if (n->kind == 4) { auto mr = d_->marker_reply.find(n->seq);   // [CLIENT_LANDING_LOCKLESS_V1] a payload landed
                                     if (mr == d_->marker_reply.end()) d_->early_payload[n->seq] = std::move(n->reply);
                                     else { std::string full = fnwp::ClientEngine::reply_from_marker(mr->second, n->reply); d_->marker_reply.erase(mr);
                                            auto m = d_->marked.find(n->seq); InFlight inf = m->second; d_->marked.erase(m); ready(inf, full); } }''')
rep('''                d_->after_landing.clear(); d_->landing.clear();''',
    '''                d_->after_landing.clear(); d_->landing.clear();
                for (auto& m : d_->marked) Impl::finish(m.second.waiter, nullptr, d_->dead_why());   // a payload that will not land
                d_->marked.clear(); d_->marker_reply.clear(); d_->early_payload.clear();''')
open(p, 'w').write(s)
print("client landing lockless applied")
