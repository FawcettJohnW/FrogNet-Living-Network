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
// frognet.h — the whole client contract, in one header.
//
// A FrogNet client implements NO part of FrogNet. It makes HTTP requests to the
// local proxy with a Host header naming the real destination, and parses JSON.
// That is the entire surface. There is no SDK, no generated stub, no vendored
// client library, and no version to keep in step with the nodes.
#pragma once

#include <map>
#include <string>
#include <vector>

namespace frognet {

// ---------------------------------------------------------------------------
// JSON — a minimal reader. Enough for the API envelope and arbitrary jsonData.
// ---------------------------------------------------------------------------
struct Json {
  enum class Type { Null, Bool, Number, String, Array, Object };
  Type type = Type::Null;
  bool boolean = false;
  double number = 0;
  std::string str;
  std::vector<Json> array;
  std::vector<std::pair<std::string, Json>> object;  // insertion order preserved

  bool isNull()   const { return type == Type::Null; }
  bool isArray()  const { return type == Type::Array; }
  bool isObject() const { return type == Type::Object; }

  const Json* find(const std::string& key) const;
  std::string atString(const std::string& key) const;
  std::string toCompactString() const;

  static Json parse(const std::string& text, std::string* error = nullptr);
};

// ---------------------------------------------------------------------------
// Parsers — pure functions. No I/O, no sockets, no clock. These are what the
// shared vectors in ../frognet_monitor_shared/parser_vectors.json exercise, and
// they must agree byte for byte with the Python and C# implementations.
// ---------------------------------------------------------------------------

// One CSV line from frognet_echo.php: fqdn,eth0IP,wlan0IP,wlan1IP
//
// [IDENTITY_FAILS_LOUD_V1] Four fields with a non-empty NAME, or it failed.
// An empty interface field is accurate data — a node with no carrier on eth0
// genuinely has no eth0 address. An empty name is a failure, because every node
// answers to the hostname "FrogNetHost" and a name that is not the domain is
// not an identity. There is no fallback and nothing to reconstruct.
struct Identity {
  bool ok = false;
  std::string domain, eth0, wlan0, wlan1;
};
Identity parseEchoLine(const std::string& body);

// /etc/hosts → [(ip, name)] for lines that describe a FrogNet node.
// 10/8 only; 10.253/16 (tunnel transit) and 10.254/16 (chorus virtual) excluded;
// a FrogNetHost.<name> alias required, and the name is the part after the dot.
struct HostEntry { std::string ip, name; };
std::vector<HostEntry> parseEtcHosts(const std::string& content);

// Own address → the .1 that can state this pond's identity.
// Returns empty for anything outside 10/8 or inside the two reserved ranges:
// a node carries those addresses but is not identified by them.
std::string dotOneOf(const std::string& ip);

// ---------------------------------------------------------------------------
// HTTP — every request goes to the local proxy. Never to :9009.
// ---------------------------------------------------------------------------
struct HttpResult {
  bool ok = false;
  long status = 0;
  std::string body, error;
};

class Client {
 public:
  // proxy_host/port is where requests are SENT; the Host header says where they
  // are FOR. That indirection is what lets the proxy do hop-by-hop conversion,
  // and it is why a client never resolves databasehost.frognet itself — the
  // role floats, and the name is the point.
  explicit Client(std::string proxy_host = "127.0.0.1", int proxy_port = 80);

  HttpResult get(const std::string& path, const std::string& host_header,
                 long timeout_sec = 5) const;

  // Unwraps {"ok":true,"rows":[...]} / {"ok":true,"row":{...}}.
  Json apiGet(const std::string& query, long timeout_sec = 5) const;

  // Identity of the pond reachable at dot_one, via frognet_echo.php.
  Identity echo(const std::string& dot_one, long timeout_sec = 5) const;

  // getHosts.php, falling back to /etc/hosts.
  std::vector<HostEntry> discoverHosts() const;

  // Every sensor whose name begins "<domain>." — server-side filtered, so only
  // this host's rows cross the wire. Name prefix ONLY: SensorAddress keys on a
  // /24, which is a network and not a host, and leaks other nodes' rows in.
  Json sensorsForHost(const std::string& domain) const;

  // The two calls. Name → SensorID, then SensorID → jsonData.
  Json sensorDetail(const std::string& sensor_name) const;

 private:
  std::string proxy_host_;
  int proxy_port_;
};

// Own non-loopback 10/8 address, excluding the two reserved ranges.
// Empty if this machine is not on a FrogNet.
std::string localFrogNetAddress();

std::string urlEncode(const std::string& s);

}  // namespace frognet
