# Outgoing Payload Size Reduction Findings
This document is planning-only. It covers reducing outgoing data size: slimmer heartbeats, smaller payload envelopes, fewer repeated fields, and moving large inline data out of messages. Transport logging and statistics work lives in `COMMS.md`.

## Scope
- outgoing heartbeat construction in `naeural_core/main/app_monitor.py`
- outgoing payload envelope construction in `naeural_core/comm/base/base_comm_thread.py`
- outgoing business payload generation in `naeural_core/data_structures.py`
- payload-heavy outgoing business helpers in `naeural_core/business/` and `naeural_core/data/`

## Summary
The highest-value outgoing-byte reductions are not generic compression tweaks. The runtime already compresses heartbeat bodies and already JPEG-compresses image payloads before base64 encoding. The biggest remaining wins are:
- shrink admin and network-monitor snapshots that currently dominate observed mainnet bytes
- stop resending large diagnostic sections in frequent heartbeats
- stop resending unchanged pipeline and plugin snapshots
- trim legacy envelope fields that are still carried for backward compatibility
- stop copying broad capture metadata into every payload by default
- keep large histories and artifacts out of inline messages

Correction after recheck:
- `DCT_STATS`, `COMM_STATS`, whitelist data, temperature info, serving PIDs, and loop timings are still built into regular heartbeat bodies even when `HEARTBEAT_TIMERS` is false
- `CONFIG_STREAMS` and `ACTIVE_PLUGINS` are not unconditional regular-heartbeat fields anymore; they are gated by `EE_HB_CONTAINS_PIPELINES` and `EE_HB_CONTAINS_ACTIVE_PLUGINS`
- when present, those sections are usually carried inside compressed heartbeat `ENCODED_DATA`, not as top-level wire keys
- `NetworkMonitor` pipeline state is now refreshed through direct `NET_CONFIG_MONITOR` payloads as well, so heartbeat-carried pipeline snapshots are no longer the only source of remote pipeline state

## SDK Measurement Fold-In
The SDK-side mainnet sample in `PAYLOADS_SDK_RESULTS.md` adds a reality check to the code-path analysis here:
- the sample captured `1957` messages over `65.7s` and was dominated by `heartbeat` (`18.4MB`, `58.1%`) and `payload:NET_MON_01` (`8.8MB`, `27.8%`)
- the single largest field was `CURRENT_NETWORK` at `8.6MB` across only `15` `NET_MON_01` payloads
- heartbeat-style diagnostic sections were still heavy in practice, led by `ACTIVE_PLUGINS`, `EE_WHITELIST`, `COMM_STATS`, and `DCT_STATS`
- `_C_*` and `_P_*` together were only `0.5%` of the measured sample, image fields were negligible, and history/result fields were absent in that run
- `ENCODED_DATA` observed from the SDK callback path should not be treated as proven raw-wire duplication; it is at least partly a decoded-view artifact

## Main Traffic Drivers From The SDK Sample
### Who
- the traffic is not dominated by a single pathological sender; the top senders are many `r1s-*` nodes with similar totals in the `724KB` to `763KB` range over the sample window
- this shape suggests fleet-wide periodic control-plane broadcasts, not one isolated noisy application node

### What
- the largest total-byte class is `heartbeat`
- the largest per-message class is `payload:NET_MON_01`
- the next recurring control-plane drivers are `payload:NET_CONFIG_MONITOR` and `payload:CHAIN_STORE_BASE`
- the heaviest fields are `CURRENT_NETWORK`, `ACTIVE_PLUGINS`, `EE_WHITELIST`, `COMM_STATS`, `NET_CONFIG_DATA`, and `EE_ENCRYPTED_DATA`

### Where
- the big traffic sits primarily in admin and control-plane flows, not in the sampled business payload streams
- specifically, the measured heavy paths were `admin_pipeline / NET_MON_01`, `admin_pipeline / NET_CONFIG_MONITOR`, `admin_pipeline / CHAIN_STORE_BASE`, plus recurring heartbeat traffic
- inside those messages, the byte concentration is in full network maps, repeated heartbeat diagnostics, config-sync payloads, and encrypted envelopes rather than images or histories

## Findings

