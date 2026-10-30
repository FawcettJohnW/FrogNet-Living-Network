#!/usr/bin/env python3
"""Provision stock meshtasticd sim nodes, over the client API only, to the meshtastic-mcp
harness session profile (the values its test_00_bake would compile into userPrefs.jsonc),
so its baked_mesh fixture verifies them as baked. No firmware change, no reflash.

Usage: mcp_sim_provision.py SEED HOST:PORT [HOST:PORT ...]
"""
import os
import re
import sys
import time

from meshtastic.protobuf import config_pb2, module_config_pb2  # noqa: F401
from meshtastic.tcp_interface import TCPInterface

sys.path.insert(0, "/home/claude/mesh/mcp/src")
from meshtastic_mcp import userprefs  # noqa: E402


def connect(hostport, tries=60):
    h, p = hostport.split(":")
    for _ in range(tries):
        try:
            return TCPInterface(hostname=h, portNumber=int(p))
        except Exception:
            time.sleep(1)
    raise SystemExit(f"cannot connect {hostport}")


def main():
    seed, *targets = sys.argv[1:]
    prof = userprefs.build_testing_profile(psk_seed=seed, channel_name="McpTest", channel_num=88,
                                           region="US", modem_preset="LONG_FAST", enable_ui_log=True)
    psk = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", prof["USERPREFS_CHANNEL_0_PSK"]))
    for t in targets:
        # 1. LoRa: region, preset, slot, ignore_mqtt (one write; region change restarts the daemon)
        i = connect(t)
        n = i.localNode
        lora = n.localConfig.lora
        lora.region = config_pb2.Config.LoRaConfig.RegionCode.US
        lora.use_preset = True
        lora.modem_preset = config_pb2.Config.LoRaConfig.ModemPreset.LONG_FAST
        lora.channel_num = int(prof["USERPREFS_LORACONFIG_CHANNEL_NUM"])
        lora.ignore_mqtt = True
        n.writeConfig("lora")
        time.sleep(1)
        i.close()
        time.sleep(4)
        # 2. MQTT off, primary channel name / PSK / precision / up-down links
        i = connect(t)
        n = i.localNode
        n.moduleConfig.mqtt.enabled = False
        n.writeConfig("mqtt")
        ch = n.channels[0]
        ch.settings.name = prof["USERPREFS_CHANNEL_0_NAME"]
        ch.settings.psk = psk
        ch.settings.module_settings.position_precision = int(prof["USERPREFS_CHANNEL_0_PRECISION"])
        ch.settings.uplink_enabled = False
        ch.settings.downlink_enabled = False
        n.writeChannel(0)
        time.sleep(1)
        i.close()
        time.sleep(4)
        # 3. read back
        i = connect(t)
        n = i.localNode
        lc = n.localConfig.lora
        c0 = n.channels[0].settings
        print(f"{t} node=!{i.myInfo.my_node_num:08x} region={lc.region} preset={lc.modem_preset} slot={lc.channel_num} "
              f"ch0={c0.name!r} psk_ok={c0.psk == psk} prec={c0.module_settings.position_precision}")
        i.close()


if __name__ == "__main__":
    main()
