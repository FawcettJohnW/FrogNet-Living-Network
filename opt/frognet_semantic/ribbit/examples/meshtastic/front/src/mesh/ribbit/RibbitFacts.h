// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// RibbitFacts.h -- [MT_NODEDB_IS_A_HELD_VIEW_V1] Phase 1, mechanical step 2b: the NodeDB's other nodes come from their
// owners' fact cells, not from received packets.
//
// One held read on the fact space (mtf, *) collects every owner's user, position, telemetry and status cells. The held
// read's thread only wakes this thread (the router's own wake: setInterval(0) + runASAP); the rows are applied here, on
// the main loop, through the same NodeDB functions the receive path used, so every NodeDB reader is unchanged. A gone
// row (the owner's session ended) removes the node. While this is running, the receive path no longer writes other
// nodes into the NodeDB (ribbitFactsActive).
#pragma once
#ifdef ARCH_PORTDUINO
#include "concurrency/OSThread.h"
#include "gps/RTC.h"
#include "main.h"
#include "mesh/NodeDB.h"
#include "mesh/generated/meshtastic/telemetry.pb.h"
#include "mt_ribbit.h"
#include <cstring>

extern bool ribbitFactsActive;

class RibbitFacts : public concurrency::OSThread
{
  public:
    RibbitFacts() : concurrency::OSThread("RibbitFacts") {} // enabled; runs only when woken (interval INT32_MAX)

    bool start()
    {
        if (mtr_hold_facts(&RibbitFacts::wake, this) != 0)
            return false;
        ribbitFactsActive = true;
        LOG_INFO("[MT_RIBBIT] NodeDB other nodes: from their owners' fact cells (held read on mtf)");
        wake(this); // what was there when the hold started
        return true;
    }

  protected:
    int32_t runOnce() override
    {
        size_t n = mtr_take_facts(&RibbitFacts::apply, this);
        if (n)
            LOG_INFO("[MT_RIBBIT] applied %u fact rows", (unsigned)n);
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
    static void apply(const char *topic, uint32_t node, const uint8_t *b, size_t len, int gone, void *)
    {
        if (node == nodeDB->getNodeNum())
            return;
        if (gone) {
            LOG_INFO("[MT_RIBBIT] fact %s !%08x gone: node removed", topic, node);
            if (!strcmp(topic, "user"))
                nodeDB->removeNodeByNum(node);
            return;
        }
        // the node was heard now: the stock last_heard path, fed by the cell's arrival
        meshtastic_MeshPacket heard = meshtastic_MeshPacket_init_zero;
        heard.from = node;
        heard.which_payload_variant = meshtastic_MeshPacket_decoded_tag;
        heard.rx_time = getValidTime(RTCQualityFromNet);
        heard.has_rx_time = heard.rx_time != 0;
        nodeDB->updateFrom(heard);
        if (!strcmp(topic, "user")) {
            meshtastic_User u = meshtastic_User_init_zero;
            if (!pb_decode_from_bytes(b, len, &meshtastic_User_msg, &u))
                return bad(topic, node);
            snprintf(u.id, sizeof(u.id), "!%08x", node);
            bool changed = nodeDB->updateUser(node, u, 0, false);
            LOG_INFO("[MT_RIBBIT] fact user !%08x '%s' changed=%d", node, u.long_name, changed);
        } else if (!strcmp(topic, "pos")) {
            meshtastic_Position p = meshtastic_Position_init_zero;
            if (!pb_decode_from_bytes(b, len, &meshtastic_Position_msg, &p))
                return bad(topic, node);
            nodeDB->updatePosition(node, p);
            LOG_INFO("[MT_RIBBIT] fact pos !%08x", node);
        } else if (!strncmp(topic, "tel.", 4)) {
            meshtastic_Telemetry t = meshtastic_Telemetry_init_zero;
            if (!pb_decode_from_bytes(b, len, &meshtastic_Telemetry_msg, &t))
                return bad(topic, node);
            nodeDB->updateTelemetry(node, t);
            LOG_INFO("[MT_RIBBIT] fact %s !%08x", topic, node);
        } else if (!strcmp(topic, "status")) {
            meshtastic_StatusMessage s = meshtastic_StatusMessage_init_zero;
            if (!pb_decode_from_bytes(b, len, &meshtastic_StatusMessage_msg, &s))
                return bad(topic, node);
            nodeDB->setNodeStatus(node, s);
            LOG_INFO("[MT_RIBBIT] fact status !%08x", node);
        }
    }

    static void bad(const char *topic, uint32_t node) { LOG_WARN("[MT_RIBBIT] fact %s !%08x does not decode", topic, node); }
};

extern RibbitFacts *ribbitFacts;
#endif
