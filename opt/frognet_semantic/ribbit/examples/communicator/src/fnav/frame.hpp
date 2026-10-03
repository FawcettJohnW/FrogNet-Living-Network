// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// fnav frame layer -- byte-for-byte the wire of fnav.py (FNAV-SPEC.md, "Frame layer").
//
//   frame   = [len u32 BE][kind u8][srclen u16 BE][src utf-8][payload]
//   VIDEO   payload = [level u32 BE: rung, high bit = keyframe][codec u8][codec packet]
//   VSEG    payload = [frame_id u16 BE][index u16 BE][flags u8] + piece
//   ABORT   the remainder of a frame the sender could not finish is padded with DE AD BE EF; the receiver discards it
#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace fnav {

using Bytes = std::vector<uint8_t>;

enum Kind : uint8_t {
    KIND_AUDIO = 0,              // audio block (Opus by default)
    KIND_VIDEO = 1,              // [level][codec][packet]
    KIND_BACKPRESSURE = 2,       // u32: video frames shed for a viewer (media server -> sender)
    KIND_PLANE = 3,              // first frame on a connection: 'A' or 'V' + teardown tag + call session
    KIND_KEYREQ = 4,             // emit a keyframe now
    KIND_AUDIO_BACKPRESSURE = 5, // u32: viewers whose audio is starved
    KIND_VSEG = 6,               // one piece of a video frame
};

constexpr uint8_t PLANE_AUDIO = 'A';
constexpr uint8_t PLANE_VIDEO = 'V';
constexpr uint32_t KEYFRAME_BIT = 0x80000000u;
constexpr uint8_t VSEG_LAST = 0x01;
constexpr uint8_t VSEG_ABORT = 0x02;
constexpr size_t VSEG_BYTES = 1368;            // the tunnels' MSS
constexpr size_t VSEG_WHOLE_MAX = 8192;
constexpr uint8_t ABORT_SENTINEL[4] = {0xDE, 0xAD, 0xBE, 0xEF};

enum Codec : uint8_t { CODEC_VP8 = 0, CODEC_H264_SW = 1, CODEC_H264_HW = 2 };

struct Typed {                 // one decoded frame body (without the length prefix)
    uint8_t kind = 0;
    std::string src;
    Bytes payload;
};

struct Video {                 // a VIDEO payload
    uint32_t level = 0;        // rung 0..8
    bool key = false;
    uint8_t codec = CODEC_VP8;
    Bytes packet;
};

struct Seg {                   // a VSEG payload
    uint16_t frame_id = 0, index = 0;
    uint8_t flags = 0;
    Bytes piece;
};

// Raised on a malformed frame. Never a silent default: a frame that cannot be read is reported and counted.
struct FrameError : std::runtime_error { using std::runtime_error::runtime_error; };

Bytes pack_typed(uint8_t kind, const std::string& src, const Bytes& payload);    // fnav.pack_typed
Typed unpack_typed(const Bytes& body);                                           // fnav.unpack_typed
Bytes pack_video(uint32_t level, const Bytes& packet, uint8_t codec, bool key);  // fnav.pack_video
Video unpack_video(const Bytes& payload);                                        // fnav.unpack_video
bool video_is_key(const Bytes& body);         // keyframe bit by offset, no utf-8 decode (fnav.video_is_key)
Bytes pack_seg(uint16_t frame_id, uint16_t index, uint8_t flags, const Bytes& piece);
Seg unpack_seg(const Bytes& payload);
Bytes with_length(const Bytes& body);         // [len u32 BE] + body: what goes on the wire
// [ABORT_SENTINEL_V1] An aborted frame is identified by its TAIL: the sender pads the rest of a frame it cannot
// finish so that the last four bytes are the sentinel (fnav.recv_frame checks body[-4:]).
bool is_abort(const Bytes& body);
// The padding for the `remaining` bytes of a part-written frame, aligned so that it ENDS with the sentinel.
// [ABORT_PAD_ENDS_WITH_THE_SENTINEL_V1] fnav.py cut the repeated sentinel from the front (pad[:remaining]), so a
// remainder that is not a multiple of 4 ended in e.g. BE EF DE AD: the abort went undetected and a half-garbage
// frame reached the decoder. The receiver's rule is unchanged; this sender always satisfies it.
Bytes abort_pad(size_t remaining);

}  // namespace fnav
