# lispers.net map-server / map-resolver surface -- coverage under the acceptance suite

Source: /tmp/lispers/lispers.net-master/lisp. Coverage: lispers-coverage.json. Reachability: static call graph from each entry point (by function name).

## UDP -- map-server / map-resolver

| entry point | handler | handler ran | reachable functions | ran | lines ran / reachable |
|---|---|---|---|---|---|
| Map-Request | `lisp_process_map_request` | yes | 161 | 87 | 1085 / 2823 (38%) |
| Map-Register | `lisp_process_map_register` | yes | 128 | 90 | 1206 / 2570 (47%) |
| Map-Notify-Ack | `lisp_process_map_notify_ack` | yes | 53 | 44 | 668 / 1323 (50%) |
| Map-Referral | `lisp_process_map_referral` | yes | 105 | 72 | 869 / 1952 (45%) |
| Info-Request | `lisp_process_info_request` | yes | 32 | 30 | 562 / 1156 (49%) |
| ECM (Encapsulated Control Message) | `lisp_process_ecm` | yes | 162 | 88 | 1099 / 2841 (39%) |

## UDP -- xTR side (not driven by this suite)

| entry point | handler | handler ran | reachable functions | ran | lines ran / reachable |
|---|---|---|---|---|---|
| Map-Reply | `lisp_process_map_reply` | **NO** | 173 | 74 | 847 / 2939 (29%) |
| Map-Notify (unicast) | `lisp_process_unicast_map_notify` | **NO** | 104 | 64 | 788 / 1967 (40%) |
| Info-Reply | `lisp_process_info_reply` | **NO** | 57 | 45 | 557 / 1202 (46%) |

## Configuration -- lisp.config commands of the map-server and map-resolver

| entry point | handler | handler ran | reachable functions | ran | lines ran / reachable |
|---|---|---|---|---|---|
| map-server: `lisp site` | `lisp_site_command` | yes | 38 | 33 | 334 / 490 (68%) |
| map-server: `lisp ms-authoritative-prefix` | `lisp_ms_auth_prefix_command` | yes | 27 | 24 | 157 / 286 (55%) |
| map-server: `lisp map-server-peer` | `lisp_ms_map_server_peer_command` | **NO** | 34 | 28 | 164 / 365 (45%) |
| map-server: `lisp eid-crypto-hash` | `lisp_ms_eid_crypto_hash_command` | **NO** | 15 | 13 | 62 / 133 (47%) |
| map-server: `lisp encryption-keys` | `lisp_ms_encryption_keys_command` | yes | 2 | 2 | 16 / 20 (80%) |
| map-server: `lisp geo-coordinates` | `lisp_geo_command` | **NO** | 29 | 11 | 124 / 505 (25%) |
| map-server: `lisp explicit-locator-path` | `lisp_elp_command` | **NO** | 14 | 12 | 56 / 131 (43%) |
| map-server: `lisp replication-list-entry` | `lisp_rle_command` | **NO** | 24 | 21 | 116 / 217 (53%) |
| map-server: `lisp json` | `lisp_json_command` | **NO** | 24 | 21 | 85 / 129 (66%) |
| map-resolver: `lisp ddt-root` | `lisp_ddt_root_command` | **NO** | 27 | 24 | 161 / 309 (52%) |
| map-resolver: `lisp referral-cache` | `lisp_referral_cache_command` | **NO** | 35 | 30 | 190 / 387 (49%) |

## Totals (map-server / map-resolver surface)

315 reachable functions, 137 ran; 1840 of 5427 reachable lines ran (34%).

## Reachable functions that never ran (largest first)

