// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// RibbitTransport.h -- [MT_PACKETS_ON_THE_RAM_V1] Phase 1, mechanical step 1: the node's packets on the shared RAM.
//
// Same shape as UdpMulticastHandler, which it sits beside: onSend() is a write of the encoded MeshPacket into this
// node's own cells; a held read on the family hands every other node's packet to router->enqueueReceivedMessage with the
// same ingress rules the UDP path applies. Nothing is asked of another node and nothing is acknowledged.
//
// Enabled by MESHTASTIC_RIBBIT=HOST:PORT (a memory elsewhere) or here:HOST:PORT (this process holds it).
#pragma once
#ifdef ARCH_PORTDUINO
#include "configuration.h"
#include "Default.h"
#include "main.h"
#include "mesh/Router.h"
#include "mesh/NodeDB.h"
#include "mesh/Channels.h"
#include "mesh/generated/meshtastic/telemetry.pb.h"
#include "mt_ribbit.h"
#include <array>
#include <cstring>
#include <cstdlib>

class RibbitTransport;
extern RibbitTransport *ribbitTransport;

class RibbitTransport final
{
  public:
    // Returns true when the memory is reached and the held read is running.
    bool start(const char *where, uint32_t me)
    {
        running = mtr_start(where, me, &RibbitTransport::onPacket, this) == 0;
        LOG_INFO("[MT_RIBBIT] %s: %s", mtr_version(), running ? "packets on the shared RAM" : "memory not reached");
        LOG_INFO("[MT_RIBBIT] %s", mtr_front_version());
        return running;
    }

    bool onSend(const meshtastic_MeshPacket *mp)
    {
        if (!running || !mp)
            return false;
        // [MT_ONLY_OWN_PACKETS_V1] a node writes only its own packets: every node reads the original from its owner's
        // ring, so a relay would be a second copy of another node's truth. Other transports (UDP, LoRa) still relay.
        if (!isFromUs(mp)) {
            LOG_INFO("[MT_RIBBIT] not written: id=0x%08x from=0x%08x is another node's packet (relay)", mp->id, mp->from);
            return false;
        }
        // [MT_FACT_CHANGES_TO_CLIENTS_V1] a packet that carries one of our own facts is not written: the fact is in its
        // cell, and every other node's clients get it from their held read on the cells
        for (uint32_t id : factIds)
            if (id && id == mp->id) {
                LOG_INFO("[MT_RIBBIT] not written: id=0x%08x carries an own fact (its cell is written)", mp->id);
                return false;
            }
        uint8_t buffer[meshtastic_MeshPacket_size];
        size_t n = pb_encode_to_bytes(buffer, sizeof(buffer), &meshtastic_MeshPacket_msg, mp);
        // [MT_MESSAGES_BY_FAMILY_V1] a broadcast goes to its channel's family, anything else to its addressee's
        LOG_INFO("[MT_RIBBIT] write id=0x%08x from=0x%08x to=0x%08x ch=%u len=%u -> %s", mp->id, mp->from, mp->to,
                 (unsigned)mp->channel, (unsigned)n, isBroadcast(mp->to) ? "msg|<channel>" : "dm|<to>");
        mtr_send(buffer, n, mp->to, mp->channel);
        return true;
    }

    // [MT_MESSAGES_BY_FAMILY_V1] the node holds the family of every channel it has; called when channels are (re)loaded
    void holdChannels()
    {
        if (!running)
            return;
        for (ChannelIndex i = 0; i < channels.getNumChannels(); i++) {
            const meshtastic_Channel &c = channels.getByIndex(i);
            int16_t h = channels.getHash(i);
            if (c.has_settings && c.role != meshtastic_Channel_Role_DISABLED && h >= 0) {
                mtr_hold_channel((uint8_t)h);
                LOG_INFO("[MT_RIBBIT] channel %u (%s) hash %u: holding its family", (unsigned)i, channels.getName(i), (unsigned)h);
            }
        }
    }

