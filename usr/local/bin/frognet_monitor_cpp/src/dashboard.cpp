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
// dashboard.cpp — the model and its formatting. No terminal in this file.
#include "dashboard.h"

#include <sys/stat.h>

#include <cmath>
#include <cstdio>
#include <ctime>
#include <sstream>

namespace frognet {
namespace {

std::string fmt(const char* f, double v) {
  char buf[64];
  std::snprintf(buf, sizeof buf, f, v);
  return buf;
}

double num(const Json& o, const char* key) {
  const Json* v = o.find(key);
  if (!v) return 0;
  if (v->type == Json::Type::Number) return v->number;
  if (v->type == Json::Type::String) return std::atof(v->str.c_str());
  return 0;
}

}  // namespace

const char* stateText(PeerState s) {
  switch (s) {
    case PeerState::Down: return "DOWN";
    case PeerState::Sat:  return "SAT";
    case PeerState::Busy: return "BUSY";
    case PeerState::Slow: return "SLOW";
    case PeerState::Fair: return "FAIR";
    case PeerState::Ok:   return "OK";
    default:              return "?";
  }
}

Ink stateInk(PeerState s) {
  switch (s) {
    case PeerState::Down: return Ink::Bad;
    case PeerState::Sat:  return Ink::Bad;
    case PeerState::Busy: return Ink::Warn;
    case PeerState::Slow: return Ink::Warn;
    case PeerState::Fair: return Ink::Warn;
    case PeerState::Ok:   return Ink::Good;
    default:              return Ink::Dim;
  }
}

PeerState Peer::state() const {
  // First match wins; same order as the Python.
  if (!echo_ok) return PeerState::Down;
  if (have_lq) {
    if (sat_p95 >= 0.90) return PeerState::Sat;
    if (sat_p95 >= 0.70) return PeerState::Busy;
    if (lq_rtt_p95 > 1000) return PeerState::Slow;
    if (lq_rtt_p95 > 250)  return PeerState::Fair;
    return PeerState::Ok;
  }
  // No LinkQuality entry for this peer: the proxy has not spoken to it in the
  // current window. That is not a failure and must not be reported as one, so
  // fall to the RTT we do have rather than inventing a saturation figure.
  double r = (p95_ms > 0) ? p95_ms : rtt_ms;
  if (r <= 0) return PeerState::Unknown;
  if (r > 1000) return PeerState::Slow;
  if (r > 250)  return PeerState::Fair;
  return PeerState::Ok;
}

std::string fmtRtt(double ms) {
  if (ms <= 0) return "\u2014";          // em dash: no reading, not zero
  if (ms < 10) return fmt("%.1fms", ms);
  return fmt("%.0fms", ms);
}

std::string fmtPct(double frac) {
  if (frac <= 0) return "\u2014";
  return fmt("%.0f%%", frac * 100.0);
}

std::string fmtBytes(long long n) {
  if (n <= 0) return "\u2014";
  const char* u[] = {"B", "K", "M", "G", "T"};
  double v = static_cast<double>(n);
  int i = 0;
  while (v >= 1024.0 && i < 4) { v /= 1024.0; ++i; }
  return (v < 10 && i > 0) ? fmt("%.1f", v) + u[i] : fmt("%.0f", v) + u[i];
}

std::string fmtBps(double bps) {
  if (bps <= 0) return "\u2014";
  const char* u[] = {"bps", "Kbps", "Mbps", "Gbps"};
  double v = bps;
  int i = 0;
  while (v >= 1000.0 && i < 3) { v /= 1000.0; ++i; }
  return (v < 10 && i > 0) ? fmt("%.1f", v) + u[i] : fmt("%.0f", v) + u[i];
}

Ink rttInk(double ms) {
  if (ms <= 0)   return Ink::Dim;
  if (ms < 100)  return Ink::Good;
  if (ms < 1000) return Ink::Warn;
  return Ink::Bad;
}

Ink hitInk(double frac) {
  if (frac <= 0)   return Ink::Dim;
  if (frac >= 0.5) return Ink::Good;
  if (frac >= 0.2) return Ink::Warn;
  return Ink::Dim;
}

// Eighth-block glyphs. A meter that only moves in whole cells cannot show the
// difference between 61% and 74% load on a 10-cell bar, which is exactly the
// range where a node stops being comfortable.
std::string bar(double frac, int width) {
  if (width <= 0) return "";
  if (frac < 0) frac = 0;
  if (frac > 1) frac = 1;
  static const char* kEighths[] = {
    "", "\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589"
  };
  double cells = frac * width;
  int full = static_cast<int>(cells);
  int rem  = static_cast<int>((cells - full) * 8.0);
  std::string out;
  for (int i = 0; i < full && i < width; ++i) out += "\u2588";
  if (full < width && rem > 0) { out += kEighths[rem]; ++full; }
  for (int i = full; i < width; ++i) out += "\u00b7";   // middle dot: unfilled
  return out;
}

Ink loadInk(double frac) {
  if (frac <= 0)    return Ink::Dim;
  if (frac < 0.60)  return Ink::Good;
  if (frac < 0.85)  return Ink::Warn;
  return Ink::Bad;
}

Ink tempInk(double c) {
  if (c <= 0)  return Ink::Dim;
  if (c < 60)  return Ink::Good;
  if (c < 75)  return Ink::Warn;
  return Ink::Bad;
}

std::vector<std::string> loadHeaders() {
  return {"NODE", "CPU", "BUSY", "IOW", "LOAD", "MEM", "NET RX/TX", "DISK R/W", "TEMP"};
}

std::vector<int> loadWidths() {
  return {13, 12, 7, 6, 7, 12, 18, 18, 7};
}

// Truncate to a display width (codepoints, not bytes) so a long node name
// cannot shift every column to its right. Node names come from the pond and
// are not bounded by anything this program controls.
static std::string clipWidth(const std::string& t, int w) {
  int n = 0;
  size_t cut = t.size();
  for (size_t i = 0; i < t.size(); ++i) {
    if ((static_cast<unsigned char>(t[i]) & 0xC0) == 0x80) continue;
    if (n == w) { cut = i; break; }
    ++n;
  }
  if (cut >= t.size()) return t;
  // Leave room for the ellipsis character, which is itself one column.
  std::string out = t.substr(0, cut);
  while (!out.empty()) {
    int m = 0;
    for (unsigned char ch : out) if ((ch & 0xC0) != 0x80) ++m;
    if (m <= w - 1) break;
    out.pop_back();
    while (!out.empty() && (static_cast<unsigned char>(out.back()) & 0xC0) == 0x80)
      out.pop_back();
  }
  return out + "\u2026";
}

std::vector<Cell> loadRow(const Peer& p) {
  const Perf& f = p.perf;
  std::string label = p.name;
  if (p.is_dbhost) label = "*" + label;
  label = clipWidth(label, loadWidths()[0]);

  // [STALE_IS_NOT_CURRENT_V1] System.Perf is published on the node's own clock.
  // A reading two minutes old describes a node two minutes ago, and drawing it
  // as a live meter is the same error as printing 0ms for "not measured".
  bool stale = f.have && f.age_s > 120;
  if (!f.have) {
    return {
      {label, p.is_local ? Ink::Head : Ink::Normal},
      {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim},
      {"\u2014", Ink::Dim}, {"no perf", Ink::Dim},
      {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim},
    };
  }
  if (stale) {
    char aged[32];
    std::snprintf(aged, sizeof aged, "stale %.0fs", f.age_s);
    return {
      {label, p.is_local ? Ink::Head : Ink::Normal},
      {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim},
      {"\u2014", Ink::Dim}, {aged, Ink::Warn},
      {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim}, {"\u2014", Ink::Dim},
    };
  }

  double busy = f.cpu_busy / 100.0;
  double memf = f.mem_pct / 100.0;
  return {
    {label,                              p.is_local ? Ink::Head : Ink::Normal},
    {bar(busy, 10),                      loadInk(busy)},
    {fmt("%.0f%%", f.cpu_busy),          loadInk(busy)},
    {fmt("%.0f%%", f.cpu_iowait),        f.cpu_iowait > 10 ? Ink::Bad
                                          : (f.cpu_iowait > 3 ? Ink::Warn : Ink::Dim)},
    {fmt("%.2f", f.load1),               Ink::Normal},
    {bar(memf, 6) + fmt(" %.0f%%", f.mem_pct), loadInk(memf)},
    {fmtBps(f.rx_bps * 8) + "/" + fmtBps(f.tx_bps * 8),
                                         (f.rx_bps + f.tx_bps) > 0 ? Ink::Normal : Ink::Dim},
    {fmtBps(f.disk_r_bps * 8) + "/" + fmtBps(f.disk_w_bps * 8),
                                         (f.disk_r_bps + f.disk_w_bps) > 0 ? Ink::Normal : Ink::Dim},
    {f.temp_c > 0 ? fmt("%.0f\u00b0C", f.temp_c) : "\u2014", tempInk(f.temp_c)},
  };
}

std::vector<std::string> columnHeaders() {
  return {"PEER", "STATUS", "RTT", "P50", "P95", "HIT", "SAVED", "EFF", "ACTUAL"};
}

std::vector<int> columnWidths() {
  return {13, 8, 7, 7, 7, 5, 9, 10, 9};
}

std::vector<Cell> peerRow(const Peer& p) {
  std::string label = p.name;
  if (p.is_dbhost) label = "*" + label;      // marks the elected data host
  if (p.is_local)  label += " (this)";

  PeerState st = p.state();
  // Self has no link measurements to itself; say so rather than showing "?".
  std::string stxt = p.is_local ? "SELF" : stateText(st);
  Ink sink = p.is_local ? Ink::Head : stateInk(st);
  return {
    {label,                 p.is_local ? Ink::Head : Ink::Normal},
    {stxt,                  sink},
    {fmtRtt(p.rtt_ms),      rttInk(p.rtt_ms)},
    {fmtRtt(p.p50_ms),      rttInk(p.p50_ms)},
    {fmtRtt(p.p95_ms),      rttInk(p.p95_ms)},
    {fmtPct(p.hit_rate),    hitInk(p.hit_rate)},
    {fmtBytes(p.saved),     p.saved > 0 ? Ink::Good : Ink::Dim},
    {fmtBps(p.eff_bps),     p.eff_bps > 0 ? Ink::Good : Ink::Dim},
    {fmtBps(p.actual_bps),  p.actual_bps > 0 ? Ink::Normal : Ink::Dim},
  };
}

Snapshot collect(const Client& c, const std::string& domain) {
  Snapshot s;
  s.domain = domain;

  // [MERGE_IS_VISIBLE_V1] stat the sentinel the merge maintains -- do not scrape
  // the process table for a script name, which reports the wrapper's own
  // defunct children as a live merge. Three states, not two: present (with an
  // age), absent, and unreadable, which is neither.
  struct stat mb{};
  if (::stat("/etc/sentinels/mergePending", &mb) == 0) {
    s.merge_known = true;
    s.merge_pending = true;
    s.merge_age_s = std::difftime(std::time(nullptr), mb.st_mtime);
  } else if (errno == ENOENT) {
    s.merge_known = true;
    s.merge_pending = false;
  }  // anything else: merge_known stays false

  std::vector<HostEntry> hosts = c.discoverHosts();

  // The elected data host, from /etc/hosts and never the resolver: in practice
  // the two disagree, and the file is what every other component routes by.
  std::string content;
  if (FILE* f = std::fopen("/etc/hosts", "r")) {
    char buf[4096];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) content.append(buf, n);
    std::fclose(f);
    std::istringstream is(content);
    std::string line;
    while (std::getline(is, line)) {
      if (line.find("databasehost.frognet") == std::string::npos) continue;
      if (line.find('#') == 0) continue;
      std::istringstream ls(line);
      ls >> s.dbhost_ip;
      break;
    }
  }

