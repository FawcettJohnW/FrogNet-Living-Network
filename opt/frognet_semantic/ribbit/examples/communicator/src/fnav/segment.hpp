// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Video segmentation (FNAV-SPEC.md; fnav.py SotFDataPlane.put_segmented and the KIND_VSEG receive path).
//
// Sender: a frame larger than whole_frame_max() goes as VSEG pieces of VSEG_BYTES, the last flagged VSEG_LAST.
// [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] the ceiling is how much the socket accepts in one audio block (20 ms) at
// the rate it has actually been accepting -- at least one segment, at most half the send buffer, one segment while
// nothing has been measured. A piece the wire refuses abandons the WHOLE frame: an explicit VSEG_ABORT goes so the
// receiver lets go, and the frame counts as ONE shed ([A_FRAME_IS_ONE_SHED_V1]).
//
// Receiver: per source, pieces of one frame in order. A gap, a frame that does not start at piece 0, an abort, or a
// newer frame discards what was held; each is counted. The completed frame is a KIND_VIDEO payload.
#pragma once
#include "frame.hpp"
#include <cstdint>
#include <map>
#include <string>

namespace fnav {

class RateMeter {                          // bytes the socket ACCEPTED per second, EWMA with a 2 s half-life
public:
    static constexpr double HALFLIFE_S = 2.0;
    void on_accepted(size_t nbytes, double now_s);
    double bytes_per_s() const { return rate_; }
private:
    double rate_ = 0.0, last_ = -1.0;
};

constexpr double WHOLE_FRAME_MS = 20.0;    // one audio block: the deadline being protected
size_t whole_frame_max(double accepted_bytes_per_s, size_t sndbuf);

struct ReasmStats { uint64_t done = 0, aborted = 0, partial = 0, gap = 0, superseded = 0, bad = 0; };

class Reassembler {
public:
    // One VSEG payload from `src`. Returns true and fills `video_payload` when a frame completes.
    bool feed(const std::string& src, const Bytes& vseg_payload, Bytes& video_payload);
    ReasmStats stats;
private:
    struct Cur { uint16_t fid; uint16_t next; Bytes data; };
    std::map<std::string, Cur> cur_;
};

}  // namespace fnav