    // [MT_OWN_FACTS_V1] A packet this node sends that carries one of its own facts also writes that fact into the
    // node's own latest-value cell (mtf, <topic>, !<node>), at the moment stock tells the mesh. Telemetry is one cell
    // per variant, named by the variant's field number (tel.<n>).
    // [MT_OWN_FACTS_ON_CHANGE_V1] this node no longer has the fact: its cell carries no payload
    void clearOwnFact(const char *topic)
    {
        if (!running)
            return;
        mtr_clear_fact(topic, nodeDB->getNodeNum());
        LOG_INFO("[MT_RIBBIT] own %s cell cleared", topic);
    }

    void onOwnFact(const meshtastic_MeshPacket &d)
    {
        if (!running || d.which_payload_variant != meshtastic_MeshPacket_decoded_tag)
            return;
        const auto &pl = d.decoded.payload;
        char topic[24];
        switch (d.decoded.portnum) {
        case meshtastic_PortNum_NODEINFO_APP:
            strcpy(topic, "user");
            break;
        case meshtastic_PortNum_POSITION_APP:
            strcpy(topic, "pos");
            break;
        case meshtastic_PortNum_NODE_STATUS_APP:
            strcpy(topic, "status");
            break;
        case meshtastic_PortNum_TELEMETRY_APP: {
            meshtastic_Telemetry t = meshtastic_Telemetry_init_zero;
            if (!pb_decode_from_bytes(pl.bytes, pl.size, &meshtastic_Telemetry_msg, &t)) {
                LOG_WARN("[MT_RIBBIT] own telemetry id=0x%08x does not decode, not written", d.id);
                return;
            }
            snprintf(topic, sizeof topic, "tel.%u", (unsigned)t.which_variant);
            break;
        }
        default:
            return;
        }
        factIds[factNext++ % factIds.size()] = d.id;
        LOG_INFO("[MT_RIBBIT] fact %s !%08x len=%u (packet id=0x%08x)", topic, nodeDB->getNodeNum(), (unsigned)pl.size, d.id);
        mtr_write_fact(topic, nodeDB->getNodeNum(), pl.bytes, pl.size);
    }

    // [MT_OWN_FACTS_V1] At start the node writes its own user cell: its truth is known at boot, so readers do not wait
    // for its next NodeInfo broadcast. Built as NodeInfoModule builds it (owner, id from the node number).
    void writeOwnUser()
    {
        if (!running)
            return;
        meshtastic_User u = owner;
        strcpy(u.id, nodeDB->getNodeId().c_str());
        uint8_t buf[meshtastic_User_size];
        size_t n = pb_encode_to_bytes(buf, sizeof(buf), &meshtastic_User_msg, &u);
        LOG_INFO("[MT_RIBBIT] fact user %s '%s' len=%u (start)", u.id, u.long_name, (unsigned)n);
        mtr_write_fact("user", nodeDB->getNodeNum(), buf, n);
    }

    // [MT_ACK_AT_THE_EDGE_V1] D8 read both ways: N is a FrogNet node when its user cell is in the view. While the fact
    // view runs, the NodeDB holds only nodes that came from their owners' cells, so presence + user = the cell exists.
    static bool isFrogNetNode(NodeNum n)
    {
        if (!MT_FACTS_FROM_RAM() || !ribbitTransport || !ribbitTransport->running)
            return false;
        const meshtastic_NodeInfoLite *info = nodeDB->getMeshNode(n);
        return nodeInfoLiteHasUser(info);
    }

    // [MT_REPLIES_FROM_CELLS_V1] A request our client makes of a FrogNet node for one of its facts (node info,
    // position, telemetry) is answered here from the owner's cell; the request goes nowhere. true: answered (the
    // caller releases p). RibbitTransport.cpp.
    bool answerFromCell(const meshtastic_MeshPacket *p);

