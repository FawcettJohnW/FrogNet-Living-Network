// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// ram_client.hpp -- the Ribbit RAM client as a base class: the participant's side of a vendor's region.
//
// The mirror of ram_host.hpp. A vendor's client library derives from RamClient, says who it is, and exposes its API
// as ordinary C++ methods. Each method is one call to the vendor's RAM host (<vendor>-ram), carried by FNW1, the
// semantic wire: a repeated request travels as a reference, an unchanged answer as SAME, a changed one as the fields
// that changed; large bodies go on the session's dedicated data socket, so they never hold up small calls. The vendor
// wraps its class in a plain C ABI (an opaque handle, C strings in, library-owned strings out), and any language that
// can load a shared library -- Python ctypes, Java JNA, C# P/Invoke -- calls the same methods.
//
//     class ChatClient : public ribbit::RamClient {
//     public:
//         std::string vendor_id() const override { return "chat"; }
//         void send(const std::string& to, const std::string& from, const std::string& text);
//     };
//     ChatClient c; c.connect("ram.example.net", 8800); c.send("Bob", "Dave", "hello");
//
// Nothing here falls back: an unreachable host is frogram::Unreachable, a refusal is frogram::Refused.
#pragma once
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
#include "frogram.hpp"

namespace ribbit {

class RamClient {
public:
    virtual ~RamClient();

    // ---- only the vendor can say this -------------------------------------------------------------------------
    // Whose region this client speaks to. It names the endpoint (/<vendor_id>-api by default) exactly as the host's
    // vendor_id() does, so a client and its host agree by construction.
    virtual std::string vendor_id() const = 0;

    // ---- defaults a vendor may override ------------------------------------------------------------------------
    virtual std::string api_path() const { return "/" + vendor_id() + "-api"; }

    // ---- the platform -----------------------------------------------------------------------------------------
    // Opens the session to the vendor's RAM host: its two connections and its data socket. Throws Unreachable.
    void connect(const std::string& host, int port);
    bool connected() const { return session_ != nullptr; }
    // The host and port connect() was given.
    const std::string& host() const { return host_; }
    int port() const { return port_; }
    void close();

    // Cells, addressed by (service, variable, instance). A write replaces the cell and returns its id -- the memory's
    // own write order. A read with after >= 0 and wait_s > 0 is held by the host until something is written past
    // `after`. bag_json is a JSON object's text.
    uint64_t write(const std::string& service, const std::string& variable, const std::string& instance,
                   const std::string& bag_json);
    std::vector<frogram::Cell> read(const std::string& service, const std::string& variable,
                                    const std::string& instance = "", int64_t after = -1, double wait_s = 0);
    void remove(uint64_t id);

    // One of the vendor's own operations (the host's RamHost::operation): POST <api>?op=<op> with a JSON body. Returns
    // the host's answer; an answer with ok:false is Refused, carrying the host's error.
    frogram::Json call(const std::string& op, const std::string& body_json, double timeout_s = 15.0);

    // The session itself, for what the methods above do not cover (its statistics, its token).
    frogram::Session& session();

    // A JSON value as text: for turning what a read returns back into a string a C caller can own.
    static std::string to_json(const frogram::Json& v);
    static std::string to_json(const std::vector<frogram::Cell>& cells);

private:
    std::string host_; int port_ = 0;
    std::unique_ptr<frogram::Session> session_;
    std::unique_ptr<frogram::Memory> memory_;
};

}  // namespace ribbit
