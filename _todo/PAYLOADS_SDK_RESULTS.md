# Ratio1 SDK Raw Bandwidth Capture Results

## Scope
- script path: `xperimental/payloads_tests/sdk_bandwidth_capture.py`
- capture command: `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds 600 --max-messages 30000`
- analysis command: `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --analyze-only --capture-file xperimental/payloads_tests/evidence/raw_bandwidth/20260318T192853+0000_mainnet_bandwidth.jsonl`
- network: `mainnet`
- capture window: `2026-03-18T19:28:53+00:00` to `2026-03-18T19:38:53+00:00` (600.1s)
- message count: `13421`
- stop reason: `time-window`

## Measurement Rules
- Primary bandwidth metric is raw MQTT payload size: `len(message.payload)` captured before SDK parsing or heartbeat decompression.
- Heartbeat decoded-body analysis is reported separately and is **not** counted as raw bandwidth.
- MQTT topic/header overhead is still excluded because the SDK callback surface does not expose it.
- The session disables automatic SDK net-config requests to keep the run passive.

## Executive Summary
- Raw MQTT payload throughput averaged `214.6KB/s` (`12.6MB/min`).
- Raw message bandwidth was dominated by `payload:NET_MON_01` at `49.0%` of observed bytes.
- Heartbeat decoded bodies expanded to `101.5MB` after `ENCODED_DATA` decompression, but those bytes were **not** used in the raw bandwidth totals.
- First half vs second half raw bytes: `61.0MB` vs `64.8MB`.

## Raw Byte Distribution
| message class | count | total raw bytes | avg bytes | p95 bytes | max bytes | byte share |
| --- | --- | --- | --- | --- | --- | --- |
| payload:NET_MON_01 | 182 | 61.6MB | 354839 | 369816 | 370249 | 49.0% |
| heartbeat | 11859 | 58.8MB | 5195 | 5729 | 7241 | 46.7% |
| payload:NET_CONFIG_MONITOR | 314 | 2.8MB | 9482 | 11811 | 23807 | 2.3% |
| payload:CHAIN_STORE_BASE | 253 | 1.6MB | 6463 | 7435 | 7526 | 1.2% |
| notification:NORMAL | 726 | 923.8KB | 1303 | 1342 | 1342 | 0.7% |
| payload:CONTAINER_APP_RUNNER | 35 | 69.1KB | 2020 | 2048 | 2048 | 0.1% |
| notification:ABNORMAL FUNCTIONING | 46 | 63.9KB | 1422 | 1565 | 1565 | 0.0% |
| payload:CUSTOM_EXEC_01 | 4 | 8.2KB | 2091 | 2092 | 2092 | 0.0% |
| payload:REST_CUSTOM_EXEC_01 | 2 | 4.8KB | 2466 | 2467 | 2467 | 0.0% |

| sender | count | total raw bytes | avg bytes |
| --- | --- | --- | --- |
| r1s-archicava<0xai_A3rv2yBHLs2P9e5dlKXdwr5K4MFqdD17td7L3ROn2ihk> | 70 | 4.7MB | 70391 |
| r1s-smart<0xai_AtqzX1daNQGQC4k-NUBDMQX31ul4F-md3DJeiy7X22By> | 70 | 4.4MB | 65670 |
| r1s-galifrey<0xai_A5aVWqDarsDnQ40BeQ0AEw-SWL6iPqmIjg4nc-UIULaR> | 69 | 4.4MB | 66582 |
| r1s-slv01<0xai_At9Swwd9yACokN8fvJBJjdhSFZ1l4KTCn1jVSSJOxnit> | 68 | 4.4MB | 67549 |
| r1s-sbt<0xai_AkFFsvYZIjnTONnhbGtx72ZrZWiAFkxfaa6fdavn24LK> | 69 | 4.4MB | 66398 |
| r1s-03<0xai_Apkb2i2m8zy8h2H4zAhEnZxgV1sLKAPhjD29B1I_I9z7> | 69 | 4.3MB | 65788 |
| r1s-ai01<0xai_A16JyAs142gvVWCPKH3d8rxck1jtkGBocHLi7tpv6WZZ> | 68 | 4.1MB | 62526 |
| r1s-d3c0d3r<0xai_AssneYDLZUi57-3GYr9pne3WRnx1PJXYjgvK3Bohiwkb> | 70 | 4.0MB | 60579 |
| r1s-ssj<0xai_A-rqFlS6-9XR9g3LM0kuzshqg7gIjACFPMoqN0Co_8Lj> | 68 | 4.0MB | 62277 |
| r1s-01<0xai_Aj1FpPQHISEBelp-tQ8cegwk434Dcl6xaHmuhZQT74if> | 67 | 4.0MB | 62073 |

