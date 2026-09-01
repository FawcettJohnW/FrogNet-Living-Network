/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// dashboard.h — the model behind the dashboard, and the formatting of it.
//
// Kept out of main_tui.cpp so the numbers and their colour thresholds are
// testable without a terminal, and so the C# port has an obvious counterpart to
// match rather than a screenful of drawing to read.
#pragma once

#include "frognet.h"

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

namespace frognet {

// Colour intent, not a colour. The renderer maps these to ncurses pairs or ANSI
// codes; the model never knows which.
enum class Ink { Dim, Normal, Good, Warn, Bad, Head, Title, Select };

// ---------------------------------------------------------------------------
// Peer status. Mirrors the Python's decision order exactly, first match wins:
//
//   echo failed                       -> DOWN
//   saturation_ratio_p95 >= 0.90      -> SAT      (link is the limit)
//   saturation_ratio_p95 >= 0.70      -> BUSY
//   rtt_ok_p95_ms   >  1000           -> SLOW
//   rtt_ok_p95_ms   >   250           -> FAIR
//   otherwise                         -> OK
//
// A missing LinkQuality entry is not a failure: the proxy simply has not spoken
// to that peer in the current window. It degrades to the RTT thresholds rather
// than reporting a state it cannot know.
// ---------------------------------------------------------------------------
enum class PeerState { Down, Sat, Busy, Slow, Fair, Ok, Unknown };

const char* stateText(PeerState s);
Ink         stateInk(PeerState s);

// <domain>.System.Perf -- what the node is actually doing while it serves.
// A peer can answer every probe promptly and still be a node in trouble; the
// link numbers cannot see that and this can.
struct Perf {
  bool   have       = false;
  double cpu_busy   = 0;   // cpu_pct.total_busy
  double cpu_iowait = 0;   // cpu_pct.iowait -- disk pressure hiding inside "busy"
  double load1      = 0;   // loadavg."1"
  double mem_pct    = 0;   // mem_kb.pct_used
  double swap_pct   = 0;   // swap_kb.pct_used
  double rx_bps     = 0;   // net_io.total.rx_bytes_per_s (BYTES; x8 for bits)
  double tx_bps     = 0;   // net_io.total.tx_bytes_per_s
  double disk_r_bps = 0;   // disk_io.total.read_bytes_per_s
  double disk_w_bps = 0;   // disk_io.total.write_bytes_per_s
  double temp_c     = 0;   // hottest entry in temps_c
  double age_s      = 0;   // now - timestamp: a stale reading is not a reading
};

struct Peer {
  std::string ip, name;
  bool  echo_ok    = false;
  double echo_ms   = 0;
  double rtt_ms    = 0;   // SemanticCache.Peer.<ip> -> rtt.avg_ms
  double p50_ms    = 0;   //                            rtt.p50_ms
  double p95_ms    = 0;   //                            rtt.p95_ms
  double hit_rate  = 0;   //                            cache_hit_rate
  long long saved  = 0;   //                            bytes_saved
  double eff_bps   = 0;   //   effective_throughput.effective_bps
  double actual_bps = 0;  //   effective_throughput.actual_bps
  bool   have_lq   = false;
  double sat_p95   = 0;   // SemanticProxy.LinkQuality -> peers[ip].saturation_ratio_p95
  double lq_rtt_p95 = 0;  //                              peers[ip].rtt_ok_p95_ms
  bool   is_dbhost = false;
  bool   is_local  = false;
  Perf   perf;

  PeerState state() const;
};

// Formatted cell plus the ink it should carry.
struct Cell { std::string text; Ink ink = Ink::Normal; };

// A horizontal meter. width cells, filled proportionally to frac (0..1),
// using eighth-block glyphs so a 1-cell change is visible at 1/8 resolution.
std::string bar(double frac, int width);
Ink loadInk(double frac);        // <0.60 good, <0.85 warn, else bad
Ink tempInk(double c);           // <60 good, <75 warn, else bad

std::string fmtRtt(double ms);
std::string fmtPct(double frac);
std::string fmtBytes(long long n);
std::string fmtBps(double bps);

Ink rttInk(double ms);       // <100 good, <1000 warn, else bad; 0 -> dim
Ink hitInk(double frac);     // >=0.5 good, >=0.2 warn, else dim

// One row: NAME STATUS RTT P50 P95 HIT SAVED EFF ACTUAL — the same nine
// columns, in the same order, as the Python dashboard.
std::vector<Cell> peerRow(const Peer& p);
std::vector<std::string> columnHeaders();
std::vector<int> columnWidths();

// The load board: a second row set, keyed on System.Perf rather than on the
// link. Same peers, entirely different question.
std::vector<Cell> loadRow(const Peer& p);
std::vector<std::string> loadHeaders();
std::vector<int> loadWidths();

// ---------------------------------------------------------------------------
// Shared state. The network fills it on a worker thread; the draw loop reads it.
// Published by whole-value swap under one lock, so a repaint sees a complete
// snapshot or the previous complete snapshot, never half of one.
// ---------------------------------------------------------------------------
struct Snapshot {
  std::string domain;
  std::string dbhost_ip;
  std::vector<Peer> peers;
  std::string status_line;
  bool merge_pending = false;
  bool merge_known = false;      // false = could not read the sentinel at all,
  double merge_age_s = 0;        //         which is neither pending nor absent
  long long generation = 0;
};

class State {
 public:
  Snapshot get() const { std::lock_guard<std::mutex> g(m_); return snap_; }
  void set(Snapshot s) { std::lock_guard<std::mutex> g(m_); snap_ = std::move(s); }

 private:
  mutable std::mutex m_;
  Snapshot snap_;
};

// Collect one full refresh. Blocking; call from the worker thread only.
Snapshot collect(const Client& c, const std::string& domain);

}  // namespace frognet
