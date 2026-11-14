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
#include "PointerQueue.h"
#include "mesh/NodeDB.h"
#include "mesh/Channels.h"
#include "mesh/generated/meshtastic/telemetry.pb.h"
#include "mt_ribbit.h"
#include <array>
#include <set>
#include <functional>
#include <atomic>
#include "concurrency/Lock.h"
#include "concurrency/LockGuard.h"
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

    // [MT_NO_GATES_V1] D8, the one decision: after the memory write, does this packet also go the stock way (UDP, the
    // radio) for nodes that are not on the memory? A unicast to a node whose user cell is on the memory: no, it is
    // there already. Anything else -- a broadcast, a unicast to a node with no cell, a relay of another node's packet
    // -- yes: stock nodes off the memory may need it. This is the only stock Meshtastic the node still speaks; the
    // radio edge (Phase 3) is the other half of it, inbound.
    bool bridgeToStock(const meshtastic_MeshPacket *p)
    {
        if (!p)
            return false;
        if (!isBroadcast(p->to) && isFromUs(p) && isFrogNetNode(p->to) && !isBridged(p->to)) {
            LOG_INFO("[MT_RIBBIT] id=0x%08x to !%08x: on the memory; nothing goes the stock way", p->id, p->to);
            return false;
        }
        return true;
    }

    // [MT_RADIO_EDGE_V1] The inbound half of the bridge (D8): a packet heard from a node that is not on the memory --
    // over the radio, UDP, anything but the memory -- becomes that node's truth on the memory, written by this node
    // on its behalf: its facts as its cells (user, pos, tel.<variant>, status), the packet as heard into this node's
    // ring of the right family (the packet carries who said it). From then on the stock node looks like any other to
    // every reader on the memory. This node also holds (mt, dm|!S) for it, and puts what lands there on the air
    // (onPacket). p: decoded; enc: the same packet as it arrived, still encrypted (what readers decrypt).
    void fromStock(const meshtastic_MeshPacket *p, const meshtastic_MeshPacket *enc);
    // the same for a packet this node cannot decrypt (a PKI DM for a memory node): as heard, nothing decoded
    void fromStockOpaque(const meshtastic_MeshPacket *enc);
    // is this node bridging (has it heard a stock node off the memory)? which nodes are bridged?
    bool bridging() { concurrency::LockGuard g(&rowsMutex); return !bridged.empty(); }
    bool isBridged(NodeNum n) { concurrency::LockGuard g(&rowsMutex); return bridged.count(n) > 0; }
    // a memory node's NodeInfo (from its user cell) for the air: queued for the main loop like a bridged DM
    void announceToAir(const meshtastic_MeshPacket &pkt)
    {
        if (meshtastic_MeshPacket *out = packetPool.allocCopy(pkt)) {
            out->to = 0xffffffffu; // a broadcast on the air, as stock announces
            out->hop_limit = 3;
            out->hop_start = 3;
            toAir.enqueue(out, 0);
            airWake();
            LOG_INFO("[MT_RIBBIT] bridge: !%08x's user cell to the air as its NodeInfo", pkt.from);
        }
    }
    // the stock nodes this node hears for (fromStock adds them)
    std::set<NodeNum> bridged;
    // did this packet come to us from the memory? (then nothing is bridged: its writer owns it)
    bool fromMemory(const meshtastic_MeshPacket *p)
    {
        RowOf r;
        return p && rowOf(p->id, r);
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
    void writeOwnConfig(); // [MT_CONFIG_CELLS_V1]

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
        if (!ribbitTransport || !ribbitTransport->running) // null only before start
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

  public:
    // [MT_READ_MARKS_V1] which memory row a received packet came from, so the hand-off to a client can mark it
    struct RowOf {
        uint32_t id = 0;
        uint64_t row = 0;
        char family[24] = {};
    };
    bool rowOf(uint32_t id, RowOf &out)
    {
        concurrency::LockGuard g(&rowsMutex);
        for (auto &r : rows)
            if (r.id && r.id == id) {
                out = r;
                return true;
            }
        return false;
    }
    void forgetRow(uint32_t id)
    {
        concurrency::LockGuard g(&rowsMutex);
        for (auto &r : rows)
            if (r.id == id) r.id = 0;
    }
    // a client was handed the packet (its frame was written): the node's mark on its family moves to that row
    void handedId(uint32_t id)
    {
        RowOf r;
        if (!rowOf(id, r))
            return;
        mtr_mark(r.family, r.row);
        forgetRow(id);
        LOG_INFO("[MT_RIBBIT] handed id=0x%08x to a client: mark %s = row %llu", id, r.family, (unsigned long long)r.row);
    }
    // connected clients of this node (PhoneAPI instances past their config start)
    static std::atomic<int> clients;

    // [MT_RADIO_EDGE_V1] packets for nodes off the memory, from the held read's thread to the main loop
    PointerQueue<meshtastic_MeshPacket> toAir{16};
    // [MT_MQTT_EDGE_V1] the id of the toAir packet RibbitBridge is handing to router->send right now (main loop, synchronous):
    // Router::send publishes it to MQTT as well, as this node's own sends are -- a memory node's packet was never
    // received here, so stock's receive-time uplink never sees it
    uint32_t bridgingOut = 0;
    std::function<void()> airWake = [] {};

  private:
    std::array<RowOf, 64> rows{};
    size_t rowNext = 0;
    concurrency::Lock rowsMutex;
    void remember(uint32_t id, uint64_t row, const char *family)
    {
        concurrency::LockGuard g(&rowsMutex);
        RowOf &r = rows[rowNext++ % rows.size()];
        r.id = id;
        r.row = row;
        strncpy(r.family, family, sizeof r.family - 1);
        r.family[sizeof r.family - 1] = 0;
    }

    // On the held read's thread, in the family's write order: the same ingress rules as UdpMulticastHandler::onReceive.
    static void onPacket(const uint8_t *bytes, size_t len, uint64_t row, const char *family, void *ctx)
    {
        auto *self = static_cast<RibbitTransport *>(ctx);
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
        LOG_INFO("[MT_RIBBIT] read id=0x%08x from=0x%08x to=0x%08x len=%u (%s row %llu)", mp.id, mp.from, mp.to, (unsigned)len,
                 family, (unsigned long long)row);
        self->remember(mp.id, row, family); // [MT_READ_MARKS_V1]
        // [MT_RADIO_EDGE_V1] the outbound half of the bridge: a row in a dm family we hold for a node that is not on
        // the memory is for that node -- it goes the stock way (UDP, the radio), as stock would send it
        if (strncmp(family, "dm|!", 4) == 0 && strtoul(family + 4, nullptr, 16) != nodeDB->getNodeNum()) {
            LOG_INFO("[MT_RIBBIT] bridge: id=0x%08x for !%08x (not on the memory): to the air", mp.id, mp.to);
            if (meshtastic_MeshPacket *out = packetPool.allocCopy(mp)) {
                out->rx_time = 0;
                self->toAir.enqueue(out, 0); // sent on the main loop (RibbitBridge), as stock sends
                self->airWake();
            }
            return;
        }
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
