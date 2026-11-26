// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// comms-channel -- [THE_CALL_IS_UNDER_THE_FRAME_V1] the owner's command for a Role's channel setting in the
// Communicator's memory: (channel, setting, <Role>). It is the Communicator's core/channel_admin.py, with the same
// words and the same rules.
//
//   comms-channel --ram HOST:PORT show [ROLE]
//   comms-channel --ram HOST:PORT set ROLE --protocol tcp|chorus|openqasm
//   comms-channel --ram HOST:PORT set ROLE --seal [--algorithm aes-256-gcm|chacha20-poly1305]
//   comms-channel --ram HOST:PORT set ROLE --new-key
//   comms-channel --ram HOST:PORT set ROLE --algorithm NAME
//   comms-channel --ram HOST:PORT set ROLE --unseal
//   comms-channel --ram HOST:PORT move ROLE NEWHOST:NEWPORT      [THE_MEMORY_MOVES_V1] the memory is at another address
//   comms-channel secret ROLE              the fingerprint and step of this machine's secret for the Role
//   (--secrets DIR, or FROGNET_CHANNEL_SECRETS: where this machine's secrets are; ROLE defaults to mediahost;
//    --wait SECONDS, default 10: how long a write waits for every end's report)
//
// show lists every end that is there and what it is running. set returns when every end's report shows the setting,
// and says which end ignored it or has not taken it.
//
// move: another comms-ram is running at NEWHOST:NEWPORT. The setting is written there first, with the chat, then here;
// every participant and the media server learn the address from their held read on the setting, attach there, assert
// their own tuples and set up their held reads again. A call's audio and video are not touched: they go to the media
// server. It returns when every end that was here reports from there.
//
// The work is the library's (src/comms/channel.cpp channel_change), which a window's Security tab calls too.
//
// One write. --seal, --new-key and --algorithm each put a new random number in the setting: every sending end
// computes a new key from it and switches in its own stream. A protocol and an encryption are not changed in one
// write. The setting carries a check made with the Role's secret on THIS machine, so run it where the secret is: an
// end that holds the secret ignores a setting whose check does not verify. A Role with its own secret file has its
// step raised with each new number, and every machine moves its file forward.
#include "comms/channel.hpp"
#include <cstdio>
#include <iostream>

using namespace comms;

static std::string line(const std::map<std::string, std::string>& f) {
    std::string s;
    for (auto& kv : f) if (kv.first != "check") s += (s.empty() ? "" : ", ") + kv.first + "=" + kv.second;
    return s.empty() ? "tcp, not sealed (no setting)" : s;
}

static int run(int argc, char** argv) {
    std::string ram, secrets = channel_secrets_dir(), verb, role = CHANNEL_ROLE, to;
    double wait = 10.0;
    ChannelChange ch;
    int words = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string k = argv[i];
        auto v = [&]() -> std::string { if (i + 1 >= argc) throw std::runtime_error(k + " needs a value"); return argv[++i]; };
        if (k == "--ram") ram = v(); else if (k == "--secrets") secrets = v(); else if (k == "--protocol") ch.protocol = v();
        else if (k == "--algorithm") ch.algorithm = v(); else if (k == "--seal") ch.seal = true; else if (k == "--unseal") ch.unseal = true;
        else if (k == "--new-key") ch.new_key = true; else if (k == "--wait") wait = std::stod(v());
        else if (++words == 1) verb = k; else if (words == 2) role = k; else to = k;
    }
    if (verb == "secret") { std::printf("%s: this machine's secret is %s\n", role.c_str(), channel_secret_line(role, secrets).c_str()); return 0; }
    if ((verb != "show" && verb != "set" && verb != "move") || ram.empty() || (verb == "move" && to.rfind(':') == std::string::npos)) {
        std::cerr << "usage: comms-channel --ram HOST:PORT show [ROLE] | set ROLE --protocol P | --seal | --new-key | --algorithm A | --unseal | move ROLE NEWHOST:NEWPORT;  comms-channel secret ROLE\n";
        return 2;
    }
    const auto colon = ram.rfind(':');
    const std::string host = ram.substr(0, colon); const int port = std::stoi(ram.substr(colon + 1));
    if (verb == "show") {
        const ChannelNow n = channel_show(host, port, role);
        std::printf("%s: %s\n", role.c_str(), line(n.setting).c_str());
        for (auto& e : n.ends) { auto f = e.report; f.erase("role"); f.erase("end"); std::printf("  %s: %s\n", e.end.c_str(), line(f).c_str()); }
        return 0;
    }
    if (verb == "move") { ch.move_host = to.substr(0, to.rfind(':')); ch.move_port = std::stoi(to.substr(to.rfind(':') + 1)); }
    const ChannelDone d = channel_change(host, port, role, ch, wait, secrets);
    std::printf("%s: %s%s\n", role.c_str(), line(d.setting).c_str(),
                d.checked ? "" : "  (this machine holds no secret for it: the setting carries no check, and an end that holds one ignores it)");
    if (verb == "move") std::printf("  the memory is at %s now; %d line(s) of chat carried there\n", to.c_str(), d.chat_carried);
    for (auto& l : d.lines) std::printf("  %s\n", l.c_str());
    std::printf("  %s\n", d.summary().c_str());
    return 0;
}

int main(int argc, char** argv) {
    try { return run(argc, argv); }
    catch (const std::exception& e) { std::fprintf(stderr, "comms-channel: %s\n", e.what()); return 1; }
}