| stream / signature | count | total raw bytes | avg bytes |
| --- | --- | --- | --- |
| admin_pipeline / NET_MON_01 | 182 | 61.6MB | 354839 |
| - / - | 11905 | 58.8MB | 5180 |
| admin_pipeline / NET_CONFIG_MONITOR | 314 | 2.8MB | 9482 |
| admin_pipeline / CHAIN_STORE_BASE | 253 | 1.6MB | 6463 |
| admin_pipeline / - | 726 | 923.8KB | 1303 |
| pg_service_b2_e00937a / CONTAINER_APP_RUNNER | 18 | 35.1KB | 1995 |
| keytrail-nati_5144838 / CONTAINER_APP_RUNNER | 17 | 34.0KB | 2047 |
| admin_pipeline / REST_CUSTOM_EXEC_01 | 2 | 4.8KB | 2466 |
| custom_code_remote / CUSTOM_EXEC_01 | 2 | 4.1KB | 2091 |
| custom_code_remote_1 / CUSTOM_EXEC_01 | 2 | 4.1KB | 2091 |

## Top Raw Fields
| field | messages present | total estimated raw bytes | avg bytes when present |
| --- | --- | --- | --- |
| CURRENT_NETWORK | 182 | 59.3MB | 341539 |
| ENCODED_DATA | 11859 | 47.8MB | 4226 |
| EE_ENCRYPTED_DATA | 567 | 3.5MB | 6493 |
| EE_SIGN | 13421 | 1.3MB | 98 |
| CURRENT_RANKING | 182 | 963.1KB | 5418 |
| EE_HASH | 13421 | 865.0KB | 66 |
| EE_SENDER | 13421 | 668.4KB | 51 |
| EE_ADDR | 12854 | 640.2KB | 51 |
| EE_ETH_SENDER | 13421 | 576.7KB | 44 |
| WHITELIST_MAP | 182 | 549.4KB | 3091 |
| EE_MESSAGE_ID | 13421 | 498.0KB | 38 |
| EE_DEST | 567 | 465.7KB | 841 |
| EE_PAYLOAD_PATH | 13421 | 386.2KB | 29 |
| EE_TIMESTAMP | 13421 | 367.0KB | 28 |
| CURRENT_ALERTED | 182 | 284.7KB | 1601 |

## Heartbeat Decoded Composition
These fields describe what sits inside compressed heartbeat bodies. They are useful for optimization analysis but are not additive with the raw heartbeat bytes above.

| field | messages present | total decoded bytes | avg bytes when present |
| --- | --- | --- | --- |
| ACTIVE_PLUGINS | 11859 | 51.3MB | 4536 |
| EE_WHITELIST | 11859 | 13.3MB | 1176 |
| COMM_STATS | 11859 | 7.6MB | 674 |
| GPU_INFO | 11859 | 4.0MB | 354 |
| TEMPERATURE_INFO | 11859 | 3.8MB | 339 |
| DCT_STATS | 11859 | 3.2MB | 281 |
| R1FS_RELAY | 11859 | 1002.2KB | 86 |
| LOOPS_TIMINGS | 11859 | 984.7KB | 85 |
| VERSION | 11859 | 800.6KB | 69 |
| DEVICE_LOG | 11859 | 752.8KB | 65 |
| ERROR_LOG | 11859 | 741.2KB | 64 |
| R1FS_ID | 11859 | 625.4KB | 54 |
| STOP_LOG | 11859 | 600.0KB | 51 |
| EE_ADDR | 11859 | 590.6KB | 51 |
| CPU | 11859 | 401.0KB | 34 |

