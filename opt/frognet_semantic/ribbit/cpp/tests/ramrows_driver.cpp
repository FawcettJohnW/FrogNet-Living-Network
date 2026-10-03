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
// ramrows_driver -- one JSON op per stdin line: {"op":"check","ref":TEXT,"new":TEXT}. Answers with what the handler did:
//   {"ok":true,"hold":false}                                 extract said cannot-hold
//   {"ok":true,"hold":true,"exact":B,"diff_bytes":N,"raw_bytes":M,"fields":F,"changed":C}
// exact: rebuild(learn(ref), decode(encode_reply_diff(extract(new), reference=extract(ref)))) == new, byte for byte.
#include "ramrows.hpp"
#include <iostream>
using frogram::Json; using pyv::Dict; using pyv::List;
int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            Json r = Json::parse(line); std::string ref = r["ref"].s, nw = r["new"].s;
            Dict frag = ramrows::learn(ref);
            auto fnew = ramrows::extract(nw, frag);
            if (!fnew) { std::cout << "{\"ok\":true,\"hold\":false}\n" << std::flush; continue; }
            auto fref = ramrows::extract(ref, frag);
            if (!fref) throw std::logic_error("the reference cannot hold itself");
            Dict refd; for (auto& kv : *fref) refd.set(kv.first, kv.second);
            auto d = semcodec::encode_reply_diff(1, *fnew, &refd, true);
            List vals;
            if (d.identical) for (auto& kv : *fref) vals.push_back(kv.second);
            else {
                std::vector<std::string> names; for (auto& kv : *fref) names.push_back(kv.first);
                auto dec = semcodec::decode_reply(d.bytes, names, &refd);
                for (auto& kv : dec) vals.push_back(kv.second);
            }
            std::string out = ramrows::rebuild(frag, vals);
            size_t changed = 0; for (size_t i = 0; i < fnew->size(); ++i) if (!semcodec::values_equal((*fnew)[i].second, (*fref)[i].second)) ++changed;
            std::cout << "{\"ok\":true,\"hold\":true,\"exact\":" << (out == nw ? "true" : "false") << ",\"diff_bytes\":" << (d.identical ? 0 : d.bytes.size())
                      << ",\"raw_bytes\":" << nw.size() << ",\"fields\":" << fnew->size() << ",\"changed\":" << changed << "}\n" << std::flush;
        } catch (const std::exception& e) { std::cout << "{\"ok\":false,\"error\":" << Json::quote(e.what()) << "}\n" << std::flush; }
    }
}
