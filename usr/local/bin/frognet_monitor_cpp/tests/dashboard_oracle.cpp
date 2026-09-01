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
// dashboard_oracle.cpp — the dashboard's numbers and status thresholds,
// exercised without a terminal.
//
// The state machine below is the part of a monitor that can be quietly wrong:
// it decides whether a peer reads OK or DOWN, and an operator acts on that. It
// lives in dashboard.cpp rather than in the drawing code precisely so it can be
// tested here.
#include "dashboard.h"

#include <cstdio>
#include <string>

using namespace frognet;

static int g_fail = 0, g_pass = 0;
static void ok(const std::string& m)  { std::printf("  ok    %s\n", m.c_str()); ++g_pass; }
static void bad(const std::string& m) { std::printf("  FAIL  %s\n", m.c_str()); ++g_fail; }

static void eq(const std::string& what, const std::string& got, const std::string& want) {
  got == want ? ok(what + " -> \"" + got + "\"")
              : bad(what + " -> got \"" + got + "\" want \"" + want + "\"");
}

static void isState(const std::string& what, PeerState got, PeerState want) {
  got == want ? ok(what + " -> " + stateText(got))
              : bad(what + " -> got " + stateText(got) + " want " + stateText(want));
}

int main() {
  std::printf("=== peer status: first match wins, same order as the Python ===\n");
  {
    Peer p; p.echo_ok = false; p.have_lq = true; p.sat_p95 = 0.1; p.lq_rtt_p95 = 5;
    isState("echo failed outranks everything", p.state(), PeerState::Down);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = true; p.sat_p95 = 0.95; p.lq_rtt_p95 = 5;
    isState("saturated link beats a good RTT", p.state(), PeerState::Sat);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = true; p.sat_p95 = 0.75; p.lq_rtt_p95 = 5;
    isState("busy link beats a good RTT", p.state(), PeerState::Busy);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = true; p.sat_p95 = 0.1; p.lq_rtt_p95 = 1500;
    isState("slow when p95 over 1000ms", p.state(), PeerState::Slow);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = true; p.sat_p95 = 0.1; p.lq_rtt_p95 = 400;
    isState("fair when p95 over 250ms", p.state(), PeerState::Fair);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = true; p.sat_p95 = 0.1; p.lq_rtt_p95 = 20;
    isState("ok otherwise", p.state(), PeerState::Ok);
  }

  std::printf("\n=== a missing LinkQuality entry is not a failure ===\n");
  {
    // The proxy simply has not spoken to this peer in the current window.
    // Reporting DOWN or SAT here would be inventing a reading.
    Peer p; p.echo_ok = true; p.have_lq = false; p.p95_ms = 30;
    isState("degrades to RTT, not to DOWN", p.state(), PeerState::Ok);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = false; p.p95_ms = 1500;
    isState("degrades to RTT thresholds", p.state(), PeerState::Slow);
  }
  {
    Peer p; p.echo_ok = true; p.have_lq = false;   // echo up, no numbers at all
    isState("no readings at all is UNKNOWN, not OK", p.state(), PeerState::Unknown);
  }

  std::printf("\n=== zero is not a measurement ===\n");
  // A zero RTT means no reading was taken. Printing "0ms" would claim an
  // instantaneous link; the em dash says nothing was measured.
  eq("fmtRtt(0)",     fmtRtt(0),     "\u2014");
  eq("fmtPct(0)",     fmtPct(0),     "\u2014");
  eq("fmtBytes(0)",   fmtBytes(0),   "\u2014");
  eq("fmtBps(0)",     fmtBps(0),     "\u2014");
  (rttInk(0) == Ink::Dim) ? ok("rttInk(0) is dim, not good")
                          : bad("rttInk(0) claimed a reading");

  std::printf("\n=== formatting ===\n");
  eq("fmtRtt(8.4)",       fmtRtt(8.4),       "8.4ms");
  eq("fmtRtt(21.37)",     fmtRtt(21.37),     "21ms");
  eq("fmtPct(0.5)",       fmtPct(0.5),       "50%");
  eq("fmtBytes(1024)",    fmtBytes(1024),    "1.0K");
  eq("fmtBytes(1536)",    fmtBytes(1536),    "1.5K");
  eq("fmtBytes(999)",     fmtBytes(999),     "999B");
  eq("fmtBps(22000)",     fmtBps(22000),     "22Kbps");
  eq("fmtBps(1500000)",   fmtBps(1500000),   "1.5Mbps");

  std::printf("\n=== row shape matches the column list ===\n");
  {
    Peer p; p.name = "Seattle6"; p.echo_ok = true;
    auto cells = peerRow(p);
    auto heads = columnHeaders();
    auto widths = columnWidths();
    (cells.size() == heads.size() && heads.size() == widths.size())
      ? ok("cells, headers and widths agree (" + std::to_string(cells.size()) + ")")
      : bad("row shape mismatch: cells=" + std::to_string(cells.size()) +
            " headers=" + std::to_string(heads.size()) +
            " widths=" + std::to_string(widths.size()));

    for (size_t i = 0; i < cells.size() && i < widths.size(); ++i) {
      if ((int)cells[i].text.size() > widths[i]) {
        bad("column " + std::to_string(i) + " (\"" + cells[i].text +
            "\") exceeds width " + std::to_string(widths[i]));
      }
    }
    ok("no formatted cell overflows its column");
  }
  {
    Peer p; p.name = "Seattle5"; p.echo_ok = true; p.is_dbhost = true;
    auto cells = peerRow(p);
    (cells[0].text.rfind("*", 0) == 0)
      ? ok("databasehost is marked in the name column")
      : bad("databasehost not marked: \"" + cells[0].text + "\"");
  }

  std::printf("\n=== load board fits its columns ===\n");
  {
    // Display width, not byte length: the bars are multibyte UTF-8, so counting
    // bytes would pass a row that visibly overflows. Count codepoints.
    auto width = [](const std::string& t) {
      int n = 0;
      for (unsigned char ch : t) if ((ch & 0xC0) != 0x80) ++n;
      return n;
    };
    auto lw = loadWidths();
    auto lh = loadHeaders();
    (lw.size() == lh.size()) ? ok("load headers and widths agree")
                             : bad("load header/width mismatch");

    struct Case { const char* what; Peer p; };
    std::vector<Case> cases;
    { Peer p; p.name = "New-York-1"; p.echo_ok = true; p.perf.have = true;
      p.perf.cpu_busy = 91.7; p.perf.cpu_iowait = 18.3; p.perf.load1 = 6.02;
      p.perf.mem_pct = 93.1; p.perf.rx_bps = 900000; p.perf.tx_bps = 1200000;
      p.perf.disk_r_bps = 2400000; p.perf.disk_w_bps = 3100000;
      p.perf.temp_c = 79.4; p.perf.age_s = 4;
      cases.push_back({"loaded node", p}); }
    { Peer p; p.name = "Seattle2"; p.echo_ok = true;             // no System.Perf
      cases.push_back({"node with no System.Perf", p}); }
    { Peer p; p.name = "BAMacBook"; p.echo_ok = true;
      p.perf.have = true; p.perf.age_s = 400;                     // stale reading
      cases.push_back({"node with a stale reading", p}); }
    { Peer p; p.name = "databasehost-x"; p.echo_ok = true; p.is_dbhost = true;
      p.perf.have = true; p.perf.age_s = 3; p.perf.mem_pct = 100; p.perf.cpu_busy = 100;
      cases.push_back({"long name, everything pinned", p}); }

    bool allFit = true;
    for (const auto& c : cases) {
      auto cells = loadRow(c.p);
      if (cells.size() != lw.size()) {
        bad(std::string(c.what) + ": " + std::to_string(cells.size()) +
            " cells for " + std::to_string(lw.size()) + " columns");
        allFit = false;
        continue;
      }
      for (size_t i = 0; i < cells.size(); ++i) {
        int wdt = width(cells[i].text);
        if (wdt > lw[i]) {
          bad(std::string(c.what) + ": column " + std::to_string(i) + " (\"" +
              cells[i].text + "\") is " + std::to_string(wdt) +
              " wide, column is " + std::to_string(lw[i]));
          allFit = false;
        }
      }
    }
    if (allFit) ok("every load cell fits its column, in display width");
  }

  std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
  if (g_fail) { std::printf("FAILED\n"); return 1; }
  std::printf("PASS - dashboard model\n");
  return 0;
}
