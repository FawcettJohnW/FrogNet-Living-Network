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
// http.cpp — every request goes to the local proxy on port 80, with a Host
// header naming the real destination. Never a socket to daemon port 9009.
#include "frognet.h"

#include <curl/curl.h>

#include <cstring>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/types.h>

namespace frognet {
namespace {

size_t sink(void* data, size_t sz, size_t n, void* user) {
  static_cast<std::string*>(user)->append(static_cast<char*>(data), sz * n);
  return sz * n;
}

struct CurlGlobal {
  CurlGlobal()  { curl_global_init(CURL_GLOBAL_DEFAULT); }
  ~CurlGlobal() { curl_global_cleanup(); }
};
const CurlGlobal g_curl_init;

bool startsWith(const std::string& s, const std::string& p) {
  return s.size() >= p.size() && s.compare(0, p.size(), p) == 0;
}

}  // namespace

std::string urlEncode(const std::string& s) {
  CURL* c = curl_easy_init();
  if (!c) return s;
  char* enc = curl_easy_escape(c, s.c_str(), static_cast<int>(s.size()));
  std::string out = enc ? enc : s;
  if (enc) curl_free(enc);
  curl_easy_cleanup(c);
  return out;
}

Client::Client(std::string proxy_host, int proxy_port)
    : proxy_host_(std::move(proxy_host)), proxy_port_(proxy_port) {}

HttpResult Client::get(const std::string& path, const std::string& host_header,
                       long timeout_sec) const {
  HttpResult r;
  CURL* c = curl_easy_init();
  if (!c) { r.error = "curl_easy_init failed"; return r; }

  std::string url = "http://" + proxy_host_ + ":" + std::to_string(proxy_port_) + path;

  struct curl_slist* hdrs = nullptr;
  if (!host_header.empty())
    hdrs = curl_slist_append(hdrs, ("Host: " + host_header).c_str());
  // The proxy speaks a compressed wire between nodes; asking for identity here
  // keeps the client out of that negotiation entirely.
  hdrs = curl_slist_append(hdrs, "Accept-Encoding: identity");

  curl_easy_setopt(c, CURLOPT_URL, url.c_str());
  curl_easy_setopt(c, CURLOPT_HTTPHEADER, hdrs);
  curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, sink);
  curl_easy_setopt(c, CURLOPT_WRITEDATA, &r.body);
  curl_easy_setopt(c, CURLOPT_TIMEOUT, timeout_sec);
  curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, timeout_sec);
  curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 0L);

  CURLcode rc = curl_easy_perform(c);
  if (rc == CURLE_OK) {
    curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &r.status);
    r.ok = (r.status >= 200 && r.status < 300);
    if (!r.ok) r.error = "HTTP " + std::to_string(r.status);
  } else {
    r.error = curl_easy_strerror(rc);
  }

  curl_slist_free_all(hdrs);
  curl_easy_cleanup(c);
  return r;
}

Json Client::apiGet(const std::string& query, long timeout_sec) const {
  // databasehost.frognet floats. The name goes in the Host header and the
  // pond resolves it; a client that cached an address would be wrong the first
  // time the role moved.
  HttpResult r = get("/api.php?" + query, "databasehost.frognet", timeout_sec);
  if (!r.ok) return Json{};

  std::string err;
  Json body = Json::parse(r.body, &err);
  if (!err.empty()) return Json{};

  // Unwrap {"ok":true,"rows":[...]} / {"ok":true,"row":{...}}
  if (body.isObject()) {
    if (const Json* rows = body.find("rows")) return *rows;
    if (const Json* row  = body.find("row"))  return *row;
  }
  return body;
}

Identity Client::echo(const std::string& dot_one, long timeout_sec) const {
  // Straight to the .1, not through the proxy's Host indirection: this asks a
  // specific machine who it is, and the answer must come from that machine.
  Client direct(dot_one, 80);
  HttpResult r = direct.get("/frognet_echo.php", "", timeout_sec);
  if (!r.ok) return Identity{};
  return parseEchoLine(r.body);
}

