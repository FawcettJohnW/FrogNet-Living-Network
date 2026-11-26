// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_channel -- [THE_CALL_IS_UNDER_THE_FRAME_V1] planes under the protocol layer's frame.
//
//   1  two planes, both under the frame: bodies cross both ways while the Role's protocol, key and algorithm are
//      switched 24 times, the two ends' settings arriving at different moments. What a send() called Whole arrives,
//      once, in order, as it was sent. The receiver reads slowly at times, so the plane sheds and aborts frames:
//      a frame that was not Whole never arrives.
//   2  the end that accepted reads from the far end's first unit whether it speaks the frame: a plane with no frame is
//      spoken to as before.
//   3  through the media server: one participant's audio reaches another while the Role's setting is switched 8 times
//      under it (sealed, new keys, chacha20, chorus, openqasm, unsealed, sealed again). Every block arrives, in order;
//      the longest silence between two blocks is reported.
//   4  a participant with another secret, in a sealed call: it hears nothing and nothing of its is passed on.
//   5  a participant that does not speak the frame is not let into a call the server holds as sealed.
#include "comms/channel.hpp"
#include "media/server.hpp"
#include <cstdio>
#include <fstream>
#include <iostream>
#include <poll.h>
#include <random>
#include <sys/socket.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

using namespace fnav;
namespace cf = channel_frame;
static long fail = 0;
static void check(bool ok, const std::string& w) { std::cout << (ok ? "PASS  " : "FAIL  ") << w << "\n" << std::flush; if (!ok) ++fail; }
static std::mt19937_64 rng(20261124);
static const std::string ROLE = "mediahost";
static double now_s() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

static std::string secrets_dir(const std::string& name) {
    const std::string d = "/tmp/test_channel." + std::to_string(getpid()) + "." + name;
    mkdir(d.c_str(), 0700);
    std::ofstream f(d + "/default", std::ios::binary);
    for (int i = 0; i < 48; ++i) f.put(char(rng()));
    return d;
}
static cf::Setting setting(const std::string& driver, bool sealed, const std::string& protection = "aes-256-gcm") {
    cf::Setting s; s.driver = driver; s.number = sealed ? (rng() >> 2) + 1 : 0; s.protection = protection; s.step = 0;
    return s;
}
static std::map<std::string, std::string> fields_with_check(const cf::Setting& s, const std::string& dir) {
    std::map<std::string, std::string> f{{"driver", s.driver}};
    if (s.number) { f["number"] = std::to_string(s.number); f["protection"] = s.protection; }
    f["check"] = cf::check_of(cf::read_secret(ROLE, dir).value, ROLE, f);
    return f;
}
static Bytes body_of(uint8_t kind, const std::string& src, uint32_t n, size_t size) {
    Bytes p(4 + size);
    p[0] = uint8_t(n >> 24); p[1] = uint8_t(n >> 16); p[2] = uint8_t(n >> 8); p[3] = uint8_t(n);
    for (size_t i = 4; i < p.size(); ++i) p[i] = uint8_t(n * 31 + i * 7);
    return pack_typed(kind, src, p);
}
static uint32_t number_of(const Bytes& body) { const Typed t = unpack_typed(body); return uint32_t(t.payload[0]) << 24 | uint32_t(t.payload[1]) << 16 | uint32_t(t.payload[2]) << 8 | t.payload[3]; }

static std::pair<std::unique_ptr<Plane>, std::unique_ptr<Plane>> connected(int port, int rcvbuf = 0) {
    int lfd = Plane::listen_on("127.0.0.1", port);
    int a = Plane::connect_to("127.0.0.1", port, 2000);
    pollfd p{lfd, POLLIN, 0}; poll(&p, 1, 2000);
    int b = accept(lfd, nullptr, nullptr);
    close(lfd);
    if (rcvbuf) setsockopt(a, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf);
    return {std::make_unique<Plane>(a), std::make_unique<Plane>(b)};
}

