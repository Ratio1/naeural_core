# Ratio1 SDK Payload Capture Results

## Scope
- SDK repo revision: `ad372fb618b33015e871b0e970839e0e7a0b203d`
- Tutorial/example path: `tutorials/ex27_payload_capture.py`
- Capture command: `python tutorials/ex27_payload_capture.py --seconds 60 --max-messages 2500`
- Analysis command: `python tutorials/ex27_payload_capture.py --analyze-only --capture-file <capture.jsonl>`
- Network: `mainnet`
- Capture window: `2026-03-18T06:20:05+00:00` to `2026-03-18T06:21:10+00:00` (65.7s)
- Message count: `1957`
- Any filters: `none`

## Environment and Limits
- SDK/session setup used: `Session(name='sdk-payload-capture', silent=True, auto_configuration=True, run_dauth=True, use_home_folder=False, local_cache_base_folder=<repo>)`
- Auth or public-access assumptions: `Used the SDK's normal dAuth-backed public listening path on mainnet. No pipelines, commands, or config mutations were issued by this tutorial.`
- Sample stop conditions: `60s wall clock or 2500 messages, whichever comes first`; actual stop reason=`time-window`
- Known blind spots: `Callback-level JSON view only; raw MQTT wire bytes and payload internals for messages encrypted to other recipients are not observable here.`

## Executive Summary
- heartbeat dominated the sample with 1370 messages and 18.4MB (58.1%) of observed bytes.
- The heaviest top-level field was `CURRENT_NETWORK` at 8.6MB across 15 messages.
- The single biggest payload driver was full NET_MON network-map state, not image or history payload content.
- The largest payload category was `heartbeat` with 1370 messages and 18.4MB.
- `ENCODED_DATA` alone contributed 5.6MB; in this SDK it should be treated as a callback-view expansion cost, not a confirmed raw-wire duplicate.
- Heartbeat-style diagnostic sections accounted for 8.8MB (27.8%) of measured bytes.
- `_C_*` and `_P_*` metadata together contributed 167.8KB (0.5%) of the sample.

## Answers To Required Questions
- 1. Message classes that dominated total bytes were: heartbeat (18.4MB, 58.1%), payload:NET_MON_01 (8.8MB, 27.8%), payload:NET_CONFIG_MONITOR (2.6MB, 8.2%), payload:CHAIN_STORE_BASE (1.8MB, 5.6%).
- 2. The largest payloads were dominated by network-map and diagnostic metadata, not by images or histories. The largest examples were payload:NET_MON_01 dominated by CURRENT_NETWORK, payload:NET_MON_01 dominated by CURRENT_NETWORK, payload:NET_MON_01 dominated by CURRENT_NETWORK; image fields contributed only 3.2KB overall.
- 3. The clearest low-hanging-fruit fields were `CURRENT_NETWORK` at 8.6MB, `ACTIVE_PLUGINS`, `ENCODED_DATA` at 5.6MB, `EE_WHITELIST`, `NET_CONFIG_DATA`, and `EE_ENCRYPTED_DATA`.
- 4. Empty/default-like fields accounted for 596.1KB (1.8%) in this SDK-visible sample.
- 5. `_C_*` and `_P_*` metadata accounted for 167.8KB (0.5%) combined.
- 6. Yes. Heartbeat-like diagnostic sections still exposed 8.8MB (27.8%) from the SDK side.
- 7. Direct mapping back to core `PAYLOADS.md` is blocked because that file is absent in this checkout on 2026-03-18, but the strongest likely overlap is heartbeat diagnostics, NET_MON full snapshots, repeated metadata, and encrypted envelopes.
- 8. SDK-side guidance from this run: separate callback-view byte counts from raw-wire claims, drop heartbeat `ENCODED_DATA` after decode when archiving locally, and analyze admin payloads separately from business payloads because NET_MON and heartbeat traffic dominate the sample.