### 1. `NET_MON_01` full `CURRENT_NETWORK` snapshots are a top payload driver
Evidence:
- `PAYLOADS_SDK_RESULTS.md` measured `payload:NET_MON_01` at `8.8MB` (`27.8%`) across only `15` messages and `CURRENT_NETWORK` alone at `8.6MB` (`27.0%`) of the sample
- the largest sampled `NET_MON_01` payloads were around `611KB` to `614KB`, with `CURRENT_NETWORK` around `584KB` to `586KB` plus `CURRENT_ALERTED`, `CURRENT_RANKING`, `WHITELIST_MAP`, `MESSAGE`, and `STATUS`
- `naeural_core/business/default/admin/net_mon_01.py` emits the full `current_network` object through `_create_payload(...)` whenever `should_send` is true
- the plugin already has cadence and filtering knobs such as `EE_NETMON_SEND_CURRENT_NETWORK_EACH`, `EE_NETMON_SEND_ONLY_ONLINE`, and whitelist compression, but it does not currently emit a compact delta or digest form of `CURRENT_NETWORK`

Proposal:
- make full `CURRENT_NETWORK` snapshots slower, on-demand, or otherwise less frequent than the current broadcast path
- add a compact default mode based on counts, hashes, per-node digests, top-K summaries, or deltas instead of full maps
- if full snapshots must still be shipped, add a compressed `NET_MON_01` mode for the bulky `CURRENT_NETWORK` body or for the whole payload, similar in spirit to heartbeat `ENCODED_DATA` but with explicit compatibility and observability notes
- review whether `CURRENT_ALERTED`, `CURRENT_RANKING`, `WHITELIST_MAP`, `MESSAGE`, and duplicated `STATUS` must accompany every network snapshot

Priority:
- very high

Risk:
- medium; existing supervisor or UI consumers may expect full point-in-time snapshots

### 2. Periodic heartbeats still carry a broad summary payload
Evidence:
- periodic heartbeats are emitted from `naeural_core/main/orchestrator.py` with `full_info=self.cfg_heartbeat_timers` and `send_log=self.cfg_heartbeat_log`
- even when `full_info` is false, `naeural_core/main/app_monitor.py` still includes `DCT_STATS`, `COMM_STATS`, `SERVING_PIDS`, `LOOPS_TIMINGS`, `TEMPERATURE_INFO`, `EE_WHITELIST`, GPU summary, and other summary fields
- `ACTIVE_PLUGINS` and `CONFIG_STREAMS` are separate gates controlled by `EE_HB_CONTAINS_ACTIVE_PLUGINS` and `EE_HB_CONTAINS_PIPELINES`; with current code defaults they are enabled unless the operator disables them in the environment
- heartbeats are compressed afterward, so large sections still get built, serialized, compressed, signed, and transmitted even though they are not visible as top-level keys on the outer wire payload
- `PAYLOADS_SDK_RESULTS.md` measured `heartbeat` at `18.4MB` (`58.1%`) of the sample, with `ACTIVE_PLUGINS` at `5.9MB`, `EE_WHITELIST` at `1.6MB`, `COMM_STATS` at `958KB`, `TEMPERATURE_INFO` at `495KB`, and `DCT_STATS` at `389KB`

Proposal:
- make the default periodic heartbeat a slim profile with only liveness, identity, health, top-level counters, and compact comm totals
- move unconditional diagnostic sections such as `DCT_STATS`, `EE_WHITELIST`, and verbose runtime summaries to startup, change-triggered, slower-cadence, or on-demand heartbeats
- keep `CONFIG_STREAMS` and `ACTIVE_PLUGINS` on the work list only for deployments where `EE_HB_CONTAINS_*` is still enabled
- reuse the existing command split concept already present in `SIMPLE_HEARTBEAT`, `TIMERS_ONLY_HEARTBEAT`, and `FULL_HEARTBEAT`

Priority:
- high

Risk:
- medium; remote tooling that expects these fields on every heartbeat needs a staged compatibility plan

