// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_resample -- the device <-> wire resampler: a 440 Hz tone keeps its pitch and its duration from 44.1 kHz
// (the eMeet C950's microphone) to the 16 kHz wire, and from the wire to 44.1 and 48 kHz speakers. Fed in 20 ms
// device blocks, as PortAudio delivers them.
#include "comms/devices.hpp"
#include <cmath>
#include <iostream>

using namespace comms;
static int fail = 0;
static void run(double in_rate, double out_rate) {
    Resampler r(in_rate, out_rate);
    std::vector<int16_t> out;
    const double secs = 5.0;
    const long n = long(in_rate * secs), block = long(in_rate / 50);
    std::vector<int16_t> in(static_cast<size_t>(n));
    for (long i = 0; i < n; ++i) in[size_t(i)] = int16_t(8000 * std::sin(2 * M_PI * 440.0 * double(i) / in_rate));
    for (long o = 0; o < n; o += block) r.push(in.data() + o, size_t(std::min(block, n - o)), out);
    long crossings = 0;
    for (size_t i = 1; i < out.size(); ++i) if ((out[i - 1] < 0) != (out[i] < 0)) ++crossings;
    double hz = crossings / 2.0 / (double(out.size()) / out_rate);
    double dur = double(out.size()) / out_rate;
    bool ok = std::fabs(hz - 440.0) < 2.0 && std::fabs(dur - secs) < 0.01;
    if (!ok) ++fail;
    std::cout << (ok ? "ok   " : "FAIL ") << int(in_rate) << " -> " << int(out_rate) << " Hz: " << out.size() << " samples ("
              << dur << " s of " << secs << "), tone " << hz << " Hz\n";
}
static void rate(double measured, double nominal, double want, const char* what) {
    double got = true_rate(measured, nominal);
    bool ok = got == want;
    if (!ok) ++fail;
    std::cout << (ok ? "ok   " : "FAIL ") << what << ": measured " << measured << " of a claimed " << nominal << " -> " << got << "\n";
}
int main() {
    rate(40250, 44100, 44100, "Razer Seiren start-up reading");
    rate(43954, 44100, 44100, "Razer Seiren");
    rate(15611, 44100, 16000, "eMeet C950 (fnav, 2026-08: 17.7 blocks/s)");
    rate(13678, 44100, 16000, "eMeet C950 (Pi, 2026-10-01, window incl. start)");
    rate(47200, 48000, 48000, "a 48 kHz device");
    rate(0, 44100, 44100, "nothing measured");
    // the eMeet case end to end: a tone really sampled at 16 kHz, resampled from the rate true_rate() names
    run(true_rate(15611, 44100), 16000);
    run(44100, 16000); run(48000, 16000); run(16000, 44100); run(16000, 48000); run(16000, 16000);
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " resampler: " << fail << " fail\n";
    return fail ? 1 : 0;
}