// one direction of test 1: `from` sends N bodies, `to` reads them (slowly now and then); settings switch under it
struct OneWay {
    std::vector<uint32_t> whole, got; std::atomic<bool> done{false}; bool intact = true; long not_whole = 0;
    std::string ended;                                     // why the plane ended, when it did (a FAIL, said in its own words)
};
static void sender(Plane& from, OneWay& w, int n, uint8_t kind) {
    try {
    for (int i = 0; i < n; ++i) {
        const size_t size = (i % 7 == 3) ? 4000 : 200 + size_t(rng() % 1200);   // a plane's bodies are at most 8 KiB (VSEG_WHOLE_MAX)
        const Sent s = from.send(body_of(kind, "p", uint32_t(i), size), i % 5 == 0 ? SendKind::Video : SendKind::Audio);
        if (s == Sent::Whole) w.whole.push_back(uint32_t(i)); else w.not_whole++;
        if (getenv("TEST_CHANNEL_SAYS") && i % 300 == 299) std::fprintf(stderr, "%.3f sender i=%d: units sent %llu dropped %llu aborted %llu owed %zu room %ld last=%d\n", now_s(), i,
            (unsigned long long)from.stats.frames_sent, (unsigned long long)from.stats.dropped, (unsigned long long)from.stats.aborted_out, from.owed(), from.send_room(), int(s));
        std::this_thread::sleep_for(std::chrono::microseconds(1500));
    }
    } catch (const std::exception& e) { w.ended = e.what(); }
    w.done = true;
}
// A plane's two ends read for as long as the connection is open: a request to send again goes the other way. So
// neither reader stops until both senders are done and no body has arrived at either end for 1.5 s.
static std::atomic<double> last_body{0};
static void reader(Plane& to, OneWay& w, const OneWay& other, bool slow) {
    Bytes b; double said = now_s();
    last_body = now_s();
    try {
    while (!(w.done && other.done && now_s() - last_body > 1.5)) {
        if (getenv("TEST_CHANNEL_SAYS") && now_s() - said > 0.5) { said = now_s(); std::fprintf(stderr, "%.3f reader%s: units %llu aborted %llu taken %zu\n", said, slow ? " (stalls)" : "", (unsigned long long)to.stats.frames_recv, (unsigned long long)to.stats.aborted_in, w.got.size()); }
        const Recv r = to.recv(b, 50);
        if (r == Recv::Frame) {
            last_body = now_s();
            try { const uint32_t n = number_of(b); w.got.push_back(n); if (b != body_of(unpack_typed(b).kind, "p", n, unpack_typed(b).payload.size() - 4)) w.intact = false; }
            catch (const std::exception&) { w.intact = false; }
            if (slow && w.got.size() % 150 == 100) std::this_thread::sleep_for(std::chrono::milliseconds(450));   // the link stalls
        } else if (r == Recv::Closed) break;
    }
    } catch (const std::exception&) {}                     // the sender's side says why
}

