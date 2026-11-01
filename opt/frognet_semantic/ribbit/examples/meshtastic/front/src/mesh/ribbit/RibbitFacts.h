// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// RibbitFacts.h -- one held read on the fact space (mtf, *): every owner's user, position, telemetry and status cells.
//
// [MT_OTHER_NODES_ARE_CELLS_V1] Nothing about another node is kept here: a read of another node is a read of its cell
// (NodeDB.cpp). This held read exists for the one thing a read cannot do, telling connected clients when a cell changes
// ([MT_FACT_CHANGES_TO_CLIENTS_V1]). The held read's thread only wakes this thread (the router's own wake: setInterval(0)
// + runASAP); the rows are taken on the main loop. No received packet writes another
// node into the NodeDB.
#pragma once
#ifdef ARCH_PORTDUINO
#include "concurrency/OSThread.h"
#include "gps/RTC.h"
#include "main.h"
#include "mesh/MeshService.h"
#include "mesh/NodeDB.h"
#include "mesh/ribbit/RibbitTransport.h"
#include "mesh/generated/meshtastic/telemetry.pb.h"
#include "mt_ribbit.h"
#include <cstring>


class RibbitFacts : public concurrency::OSThread
{
  public:
    RibbitFacts() : concurrency::OSThread("RibbitFacts") {} // enabled; runs only when woken (interval INT32_MAX)

    bool start()
    {
        if (mtr_hold_facts(&RibbitFacts::wake, this) != 0)
            return false;
        LOG_INFO("[MT_RIBBIT] other nodes: read from their owners' cells; changes to our clients (held read on mtf)");
        wake(this); // what was there when the hold started
        return true;
    }

  protected:
    int32_t runOnce() override
    {
        size_t n = mtr_take_facts(&RibbitFacts::apply, this);
        if (n)
            LOG_INFO("[MT_RIBBIT] took %u fact rows", (unsigned)n);
        return INT32_MAX; // stay enabled; the held read's wake brings it back
    }

  private:
    // on the held read's thread: schedule this thread, nothing else
    static void wake(void *ctx)
    {
        auto *self = static_cast<RibbitFacts *>(ctx);
        self->setInterval(0); // the router's own wake (Router::setReceivedMessage)
        runASAP = true;
    }

    // on the main loop
    static void apply(const char *topic, uint32_t node, const uint8_t *b, size_t len, int gone, uint32_t t, int live,
                      void *)
    {
        // [MT_FACT_CHANGES_TO_CLIENTS_V1] a cell written while held reaches this node's clients as the packet they expect;
        // what was there at the start reached them in the config dump and its replay
        if (gone || node == nodeDB->getNodeNum() || !service)
            return;
        meshtastic_MeshPacket pkt;
        if (!RibbitTransport::cellPacket(topic, node, b, len, t, pkt))
            return;
        // [MT_RADIO_EDGE_V1] a memory node's user cell, now or changed: when this node bridges stock nodes, they must know
        // the memory nodes too (a PKI DM either way needs the other's key), so the cell goes on the air as that node's
        // NodeInfo -- a relay of its own announcement, from = the node
        if (strcmp(topic, "user") == 0 && ribbitTransport->bridging() && !ribbitTransport->isBridged(node))
            ribbitTransport->announceToAir(pkt);
        if (!live)
            return;
        meshtastic_MeshPacket *p = packetPool.allocCopy(pkt);
        if (!p)
            return;
        LOG_INFO("[MT_RIBBIT] cell %s !%08x changed: to our clients (port %u)", topic, node, (unsigned)pkt.decoded.portnum);
        service->sendToPhone(p);
    }

};

extern RibbitFacts *ribbitFacts;

// [MT_RADIO_EDGE_V1] On the main loop: what the held read put in toAir goes out the stock way (UDP, the radio), for the
// nodes this node hears for. Woken by the held read's thread, like RibbitFacts.
class RibbitBridge : public concurrency::OSThread
{
  public:
    RibbitBridge() : OSThread("RibbitBridge")
    {
        ribbitTransport->airWake = [this] {
            setInterval(0);
            runASAP = true;
        };
    }

  protected:
    int32_t runOnce() override
    {
        while (meshtastic_MeshPacket *p = ribbitTransport->toAir.dequeuePtr(0)) {
            LOG_INFO("[MT_RIBBIT] bridge: id=0x%08x from !%08x to !%08x on the air", p->id, p->from, p->to);
            router->send(p); // onSend refuses it (another node's packet); bridgeToStock lets it through
        }
        return INT32_MAX;
    }
};
extern RibbitBridge *ribbitBridge;
#endif
