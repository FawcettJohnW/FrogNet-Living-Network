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
// parsers.cpp — the three pure functions. No I/O, no sockets, no clock.
//
// These are exercised by ../frognet_monitor_shared/parser_vectors.json, the same
// file the Python and C# implementations read, so agreement between the ports is
// a test result rather than a claim.
#include "frognet.h"

#include <algorithm>
#include <cctype>
#include <sstream>

namespace frognet {
namespace {

std::string trim(const std::string& s) {
  size_t a = 0, b = s.size();
  while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
  while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
  return s.substr(a, b - a);
}

// Split on a delimiter WITHOUT collapsing adjacent ones and WITHOUT dropping
// empties. This is the whole reason the shared vectors exist: strtok collapses
// runs of delimiters, and C#'s Split with RemoveEmptyEntries drops empties, so
// "Seattle2,,10.120.120.1,10.160.160.47" silently becomes a 3-field line whose
// wlan0 holds the wlan1 address. Wrong, and it looks right.
std::vector<std::string> splitKeepEmpty(const std::string& s, char delim) {
  std::vector<std::string> out;
  std::string cur;
  for (char c : s) {
    if (c == delim) { out.push_back(cur); cur.clear(); }
    else cur += c;
  }
  out.push_back(cur);
  return out;
}

std::vector<std::string> splitWhitespace(const std::string& s) {
  std::vector<std::string> out;
  std::istringstream is(s);
  std::string tok;
  while (is >> tok) out.push_back(tok);
  return out;
}

bool startsWith(const std::string& s, const std::string& p) {
  return s.size() >= p.size() && s.compare(0, p.size(), p) == 0;
}

}  // namespace

Identity parseEchoLine(const std::string& body) {
  Identity id;

  // The body may carry a trailing newline; take the first non-empty line and
  // ignore anything after it.
  std::string line = trim(body);
  size_t nl = line.find('\n');
  if (nl != std::string::npos) line = trim(line.substr(0, nl));

  // [IDENTITY_FAILS_LOUD_V1] Empty body means the node could not state its
  // identity. getFrogNet.bash exits non-zero and prints nothing, so
  // frognet_echo.php returns an empty body. There is no fallback name to
  // reconstruct and nothing to guess: it failed.
  if (line.empty()) return id;

  std::vector<std::string> f = splitKeepEmpty(line, ',');
  if (f.size() != 4) return id;      // exactly four fields, or it failed

  for (auto& x : f) x = trim(x);

  // An empty INTERFACE field is accurate data — Seattle2 has no carrier on eth0
  // and reporting that honestly is correct. An empty NAME is a failure, because
  // every node answers to the hostname "FrogNetHost" and only the domain
  // identifies which node this is.
  if (f[0].empty()) return id;

  id.ok = true;
  id.domain = f[0];
  id.eth0   = f[1];
  id.wlan0  = f[2];
  id.wlan1  = f[3];
  return id;
}

std::vector<HostEntry> parseEtcHosts(const std::string& content) {
  std::vector<HostEntry> out;
  std::istringstream is(content);
  std::string raw;
  while (std::getline(is, raw)) {
    std::string line = trim(raw);
    if (line.empty() || line[0] == '#') continue;

    std::vector<std::string> parts = splitWhitespace(line);
    if (parts.size() < 2) continue;

    const std::string& ip = parts[0];
    // 10/8 only. 10.253/16 is tunnel transit and 10.254/16 is chorus virtual —
    // a node carries those addresses but is not reachable as a node at them.
    if (!startsWith(ip, "10.")) continue;
    if (startsWith(ip, "10.253.") || startsWith(ip, "10.254.")) continue;

    // A FrogNetHost.<name> alias is required, and the name is what follows it.
    // Every node's hostname is FrogNetHost, so the suffix is the only thing on
    // the line that says which node it is.
    static const std::string kMarker = "FrogNetHost.";
    for (size_t k = 1; k < parts.size(); ++k) {
      size_t at = parts[k].find(kMarker);
      if (at == std::string::npos) continue;
      std::string name = parts[k].substr(at + kMarker.size());
      if (!name.empty()) out.push_back(HostEntry{ip, name});
      break;
    }
  }
  return out;
}

std::string dotOneOf(const std::string& ip) {
  std::string s = trim(ip);
  if (!startsWith(s, "10.")) return "";
  if (startsWith(s, "10.253.") || startsWith(s, "10.254.")) return "";

  std::vector<std::string> o = splitKeepEmpty(s, '.');
  if (o.size() != 4) return "";
  for (const auto& part : o) {
    if (part.empty() || part.size() > 3) return "";
    for (char c : part) if (!std::isdigit(static_cast<unsigned char>(c))) return "";
    int v = std::atoi(part.c_str());
    if (v < 0 || v > 255) return "";
  }
  return o[0] + "." + o[1] + "." + o[2] + ".1";
}

}  // namespace frognet