  // This node's per-peer cache stats and link quality, in one pair of reads
  // rather than one pair per peer.
  Json cachePeers, lqPeers;
  {
    Json d = c.sensorDetail(domain + ".SemanticProxy.LinkQuality");
    if (const Json* jd = d.find("jsonData"))
      if (const Json* pp = jd->find("peers")) lqPeers = *pp;
  }

  for (const auto& h : hosts) {
    Peer p;
    p.ip = h.ip;
    p.name = h.name;
    p.is_dbhost = (!s.dbhost_ip.empty() && h.ip == s.dbhost_ip);
    // A node holds no cache or link-quality entry for ITSELF, so its status
    // resolves to UNKNOWN -- true, but it reads as a fault on the one row the
    // operator is most likely to trust. Mark it as self instead.
    p.is_local = (h.name == domain);

    // Echo proves the whole semantic chain: local proxy, local daemon, remote
    // daemon, remote Apache. A bare socket connect could not.
    Identity id = c.echo(h.ip, 3);
    p.echo_ok = id.ok;

    Json pd = c.sensorDetail(domain + ".SemanticCache.Peer." + h.ip);
    if (const Json* jd = pd.find("jsonData")) {
      if (const Json* rtt = jd->find("rtt")) {
        p.rtt_ms = num(*rtt, "avg_ms");
        p.p50_ms = num(*rtt, "p50_ms");
        p.p95_ms = num(*rtt, "p95_ms");
      }
      p.hit_rate = num(*jd, "cache_hit_rate");
      p.saved = static_cast<long long>(num(*jd, "bytes_saved"));
      if (const Json* et = jd->find("effective_throughput")) {
        p.eff_bps = num(*et, "effective_bps");
        p.actual_bps = num(*et, "actual_bps");
      }
    }

    // <domain-of-that-node>.System.Perf -- named for the node it describes, so
    // the peer's own domain is the prefix, not ours.
    Json perf = c.sensorDetail(h.name + ".System.Perf");
    if (const Json* jd = perf.find("jsonData")) {
      Perf& f = p.perf;
      f.have = true;
      if (const Json* cpu = jd->find("cpu_pct")) {
        f.cpu_busy = num(*cpu, "total_busy");
        f.cpu_iowait = num(*cpu, "iowait");
      }
      if (const Json* la = jd->find("loadavg")) f.load1 = num(*la, "1");
      if (const Json* mk = jd->find("mem_kb"))  f.mem_pct = num(*mk, "pct_used");
      if (const Json* sk = jd->find("swap_kb")) f.swap_pct = num(*sk, "pct_used");
      if (const Json* ni = jd->find("net_io"))
        if (const Json* t = ni->find("total")) {
          f.rx_bps = num(*t, "rx_bytes_per_s");
          f.tx_bps = num(*t, "tx_bytes_per_s");
        }
      if (const Json* di = jd->find("disk_io"))
        if (const Json* t = di->find("total")) {
          f.disk_r_bps = num(*t, "read_bytes_per_s");
          f.disk_w_bps = num(*t, "write_bytes_per_s");
        }
      if (const Json* tc = jd->find("temps_c"))
        if (tc->isArray())
          for (const Json& e : tc->array) {
            double v = num(e, "temp_c");
            if (v > f.temp_c) f.temp_c = v;   // hottest sensor is the one that matters
          }
      double ts = num(*jd, "timestamp");
      if (ts > 0) f.age_s = std::difftime(std::time(nullptr), static_cast<time_t>(ts));
    }

    if (const Json* lq = lqPeers.find(h.ip)) {
      p.have_lq = true;
      p.sat_p95 = num(*lq, "saturation_ratio_p95");
      p.lq_rtt_p95 = num(*lq, "rtt_ok_p95_ms");
    }

    s.peers.push_back(std::move(p));
  }

  return s;
}

}  // namespace frognet
