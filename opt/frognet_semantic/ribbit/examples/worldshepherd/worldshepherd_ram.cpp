// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// worldshepherd_ram.cpp -- worldshepherd-ram: WorldShepherd's own RAM manager, one executable.
//
// WorldShepherd needs nothing from its host but the memory. A SARA's read ticket is cells that SARAs write, each its
// own, and read (ribbit_claim_store.py): there is no ticket logic here, so this host has no operations of its own,
// as chat-ram has none. It keeps no file either. When this process ends its memory is gone, and the memory that
// answers next is another one: each SARA then writes its own truth into it again (ribbit_claim_store.py,
// [A_SARA_REASSERTS_ITS_OWN_V1]). A cell stays for the life of the memory, not of its writer's session: a ticket a
// SARA consumed is still consumed after that SARA has gone.
//
// Run it on a listening socket that outlives it (systemd socket activation, or RIBBIT_LISTEN_FD from a parent: the
// platform's [LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]) and a SARA's connect made while it is being started again waits
// in the socket's queue and completes when the new one accepts.
//
//     worldshepherd-ram [--listen HOST:PORT]        answers /worldshepherd-api
#include "ram_host.hpp"
#define WORLDSHEPHERD_VERSION "worldshepherd-ram 0.7 [NO_OPERATIONS_V1] [NO_FILE_V1]"

class WorldShepherdRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "worldshepherd"; }
    std::string vendor_version() const override { return WORLDSHEPHERD_VERSION; }
    bool own_flag_decides() const override { return true; }     // a cell stays until it is written again or removed
};

int main(int argc, char** argv) { return WorldShepherdRam().run(argc, argv); }
