// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// comms_ram.cpp -- comms-ram: the Communicator's shared memory, one executable.
//
// Presence, chat, the five call-state tuples, each media host's connection info: all of it is tuples on the default
// Ribbit API, written by the party with standing to know it and read by everyone else. The streams themselves go to
// the media host, a separate role that every call meets at; it publishes where it listens as a tuple here, and
// callers read that tuple and dial it. The host needs nothing but the memory, so it has no operations of its own.
//
//     comms-ram [--listen HOST:PORT]        answers /comms-api
#include "ram_host.hpp"
#define COMMS_RAM_VERSION "communicator 2.2"

class CommsRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "comms"; }
    std::string vendor_version() const override { return COMMS_RAM_VERSION; }
};

int main(int argc, char** argv) { return CommsRam().run(argc, argv); }