## Byte Distribution
| message class | count | total bytes | avg bytes | p95 bytes | max bytes |
| --- | --- | --- | --- | --- | --- |
| heartbeat | 1370 | 18.4MB | 14120 | 17899 | 20491 |
| payload:NET_MON_01 | 15 | 8.8MB | 617042 | 629184 | 629184 |
| payload:NET_CONFIG_MONITOR | 253 | 2.6MB | 10804 | 18225 | 29834 |
| payload:CHAIN_STORE_BASE | 264 | 1.8MB | 7038 | 7935 | 7950 |
| notification:NORMAL | 42 | 56.6KB | 1379 | 1416 | 1416 |
| notification:ABNORMAL FUNCTIONING | 9 | 13.7KB | 1554 | 1645 | 1645 |
| payload:CONTAINER_APP_RUNNER | 2 | 3.9KB | 2004 | 2004 | 2004 |
| payload:CUSTOM_EXEC_01 | 1 | 2.0KB | 2091 | 2091 | 2091 |
| notification:EXCEPTION | 1 | 1.4KB | 1417 | 1417 | 1417 |

| sender | count | total bytes | avg bytes |
| --- | --- | --- | --- |
| r1s-db<0xai_A_b...WbiMiF> | 9 | 762.6KB | 86769 |
| r1s-vi01<0xai_A7X...uKsGKD> | 9 | 754.7KB | 85872 |
| r1s-ssj<0xai_A-r...Co_8Lj> | 9 | 744.2KB | 84668 |
| r1s-ai01<0xai_A16...pv6WZZ> | 8 | 744.1KB | 95247 |
| r1s-bcps<0xai_AlK...clxVCj> | 8 | 743.0KB | 95104 |
| r1s-d3c0d3r<0xai_Ass...ohiwkb> | 9 | 738.6KB | 84034 |
| r1s-03<0xai_Apk...I_I9z7> | 8 | 733.9KB | 93936 |
| r1s-smart<0xai_Atq...7X22By> | 8 | 733.1KB | 93832 |
| r1s-sbt<0xai_AkF...vn24LK> | 8 | 733.0KB | 93824 |
| r1s-02<0xai_Azy...ZaDhy8> | 8 | 724.5KB | 92738 |

| stream / signature | count | total bytes | avg bytes |
| --- | --- | --- | --- |
| admin_pipeline / NET_MON_01 | 15 | 8.8MB | 617042 |
| admin_pipeline / NET_CONFIG_MONITOR | 253 | 2.6MB | 10804 |
| admin_pipeline / CHAIN_STORE_BASE | 264 | 1.8MB | 7038 |
| admin_pipeline / - | 42 | 56.6KB | 1379 |
| pg_service_b2_e00937a / CONTAINER_APP_RUNNER | 2 | 3.9KB | 2004 |
| custom_code_remote / CUSTOM_EXEC_01 | 1 | 2.0KB | 2091 |

## Top Heavy Fields
| field | messages present | total estimated bytes | avg bytes when present | notes |
| --- | --- | --- | --- | --- |
| CURRENT_NETWORK | 15 | 8.6MB | 599803 | empty=0, default_like=0 |
| ACTIVE_PLUGINS | 1370 | 5.9MB | 4537 | empty=0, default_like=0 |
| ENCODED_DATA | 1370 | 5.6MB | 4283 | empty=0, default_like=0 |
| EE_ENCRYPTED_DATA | 312 | 1.7MB | 5797 | empty=0, default_like=0 |
| NET_CONFIG_DATA | 205 | 1.7MB | 8723 | empty=0, default_like=0 |
| EE_WHITELIST | 1370 | 1.6MB | 1188 | empty=0, default_like=0 |
| COMM_STATS | 1370 | 958.3KB | 716 | empty=0, default_like=0 |
| TEMPERATURE_INFO | 1370 | 494.8KB | 369 | empty=0, default_like=0 |
| GPU_INFO | 1370 | 488.1KB | 364 | empty=0, default_like=0 |
| DCT_STATS | 1370 | 389.2KB | 290 | empty=6, default_like=6 |
| EE_DEST | 517 | 271.5KB | 537 | empty=0, default_like=0 |
| EE_SIGN | 1957 | 206.4KB | 107 | empty=0, default_like=0 |
| EE_HASH | 1957 | 145.2KB | 76 | empty=0, default_like=0 |
| LOOPS_TIMINGS | 1370 | 135.3KB | 101 | empty=0, default_like=0 |
| R1FS_RELAY | 1370 | 132.9KB | 99 | empty=0, default_like=0 |
| STOP_LOG | 1370 | 123.9KB | 92 | empty=1148, default_like=1148 |
| EE_SENDER | 1957 | 120.4KB | 63 | empty=0, default_like=0 |
| EE_ETH_SENDER | 1957 | 114.7KB | 60 | empty=0, default_like=0 |
| EE_PAYLOAD_PATH | 1957 | 109.8KB | 57 | empty=0, default_like=0 |
| VERSION | 1376 | 106.0KB | 78 | empty=0, default_like=0 |