int main() {
    const std::string dir = secrets_dir("a"), other = secrets_dir("b");
    setenv("FROGNET_CHANNEL_SECRETS", dir.c_str(), 1);

    // ---- 1
    {
        auto pq = connected(18941, 65536);
        auto& P = *pq.first; auto& Q = *pq.second;
        auto said = [](const char* who) { return [who](const std::string& t) { if (getenv("TEST_CHANNEL_SAYS")) std::fprintf(stderr, "%.3f %s: %s\n", now_s(), who, t.c_str()); }; };
        auto pc = std::make_shared<cf::Codec>(true, "Q", "node-P", dir, said("P")), qc = std::make_shared<cf::Codec>(false, "P", "node-Q", dir, said("Q"));
        P.set_channel(pc, ROLE); Q.set_channel(qc, ROLE, true);
        Q.set_debt_mode(true);                             // one end writes as the media server does: it never waits
        OneWay pq_way, qp_way;
        P.send(body_of(KIND_AUDIO, "p", 0xFFFFFFFF, 10), SendKind::Audio);                    // the opener names the Role's sequence
        { Bytes b; for (int i = 0; i < 40 && Q.recv(b, 50) != Recv::Frame; ++i) {} }
        std::thread r1([&] { reader(Q, pq_way, qp_way, false); }), r2([&] { reader(P, qp_way, pq_way, true); });   // P reads in stalls: Q, which never waits, sheds and aborts
        std::thread s1([&] { sender(P, pq_way, 3000, KIND_AUDIO); }), s2([&] { sender(Q, qp_way, 3000, KIND_AUDIO); });
        int switches = 0;
        const char* drivers[] = {"tcp", "chorus", "openqasm"};
        while (!pq_way.done || !qp_way.done) {
            cf::Setting s = setting(drivers[switches % 3], switches % 8 != 7, switches % 4 == 1 ? "chacha20-poly1305" : "aes-256-gcm");
            auto& first = switches % 2 ? pc : qc; auto& second = switches % 2 ? qc : pc;
            first->setting(ROLE, s);
            std::this_thread::sleep_for(std::chrono::milliseconds(20 + long(rng() % 120)));     // the other end's held read fires later
            second->setting(ROLE, s);
            ++switches;
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }
        s1.join(); s2.join(); r1.join(); r2.join();
        const auto kp = pc->counts(), kq = qc->counts();
        if (getenv("TEST_CHANNEL_SAYS"))
            std::fprintf(stderr, "P: sent %llu dropped %llu aborted_out %llu recv %llu aborted_in %llu | Q: sent %llu dropped %llu aborted_out %llu recv %llu aborted_in %llu owed %zu | P frame bytes in %llu out %llu, Q in %llu out %llu\n",
                (unsigned long long)P.stats.frames_sent, (unsigned long long)P.stats.dropped, (unsigned long long)P.stats.aborted_out, (unsigned long long)P.stats.frames_recv, (unsigned long long)P.stats.aborted_in,
                (unsigned long long)Q.stats.frames_sent, (unsigned long long)Q.stats.dropped, (unsigned long long)Q.stats.aborted_out, (unsigned long long)Q.stats.frames_recv, (unsigned long long)Q.stats.aborted_in, Q.owed(),
                (unsigned long long)kp.received, (unsigned long long)kp.sent, (unsigned long long)kq.received, (unsigned long long)kq.sent);
        auto differs = [](const char* which, const OneWay& w) {    // on a failure, say where the two lists part
            if (w.got == w.whole) return;
            size_t i = 0; while (i < w.got.size() && i < w.whole.size() && w.got[i] == w.whole[i]) ++i;
            std::set<uint32_t> g(w.got.begin(), w.got.end()), h(w.whole.begin(), w.whole.end());
            size_t missing = 0, extra = 0, twice = w.got.size() - g.size(); for (auto v : h) if (!g.count(v)) ++missing; for (auto v : g) if (!h.count(v)) ++extra;
            std::fprintf(stderr, "  %s: the lists part at place %zu (whole has %ld there, arrived has %ld); sent whole and never arrived %zu, arrived and not sent whole %zu, arrived twice %zu, in order %d; last whole %ld, last arrived %ld\n",
                which, i, i < w.whole.size() ? long(w.whole[i]) : -1L, i < w.got.size() ? long(w.got[i]) : -1L, missing, extra, twice, int(std::is_sorted(w.got.begin(), w.got.end())),
                w.whole.empty() ? -1L : long(w.whole.back()), w.got.empty() ? -1L : long(w.got.back()));
        };
        differs("P to Q", pq_way); differs("Q to P", qp_way);
        for (const OneWay* w : {&pq_way, &qp_way}) if (!w->ended.empty()) std::fprintf(stderr, "  a plane ended under the test: %s\n", w->ended.c_str());
        check(pq_way.ended.empty() && qp_way.ended.empty() && pq_way.got == pq_way.whole && pq_way.intact && qp_way.got == qp_way.whole && qp_way.intact && switches >= 12,
              "1 two planes under the frame, " + std::to_string(switches) + " switches of protocol, key and algorithm under the stream: of 3000 bodies each way, "
              + std::to_string(pq_way.whole.size()) + " and " + std::to_string(qp_way.whole.size()) + " were sent whole and exactly those arrived, in order, intact ("
              + std::to_string(pq_way.got.size()) + " and " + std::to_string(qp_way.got.size()) + " arrived; shed or aborted and taken back: " + std::to_string(pq_way.not_whole) + " and "
              + std::to_string(qp_way.not_whole) + "; sent again after a late setting: " + std::to_string(kp.sent_again) + " and " + std::to_string(kq.sent_again) + ")");
    }
    if (getenv("TEST_CHANNEL_FIRST_ONLY")) { std::printf("RESULT %s (the first check only)\n", fail ? "FAIL" : "PASS"); return fail ? 1 : 0; }
    // ---- 2
    {
        auto pq = connected(18942);
        auto& P = *pq.first; auto& Q = *pq.second;
        Q.set_channel(std::make_shared<cf::Codec>(false, "P", "node-Q", dir), ROLE, true);
        Bytes decl{PLANE_AUDIO}; const Bytes hello = pack_typed(KIND_PLANE, "old", decl);
        P.send(hello, SendKind::Control);
        Bytes b; Recv r = Recv::Timeout; for (int i = 0; i < 40 && r != Recv::Frame; ++i) r = Q.recv(b, 50);
        bool ok = r == Recv::Frame && b == hello && !Q.framed();
        Q.send(body_of(KIND_AUDIO, "q", 7, 100), SendKind::Audio);
        r = Recv::Timeout; for (int i = 0; i < 40 && r != Recv::Frame; ++i) r = P.recv(b, 50);
        check(ok && r == Recv::Frame && b == body_of(KIND_AUDIO, "q", 7, 100), "2 a far end that does not speak the frame: the accepting plane reads that from its first unit and speaks to it as before");
    }
    // ---- 3, 4, 5: through the media server
    {
        const int PORT = 18943;
        auto ch = std::make_shared<comms::Channel>(ROLE, "node-media", dir);
        comms::Channel::set_for_process(ch);               // the server's planes, and this test's, take their codecs from it
        media::Server srv("127.0.0.1", PORT);
        std::thread th([&] { srv.run(); });
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        auto join = [&](const std::string& name, const std::string& tag, std::shared_ptr<cf::Codec> codec) {
            auto p = std::make_unique<Plane>(Plane::connect_to("127.0.0.1", PORT, 2000));
            if (codec) p->set_channel(codec, ROLE);
            Bytes decl{PLANE_AUDIO}; decl.insert(decl.end(), tag.begin(), tag.end()); decl.insert(decl.end(), {'S', '1'});
            p->send(pack_typed(KIND_PLANE, name, decl), SendKind::Control);
            return p;
        };
        auto john = join("John", "jjjjjjjj", ch->codec(true, "media")), donna = join("Donna", "dddddddd", ch->codec(true, "media"));
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        std::atomic<bool> done{false};
        std::vector<uint32_t> got; std::vector<double> at; bool intact = true;
        std::thread rd([&] { Bytes b; while (!done) if (donna->recv(b, 50) == Recv::Frame) { try { got.push_back(number_of(b)); at.push_back(now_s()); if (b != body_of(KIND_AUDIO, "John", got.back(), 160)) intact = false; } catch (const std::exception&) { intact = false; } } });
        std::thread rj([&] { Bytes b; while (!done) john->recv(b, 50); });
        std::vector<double> switched; std::vector<std::string> what;
        std::thread admin([&] {                             // the owner's writes, as each process's held read delivers them
            const std::vector<std::pair<std::string, cf::Setting>> steps = {
                {"sealed", setting("tcp", true)}, {"a new key", setting("tcp", true)}, {"chacha20-poly1305", setting("tcp", true, "chacha20-poly1305")},
                {"chorus", setting("chorus", true, "chacha20-poly1305")}, {"openqasm", setting("openqasm", true, "chacha20-poly1305")},
                {"not sealed", setting("openqasm", false)}, {"sealed again, tcp", setting("tcp", true)}, {"a new key", setting("tcp", true)}};
            for (auto& s : steps) {
                std::this_thread::sleep_for(std::chrono::milliseconds(700));
                switched.push_back(now_s()); what.push_back(s.first);
                ch->read(fields_with_check(s.second, dir));
            }
        });
        std::vector<uint32_t> whole;
        const int BLOCKS = 350;                             // 20 ms blocks: 7 s of audio
        for (int i = 0; i < BLOCKS; ++i) {
            if (john->send(body_of(KIND_AUDIO, "John", uint32_t(i), 160), SendKind::Audio) == Sent::Whole) whole.push_back(uint32_t(i));
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        admin.join();
        for (int i = 0; i < 100 && got.size() < whole.size(); ++i) std::this_thread::sleep_for(std::chrono::milliseconds(20));
        double worst = 0, worst_at = 0;
        for (size_t i = 1; i < at.size(); ++i) if (at[i] - at[i - 1] > worst) { worst = at[i] - at[i - 1]; worst_at = at[i - 1]; }
        std::string near = "no switch";
        for (size_t i = 0; i < switched.size(); ++i) if (worst_at >= switched[i] - 0.05 && worst_at <= switched[i] + 0.3) near = what[i];
        char ms[64]; std::snprintf(ms, sizeof ms, "%.0f", worst * 1000);
        check(got == whole && intact && int(whole.size()) == BLOCKS && ch->changes() == 8,
              "3 through the media server, 8 switches under 7 s of audio (sealed, new key, chacha20, chorus, openqasm, not sealed, sealed, new key): "
              + std::to_string(got.size()) + " of " + std::to_string(BLOCKS) + " blocks arrived, in order, intact; the longest gap between two blocks was "
              + ms + " ms (blocks are sent 20 ms apart), at: " + near);

        // 4: a participant whose machine holds another secret
        auto eve = join("Eve", "eeeeeeee", std::make_shared<cf::Codec>(true, "media", "node-eve", other));
        eve->channel()->setting(ROLE, setting("tcp", true));          // it names itself sealed, under a key from its own secret
        std::atomic<long> eve_heard{0};
        std::thread re([&] { Bytes b; while (!done) if (eve->recv(b, 50) == Recv::Frame) eve_heard++; });
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        const size_t before = got.size();
        for (int i = 0; i < 25; ++i) {
            eve->send(body_of(KIND_AUDIO, "Eve", 900000 + uint32_t(i), 160), SendKind::Audio);
            john->send(body_of(KIND_AUDIO, "John", uint32_t(BLOCKS + i), 160), SendKind::Audio);
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(400));
        bool from_eve = false; for (size_t i = before; i < got.size(); ++i) if (got[i] >= 900000) from_eve = true;
        check(eve_heard == 0 && !from_eve && got.size() == before + 25,
              "4 a participant whose machine holds another secret, in the sealed call: it heard " + std::to_string(eve_heard.load()) + " blocks, and of its 25 blocks "
              + (from_eve ? "some were" : "none was") + " passed on (the others still heard each other: " + std::to_string(got.size() - before) + " of 25)");

        // 5: a participant with no frame at all
        auto old = join("Old", "oooooooo", nullptr);
        Bytes b; Recv r = Recv::Timeout; bool closed = false;
        try { for (int i = 0; i < 40 && !closed; ++i) { r = old->recv(b, 50); closed = r == Recv::Closed; } } catch (const ConnectionError&) { closed = true; }
        check(closed, "5 a participant that does not speak the frame is not let into the call while the server holds the Role as sealed: its connection is closed");
        done = true; rd.join(); rj.join(); re.join();
        srv.stop(); th.join();
    }
    std::cout << (fail ? "RESULT FAIL: " + std::to_string(fail) : std::string("RESULT PASS")) << "  (" << cf::BUILD << ")\n";
    return fail ? 1 : 0;
}
