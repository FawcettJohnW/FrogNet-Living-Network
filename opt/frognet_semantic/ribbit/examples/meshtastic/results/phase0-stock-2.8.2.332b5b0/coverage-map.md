# Phase 0 coverage map: Meshtastic defined client API vs. existing oracles, stock meshtasticd

Cell = frames/packets observed on the wire during that oracle's run against stock. `native-unit(static)` = native C++ suites whose source references the item (touches, not necessarily asserts).

## Summary

| section | items | covered by any wire oracle | covered by native-unit (static) | covered by none |
|---|---|---|---|---|
| ToRadio | 6 | 3 | 3 | 3 |
| FromRadio | 18 | 12 | 13 | 5 |
| AdminMessage | 58 | 9 | 24 | 32 |
| PortNum | 41 | 8 | 18 | 22 |

## ToRadio

| item | # | py-smokevirt | py-smokemesh | mcp-ci-device-mesh-e2e | rt-vectors | native-unit (static) |
|---|---|---|---|---|---|---|
| packet | 1 | 101 | 219 | 7 | 15 | 1: event_channel_phone_api |
| want_config_id | 3 | 238 | 84 | 7 | 6 | 2: phone_api_config_dump, stream_api |
| disconnect | 4 | — | — | — | — | — |
| xmodemPacket | 5 | — | — | — | — | — |
| mqttClientProxyMessage | 6 | — | — | — | — | — |
| heartbeat | 7 | 138 | 49 | 6 | 7 | 1: phone_api_config_dump |

**Gaps (no oracle at all): 3** — disconnect, xmodemPacket, mqttClientProxyMessage

## FromRadio

| item | # | py-smokevirt | py-smokemesh | mcp-ci-device-mesh-e2e | rt-vectors | native-unit (static) |
|---|---|---|---|---|---|---|
| packet | 2 | 62 | 215 | 17 | 52 | 2: phone_api_config_dump, stream_api |
| my_info | 3 | 237 | 84 | 7 | 6 | 1: phone_api_config_dump |
| node_info | 4 | 237 | 204 | 11 | 11 | 1: phone_api_config_dump |
| config | 5 | 2370 | 840 | 70 | 60 | 1: phone_api_config_dump |
| log_record | 6 | — | — | — | — | — |
| config_complete_id | 7 | 237 | 84 | 7 | 6 | 1: phone_api_config_dump |
| rebooted | 8 | — | — | — | — | — |
| moduleConfig | 9 | 4029 | 1428 | 119 | 102 | 2: phone_api_config_dump, stream_api |
| channel | 10 | 1896 | 672 | 56 | 48 | 1: phone_api_config_dump |
| queueStatus | 11 | 225 | 235 | 28 | 44 | 1: phone_api_config_dump |
| xmodemPacket | 12 | — | — | — | — | — |
| metadata | 13 | 237 | 84 | 7 | 6 | 1: phone_api_config_dump |
| mqttClientProxyMessage | 14 | — | — | — | — | — |
| fileInfo | 15 | 1190 | 483 | 39 | 34 | 1: phone_api_config_dump |
| clientNotification | 16 | — | — | — | — | 1: stream_api |
| deviceuiConfig | 17 | 237 | 84 | 7 | 6 | 1: phone_api_config_dump |
| lockdown_status | 18 | — | — | — | — | — |
| region_presets | 19 | 237 | 84 | 7 | 6 | 1: phone_api_config_dump |

**Gaps (no oracle at all): 5** — log_record, rebooted, xmodemPacket, mqttClientProxyMessage, lockdown_status

## AdminMessage

