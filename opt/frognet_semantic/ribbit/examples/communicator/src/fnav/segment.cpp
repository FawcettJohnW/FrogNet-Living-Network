// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "segment.hpp"
#include <algorithm>
#include <cmath>

namespace fnav {

// fnav.py SotFDataPlane._note_accepted, exactly: a = 1 - exp(-dt / RATE_HALFLIFE_S).
void RateMeter::on_accepted(size_t nbytes, double now_s) {
    if (last_ < 0) { last_ = now_s; rate_ = 0.0; return; }
    double dt = now_s - last_;
    last_ = now_s;
    if (dt <= 0) return;
    double inst = double(nbytes) / dt;
    double a = 1.0 - std::exp(-dt / HALFLIFE_S);
    rate_ = (1.0 - a) * rate_ + a * inst;
}

size_t whole_frame_max(double rate, size_t sndbuf) {
    size_t hard = sndbuf / 2;
    if (rate <= 0.0) return VSEG_BYTES;
    double lim = std::min(double(hard), rate * (WHOLE_FRAME_MS / 1000.0));
    return size_t(std::max(double(VSEG_BYTES), lim));
}

bool Reassembler::feed(const std::string& src, const Bytes& p, Bytes& out) {
    if (p.size() < 5) { stats.bad++; return false; }
    Seg s = unpack_seg(p);
    auto it = cur_.find(src);
    if (s.flags & VSEG_ABORT) {
        if (it != cur_.end()) { cur_.erase(it); stats.aborted++; }
        return false;
    }
    if (it == cur_.end() || it->second.fid != s.frame_id) {
        if (s.index != 0) { stats.partial++; if (it != cur_.end()) cur_.erase(it); return false; }
        if (it != cur_.end()) stats.superseded++;
        cur_[src] = Cur{s.frame_id, 1, s.piece};
    } else {
        if (s.index != it->second.next) { stats.gap++; cur_.erase(it); return false; }
        it->second.data.insert(it->second.data.end(), s.piece.begin(), s.piece.end());
        it->second.next++;
    }
    if (s.flags & VSEG_LAST) {
        out = std::move(cur_[src].data);
        cur_.erase(src);
        stats.done++;
        return true;
    }
    return false;
}

}  // namespace fnav
