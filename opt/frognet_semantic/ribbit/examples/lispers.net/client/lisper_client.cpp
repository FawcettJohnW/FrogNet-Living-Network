// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "lisper_client.hpp"

namespace lisper {
using frogram::Json;

std::string Client::operation(const std::string& name, const std::string& args_json) {
    Json r = call("lisp", "{\"operation\":" + Json::quote(name) + ",\"args\":" + args_json + "}");
    return r["result_json"].s;
}

std::string Client::register_packet(const std::string& packet_hex, const std::string& source) {
    Json r = Json::parse(operation("wire.register_notify", "{\"hex\":" + Json::quote(packet_hex) + ",\"source\":" + Json::quote(source) + "}"));
    return r["notify_hex"].s;
}

std::string Client::resolve(const std::string& iid, const std::string& prefix, const std::string& group) {
    return operation("resolution.get", "{\"iid\":" + Json::quote(iid) + ",\"prefix\":" + Json::quote(prefix) + ",\"group\":" + Json::quote(group) + "}");
}

void Client::add_site(const std::string& iid, const std::string& prefix, const std::string& group, bool ams, int key_id,
                      const std::string& password) {
    operation("site.add", "{\"iid\":" + Json::quote(iid) + ",\"prefix\":" + Json::quote(prefix) + ",\"group\":" + Json::quote(group)
              + ",\"accept_more_specifics\":" + (ams ? "true" : "false") + ",\"key_id\":" + std::to_string(key_id)
              + ",\"password\":" + Json::quote(password) + "}");
}

void Client::delete_site(const std::string& iid, const std::string& prefix, const std::string& group) {
    operation("site.delete", "{\"iid\":" + Json::quote(iid) + ",\"prefix\":" + Json::quote(prefix) + ",\"group\":" + Json::quote(group) + "}");
}

}  // namespace lisper
