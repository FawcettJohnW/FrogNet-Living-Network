// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mqtt_ram.cpp -- mqtt-ram: the memory MQTT's boxes share. [MEMORY_IS_MEMORY_V1]
//
// It has no operations of its own. Every box writes its truths to its own tuples (service mqtt: msg, retain, sub, at,
// session, sys -- region_client.cpp) and reads the others' with held reads; the memory, the wire and the triggers
// are the platform's. MQTT's state is never asked for, only read.
//
//     mqtt-ram [--listen HOST:PORT] [--announce-fd N]      answers /mqtt-api
#include "ram_host.hpp"
#ifndef MQTT_RAM_VERSION
#define MQTT_RAM_VERSION "0.4"
#endif

class MqttRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "mqtt"; }
    std::string vendor_version() const override { return MQTT_RAM_VERSION; }
    std::string default_listen() const override { return "0.0.0.0:8883"; }
};

int main(int argc, char** argv) { return MqttRam().run(argc, argv); }