### 3. When enabled, full pipeline configuration snapshots are resent in compressed heartbeats
Evidence:
- `naeural_core/main/orchestrator.py:get_pipelines_view()` returns `list(self._current_dct_config_streams.values())`
- `naeural_core/main/orchestrator.py:cfg_hb_contains_pipelines` reads `EE_HB_CONTAINS_PIPELINES` and defaults to enabled when the env var is absent
- `naeural_core/main/app_monitor.py` places that list into `ct.HB.CONFIG_STREAMS` only when the gate is enabled
- with default `COMPRESS_HEARTBEAT=true`, the payload travels inside `ENCODED_DATA`
- `naeural_core/business/default/admin/net_config_monitor.py` now updates `NetworkMonitor` pipeline cache directly through `register_node_pipelines()`, so live heartbeat parsing is no longer the only source of pipeline state
- `PAYLOADS_SDK_RESULTS.md` saw only `25.4KB` total `CONFIG_STREAMS` in the sampled heartbeat diagnostics, so this is real but not a dominant byte source in that mainnet run

Proposal:
- for deployments that still enable heartbeat pipeline export, replace full `CONFIG_STREAMS` with `count + revision/hash`
- otherwise, prefer the direct config-sync path and remove stale assumptions that netmon still depends on heartbeat snapshots

Priority:
- medium, unless a deployment still exports large pipeline snapshots in heartbeats

Risk:
- low to medium

### 4. When enabled, `ACTIVE_PLUGINS` is rich and repetitive
Evidence:
- `naeural_core/business/business_manager.py` includes per-instance stream, signature, instance, process delay, frequency, init timestamp, exec timestamp, config timestamp, error timestamps, working-hours flag, iteration counters, last payload time, total payload count, and info
- this structure is emitted into heartbeat payloads from `naeural_core/main/app_monitor.py` only when `EE_HB_CONTAINS_ACTIVE_PLUGINS` is enabled
- `PAYLOADS_SDK_RESULTS.md` measured `ACTIVE_PLUGINS` at `5.9MB`, making it the heaviest sampled heartbeat diagnostic field

Proposal:
- keep only a compact per-instance digest in regular heartbeats
- move full per-instance detail to slower diagnostic or on-demand heartbeats
- consider delta-only reporting keyed by stream/signature/instance

Priority:
- high

Risk:
- medium

### 5. `DCT_STATS` looks removable from regular heartbeats
Evidence:
- `naeural_core/main/app_monitor.py` emits `ct.HB.DCT_STATS`
- repo search shows `DCT_STATS` is produced there and then removed in `naeural_core/main/net_mon.py`; there are no other in-repo consumers
- `PAYLOADS_SDK_RESULTS.md` measured `DCT_STATS` at `389KB`, which is smaller than `ACTIVE_PLUGINS` or `EE_WHITELIST` but still recurring heartbeat weight with little in-repo consumption evidence

Proposal:
- remove `DCT_STATS` from frequent heartbeats or reduce it to top-level capture counts
- if operators still need detail, send it only in full diagnostics or persist it locally

Priority:
- high

Risk:
- low

### 6. Every normal payload carries legacy envelope fields
Evidence:
- `naeural_core/comm/base/base_comm_thread.py:_prepare_message()` adds `EE_MESSAGE_SEQ` with a `TODO: delete`
- it also adds `EE_TOTAL_MESSAGES`
- it writes `EE_MESSAGE_ID` once into the outgoing dict, then writes it again in the backward-compat section
- it adds `SB_IMPLEMENTATION` in the backward-compat section
- `PAYLOADS_SDK_RESULTS.md` measured `596.1KB` (`1.8%`) of empty/default-like field cost, led by `SB_IMPLEMENTATION`, `MODIFIED_BY_ADDR`, `INITIATOR_ADDR`, `MODIFIED_BY_ID`, and `INITIATOR_ID`

Proposal:
- audit current consumers, then gate or remove `EE_MESSAGE_SEQ`, `EE_TOTAL_MESSAGES`, duplicate `EE_MESSAGE_ID`, and `SB_IMPLEMENTATION`
- if compatibility is still needed, hide them behind a protocol/version flag instead of sending them universally

Priority:
- medium to high

Risk:
- medium to high; wire compatibility must be checked first

### 7. Payloads copy broad capture metadata by default
Evidence:
- `naeural_core/data_structures.py:GeneralPayload._add_metadata_to_payload()` copies almost all capture metadata into every payload, excluding only a short list like `original_image` and `temp_data`
- direct metadata keys such as `payload_context` are passed through, while the rest become `_C_*` fields
- `GeneralPayload._pre_process_object()` also adds `TAGS`, `ID_TAGS`, `USE_LOCAL_COMMS_ONLY`, and multiple `_P_*` runtime/debug fields
- `PAYLOADS_SDK_RESULTS.md` measured `_C_*` plus `_P_*` at `167.8KB` (`0.5%`) in the sampled run, so this is real but second-tier relative to admin snapshots and heartbeat diagnostics