## Field Families
### _C_* metadata
- messages present: 220
- total estimated bytes: 104.8KB (0.3%)
- leading fields: _C_cap_signature (10.1KB), _C_cap_time (9.0KB), _C_cap_elapsed_time (6.7KB), _C_use_local_comms_only (6.7KB), _C_current_interval (5.6KB)
### _P_* runtime/debug fields
- messages present: 223
- total estimated bytes: 63.0KB (0.2%)
- leading fields: _P_ALERT_HELPER (13.0KB), _P_PLUGIN_REAL_RESOLUTION (6.9KB), _P_PLUGIN_LOOP_RESOLUTION (6.8KB), _P_DATASET_BUILDER_USED (6.8KB), _P_DEBUG_SAVE_PAYLOAD (6.3KB)
### image fields
- messages present: 220
- total estimated bytes: 3.2KB (0.0%)
- leading fields: IMG_ORIG (3.2KB)
### heartbeat-style diagnostic sections
- messages present: 1370
- total estimated bytes: 8.8MB (27.8%)
- leading fields: ACTIVE_PLUGINS (5.9MB), EE_WHITELIST (1.6MB), COMM_STATS (958.3KB), DCT_STATS (389.2KB), CONFIG_STREAMS (25.4KB)
### history/result fields
- messages present: 0
- total estimated bytes: 0B (0.0%)
- leading fields: n/a
### empty/default fields
- messages present: 1957
- total estimated bytes: 596.1KB (1.8%)
- leading fields: SB_IMPLEMENTATION (45.9KB), MODIFIED_BY_ADDR (43.9KB), INITIATOR_ADDR (40.1KB), MODIFIED_BY_ID (40.1KB), INITIATOR_ID (36.3KB)

