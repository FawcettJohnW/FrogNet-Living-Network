// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// The bearer: how a sender picks its upload rung -- John's rule, 2026-08-03 ([LADDER_RATE_V2]), ported line for line
// from fnav.py Bearer.sample (FNAV-SPEC.md, "The bearer"). Called once per video frame.
//   Down at once on: audio shed; more than 3 consecutive dirty samples (a drop, or a backlog of 8 or more);
//   more than 5 drops in one second; below max(10 fps, 75% of target) for more than 3 samples.
//   Up only after 3 whole clean seconds AND full frame rate (98% of target), unless that rung is held off:
//   a failed rung is held 15 s, doubling to 300 s; 60 s clean at a rung forgives its record ([PROBE_BACKOFF_V1]).
#pragma once
#include <map>
#include <optional>
#include <string>

namespace fnav {

class Bearer {
public:
    static constexpr int BAD_READS = 3, FPS_DOWN_READS = 3, SEC_DROPS = 5, CLEAN_SECS = 3, BACKLOG_DIRTY = 8;
    static constexpr double FPS_DOWN = 10.0, FPS_DOWN_FRAC = 0.75, FULL_RATE_FRAC = 0.98;
    static constexpr double HOLD_BASE_S = 15.0, HOLD_MAX_S = 300.0, HOLD_CLEAR_S = 60.0;

    Bearer(int ceiling, int floor, double now);
    int sample(double now, int backlog, int dropped, std::optional<double> fps_sent,
               std::optional<double> fps_target, int audio_shed = 0);
    int idx() const { return idx_; }
    int ceiling() const { return ceiling_; }
    int bad_reads() const { return bad_reads_; }       // consecutive stressed samples (down at > BAD_READS)
    int clean_secs() const { return clean_secs_; }     // whole clean seconds (up at CLEAN_SECS)
    int sec_drops() const { return sec_drops_; }       // drops this second (down at > SEC_DROPS)
    // [THE_CLIMB_BACK_IS_A_CLIMB_V1] the bottom walk finished: resume the ladder AT this rung, with no credit carried
    void resume_at(int idx, double now);
    const std::string& last_reason() const { return reason_; }

private:
    int idx_, ceiling_, floor_;
    int bad_reads_ = 0, slow_reads_ = 0, sec_drops_ = 0, clean_secs_ = 0;
    std::optional<double> sec_fps_min_;
    double sec_t0_, held_since_;
    std::map<int, int> fails_;
    std::map<int, double> blocked_;
    std::string reason_;
};

}  // namespace fnav