## Empty or Default-like Raw Fields
| field | total estimated raw bytes |
| --- | --- |
| EE_IS_ENCRYPTED | 62.8KB |
| SB_IMPLEMENTATION | 52.4KB |
| INITIATOR_ADDR | 52.3KB |
| INITIATOR_ID | 52.3KB |
| MODIFIED_BY_ADDR | 52.3KB |
| MODIFIED_BY_ID | 52.3KB |
| SESSION_ID | 52.3KB |
| EE_FORMATTER | 50.2KB |
| NR_INFERENCES | 11.6KB |
| NR_PAYLOADS | 10.9KB |

## Largest Raw Message Examples
- Example 1: `payload:NET_MON_01` from `r1s-ai01<0xai_A16JyAs142gvVWCPKH3d8rxck1jtkGBocHLi7tpv6WZZ>` (361.6KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large raw fields: CURRENT_NETWORK=334.1KB, CURRENT_ALERTED=11.7KB, CURRENT_RANKING=5.6KB, WHITELIST_MAP=3.0KB, MESSAGE=2.3KB
  preview: `{"CURRENT_NETWORK": "<dict 209 keys, 342150B>", "CURRENT_ALERTED": "<dict 208 keys, 11965B>", "CURRENT_RANKING": "<list 264 items, 5773B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "MESSAGE": "<string 2357B> Missing/lost processing nodes: ['Cap_32', 'Cap_06', 'mgstaking8', 'mgstaking4', ..."}`
- Example 2: `payload:NET_MON_01` from `r1s-bcps<0xai_AlK6YCNv6mNqKi0od63gqQTkS8tNXGPs9MZNCxclxVCj>` (361.5KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large raw fields: CURRENT_NETWORK=334.1KB, CURRENT_ALERTED=11.7KB, CURRENT_RANKING=5.6KB, WHITELIST_MAP=3.0KB, MESSAGE=2.3KB
  preview: `{"CURRENT_NETWORK": "<dict 209 keys, 342128B>", "CURRENT_ALERTED": "<dict 208 keys, 11965B>", "CURRENT_RANKING": "<list 264 items, 5764B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "MESSAGE": "<string 2357B> Missing/lost processing nodes: ['mgstaking11', 'Michael4', 'Gabi1', 'smart-10-br..."}`
- Example 3: `payload:NET_MON_01` from `r1s-ssj<0xai_A-rqFlS6-9XR9g3LM0kuzshqg7gIjACFPMoqN0Co_8Lj>` (361.5KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large raw fields: CURRENT_NETWORK=334.1KB, CURRENT_ALERTED=11.7KB, CURRENT_RANKING=5.6KB, WHITELIST_MAP=3.0KB, MESSAGE=2.3KB
  preview: `{"CURRENT_NETWORK": "<dict 209 keys, 342105B>", "CURRENT_ALERTED": "<dict 208 keys, 11965B>", "CURRENT_RANKING": "<list 264 items, 5757B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "MESSAGE": "<string 2357B> Missing/lost processing nodes: ['node-3', 'Cap_49', 'cpu-28-r1', 'Cap_89', 'Mich..."}`
- Example 4: `payload:NET_MON_01` from `r1s-bcps<0xai_AlK6YCNv6mNqKi0od63gqQTkS8tNXGPs9MZNCxclxVCj>` (361.5KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large raw fields: CURRENT_NETWORK=334.1KB, CURRENT_ALERTED=11.7KB, CURRENT_RANKING=5.6KB, WHITELIST_MAP=3.0KB, MESSAGE=2.3KB
  preview: `{"CURRENT_NETWORK": "<dict 209 keys, 342083B>", "CURRENT_ALERTED": "<dict 208 keys, 11965B>", "CURRENT_RANKING": "<list 264 items, 5762B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "MESSAGE": "<string 2357B> Missing/lost processing nodes: ['mgstaking11', 'Michael4', 'Gabi1', 'smart-10-br..."}`
- Example 5: `payload:NET_MON_01` from `r1s-galifrey<0xai_A5aVWqDarsDnQ40BeQ0AEw-SWL6iPqmIjg4nc-UIULaR>` (361.3KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large raw fields: CURRENT_NETWORK=334.1KB, CURRENT_ALERTED=11.7KB, CURRENT_RANKING=5.3KB, WHITELIST_MAP=3.0KB, MESSAGE=2.3KB
  preview: `{"CURRENT_NETWORK": "<dict 209 keys, 342165B>", "CURRENT_ALERTED": "<dict 208 keys, 11965B>", "CURRENT_RANKING": "<list 253 items, 5435B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "MESSAGE": "<string 2357B> Missing/lost processing nodes: ['smart-04', 'Cap_44', 'Cap_14', 'ACM_N1', 'r1s-d..."}` 

## Stability Check
- first half message count / raw bytes: `6535` / `61.0MB`
- second half message count / raw bytes: `6886` / `64.8MB`
- top shape hashes: `3e223e3602:11859, f5764b6d5b:726, 071cdbbb12:567, b71f8d4158:161, a2242bb442:30`

## Raw Bytes By Minute
- minute 0: heartbeat=5.8MB, payload:NET_MON_01=4.7MB, payload:NET_CONFIG_MONITOR=217.8KB
- minute 1: heartbeat=5.0MB, payload:NET_MON_01=4.4MB, payload:NET_CONFIG_MONITOR=487.0KB
- minute 2: payload:NET_MON_01=7.8MB, heartbeat=5.3MB, payload:NET_CONFIG_MONITOR=335.8KB
- minute 3: heartbeat=6.2MB, payload:NET_MON_01=6.1MB, payload:CHAIN_STORE_BASE=356.2KB
- minute 4: payload:NET_MON_01=6.8MB, heartbeat=6.1MB, payload:NET_CONFIG_MONITOR=274.2KB
- minute 5: heartbeat=6.1MB, payload:NET_MON_01=5.8MB, payload:NET_CONFIG_MONITOR=286.3KB
- minute 6: payload:NET_MON_01=6.7MB, heartbeat=6.1MB, payload:NET_CONFIG_MONITOR=303.0KB
- minute 7: heartbeat=6.1MB, payload:NET_MON_01=6.1MB, payload:NET_CONFIG_MONITOR=252.4KB
- minute 8: payload:NET_MON_01=6.4MB, heartbeat=6.1MB, payload:NET_CONFIG_MONITOR=300.3KB
- minute 9: payload:NET_MON_01=6.8MB, heartbeat=5.9MB, payload:NET_CONFIG_MONITOR=293.1KB

## Artifacts
- capture jsonl: `xperimental/payloads_tests/evidence/raw_bandwidth/20260318T192853+0000_mainnet_bandwidth.jsonl`
- summary json: `xperimental/payloads_tests/evidence/raw_bandwidth/20260318T192853+0000_mainnet_bandwidth_summary.json`
- results md: `xperimental/payloads_tests/evidence/raw_bandwidth/20260318T192853+0000_mainnet_bandwidth_results.md`
- related NET_MON compression probe: `xperimental/payloads_tests/evidence/netmon_compression/20260318T200033+0000_netmon_compression_results.md`

## Verification
- command: `python3 xperimental/payloads_tests/sdk_bandwidth_capture.py --seconds 600 --max-messages 30000`
  result: `pass`
  evidence: `Captured 13421 raw MQTT payloads over 600.1s on mainnet`