| item | # | py-smokevirt | py-smokemesh | mcp-ci-device-mesh-e2e | rt-vectors | native-unit (static) |
|---|---|---|---|---|---|---|
| get_channel_request | 1 | — | — | — | — | — |
| get_channel_response | 2 | — | — | — | — | — |
| get_owner_request | 3 | — | — | — | — | 1: admin_session_repro |
| get_owner_response | 4 | — | — | — | — | 1: admin_session_repro |
| get_config_request | 5 | 24 | 21 | — | 4 | 1: admin_session_repro |
| get_config_response | 6 | 13 | — | — | 4 | 1: admin_session_repro |
| get_module_config_request | 7 | — | — | — | — | 1: admin_session_repro |
| get_module_config_response | 8 | — | — | — | — | 3: admin_session_repro, module_config, tak_config |
| get_canned_message_module_messages_request | 10 | — | — | — | — | — |
| get_canned_message_module_messages_response | 11 | — | — | — | — | — |
| get_device_metadata_request | 12 | — | — | — | — | 1: admin_radio |
| get_device_metadata_response | 13 | — | — | — | — | — |
| get_ringtone_request | 14 | — | — | — | — | — |
| get_ringtone_response | 15 | — | — | — | — | — |
| get_device_connection_status_request | 16 | — | — | — | — | — |
| get_device_connection_status_response | 17 | — | — | — | — | — |
| set_ham_mode | 18 | — | — | — | — | 1: admin_radio |
| get_node_remote_hardware_pins_request | 19 | — | — | — | — | — |
| get_node_remote_hardware_pins_response | 20 | — | — | — | — | — |
| enter_dfu_mode_request | 21 | — | — | — | — | — |
| delete_file_request | 22 | — | — | — | — | — |
| set_scale | 23 | — | — | — | — | — |
| backup_preferences | 24 | — | — | — | — | — |
| restore_preferences | 25 | — | — | — | — | — |
| remove_backup_preferences | 26 | — | — | — | — | — |
| send_input_event | 27 | — | — | — | — | — |
| set_owner | 32 | 4 | — | — | 2 | 2: admin_session_repro, stream_api |
| set_channel | 33 | 14 | — | — | — | 2: admin_radio, fuzz_packets |
| set_config | 34 | 53 | 21 | 4 | 2 | 1: fuzz_packets |
| set_module_config | 35 | — | — | — | — | 1: fuzz_packets |
| set_canned_message_module_messages | 36 | — | — | — | — | — |
| set_ringtone_message | 37 | — | — | — | — | — |
| remove_by_nodenum | 38 | — | — | — | — | 1: fuzz_packets |
| set_favorite_node | 39 | — | — | — | — | 2: admin_radio, fuzz_packets |
| remove_favorite_node | 40 | — | — | — | — | 1: fuzz_packets |
| set_fixed_position | 41 | 2 | — | — | 2 | — |
| remove_fixed_position | 42 | 1 | — | — | — | 1: fuzz_packets |
| set_time_only | 43 | — | — | — | — | 1: fuzz_packets |
| get_ui_config_request | 44 | — | — | — | — | — |
| get_ui_config_response | 45 | — | — | — | — | — |
| store_ui_config | 46 | — | — | — | — | — |
| set_ignored_node | 47 | — | — | — | — | 2: admin_radio, fuzz_packets |
| remove_ignored_node | 48 | — | — | — | — | 1: fuzz_packets |
| toggle_muted_node | 49 | — | — | — | — | 2: admin_radio, fuzz_packets |
| begin_edit_settings | 64 | 1 | — | — | — | 1: admin_radio |
| commit_edit_settings | 65 | — | — | — | — | 1: admin_radio |
| add_contact | 66 | — | — | — | — | 1: fuzz_packets |
| key_verification | 67 | — | — | — | — | — |
| factory_reset_device | 94 | — | — | — | — | — |
| reboot_ota_seconds | 95 | — | — | — | — | — |
| exit_simulator | 96 | 2 | 21 | — | — | — |
| reboot_seconds | 97 | — | — | — | — | — |
| shutdown_seconds | 98 | — | — | — | — | — |
| factory_reset_config | 99 | — | — | — | — | — |
| nodedb_reset | 100 | — | — | — | — | — |
| ota_request | 102 | — | — | — | — | — |
| sensor_config | 103 | — | — | — | — | — |
| lockdown_auth | 104 | — | — | — | — | 1: stream_api |

**Gaps (no oracle at all): 32** — get_channel_request, get_channel_response, get_canned_message_module_messages_request, get_canned_message_module_messages_response, get_device_metadata_response, get_ringtone_request, get_ringtone_response, get_device_connection_status_request, get_device_connection_status_response, get_node_remote_hardware_pins_request, get_node_remote_hardware_pins_response, enter_dfu_mode_request, delete_file_request, set_scale, backup_preferences, restore_preferences, remove_backup_preferences, send_input_event, set_canned_message_module_messages, set_ringtone_message, get_ui_config_request, get_ui_config_response, store_ui_config, key_verification, factory_reset_device, reboot_ota_seconds, reboot_seconds, shutdown_seconds, factory_reset_config, nodedb_reset, ota_request, sensor_config

## PortNum

