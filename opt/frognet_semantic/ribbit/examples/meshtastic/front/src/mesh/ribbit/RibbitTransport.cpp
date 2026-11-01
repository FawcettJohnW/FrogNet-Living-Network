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
#include "mesh/generated/meshtastic/config.pb.h"
#include "mesh/generated/meshtastic/module_config.pb.h"
#include "mesh/generated/meshtastic/channel.pb.h"
#include <cstring>

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

// [MT_CONFIG_CELLS_V1] The node's config as its own permanent cells: (mtf, cfg.<variant>, !N) per Config variant,
// (mtf, mcfg.<variant>, !N) per ModuleConfig variant, (mtf, chan.<i>, !N) per channel -- each the protobuf a client's
// config dump carries, with the node's secrets stripped before the write: the channel PSK and the security private key
// never leave the node (the public and admin keys are public). Written at start and after every save. Nothing in the
// contract reads them yet; they are the extended API's view of a node.
namespace
{
template <typename T> void writeCfg(const char *topic, const pb_msgdesc_t *fields, const T &v)
{
    uint8_t buf[512];
    size_t n = pb_encode_to_bytes(buf, sizeof buf, fields, &v);
    if (n)
        mtr_write_fact(topic, nodeDB->getNodeNum(), buf, n);
}
} // namespace

void RibbitTransport::writeOwnConfig()
{
    if (!running)
        return;
    char t[16];
    meshtastic_Config c = meshtastic_Config_init_zero;
#define CFG(TAG, FIELD, SRC)                                                                                                     \
    c.which_payload_variant = TAG;                                                                                               \
    c.payload_variant.FIELD = SRC;                                                                                               \
    snprintf(t, sizeof t, "cfg.%u", (unsigned)TAG);                                                                              \
    writeCfg(t, &meshtastic_Config_msg, c);
    CFG(meshtastic_Config_device_tag, device, config.device)
    CFG(meshtastic_Config_position_tag, position, config.position)
    CFG(meshtastic_Config_power_tag, power, config.power)
    CFG(meshtastic_Config_network_tag, network, config.network)
    CFG(meshtastic_Config_display_tag, display, config.display)
    CFG(meshtastic_Config_lora_tag, lora, config.lora)
    CFG(meshtastic_Config_bluetooth_tag, bluetooth, config.bluetooth)
    {
        meshtastic_Config_SecurityConfig sec = config.security;
        sec.private_key.size = 0; // the node's secret stays in the node
        memset(sec.private_key.bytes, 0, sizeof sec.private_key.bytes);
        CFG(meshtastic_Config_security_tag, security, sec)
    }
#undef CFG
    meshtastic_ModuleConfig m = meshtastic_ModuleConfig_init_zero;
#define MCFG(TAG, FIELD)                                                                                                         \
    m.which_payload_variant = TAG;                                                                                               \
    m.payload_variant.FIELD = moduleConfig.FIELD;                                                                                \
    snprintf(t, sizeof t, "mcfg.%u", (unsigned)TAG);                                                                             \
    writeCfg(t, &meshtastic_ModuleConfig_msg, m);
    MCFG(meshtastic_ModuleConfig_mqtt_tag, mqtt)
    MCFG(meshtastic_ModuleConfig_serial_tag, serial)
    MCFG(meshtastic_ModuleConfig_external_notification_tag, external_notification)
    MCFG(meshtastic_ModuleConfig_store_forward_tag, store_forward)
    MCFG(meshtastic_ModuleConfig_range_test_tag, range_test)
    MCFG(meshtastic_ModuleConfig_telemetry_tag, telemetry)
    MCFG(meshtastic_ModuleConfig_canned_message_tag, canned_message)
    MCFG(meshtastic_ModuleConfig_audio_tag, audio)
    MCFG(meshtastic_ModuleConfig_remote_hardware_tag, remote_hardware)
    MCFG(meshtastic_ModuleConfig_neighbor_info_tag, neighbor_info)
    MCFG(meshtastic_ModuleConfig_ambient_lighting_tag, ambient_lighting)
    MCFG(meshtastic_ModuleConfig_detection_sensor_tag, detection_sensor)
    MCFG(meshtastic_ModuleConfig_paxcounter_tag, paxcounter)
    MCFG(meshtastic_ModuleConfig_statusmessage_tag, statusmessage)
    MCFG(meshtastic_ModuleConfig_traffic_management_tag, traffic_management)
#undef MCFG
    unsigned written = 0;
    for (size_t i = 0; i < channelFile.channels_count; i++) {
        meshtastic_Channel ch = channelFile.channels[i];
        if (!ch.has_settings || ch.role == meshtastic_Channel_Role_DISABLED)
            continue;
        ch.settings.psk.size = 0; // the channel's secret stays in the node
        memset(ch.settings.psk.bytes, 0, sizeof ch.settings.psk.bytes);
        snprintf(t, sizeof t, "chan.%u", (unsigned)i);
        writeCfg(t, &meshtastic_Channel_msg, ch);
        written++;
    }
    LOG_INFO("[MT_RIBBIT] own config cells written: 8 config, 15 module config, %u channels; PSKs and private key stripped",
             written);
}

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

std::atomic<int> RibbitTransport::clients{0}; // [MT_READ_MARKS_V1]
