// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// RibbitTransport.cpp -- [MT_REPLIES_FROM_CELLS_V1] a request for another node's fact is a read of that node's cell.
//
// Stock: our client sends N a packet with want_response on NODEINFO / POSITION / TELEMETRY; N's module builds the reply
// from N's own state and sends it back. On the memory N's state is already in N's cells, so the reply is built here, at
// the node serving the client, from one read: the owner's payload unchanged, from = N, request_id = the request.
// No cell: the routing error stock gives when the destination has no answer, NO_RESPONSE, from = N.
#ifdef ARCH_PORTDUINO
#include "RibbitTransport.h"
#include "MeshService.h"
#include "modules/RoutingModule.h"

namespace
{
struct Want {
    uint32_t node;
    std::string topic;
    bool found = false;
    std::vector<uint8_t> bytes;
    uint32_t t = 0;
};

void takeCell(const char *topic, uint32_t node, const uint8_t *b, size_t len, uint32_t t, void *ctx)
{
    auto *w = static_cast<Want *>(ctx);
    if (node != w->node || w->topic != topic)
        return;
    w->found = true;
    w->bytes.assign(b, b + len);
    w->t = t;
}
} // namespace

bool RibbitTransport::answerFromCell(const meshtastic_MeshPacket *p)
{
    if (!running || !p || !isFromUs(p) || isBroadcast(p->to) || p->which_payload_variant != meshtastic_MeshPacket_decoded_tag ||
        !p->decoded.want_response || !isFrogNetNode(p->to))
        return false;
    Want w{p->to, ""};
    switch (p->decoded.portnum) {
    case meshtastic_PortNum_NODEINFO_APP:
        w.topic = "user";
        break;
    case meshtastic_PortNum_POSITION_APP:
        w.topic = "pos";
        break;
    case meshtastic_PortNum_TELEMETRY_APP: {
        // the request names the variant it wants by which_variant; none named is device metrics, as stock answers
        meshtastic_Telemetry asked = meshtastic_Telemetry_init_zero;
        pb_decode_from_bytes(p->decoded.payload.bytes, p->decoded.payload.size, &meshtastic_Telemetry_msg, &asked);
        unsigned v = asked.which_variant ? asked.which_variant : meshtastic_Telemetry_device_metrics_tag;
        w.topic = "tel." + std::to_string(v);
        break;
    }
    default:
        return false;
    }
    mtr_read_facts(w.topic == "user" || w.topic == "pos" ? w.topic.c_str() : "tel.*", takeCell, &w);
    meshtastic_MeshPacket *reply = nullptr;
    if (w.found) {
        meshtastic_MeshPacket pkt;
        if (cellPacket(w.topic.c_str(), w.node, w.bytes.data(), w.bytes.size(), w.t, pkt)) {
            pkt.to = nodeDB->getNodeNum();
            pkt.channel = p->channel;
            pkt.decoded.request_id = p->id;
            pkt.priority = meshtastic_MeshPacket_Priority_RELIABLE;
            reply = packetPool.allocCopy(pkt);
        }
        LOG_INFO("[MT_RIBBIT] request id=0x%08x for (%s,!%08x) answered from the cell; nothing sent", p->id, w.topic.c_str(),
                 w.node);
    } else if (routingModule) {
        reply = routingModule->allocAckNak(meshtastic_Routing_Error_NO_RESPONSE, nodeDB->getNodeNum(), p->id, p->channel, 0);
        if (reply)
            reply->from = w.node;
        LOG_INFO("[MT_RIBBIT] request id=0x%08x for (%s,!%08x): no cell, NO_RESPONSE; nothing sent", p->id, w.topic.c_str(),
                 w.node);
    }
    if (reply && service)
        service->sendToPhone(reply);
    else if (reply)
        packetPool.release(reply);
    return true;
}
#endif
