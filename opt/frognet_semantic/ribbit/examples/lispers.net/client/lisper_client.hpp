// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lisper_client.hpp -- the LISP vendor's client: the LISP region's operations as C++ calls, over FNW1 to lisper-ram.
//
// Each method is one operation of the LISP region, run by lisper-ram in the memory itself: a whole Map-Register
// (authentication, site policy, registration, Map-Notify) is one call, as is a lookup, a site or a key. What a
// map-server front, an ETR or an operator's tool needs from the region, and nothing about how the region stores it.
#pragma once
#include <string>
#include "ram_client.hpp"
#include "version.hpp"

namespace lisper {

class Client : public ribbit::RamClient {
public:
    std::string vendor_id() const override { return "lisper"; }
    std::string api_path() const override { return LISPER_API; }

    // Any LISP region operation by name, its arguments a JSON object; returns the operation's result as JSON text.
    std::string operation(const std::string& name, const std::string& args_json);

    // A Map-Register as it arrived on the wire (hex) from `source`: authenticated, authorized, applied all-or-nothing.
    // Returns the Map-Notify to send back (hex), or "" when the register asked for none or was refused.
    std::string register_packet(const std::string& packet_hex, const std::string& source);
    // The registration a lookup for `prefix` resolves to (longest-prefix match), as JSON; "[]" when none does.
    std::string resolve(const std::string& iid, const std::string& prefix, const std::string& group = "");
    // A site: the EID-prefix a key authorizes. key_id/password as a Map-Register carries them.
    void add_site(const std::string& iid, const std::string& prefix, const std::string& group, bool accept_more_specifics,
                  int key_id, const std::string& password);
    void delete_site(const std::string& iid, const std::string& prefix, const std::string& group);
};

}  // namespace lisper