std::vector<HostEntry> Client::discoverHosts() const {
  HttpResult r = get("/getHosts.php", "", 5);
  if (r.ok) {
    std::string err;
    Json body = Json::parse(r.body, &err);
    if (err.empty() && body.isArray()) {
      std::vector<HostEntry> out;
      for (const Json& h : body.array) {
        std::string ip = h.atString("ip");
        if (ip.empty()) continue;
        std::string name = h.atString("name");
        out.push_back(HostEntry{ip, name.empty() ? ip : name});
      }
      if (!out.empty()) return out;
    }
  }
  // Fallback to the same file, parsed by the same rules as the Python.
  FILE* f = std::fopen("/etc/hosts", "r");
  if (!f) return {};
  std::string content;
  char buf[4096];
  size_t n;
  while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) content.append(buf, n);
  std::fclose(f);
  return parseEtcHosts(content);
}

Json Client::sensorsForHost(const std::string& domain) const {
  if (domain.empty()) return Json{};
  // Server-side filter on the domain-prefixed NAME only. SensorAddress keys on
  // a /24, which is a network rather than a host, so it pulls in every node
  // sharing that network.
  return apiGet("entity=sensors&action=list&SensorName__like=" +
                urlEncode(domain + ".%"));
}

Json Client::sensorDetail(const std::string& sensor_name) const {
  // Step 1: exact name -> SensorID
  Json rows = apiGet("entity=sensors&action=list&SensorName=" +
                     urlEncode(sensor_name) + "&limit=1", 3);
  if (!rows.isArray() || rows.array.empty()) return Json{};
  Json sensor = rows.array[0];

  std::string sid = sensor.atString("SensorID");
  if (sid.empty()) return sensor;   // metadata only

  // Step 2: SensorID -> jsonData
  Json sd = apiGet("entity=sensor_data&action=get&SensorID=" + urlEncode(sid), 3);
  if (sd.isObject()) {
    if (const Json* jd = sd.find("jsonData")) {
      Json payload = *jd;
      // jsonData arrives as a STRING holding JSON. Parse it once, at the edge,
      // so everything above works with a structure.
      if (payload.type == Json::Type::String) {
        std::string err;
        Json inner = Json::parse(payload.str, &err);
        if (err.empty() && !inner.isNull()) payload = inner;
      }
      sensor.object.emplace_back("jsonData", std::move(payload));
    }
  }
  return sensor;
}

std::string localFrogNetAddress() {
  struct ifaddrs* ifa = nullptr;
  if (getifaddrs(&ifa) != 0) return "";
  std::string found;
  for (struct ifaddrs* p = ifa; p; p = p->ifa_next) {
    if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET) continue;
    // Skip loopback INTERFACES, not just the 127.0.0.1 address. A 10/8 alias on
    // lo is not a pond attachment, and answering identity from one would name a
    // pond this machine is not actually on. Found by running both ports against
    // the simulator: C# excluded it and C++ did not, so they disagreed about
    // whether this machine was on a FrogNet at all.
    if (p->ifa_flags & IFF_LOOPBACK) continue;
    if (!(p->ifa_flags & IFF_UP)) continue;
    char buf[INET_ADDRSTRLEN] = {0};
    auto* sin = reinterpret_cast<struct sockaddr_in*>(p->ifa_addr);
    if (!inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof buf)) continue;
    std::string ip(buf);
    if (ip == "127.0.0.1") continue;
    if (!startsWith(ip, "10.")) continue;
    // Carried by a node, but not the node: transit and chorus addresses are not
    // a basis for identity.
    if (startsWith(ip, "10.253.") || startsWith(ip, "10.254.")) continue;
    found = ip;
    break;
  }
  freeifaddrs(ifa);
  return found;
}

}  // namespace frognet
