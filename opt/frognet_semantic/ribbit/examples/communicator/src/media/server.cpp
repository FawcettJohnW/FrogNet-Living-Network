// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "server.hpp"
#include <algorithm>
#include <cmath>
#include <arpa/inet.h>
#include <cstdio>
#include <netinet/in.h>
#include <poll.h>
#include <sstream>
#include <sys/socket.h>
#include <unistd.h>

namespace media {

using namespace fnav;
static double secs(Clock::time_point a, Clock::time_point b) { return std::chrono::duration<double>(b - a).count(); }

Server::Server(const std::string& host, int port) : lfd_(Plane::listen_on(host, port)) {}
Server::~Server() {
    stop();
    for (auto& t : threads_) if (t.joinable()) t.join();
    ::close(lfd_);
}
void Server::stop() {
    stop_ = true;
    std::lock_guard<std::mutex> g(mu_);
    for (auto& c : conns_) if (c->plane) c->plane->shutdown_both();
}

void Server::run() {
    std::printf("[media] listening\n"); std::fflush(stdout);
    threads_.emplace_back([this] {                         // the per-viewer link controller, one window at a time
        while (!stop_) {
            std::this_thread::sleep_for(std::chrono::milliseconds(int(WINDOW_S * 1000)));
            control_tick();
        }
    });
    while (!stop_) {
        pollfd p{lfd_, POLLIN, 0};
        if (poll(&p, 1, 200) <= 0) continue;
        sockaddr_in a{}; socklen_t l = sizeof a;
        int fd = accept(lfd_, reinterpret_cast<sockaddr*>(&a), &l);
        if (fd < 0) continue;
        auto c = std::make_shared<Conn>();
        c->plane = std::make_unique<Plane>(fd);
        c->plane->set_debt_mode(true);                     // a viewer never makes a sender's thread wait
        char ip[64]; inet_ntop(AF_INET, &a.sin_addr, ip, sizeof ip);
        c->peer = std::string(ip) + ":" + std::to_string(ntohs(a.sin_port));
        { std::lock_guard<std::mutex> g(mu_); conns_.push_back(c); }
        threads_.emplace_back([this, c] { pump(c); });
    }
}

void Server::declare(const std::shared_ptr<Conn>& c, const Typed& t) {
    std::lock_guard<std::mutex> g(mu_);
    c->name = t.src;
    c->kind = (!t.payload.empty() && t.payload[0] == PLANE_VIDEO) ? PLANE_VIDEO : PLANE_AUDIO;
    if (t.payload.size() >= 9) c->tag.assign(t.payload.begin() + 1, t.payload.begin() + 9);
    if (t.payload.size() > 9) c->session.assign(t.payload.begin() + 9, t.payload.end());
    std::printf("[media] %s declared %s plane (%s) session=%s\n", c->name.c_str(),
                c->kind == PLANE_VIDEO ? "video" : "audio", c->peer.c_str(), c->session.empty() ? "NONE" : c->session.c_str());
    std::fflush(stdout);
}

std::vector<std::shared_ptr<Conn>> Server::peers_of(const std::shared_ptr<Conn>& c, uint8_t kind) {
    std::vector<std::shared_ptr<Conn>> out;
    if (c->session.empty()) return out;                    // [FAN_IS_PER_SESSION_V1]
    std::lock_guard<std::mutex> g(mu_);
    for (auto& o : conns_)
        if (o != c && !o->dead && o->kind == kind && o->session == c->session && o->name != c->name) out.push_back(o);
    return out;
}

void Server::pump(std::shared_ptr<Conn> c) {
    Bytes body;
    try {
        while (!stop_) {
            Recv r = c->plane->recv(body, 500);
            if (r == Recv::Timeout) continue;
            if (r == Recv::Closed) break;
            if (r == Recv::Aborted) continue;              // one frame lost, the stream intact
            Typed t;
            try { t = unpack_typed(body); } catch (const FrameError&) { continue; }
            if (c->kind == 0) {                            // the first frame declares the plane
                if (t.kind == KIND_PLANE) { declare(c, t); continue; }
                declare(c, Typed{KIND_PLANE, t.src, Bytes{PLANE_AUDIO}});   // undeclared: audio, isolated
            }
            if (c->kind == PLANE_AUDIO && t.kind == KIND_AUDIO) fan_audio(c, body);
            else if (c->kind == PLANE_VIDEO && t.kind == KIND_VIDEO) {
                try { fan_video(c, t.payload, unpack_video(t.payload).key); } catch (const FrameError&) {}
            } else if (c->kind == PLANE_VIDEO && t.kind == KIND_VSEG) {
                Bytes vp;
                if (c->reasm.feed(c->name, t.payload, vp)) {
                    try { fan_video(c, vp, unpack_video(vp).key); } catch (const FrameError&) {}
                }
            }
        }
    } catch (const ConnectionError& e) {
        std::printf("[media] %s %s plane ended: %s\n", c->name.c_str(), c->kind == PLANE_VIDEO ? "video" : "audio", e.what());
    }
    teardown(c);
}

void Server::fan_audio(const std::shared_ptr<Conn>& from, const Bytes& body) {
    for (auto& to : peers_of(from, PLANE_AUDIO)) {
        to->audio_aimed++;
        try { if (to->plane->send(body, SendKind::Audio) == Sent::Whole) to->audio_delivered++; }
        catch (const ConnectionError& e) {
            std::printf("[media] %s: audio connection broken (%s); closing it\n", to->name.c_str(), e.what());
            to->plane->shutdown_both();
        }
    }
}

int Server::target_for_cap(long bps) {
    if (bps <= 0) return -1;
    for (size_t i = 0; i < SIZES.size(); ++i)
        if (SIZES[i].bitrate + AUDIO_BPS <= bps) return int(i);
    return int(SIZES.size());                              // not even the floor: audio only
}

void Server::set_cap(const std::string& viewer, long bps) {
    std::lock_guard<std::mutex> g(mu_);
    for (auto& c : conns_)
        if (c->name == viewer && c->kind == PLANE_VIDEO && !c->dead) {
            std::lock_guard<std::mutex> v(c->vmu);
            c->view.cap_bps = bps;
            retarget(c->view);
        }
}

// The target is the smaller picture of the cap's and the link's ("smaller" = the higher index; -1 = no limit).
void Server::retarget(Viewer& v) {
    int cap = target_for_cap(v.cap_bps), link = v.link_target;
    if (cap < 0) v.target = link;
    else if (link < 0) v.target = cap;
    else v.target = std::max(cap, link);
}

void Server::control_tick() {
    std::vector<std::shared_ptr<Conn>> vs;
    { std::lock_guard<std::mutex> g(mu_); for (auto& c : conns_) if (c->kind == PLANE_VIDEO && !c->dead) vs.push_back(c); }
    auto now = Clock::now();
    for (auto& c : vs) {
        std::lock_guard<std::mutex> g(c->vmu);
        Viewer& v = c->view;
        uint64_t aimed = v.aimed - v.w_aimed0, deliv = v.delivered - v.w_deliv0;
        v.w_aimed0 = v.aimed; v.w_deliv0 = v.delivered;
        if (aimed == 0) continue;                          // [ABSENCE_IS_NOT_A_MEASUREMENT_V1] nothing sent, nothing learned
        if (now < v.judge_after) continue;                 // a step needs time to be felt
        double ratio = double(deliv) / double(aimed);
        v.last_ratio = ratio;
        // the size the viewer is actually on: its transcoded size, or the upload's
        int on = v.stream >= 0 ? v.stream : -1;
        if (ratio < HAPPY_FRACTION) {
            v.good_windows = 0;
            if (++v.bad_windows < BAD_WINDOWS) continue;
            v.bad_windows = 0;
            // step down one size from where it is (from the upload: the size below the upload's)
            // from where it is: its transcoded size, or the upload's own size (from the rung in the upload's headers)
            int from = on >= 0 ? on : v.upload_from;
            if (from < 0) from = 0;
            int next = from + 1;
            if (next > int(SIZES.size())) next = int(SIZES.size());
            int f = ++v.fails[from];                       // the size that just failed is held off, doubling
            double hold = std::min(HOLD_MAX_S, HOLD_BASE_S * std::pow(2.0, f - 1));
            v.held_off[from] = now + std::chrono::milliseconds(long(hold * 1000));
            v.link_target = next;
            v.changed_at = now;
            v.judge_after = now + std::chrono::milliseconds(long(DROP_SETTLE_S * 1000));
            retarget(v);
            std::printf("[media] %s: delivered %.0f%% -> down to %s\n", c->name.c_str(), ratio * 100,
                        next >= int(SIZES.size()) ? "audio only" :
                        (std::to_string(SIZES[size_t(next)].w) + "x" + std::to_string(SIZES[size_t(next)].h)).c_str());
            std::fflush(stdout);
        } else {
            v.bad_windows = 0;
            if (++v.good_windows < HAPPY_WINDOWS || v.link_target < 0) continue;
            if (std::chrono::duration<double>(now - v.changed_at).count() < SETTLE_S) continue;   // [SETTLE_BEFORE_YOU_CLIMB_V1]
            int up = v.link_target - 1;
            auto h = v.held_off.find(up < 0 ? 0 : up);
            if (h != v.held_off.end() && now < h->second) continue;                              // [PROBE_BACKOFF_V1]
            v.good_windows = 0;
            v.link_target = (up < 0 || (v.upload_from >= 0 && up <= v.upload_from)) ? -1 : up;
            v.changed_at = now;
            v.judge_after = now + std::chrono::milliseconds(long(RATE_SETTLE_S * 1000));
            retarget(v);
            std::printf("[media] %s: delivered %.0f%% for %d windows -> up one size\n", c->name.c_str(), ratio * 100, HAPPY_WINDOWS);
            std::fflush(stdout);
        }
    }
}

std::vector<int> Server::wanted_sizes(const std::shared_ptr<Conn>&, const std::vector<std::shared_ptr<Conn>>& viewers, int up) {
    std::vector<int> want;                                 // viewers limited BELOW the upload
    for (auto& to : viewers) {
        std::lock_guard<std::mutex> g(to->vmu);
        int t = to->view.target;
        if (t >= 0 && t < int(SIZES.size()) && (up < 0 || t > up) && std::find(want.begin(), want.end(), t) == want.end())
            want.push_back(t);
    }
    return want;
}

void Server::set_cap_row(const std::string& session, const std::string& viewer, const std::string&, long bps) {
    std::lock_guard<std::mutex> g(mu_);
    for (auto& c : conns_) {
        if (c->kind != PLANE_VIDEO || c->dead || c->session != session) continue;
        // Exact identity, never the address: two viewers behind one address (NAT, an office) must not share a cap.
        // A C++ viewer's id is its name; fnav's me_id is the name plus four hex digits.
        auto hex4 = [](const std::string& t) { return t.size() == 4 && t.find_first_not_of("0123456789abcdef") == std::string::npos; };
        bool same = c->name == viewer ||
                    (viewer.size() == c->name.size() + 4 && viewer.compare(0, c->name.size(), c->name) == 0 && hex4(viewer.substr(c->name.size())));
        if (!same) continue;
        std::lock_guard<std::mutex> v(c->vmu);
        if (c->view.cap_bps != bps) {
            c->view.cap_bps = bps;
            retarget(c->view);
            std::printf("[media] %s: MediaSpeed %ld b/s -> target %d\n", c->name.c_str(), bps, c->view.target);
            std::fflush(stdout);
        }
    }
}

void Server::fan_video(const std::shared_ptr<Conn>& from, const Bytes& vp, bool key) {
    auto viewers = peers_of(from, PLANE_VIDEO);
    int up;
    {
        std::lock_guard<std::mutex> g(from->txmu);
        if (!from->tx) from->tx = std::make_unique<SenderTranscoder>();
        up = from->tx->upload_index();
    }
    bool transcoding = false;
    for (auto& to : viewers) {                             // pass-through viewers: here, at once
        int t;
        { std::lock_guard<std::mutex> g(to->vmu); t = to->view.target; }
        if (t >= int(SIZES.size())) continue;              // audio only
        if (t < 0 || (up >= 0 && t <= up)) deliver_video(from, to, vp, key, -1);   // never larger than the upload
        else transcoding = true;
    }
    if (!transcoding) return;
    std::lock_guard<std::mutex> g(from->txmu);             // transcoded viewers: the sender's worker
    if (!from->txw.joinable()) from->txw = std::thread([this, from] { tx_worker(from); });
    from->txq.push_back(vp);
    from->txcv.notify_one();
}

void Server::tx_worker(std::shared_ptr<Conn> from) {
    const size_t BEHIND = 48;                              // 2 s at 24 fps: decoding itself is falling behind
    while (true) {
        std::deque<Bytes> work;
        {
            std::unique_lock<std::mutex> g(from->txmu);
            from->txcv.wait(g, [&] { return from->txstop || !from->txq.empty(); });
            if (from->txstop) return;
            work.swap(from->txq);
        }
        if (work.size() > BEHIND) {                        // skip to the newest keyframe; ask for one if there is none
            size_t k = work.size();
            for (size_t i = work.size(); i-- > 0;) if (unpack_video(work[i]).key) { k = i; break; }
            if (k == work.size()) { work.clear(); ask_key(from); }
            else work.erase(work.begin(), work.begin() + long(k));
        }
        bool fresh = false;
        for (auto& vp : work) {
            try { fresh |= from->tx->decode(vp); } catch (const std::exception&) {}
        }
        if (!fresh) continue;
        auto viewers = peers_of(from, PLANE_VIDEO);
        std::map<int, std::vector<Bytes>> made;
        try { made = from->tx->encode_latest(wanted_sizes(from, viewers, from->tx->upload_index())); }
        catch (const std::exception&) { continue; }
        for (auto& to : viewers) {
            int t;
            { std::lock_guard<std::mutex> g(to->vmu); t = to->view.target; }
            auto m = made.find(t);
            if (m == made.end()) continue;
            for (auto& p : m->second) deliver_video(from, to, p, unpack_video(p).key, t);
        }
    }
}

void Server::deliver_video(const std::shared_ptr<Conn>& from, const std::shared_ptr<Conn>& to, const Bytes& vp, bool key, int stream) {
    // [DROP_THE_FRAME_NOT_THE_CALLER_V1] a viewer whose connection breaks is shut down; the sender is not touched
    try { deliver_video_locked(from, to, vp, key, stream); }
    catch (const ConnectionError& e) {
        std::printf("[media] %s: viewer connection broken (%s); closing it\n", to->name.c_str(), e.what());
        to->plane->shutdown_both();
    }
}

void Server::deliver_video_locked(const std::shared_ptr<Conn>& from, const std::shared_ptr<Conn>& to, const Bytes& vp, bool key, int stream) {
    std::lock_guard<std::mutex> g(to->vmu);
    Viewer& v = to->view;
    if (v.stream != stream) {                              // a new stream: its references are not this viewer's yet
        v.stream = stream; v.unanchored = true; v.held_key.clear();
        if (stream >= 0 && !key && from->tx) from->tx->force_key(stream);
    }
    v.aimed++;                                             // [AIM_IS_COUNTED_ONCE_V1] once, before any branch
    if (stream == -1 && vp.size() >= 4) {                  // [VIDEO_IN_CARRIES_THE_RUNG_V1] the upload's size, no decode
        uint32_t lvl = (uint32_t(vp[0]) << 24 | uint32_t(vp[1]) << 16 | uint32_t(vp[2]) << 8 | vp[3]) & 0x7FFFFFFFu;
        v.upload_from = lvl >= 8 ? 0 : lvl == 7 ? 1 : lvl == 6 ? 2 : 3;
    }
    auto now = Clock::now();
    if (!v.held_key.empty() && secs(v.held_at, now) > HELD_KEY_MAX_S) v.held_key.clear();   // [A_HOLD_IS_NOT_FOREVER_V1]
    if (key) v.held_key.clear();                           // a newer keyframe replaces the held one
    if (!v.held_key.empty()) {                             // the held keyframe goes first, if it fits now
        if (to->plane->send_video(v.held_src, v.held_key, true)) { v.held_key.clear(); v.unanchored = false; v.delivered++; }
    }
    if (v.unanchored && !key) {                            // [KEYFRAME_ANCHOR_V1] no inter frames until a keyframe
        v.inters_shed++;
        if (v.held_key.empty()) {
            if (stream < 0) ask_key(from);                 // the upload: only the sender can make a keyframe
            else if (from->tx) from->tx->force_key(stream);   // a transcoded size: this server makes it
        }
        return;
    }
    if (to->plane->send_video(from->name, vp, key)) {
        v.delivered++;
        if (key) v.unanchored = false;
        return;
    }
    v.unanchored = true;                                   // a lost frame breaks the reference
    if (key) { v.held_key = vp; v.held_src = from->name; v.held_at = now; v.keys_held++; }   // held, not shed
    else {
        v.inters_shed++;
        if (stream < 0) ask_key(from); else if (from->tx) from->tx->force_key(stream);
    }
}

void Server::ask_key(const std::shared_ptr<Conn>& sender) {
    auto now = Clock::now();
    if (sender->keyreq_at.time_since_epoch().count() != 0 && secs(sender->keyreq_at, now) < KEYREQ_MIN_S) return;
    sender->keyreq_at = now;
    Bytes one{0, 0, 0, 1};
    try { sender->plane->send(pack_typed(KIND_KEYREQ, "media", one), SendKind::Control); }   // [KEYFRAME_ON_REQUEST_V1]
    catch (const ConnectionError&) { sender->plane->shutdown_both(); }
}

void Server::teardown(const std::shared_ptr<Conn>& c) {
    {
        std::lock_guard<std::mutex> g(c->txmu);
        c->txstop = true;
        c->txcv.notify_all();
    }
    if (c->txw.joinable()) c->txw.join();
    std::lock_guard<std::mutex> g(mu_);
    c->dead = true;
    for (auto& o : conns_)                                 // [TEARDOWN_IS_PER_SESSION_V1] the mate with THIS tag only
        if (o != c && !o->dead && !c->tag.empty() && o->tag == c->tag && o->name == c->name) o->plane->shutdown_both();
}

std::vector<Server::Hold> Server::holds() {
    std::vector<Hold> out;
    std::lock_guard<std::mutex> g(mu_);
    for (auto& c : conns_) {
        if (c->kind != PLANE_VIDEO || c->dead || c->session.empty()) continue;
        std::lock_guard<std::mutex> v(c->vmu);
        Hold h;
        h.session = c->session; h.viewer = c->name;
        h.unanchored = c->view.unanchored; h.key_held = !c->view.held_key.empty();
        h.aimed = c->view.aimed; h.delivered = c->view.delivered; h.inters_shed = c->view.inters_shed; h.keys_held = c->view.keys_held;
        h.passthrough = c->view.stream == -1;
        h.audio_only = c->view.target == int(SIZES.size());
        if (c->view.stream >= 0 && size_t(c->view.stream) < SIZES.size()) { h.stream_w = SIZES[size_t(c->view.stream)].w; h.stream_h = SIZES[size_t(c->view.stream)].h; }
        out.push_back(h);
    }
    return out;
}

std::string Server::snapshot() {
    std::lock_guard<std::mutex> g(mu_);
    std::ostringstream o;
    for (auto& c : conns_) {
        o << c->name << " " << (c->kind == PLANE_VIDEO ? "V" : "A") << " session=" << c->session << (c->dead ? " dead" : "");
        if (c->kind == PLANE_VIDEO) {
            std::lock_guard<std::mutex> v(c->vmu);
            o << " target=" << c->view.target << " link=" << c->view.link_target << " ratio=" << c->view.last_ratio << " stream=" << c->view.stream << " aimed=" << c->view.aimed << " delivered=" << c->view.delivered << " inters_shed=" << c->view.inters_shed
              << " keys_held=" << c->view.keys_held << " unanchored=" << c->view.unanchored;
        } else o << " audio_aimed=" << c->audio_aimed << " audio_delivered=" << c->audio_delivered;
        o << "\n";
    }
    return o.str();
}

}  // namespace media