    // [MT_FACT_CHANGES_TO_CLIENTS_V1] One owner's cell as the packet a client expects for it: a broadcast from the owner,
    // as if heard, the payload the owner's own protobuf unchanged. Used for the replay after the config dump and for
    // every change to a cell while a client is connected. false: not a fact topic, or larger than a payload.
    static bool cellPacket(const char *topic, uint32_t node, const uint8_t *b, size_t len, uint32_t t,
                           meshtastic_MeshPacket &pkt)
    {
        meshtastic_PortNum port;
        uint32_t kind;
        if (!strcmp(topic, "user"))
            port = meshtastic_PortNum_NODEINFO_APP, kind = port;
        else if (!strcmp(topic, "pos"))
            port = meshtastic_PortNum_POSITION_APP, kind = port;
        else if (!strcmp(topic, "status"))
            port = meshtastic_PortNum_NODE_STATUS_APP, kind = port;
        else if (!strncmp(topic, "tel.", 4))
            port = meshtastic_PortNum_TELEMETRY_APP, kind = port * 256 + (uint32_t)atoi(topic + 4);
        else
            return false;
        pkt = meshtastic_MeshPacket_init_default;
        if (len > sizeof(pkt.decoded.payload.bytes))
            return false;
        pkt.from = node;
        pkt.to = NODENUM_BROADCAST;
        pkt.rx_time = t;
        pkt.has_rx_time = t >= 1577836800u; // a wall-clock time, not a boot counter
        uint32_t h = node;                  // stable per cell and write time: unchanged history is not a new packet
        h = h * 2654435761u + t;
        h = h * 2654435761u + kind;
        pkt.id = h ? h : 1;
        uint8_t hopLimit = Default::getConfiguredOrDefaultHopLimit(config.lora.hop_limit);
        pkt.hop_start = hopLimit; // on the memory every node is direct
        pkt.hop_limit = hopLimit;
        pkt.priority = meshtastic_MeshPacket_Priority_BACKGROUND;
        pkt.transport_mechanism = meshtastic_MeshPacket_TransportMechanism_TRANSPORT_LORA;
        pkt.which_payload_variant = meshtastic_MeshPacket_decoded_tag;
        pkt.decoded.portnum = port;
        memcpy(pkt.decoded.payload.bytes, b, len);
        pkt.decoded.payload.size = (pb_size_t)len;
        return true;
    }

  private:
    bool running = false;
    std::array<uint32_t, 16> factIds{}; // ids of the last packets that carried an own fact
    size_t factNext = 0;

    // On the held read's thread, in the family's write order: the same ingress rules as UdpMulticastHandler::onReceive.
    static void onPacket(const uint8_t *bytes, size_t len, void *ctx)
    {
        meshtastic_MeshPacket mp = meshtastic_MeshPacket_init_zero;
        if (!pb_decode_from_bytes(bytes, len, &meshtastic_MeshPacket_msg, &mp) || !router ||
            mp.which_payload_variant != meshtastic_MeshPacket_encrypted_tag) {
            LOG_WARN("[MT_RIBBIT] read len=%u: not an encrypted MeshPacket, dropped", (unsigned)len);
            return;
        }
        if (isFromUs(&mp)) {
            LOG_WARN("[MT_RIBBIT] read from=0x%08x is our own number, dropped", mp.from);
            return;
        }
        if (mp.hop_limit > HOP_MAX || mp.hop_start > HOP_MAX) {
            LOG_WARN("[MT_RIBBIT] read hop_limit(%u)/hop_start(%u) out of range, dropped", mp.hop_limit, mp.hop_start);
            return;
        }
        LOG_INFO("[MT_RIBBIT] read id=0x%08x from=0x%08x to=0x%08x len=%u", mp.id, mp.from, mp.to, (unsigned)len);
        mp.pki_encrypted = false;
        mp.public_key.size = 0;
        mp.tx_after = 0;
        mp.priority = meshtastic_MeshPacket_Priority_UNSET;
        UniquePacketPoolPacket p = packetPool.allocUniqueCopy(mp);
        if (!p)
            return;
        p->rx_snr = 0;
        p->rx_rssi = 0;
        p->has_rx_rssi = false;
        router->enqueueReceivedMessage(p.release());
    }
};

extern RibbitTransport *ribbitTransport;
#endif
