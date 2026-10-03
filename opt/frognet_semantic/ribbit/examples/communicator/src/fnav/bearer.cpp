// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "bearer.hpp"
#include <algorithm>
#include <cmath>

namespace fnav {

Bearer::Bearer(int ceiling, int floor, double now)
    : idx_(ceiling), ceiling_(ceiling), floor_(floor), sec_t0_(now), held_since_(now) {}

int Bearer::sample(double now, int backlog, int dropped, std::optional<double> fps_sent,
                   std::optional<double> fps_target, int audio_shed) {
    bool dirty = dropped > 0 || backlog >= BACKLOG_DIRTY;
    bad_reads_ = dirty ? bad_reads_ + 1 : 0;
    double fps_floor = FPS_DOWN;
    if (fps_target && *fps_target) fps_floor = std::max(fps_floor, *fps_target * FPS_DOWN_FRAC);
    if (fps_sent && *fps_sent < fps_floor) slow_reads_++; else slow_reads_ = 0;
    sec_drops_ += dropped;
    if (fps_sent) sec_fps_min_ = sec_fps_min_ ? std::min(*sec_fps_min_, *fps_sent) : *fps_sent;

    std::optional<int> sec_closed;
    std::optional<bool> sec_full_rate;
    if (now - sec_t0_ >= 1.0) {
        sec_closed = sec_drops_;
        sec_full_rate = true;
        if (sec_fps_min_ && fps_target && *fps_target) sec_full_rate = *sec_fps_min_ >= *fps_target * FULL_RATE_FRAC;
        if (*sec_closed == 0 && *sec_full_rate) clean_secs_++; else clean_secs_ = 0;
        sec_drops_ = 0; sec_fps_min_.reset(); sec_t0_ = now;
    }

    std::string reason;
    if (audio_shed) reason = "audio shed (" + std::to_string(audio_shed) + ")";
    else if (bad_reads_ > BAD_READS) reason = std::to_string(bad_reads_) + " consecutive reads with drops";
    else if (sec_closed && *sec_closed > SEC_DROPS) reason = std::to_string(*sec_closed) + " drops in one second";
    else if (slow_reads_ > FPS_DOWN_READS) reason = "below the frame-rate floor for " + std::to_string(slow_reads_) + " reads";
    if (!reason.empty()) {
        int f = ++fails_[idx_];
        blocked_[idx_] = now + std::min(HOLD_MAX_S, HOLD_BASE_S * std::pow(2.0, f - 1));
        reason_ = reason;
        bad_reads_ = 0; slow_reads_ = 0; clean_secs_ = 0; sec_drops_ = 0; sec_fps_min_.reset();
        if (idx_ > floor_) { idx_--; held_since_ = now; }
        return idx_;
    }
    if (clean_secs_ && now - held_since_ >= HOLD_CLEAR_S && fails_.count(idx_)) { fails_.erase(idx_); blocked_.erase(idx_); }
    if (clean_secs_ >= CLEAN_SECS && idx_ < ceiling_) {
        int target = idx_ + 1;
        auto b = blocked_.find(target);
        double until = b == blocked_.end() ? 0.0 : b->second;
        if (now < until) reason_ = "holding L" + std::to_string(target);
        else { idx_ = target; held_since_ = now; clean_secs_ = 0; reason_.clear(); }
    } else if (sec_closed && *sec_closed == 0 && sec_full_rate && !*sec_full_rate) {
        reason_ = "holding: not at full frame rate";
    }
    return idx_;
}

void Bearer::resume_at(int idx, double now) {
    idx_ = idx;
    bad_reads_ = 0; slow_reads_ = 0; sec_drops_ = 0; sec_fps_min_.reset(); sec_t0_ = now; clean_secs_ = 0;
    held_since_ = now;
    reason_ = "resumed at L" + std::to_string(idx) + " after the bottom walk";
}

}  // namespace fnav