Proposal:
- replace broad metadata pass-through with an explicit allowlist for regular payloads
- keep `payload_context` and other truly required routing keys, but move the rest behind an opt-in debug/diagnostic flag
- omit empty `TAGS`, empty `ID_TAGS`, and `USE_LOCAL_COMMS_ONLY=false` from the wire payload

Priority:
- medium to high

Risk:
- medium; custom plugins may have hidden dependencies on `_C_*` fields

### 8. Image payloads are already compressed, so the next win is to send fewer of them when workloads actually use them
Evidence:
- `naeural_core/data_structures.py:_post_process_result()` calls `maybe_prepare_img_payload()`
- `naeural_core/utils/img_utils.py` already JPEG-compresses images before base64 encoding
- `ADD_ORIGINAL_IMAGE` can add `IMG_ORIG` beside the regular witness image, effectively doubling image payload mass for many messages
- `PAYLOADS_SDK_RESULTS.md` saw only `3.2KB` of image fields in the sampled mainnet run, so this is not a current top-byte driver in that workload

Proposal:
- keep generic image compression as-is unless measurements prove otherwise
- prioritize policy changes: witness-only by default, `IMG_ORIG` only on request, and pointer-based retrieval for originals or large image batches

Priority:
- medium to low unless a target workload is image-heavy

Risk:
- low

### 9. Some flows can inline large histories and results
Evidence:
- `naeural_core/business/mixins_base/limited_data_mixin.py` can place the entire `_payload_history` list into `HISTORY` when `PROCESSING_RESULTS_CSV` is false
- `naeural_core/comm/base/base_comm_thread.py` drops messages above `MAX_MESSAGE_LEN = 2MB`
- `PAYLOADS_SDK_RESULTS.md` saw no `HISTORY` or similar result-history fields in the sampled run, so this remains workload-dependent rather than a confirmed current mainnet hotspot

Proposal:
- default long histories and large result sets to CSV/object storage plus a pointer in the payload
- reserve inline `HISTORY` only for small bounded result sets

Priority:
- medium to low unless a target workload actually emits histories

Risk:
- low

### 10. Whitelists and other slowly changing diagnostic fields should not be frequent-wire defaults
Evidence:
- `naeural_core/main/app_monitor.py` includes `EE_WHITELIST`, `TEMPERATURE_INFO`, `SERVING_PIDS`, `LOOPS_TIMINGS`, and other diagnostic sections in the regular heartbeat payload
- several of these are useful for diagnostics but change slowly relative to the heartbeat cadence
- `PAYLOADS_SDK_RESULTS.md` measured `EE_WHITELIST` at `1.6MB`, `TEMPERATURE_INFO` at `495KB`, and `LOOPS_TIMINGS` at `135KB` in the sampled heartbeat traffic

Proposal:
- keep only counts, hashes, or compact summaries in the frequent heartbeat
- move full lists and verbose structures to slower diagnostic or request/response paths

Priority:
- medium

Risk:
- medium

## Recommended Payload Work Order
1. Shrink `NET_MON_01` full `CURRENT_NETWORK` snapshots first; the SDK sample shows they are a top byte driver with very low message count.
2. Slim the periodic heartbeat profile and strip recurring diagnostic sections from the frequent heartbeat path.
3. Review admin and control-plane payload volume separately from business payloads, especially `NET_CONFIG_MONITOR`, `CHAIN_STORE_BASE`, and encrypted envelope overhead.
4. Audit and trim legacy or default-like message-envelope keys.
5. Add a metadata allowlist for regular business payloads, but treat this as second-tier unless target workloads show heavier `_C_*` or `_P_*` use.
6. Keep image compression unchanged at first; current sampled mainnet traffic does not justify image-focused optimization as an early priority.
7. Move long histories and large artifacts to file or object references when workloads actually emit them, rather than treating them as the current default bottleneck.

## Out of Scope Here
- transport counters, queue telemetry, and comm logging shape
- broker-side tuning
- MQTT QoS changes
- runtime code changes in this task
