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
// parser_oracle.cpp — run ../frognet_monitor_shared/parser_vectors.json.
//
// The same file the Python and C# oracles read. "The C++ port behaves
// identically" is then a test result, not a claim in a README.
#include "frognet.h"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using namespace frognet;

namespace {

int g_fail = 0;
int g_pass = 0;

void ok(const std::string& m)  { std::printf("  ok    %s\n", m.c_str()); ++g_pass; }
void bad(const std::string& m) { std::printf("  FAIL  %s\n", m.c_str()); ++g_fail; }

std::string readFile(const char* path) {
  std::ifstream f(path);
  if (!f) return "";
  std::ostringstream ss;
  ss << f.rdbuf();
  return ss.str();
}

const Json* need(const Json& j, const char* k) {
  const Json* v = j.find(k);
  if (!v) { std::fprintf(stderr, "FATAL: vectors missing \"%s\"\n", k); std::exit(1); }
  return v;
}

void runEcho(const Json& group) {
  std::printf("\n=== echo_line (identity) ===\n");
  const Json* cases = need(group, "cases");
  for (const Json& c : cases->array) {
    std::string name = c.atString("name");
    std::string input = c.atString("input");
    const Json* okv = c.find("ok");
    bool want_ok = okv && okv->type == Json::Type::Bool && okv->boolean;

    Identity got = parseEchoLine(input);
    if (got.ok != want_ok) {
      bad(name + " -- ok=" + (got.ok ? "true" : "false") +
          ", expected " + (want_ok ? "true" : "false"));
      continue;
    }
    if (!want_ok) { ok(name + " -- rejected, as it must be"); continue; }

    std::string wd = c.atString("domain"), we = c.atString("eth0");
    std::string w0 = c.atString("wlan0"),  w1 = c.atString("wlan1");
    if (got.domain == wd && got.eth0 == we && got.wlan0 == w0 && got.wlan1 == w1)
      ok(name);
    else
      bad(name + " -- got [" + got.domain + "|" + got.eth0 + "|" +
          got.wlan0 + "|" + got.wlan1 + "] want [" + wd + "|" + we + "|" +
          w0 + "|" + w1 + "]");
  }
}

void runHosts(const Json& group) {
  std::printf("\n=== etc_hosts (discovery) ===\n");
  const Json* cases = need(group, "cases");
  for (const Json& c : cases->array) {
    std::string name = c.atString("name");
    std::string input = c.atString("input");
    const Json* exp = c.find("expect");

    std::vector<HostEntry> got = parseEtcHosts(input);
    size_t want_n = (exp && exp->isArray()) ? exp->array.size() : 0;
    if (got.size() != want_n) {
      bad(name + " -- got " + std::to_string(got.size()) +
          " entries, expected " + std::to_string(want_n));
      continue;
    }
    bool same = true;
    for (size_t k = 0; k < want_n; ++k) {
      const Json& pair = exp->array[k];
      if (!pair.isArray() || pair.array.size() != 2) { same = false; break; }
      if (got[k].ip != pair.array[0].str || got[k].name != pair.array[1].str) {
        same = false;
        bad(name + " -- entry " + std::to_string(k) + " got [" + got[k].ip +
            "," + got[k].name + "] want [" + pair.array[0].str + "," +
            pair.array[1].str + "]");
        break;
      }
    }
    if (same) ok(name);
  }
}

void runDotOne(const Json& group) {
  std::printf("\n=== dot_one_of (identity endpoint) ===\n");
  const Json* cases = need(group, "cases");
  for (const Json& c : cases->array) {
    std::string name = c.atString("name");
    std::string input = c.atString("input");
    const Json* okv = c.find("ok");
    bool want_ok = okv && okv->type == Json::Type::Bool && okv->boolean;

    std::string got = dotOneOf(input);
    if (!want_ok) {
      got.empty() ? ok(name + " -- rejected")
                  : bad(name + " -- returned \"" + got + "\", expected rejection");
      continue;
    }
    std::string want = c.atString("expect");
    (got == want) ? ok(name)
                  : bad(name + " -- got \"" + got + "\", want \"" + want + "\"");
  }
}

std::vector<std::string> strList(const Json& c, const char* k) {
  std::vector<std::string> out;
  if (const Json* a = c.find(k); a && a->isArray())
    for (const Json& e : a->array) out.push_back(e.str);
  return out;
}

std::string show(const std::vector<std::string>& v) {
  std::string o = "[";
  for (size_t i = 0; i < v.size(); ++i) o += (i ? ", \"" : "\"") + v[i] + "\"";
  return o + "]";
}

void runSnapshot(const Json& group) {
  std::printf("\n=== snapshot (getSnapshot.php) [ONE_MONITOR_TRUTH_V1] ===\n");
  const Json* cases = need(group, "cases");
  for (const Json& c : cases->array) {
    std::string name = c.atString("name");
    const Json* okv = c.find("ok");
    bool want_ok = okv && okv->type == Json::Type::Bool && okv->boolean;
    NodeSnapshot got = parseSnapshot(c.atString("input"));
    if (!want_ok) { got.ok ? bad(name + " -- accepted, expected rejection") : ok(name + " -- rejected"); continue; }
    if (!got.ok) { bad(name + " -- rejected, expected a snapshot"); continue; }
    std::string why;
    auto eq = [&](const char* k, const std::string& g) { if (g != c.atString(k)) why += std::string(" ") + k + "=\"" + g + "\""; };
    auto eqv = [&](const char* k, const std::vector<std::string>& g) { if (g != strList(c, k)) why += std::string(" ") + k + "=" + show(g); };
    eq("mode", got.mode); eq("node_name", got.name); eq("dot1", got.dot1); eq("build", got.build); eq("dbhost", got.dbhost);
    eqv("ponds", got.ponds); eqv("hub", got.hub); eqv("members", got.members); eqv("errors", got.errors);
    why.empty() ? ok(name) : bad(name + " -- got" + why);
  }
}

}  // namespace

int main(int argc, char** argv) {
  const char* path = (argc > 1) ? argv[1]
                                : "../frognet_monitor_shared/parser_vectors.json";
  std::string text = readFile(path);
  if (text.empty()) {
    std::fprintf(stderr, "FATAL: cannot read vectors at %s\n", path);
    return 1;
  }
  std::string err;
  Json v = Json::parse(text, &err);
  if (!err.empty() || !v.isObject()) {
    std::fprintf(stderr, "FATAL: vectors did not parse: %s\n", err.c_str());
    return 1;
  }

  std::printf("vectors: %s\n", path);
  runEcho(*need(v, "echo_line"));
  runHosts(*need(v, "etc_hosts"));
  runDotOne(*need(v, "dot_one_of"));
  runSnapshot(*need(v, "snapshot"));

  std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
  if (g_fail) { std::printf("FAILED\n"); return 1; }
  std::printf("PASS - C++ parsers agree with the shared vectors\n");
  return 0;
}