## Largest Message Examples
- Example 1: `payload:NET_MON_01` from `r1s-bcps<0xai_AlK...clxVCj>` (614.4KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large fields: CURRENT_NETWORK=586.2KB, CURRENT_ALERTED=12.3KB, CURRENT_RANKING=5.6KB, WHITELIST_MAP=3.0KB, MESSAGE=2.4KB
  preview: `{"CURRENT_ALERTED": "<dict 216 keys, 12581B>", "CURRENT_NETWORK": "<dict 217 keys, 600291B>", "CURRENT_RANKING": "<list 264 items, 5717B>", "EE_SIGN": "MEUCIQDqllYUPZKAs2vHgkssDgX2wDjo2RQ67FDNQGxU85S1MwIgTv6bWvXbl9cSCLjbqsmLkVAk--MipAagYYcWrqKbqcY=", "MESSAGE": "<string 2485B>", "STATUS": "<string 2485B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "_P_ALERT_HELPER": "A=0, N=0, CT=NA, E=A[0, 1]=0.50 (in 62.3s) vs >=0.75 LstCh:274.0s "}`
- Example 2: `payload:NET_MON_01` from `r1s-vi01<0xai_A7X...uKsGKD>` (614.2KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large fields: CURRENT_NETWORK=586.3KB, CURRENT_ALERTED=12.3KB, CURRENT_RANKING=5.3KB, WHITELIST_MAP=3.0KB, MESSAGE=2.4KB
  preview: `{"CURRENT_ALERTED": "<dict 216 keys, 12581B>", "CURRENT_NETWORK": "<dict 217 keys, 600353B>", "CURRENT_RANKING": "<list 253 items, 5410B>", "EE_SIGN": "MEQCIDBst8pix1THvwghvZ1B_rxkq2YaOaDPIsl0YSrWoktHAiBm8mTrdNDazGF7WI9632NBoTkivse28veQt-n8ebJlTA==", "MESSAGE": "<string 2485B>", "STATUS": "<string 2485B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "_P_ALERT_HELPER": "A=0, N=0, CT=NA, E=A[0, 1]=0.50 (in 58.4s) vs >=0.75 LstCh:280.4s "}`
- Example 3: `payload:NET_MON_01` from `r1s-sbt<0xai_AkF...vn24LK>` (614.1KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large fields: CURRENT_NETWORK=586.2KB, CURRENT_ALERTED=12.3KB, CURRENT_RANKING=5.3KB, WHITELIST_MAP=3.0KB, MESSAGE=2.4KB
  preview: `{"CURRENT_ALERTED": "<dict 216 keys, 12581B>", "CURRENT_NETWORK": "<dict 217 keys, 600237B>", "CURRENT_RANKING": "<list 253 items, 5396B>", "EE_SIGN": "MEYCIQC4qzVBAptOpKfBCsCWq_h978wi18uICG8hBHw7fIYhGAIhANtaBUOzfMNOR98QYXGi4lVpd593hEi9IShoHao-a6-2", "MESSAGE": "<string 2485B>", "STATUS": "<string 2485B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "_P_ALERT_HELPER": "A=0, N=0, CT=NA, E=A[0, 1]=0.50 (in 50.3s) vs >=0.75 LstCh:273.6s "}`
- Example 4: `payload:NET_MON_01` from `r1s-smart<0xai_Atq...7X22By>` (614.0KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large fields: CURRENT_NETWORK=586.3KB, CURRENT_ALERTED=12.3KB, CURRENT_RANKING=5.1KB, WHITELIST_MAP=3.0KB, MESSAGE=2.4KB
  preview: `{"CURRENT_ALERTED": "<dict 216 keys, 12581B>", "CURRENT_NETWORK": "<dict 217 keys, 600315B>", "CURRENT_RANKING": "<list 247 items, 5227B>", "EE_SIGN": "MEYCIQCSSSpvQDr7uvXyOleuu79wQRhmE3wwEgrcDjCkgcKORgIhAJkTg3VKGpJDJ-dN-IRyK3z5H43JnNoNptUAdtXmChZM", "MESSAGE": "<string 2485B>", "STATUS": "<string 2485B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "_P_ALERT_HELPER": "A=0, N=0, CT=NA, E=A[0, 1]=0.50 (in 66.3s) vs >=0.75 LstCh:289.4s "}`
- Example 5: `payload:NET_MON_01` from `r1s-03<0xai_Apk...I_I9z7>` (611.4KB, stream=`admin_pipeline`, signature=`NET_MON_01`)
  large fields: CURRENT_NETWORK=583.7KB, CURRENT_ALERTED=12.3KB, CURRENT_RANKING=5.1KB, WHITELIST_MAP=3.0KB, MESSAGE=2.4KB
  preview: `{"CURRENT_ALERTED": "<dict 216 keys, 12581B>", "CURRENT_NETWORK": "<dict 216 keys, 597656B>", "CURRENT_RANKING": "<list 246 items, 5194B>", "EE_SIGN": "MEUCIQCDdQ3NRSorYiQfNTjEQmZ1H77uobSkI-bBHPOCU3RoMgIgBYwYtNDG61k6OdjLM3x7wh8HZoEhzqXe7cJBCwT021M=", "MESSAGE": "<string 2485B>", "STATUS": "<string 2485B>", "WHITELIST_MAP": "<dict 62 keys, 3091B>", "_P_ALERT_HELPER": "A=0, N=0, CT=NA, E=A[0, 1]=0.50 (in 64.4s) vs >=0.75 LstCh:270.3s "}`

## Low-Hanging-Fruit Candidates
1. Thin heartbeat-style diagnostic sections or send them less often.
   - evidence: Diagnostic fields account for 8.8MB (27.8%) with ACTIVE_PLUGINS, EE_WHITELIST, COMM_STATS leading the family.
   - likely owner: core
   - expected impact: High on heartbeat-heavy traffic because these sections recur across many messages.
   - compatibility risk: Medium if operators rely on full diagnostics every heartbeat; lower if moved to slower cadences or opt-in detail levels.
2. Shrink `CURRENT_NETWORK` snapshots in `NET_MON_01`, ideally with diffs, digests, or lower-cadence full snapshots.
   - evidence: `CURRENT_NETWORK` contributes 8.6MB (27.0%) across only 15 messages.
   - likely owner: core
   - expected impact: High because a small number of NET_MON payloads account for a large byte share.
   - compatibility risk: Medium if downstream consumers expect complete point-in-time snapshots every time.
3. SDK-side logging and analysis should drop heartbeat `ENCODED_DATA` once the SDK has already expanded the decoded heartbeat fields.
   - evidence: `ENCODED_DATA` contributes 5.6MB (17.6%) in the callback view and is paired with decoded heartbeat fields in this SDK path.
   - likely owner: sdk
   - expected impact: High for local logging, archival, and analysis noise reduction.
   - compatibility risk: Low if tooling only removes it after successful decode and keeps raw access opt-in.
4. Investigate slimmer encrypted payload envelopes or compression before encryption for large opaque payloads.
   - evidence: `EE_ENCRYPTED_DATA` alone contributes 1.7MB (5.4%) in the SDK-visible envelope view.
   - likely owner: core
   - expected impact: Medium to high if payload ciphertext frequently dominates the visible envelope.
   - compatibility risk: Medium because cryptographic envelopes are compatibility-sensitive.
5. Stop emitting empty or default-like fields by default.
   - evidence: Default-like fields account for 596.1KB (1.8%); recurring fields include SB_IMPLEMENTATION, MODIFIED_BY_ADDR, INITIATOR_ADDR, MODIFIED_BY_ID.
   - likely owner: shared
   - expected impact: Medium and low-risk because these fields carried little observed information in this sample.
   - compatibility risk: Low if omitted fields are treated as absent-equivalent by consumers.

## Mapping Back To Core PAYLOADS.md
- `PAYLOADS.md` was referenced by the task file, but no `PAYLOADS.md` exists in this checkout as of 2026-03-18, so direct reference-by-reference mapping is blocked.
- The strongest likely core-side candidates from this SDK sample are NET_MON full snapshots, heartbeat diagnostics, repeated `_C_*`/`_P_*` metadata, and empty/default field emission.
- The encrypted-envelope share in the SDK-visible view suggests any core-side analysis should separate payload envelope costs from decrypted business payload body costs.

## Open Questions
- Raw MQTT wire bytes are not exposed in the public callback path, so all byte estimates here are from the SDK-visible decoded JSON view.
- Messages encrypted for other recipients remain opaque envelopes; their inner field makeup cannot be measured from this listener.
- Because `PAYLOADS.md` is absent in this repo snapshot, the core-mapping section is necessarily partial.
- Duplicate message-shape leaders in this run: 400c5b0c82 and cc24350f2d.

## Artifacts
- local JSONL path: `_local_cache/payload_capture/20260318T062005+0000_mainnet_1957msg.jsonl`
- local summary JSON path: `_local_cache/payload_capture/20260318T062005+0000_mainnet_1957msg_summary.json`
- top message shape leaders: `400c5b0c82, cc24350f2d, 071cdbbb12, 2fa7332558, f5764b6d5b`
