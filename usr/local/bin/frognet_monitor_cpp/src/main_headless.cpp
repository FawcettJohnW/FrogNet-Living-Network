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
// main_headless.cpp — discover the pond, read a node's telemetry, print it.
//
// This is the whole demonstration. There is no FrogNet library linked, no
// generated stub, no SDK version to match against the nodes. libcurl and a
// vendored JSON reader, and the client works.
#include "frognet.h"
#include "dashboard.h"

#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>

using namespace frognet;

namespace {

void usage(const char* argv0) {
  std::fprintf(stderr,
    "usage: %s [--proxy HOST] [--port N] <command> [args]\n"
    "\n"
    "  snapshot            this node's snapshot, one frame: mode, build, ponds, hub link, members, elections\n"
    "  identity            who this pond says we are (own IP -> .1 -> echo)\n"
    "  hosts               list the FrogNet nodes (the snapshot's members)\n"
    "  sensors <domain>    every sensor whose name starts \"<domain>.\"\n"
    "  read <sensor-name>  one sensor's metadata and jsonData\n"
    "  engine [domain]     shorthand for <domain>.SemanticProxy.Engine\n"
    "\n"
    "  --proxy defaults to 127.0.0.1 and --port to 80. On a machine that is not\n"
    "  itself a node, point --proxy at any node's address.\n", argv0);
}

int cmdIdentity(const Client& c) {
  std::string me = localFrogNetAddress();
  if (me.empty()) {
    std::fprintf(stderr, "no 10/8 address on this machine - not on a FrogNet\n");
    return 2;
  }
  std::string dot1 = dotOneOf(me);
  if (dot1.empty()) {
    std::fprintf(stderr, "%s is not a basis for identity\n", me.c_str());
    return 2;
  }
  std::printf("own address : %s\n", me.c_str());
  std::printf("asking      : %s/frognet_echo.php\n", dot1.c_str());

  Identity id = c.echo(dot1);
  // [IDENTITY_FAILS_LOUD_V1] Four fields or it failed. Nothing is guessed and
  // nothing is reconstructed from a hostname - every node answers to
  // "FrogNetHost", so a name that is not the domain is not an identity.
  if (!id.ok) {
    std::fprintf(stderr, "identity FAILED - %s did not return four fields\n", dot1.c_str());
    return 1;
  }
  std::printf("domain      : %s\n", id.domain.c_str());
  std::printf("eth0        : %s\n", id.eth0.empty()  ? "(none)" : id.eth0.c_str());
  std::printf("wlan0       : %s\n", id.wlan0.empty() ? "(none)" : id.wlan0.c_str());
  std::printf("wlan1       : %s\n", id.wlan1.empty() ? "(none)" : id.wlan1.c_str());
  return 0;
}

// [ONE_MONITOR_TRUTH_V1] getSnapshot.php, printed once: the same title and node block as the dashboards, then the
// member rows and the elections. Exit 1 when the snapshot did not answer.
int cmdSnapshot(const Client& c) {
  NodeSnapshot n = c.snapshot();
  std::printf("%s\n", titleText(n).c_str());
  for (const auto& l : nodeLines(n)) std::printf("%s\n", l.c_str());
  if (!n.ok) return 1;
  std::printf("\n  members (ip name role alive|down source)\n");
  for (const auto& m : n.members) {
    std::string ip = m.substr(0, m.find(' '));
    const char* mark = (ip == n.dbhost && ip == n.control) ? "#" : ip == n.dbhost ? "*" : ip == n.control ? "+" : " ";
    std::printf("  %s %s\n", mark, m.c_str());
  }
  if (n.members.empty()) std::printf("    (none)\n");
  std::string el;
  if (const Json* e = n.raw.find("elections"); e && e->isObject())
    for (const auto& kv : e->object) el += "  " + kv.first + "=" + (kv.second.type == Json::Type::String ? kv.second.str : "");
  std::printf("\n  elections%s\n", el.empty() ? "  none in /etc/hosts" : el.c_str());
  return 0;
}

int cmdHosts(const Client& c) {
  std::vector<HostEntry> hosts = Client::hostsOf(c.snapshot());
  if (hosts.empty()) { std::fprintf(stderr, "no hosts discovered\n"); return 1; }
  for (const auto& h : hosts)
    std::printf("%-16s %s\n", h.ip.c_str(), h.name.c_str());
  return 0;
}

int cmdSensors(const Client& c, const std::string& domain) {
  Json rows = c.sensorsForHost(domain);
  if (!rows.isArray() || rows.array.empty()) {
    std::fprintf(stderr, "no sensors for \"%s\"\n", domain.c_str());
    return 1;
  }
  std::printf("%-8s %-46s %s\n", "ID", "NAME", "TYPE");
  for (const Json& r : rows.array)
    std::printf("%-8s %-46s %s\n",
                r.atString("SensorID").c_str(),
                r.atString("SensorName").c_str(),
                r.atString("SensorType").c_str());
  std::printf("\n%zu sensor(s)\n", rows.array.size());
  return 0;
}

int cmdRead(const Client& c, const std::string& name) {
  Json s = c.sensorDetail(name);
  if (s.isNull()) { std::fprintf(stderr, "no sensor named \"%s\"\n", name.c_str()); return 1; }
  std::printf("SensorID   : %s\n", s.atString("SensorID").c_str());
  std::printf("SensorName : %s\n", s.atString("SensorName").c_str());
  std::printf("SensorType : %s\n", s.atString("SensorType").c_str());
  const Json* jd = s.find("jsonData");
  std::printf("jsonData   : %s\n", jd ? jd->toCompactString().c_str() : "(none)");
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  std::string proxy = "127.0.0.1";
  int port = 80;

  int i = 1;
  for (; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--proxy") && i + 1 < argc) { proxy = argv[++i]; continue; }
    if (!std::strcmp(argv[i], "--port")  && i + 1 < argc) { port = std::atoi(argv[++i]); continue; }
    if (!std::strcmp(argv[i], "-h") || !std::strcmp(argv[i], "--help")) { usage(argv[0]); return 0; }
    break;
  }
  if (i >= argc) { usage(argv[0]); return 2; }

  Client c(proxy, port);
  std::string cmd = argv[i++];

  if (cmd == "snapshot") return cmdSnapshot(c);
  if (cmd == "identity") return cmdIdentity(c);
  if (cmd == "hosts")    return cmdHosts(c);
  if (cmd == "sensors") {
    if (i >= argc) { std::fprintf(stderr, "sensors needs a domain\n"); return 2; }
    return cmdSensors(c, argv[i]);
  }
  if (cmd == "read") {
    if (i >= argc) { std::fprintf(stderr, "read needs a sensor name\n"); return 2; }
    return cmdRead(c, argv[i]);
  }
  if (cmd == "engine") {
    std::string domain;
    if (i < argc) domain = argv[i];
    else {
      std::string me = localFrogNetAddress();
      std::string dot1 = me.empty() ? "" : dotOneOf(me);
      Identity id = dot1.empty() ? Identity{} : c.echo(dot1);
      if (!id.ok) { std::fprintf(stderr, "identity failed; pass a domain explicitly\n"); return 1; }
      domain = id.domain;
    }
    return cmdRead(c, domain + ".SemanticProxy.Engine");
  }

  usage(argv[0]);
  return 2;
}