| item | # | py-smokevirt | py-smokemesh | mcp-ci-device-mesh-e2e | rt-vectors | native-unit (static) |
|---|---|---|---|---|---|---|
| UNKNOWN_APP | 0 | — | — | — | — | 1: mesh_module |
| TEXT_MESSAGE_APP | 1 | — | 8 | 12 | 6 | 19: support, ack_proof, event_channel_router, fuzz_packets … |
| REMOTE_HARDWARE_APP | 2 | — | — | — | — | — |
| POSITION_APP | 3 | — | — | — | 7 | 12: support, channel_keys, event_channel_phone_api, event_channel_router … |
| NODEINFO_APP | 4 | — | 90 | 4 | 12 | 7: fuzz_packets, meshpacket_serializer, mqtt, nodeinfo_send_window … |
| ROUTING_APP | 5 | 45 | 40 | 4 | 14 | 9: ack_proof, admin_radio, mesh_module, mqtt … |
| ADMIN_APP | 6 | 114 | 63 | 4 | 14 | 7: admin_session_repro, fuzz_packets, mesh_module, mqtt … |
| TEXT_MESSAGE_COMPRESSED_APP | 7 | — | — | — | — | — |
| WAYPOINT_APP | 8 | — | — | — | — | 8: event_channel_phone_api, event_channel_router, fuzz_packets, meshpacket_serializer … |
| AUDIO_APP | 9 | — | — | — | — | — |
| DETECTION_SENSOR_APP | 10 | — | — | — | — | 1: mqtt |
| ALERT_APP | 11 | — | — | — | — | — |
| KEY_VERIFICATION_APP | 12 | — | — | — | — | 3: key_verification, packet_signing, pki_admin_fallback |
| REMOTE_SHELL_APP | 13 | — | — | — | — | — |
| REPLY_APP | 32 | — | — | — | — | — |
| IP_TUNNEL_APP | 33 | — | — | — | — | — |
| PAXCOUNTER_APP | 34 | — | — | — | — | — |
| STORE_FORWARD_PLUSPLUS_APP | 35 | — | — | — | — | — |
| NODE_STATUS_APP | 36 | — | — | — | — | — |
| MESH_BEACON_APP | 37 | — | — | — | — | 2: fuzz_packets, mesh_beacon |
| PAGING_APP | 38 | — | — | — | — | — |
| SERIAL_APP | 64 | — | — | — | — | — |
| STORE_FORWARD_APP | 65 | — | — | — | — | 1: mesh_module |
| RANGE_TEST_APP | 66 | — | — | — | — | 1: mqtt |
| TELEMETRY_APP | 67 | — | — | — | 7 | 9: fuzz_packets, mesh_module, meshpacket_serializer, mqtt … |
| ZPS_APP | 68 | — | — | — | — | — |
| SIMULATOR_APP | 69 | 4 | 230 | 8 | 19 | — |
| TRACEROUTE_APP | 70 | — | 3 | — | 5 | 3: fuzz_packets, packet_signing, traceroute_nexthop |
| NEIGHBORINFO_APP | 71 | — | — | — | — | 2: fuzz_packets, mesh_module |
| ATAK_PLUGIN | 72 | — | — | — | — | — |
| MAP_REPORT_APP | 73 | — | — | — | — | 3: event_channel_router, mqtt, position_precision |
| POWERSTRESS_APP | 74 | — | — | — | — | — |
| LORAWAN_BRIDGE | 75 | — | — | — | — | — |
| RETICULUM_TUNNEL_APP | 76 | — | — | — | — | — |
| CAYENNE_APP | 77 | — | — | — | — | — |
| ATAK_PLUGIN_V2 | 78 | — | — | — | — | 1: mesh_module |
| LORA_OTA_APP | 79 | — | — | — | — | — |
| GROUPALARM_APP | 112 | — | — | — | — | — |
| PRIVATE_APP | 256 | — | — | — | — | 3: mesh_module, packet_signing, pki_admin_fallback |
| ATAK_FORWARDER | 257 | — | — | — | — | — |
| MAX | 511 | — | — | — | — | — |

**Gaps (no oracle at all): 22** — REMOTE_HARDWARE_APP, TEXT_MESSAGE_COMPRESSED_APP, AUDIO_APP, ALERT_APP, REMOTE_SHELL_APP, REPLY_APP, IP_TUNNEL_APP, PAXCOUNTER_APP, STORE_FORWARD_PLUSPLUS_APP, NODE_STATUS_APP, PAGING_APP, SERIAL_APP, ZPS_APP, ATAK_PLUGIN, POWERSTRESS_APP, LORAWAN_BRIDGE, RETICULUM_TUNNEL_APP, CAYENNE_APP, LORA_OTA_APP, GROUPALARM_APP, ATAK_FORWARDER, MAX
