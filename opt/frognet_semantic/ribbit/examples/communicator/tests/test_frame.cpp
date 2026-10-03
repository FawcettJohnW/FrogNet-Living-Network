// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_frame < vectors -- the C++ frame layer against fnav.py's own packing (tools/gen_frames.py).
// Every case: pack the same inputs and require the identical bytes; unpack fnav.py's bytes and require every field.
#include "fnav/frame.hpp"
#include <iostream>
#include <sstream>

using namespace fnav;
static Bytes unhex(const std::string& h) {
    Bytes b; if (h == "-") return b;
    for (size_t i = 0; i + 1 < h.size(); i += 2) b.push_back(uint8_t(std::stoi(h.substr(i, 2), nullptr, 16)));
    return b;
}
static std::string str(const Bytes& b) { return std::string(b.begin(), b.end()); }

int main() {
    std::string line; long cases = 0, fail = 0;
    auto bad = [&](const std::string& what, long n) { if (++fail <= 10) std::cout << "FAIL " << what << " (case " << n << ")\n"; };
    while (std::getline(std::cin, line)) {
        std::istringstream in(line); std::string t; in >> t; ++cases;
        if (t == "typed") {
            int kind; std::string src, pay, want; in >> kind >> src >> pay >> want;
            Bytes w = unhex(want);
            if (pack_typed(uint8_t(kind), str(unhex(src)), unhex(pay)) != w) bad("pack_typed", cases);
            Typed u = unpack_typed(w);
            if (u.kind != kind || u.src != str(unhex(src)) || u.payload != unhex(pay)) bad("unpack_typed", cases);
        } else if (t == "video") {
            int lvl, key, codec; std::string pay, want; in >> lvl >> key >> codec >> pay >> want;
            Bytes w = unhex(want);
            if (pack_video(uint32_t(lvl), unhex(pay), uint8_t(codec), key != 0) != w) bad("pack_video", cases);
            Video v = unpack_video(w);
            if (int(v.level) != lvl || v.key != (key != 0) || v.codec != codec || v.packet != unhex(pay)) bad("unpack_video", cases);
        } else if (t == "iskey") {
            std::string body; int want; in >> body >> want;
            if (video_is_key(unhex(body)) != (want != 0)) bad("video_is_key", cases);
        } else if (t == "seg") {
            int fid, idx, fl; std::string pay, want; in >> fid >> idx >> fl >> pay >> want;
            Bytes w = unhex(want);
            if (pack_seg(uint16_t(fid), uint16_t(idx), uint8_t(fl), unhex(pay)) != w) bad("pack_seg", cases);
            Seg s = unpack_seg(w);
            if (s.frame_id != fid || s.index != idx || s.flags != fl || s.piece != unhex(pay)) bad("unpack_seg", cases);
        } else { bad("unknown line", cases); }
    }
    // malformed input is refused loudly, never read as a default
    long refused = 0;
    for (const Bytes& m : {Bytes{}, Bytes{1}, Bytes{1, 0, 9, 'a'}, Bytes{1, 0, 0, 0, 0}}) {
        try { Typed t = unpack_typed(m); if (m.size() == 5) unpack_video(t.payload); } catch (const FrameError&) { ++refused; }
    }
    if (refused != 4) bad("malformed frames refused: " + std::to_string(refused) + " of 4", 0);
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " frame layer vs fnav.py: " << cases << " cases, " << fail << " fail\n";
    return fail ? 1 : 0;
}