| lines | function | file:line |
|---|---|---|
| 165 | `decode` | lisp.py:2622 |
| 106 | `lisp_send_map_request` | lisp.py:16260 |
| 77 | `encode` | lisp.py:4546 |
| 72 | `lisp_process_rloc_probe_reply` | lisp.py:18484 |
| 64 | `fragment` | lisp.py:2405 |
| 64 | `decrypt` | lisp.py:2171 |
| 59 | `lisp_ms_map_server_peer_command` | lisp-ms.py:309 |
| 54 | `lisp_referral_cache_command` | lisp-mr.py:130 |
| 51 | `lisp_update_default_routes` | lisp.py:16738 |
| 48 | `lisp_send_multicast_map_notify` | lisp.py:9693 |
| 45 | `lisp_build_map_referral` | lisp.py:7467 |
| 44 | `lisp_verify_cga_sig` | lisp.py:10029 |
| 44 | `encode` | lisp.py:1967 |
| 42 | `lisp_geo_command` | lisp-core.py:1677 |
| 41 | `merge_rles_in_site_eid` | lisp.py:15083 |
| 41 | `lisp_queue_multicast_map_notify` | lisp.py:9804 |
| 41 | `lisp_encap_rloc_probe` | lisp.py:19257 |
| 39 | `print_flow` | lisp.py:3021 |
| 38 | `lisp_run_geo_lig` | lisp-core.py:1610 |
| 38 | `lisp_ddt_process_map_request` | lisp.py:8334 |
| 37 | `lisp_send_ddt_map_request` | lisp.py:8815 |
| 37 | `lisp_etr_process_map_request` | lisp.py:7544 |
| 35 | `resolve_dns_name` | lisp.py:15549 |
| 35 | `process_rloc_probe_reply` | lisp.py:14012 |
| 34 | `select_rloc_next_hop` | lisp.py:14090 |
| 34 | `resolve_dns_name` | lisp.py:15308 |
| 33 | `lisp_get_private_rloc_set` | lisp.py:7731 |
| 32 | `lisp_write_ipc_map_cache` | lisp.py:19635 |
| 31 | `encode` | lisp.py:5367 |
| 27 | `lisp_rtr_process_map_request` | lisp.py:7652 |
| 27 | `lisp_ddt_root_command` | lisp-mr.py:63 |
| 27 | `decode` | lisp.py:15951 |
| 26 | `send_icmp_too_big` | lisp.py:2336 |
| 26 | `add_to_rloc_probe_list` | lisp.py:14152 |
| 24 | `lisp_rle_command` | lispconfig.py:3134 |
| 23 | `lisp_elp_command` | lispconfig.py:3086 |
| 22 | `print_packet` | lisp.py:2922 |
| 22 | `encode` | lisp.py:4023 |
| 21 | `verify_map_request_sig` | lisp.py:4499 |
| 21 | `fragment_outer` | lisp.py:2292 |
| 20 | `lisp_send_ecm` | lisp.py:11509 |
| 20 | `lisp_retransmit_ddt_map_request` | lisp.py:8728 |
| 20 | `lisp_lookup_public_key` | lisp.py:9982 |
| 20 | `lisp_allow_gleaning` | lisp.py:20992 |
| 19 | `parse_geo_string` | lisp.py:12989 |
| 19 | `lisp_map_cache_lookup` | lisp.py:11770 |
| 19 | `lisp_get_map_resolver` | lisp.py:16070 |
| 17 | `lisp_mr_process_map_request` | lisp.py:8900 |
| 17 | `delete_from_rloc_probe_list` | lisp.py:14222 |
| 15 | `lisp_retry_decap_keys` | lisp.py:20606 |
| 15 | `build_best_rloc_set` | lisp.py:14475 |
| 15 | `add` | lisp.py:15874 |
| 14 | `lisp_get_decent_index` | lisp.py:20717 |
| 14 | `delete_cache` | lisp.py:14737 |
| 13 | `print_state` | lisp.py:13643 |
| 13 | `lisp_map_referral_loop` | lisp.py:11111 |
| 13 | `lisp_ip_checksum` | lisp.py:1362 |
| 13 | `lisp_icmp_checksum` | lisp.py:1399 |
| 13 | `lisp_get_referral_node` | lisp.py:8786 |
| 13 | `lisp_get_decent_eid_string` | lisp.py:20680 |
| 13 | `do_icv` | lisp.py:3637 |
| 12 | `store_rloc_probe_hops` | lisp.py:13966 |
| 12 | `lisp_write_flow_log` | lisp.py:18726 |
| 12 | `lisp_ms_eid_crypto_hash_command` | lisp-ms.py:407 |
| 12 | `lisp_find_sig_in_rloc_set` | lisp.py:9901 |
| 12 | `add_cache` | lisp.py:14719 |
| 11 | `print_keys` | lisp.py:3534 |
| 11 | `print_flags` | lisp.py:14961 |
| 11 | `make_default_multicast_route` | lisp.py:11982 |
| 11 | `lisp_update_rtr_updown` | lisp.py:18444 |
| 11 | `lisp_process_rloc_probe_request` | lisp.py:7340 |
| 11 | `lisp_is_active_interface` | lisp.py:17588 |
| 11 | `dms_to_decimal` | lisp.py:13073 |
| 10 | `store_rloc_probe_latencies` | lisp.py:13986 |
| 10 | `log_flow` | lisp.py:3005 |
| 10 | `lisp_validate_user` | lispconfig.py:1106 |
| 10 | `lisp_store_mr_stats` | lisp.py:9019 |
| 10 | `lisp_referral_cache_lookup` | lisp.py:11819 |
| 10 | `lisp_process_pubsub` | lisp.py:7973 |
| 10 | `lisp_is_json_telemetry` | lisp.py:21735 |
