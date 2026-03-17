# MQTT Traffic Observability and Optimization Plan
This document is planning-only. It proposes observability and optimization changes for the current MQTT-based comms stack without modifying runtime code in this task.

## Scope
- MQTT transport used through `CommunicationManager`, `BaseCommThread`, and `MQTTCommThread`
- Periodic comm logs emitted by `CommunicationManager.maybe_show_info()` every `COMM_SECS_SHOW_INFO` seconds
- Optional heartbeat-safe summaries via `COMM_STATS`

## Current State
- `CommunicationManager.maybe_show_info()` prints one status block every 60 seconds with per-communicator liveness, connection flags, last activity, error count, last error time, and coarse in/out kB rates.
- Per-communicator bandwidth comes from `BaseCommThread._incoming_lens/_outgoing_lens`, using the most recent 100 samples.
- Outgoing bytes are counted after `send_wrapper()` successfully calls `_send()`.
- Incoming bytes are counted only when `get_message()` pops from `_recv_buff`, so the current `IN_KB` reflects application dequeue rate, not raw MQTT arrival rate.
- `_send_buff` and `_recv_buff` are `deque(maxlen=1000)` objects. If append overflow happens, the oldest entries are silently evicted unless explicit overwrite accounting is added.
- `_CommunicationTelemetryMixin` writes CSV telemetry only for formatter `cavi2`, only on the outgoing path, and only for a legacy telemetry shape; it does not feed the periodic comm status logs and is not sufficient for transport attribution.
- Logical payload attribution is available on egress through `EE_PAYLOAD_PATH = [ee_id, stream_name, signature, instance_id]`.
- Incoming command processing currently drains messages from the `HEARTBEATS` communicator in `CommunicationManager.maybe_process_incoming()`, so command ingress and heartbeat ingress share the same transport path today.
- When local communication is enabled, the same logical message may be duplicated to both central and local communicators unless it is explicitly marked local-only.
- MQTT defaults to `QOS = 2`, which is the safest choice but is also the most expensive choice for high-volume traffic classes.

## Telemetry Objectives
Within one 60-second window, operators should be able to answer:
1. Which communicator, topic, and plugin/stream/instance produced the most ingress and egress bytes and messages?
2. Are losses caused by queue overflow, offline dropping, oversize drops, duplicates, decode failures, decrypt failures, or reconnect churn?
3. Is pressure happening at raw broker ingress, internal queueing, serialization/sign/encrypt work, or actual network egress?
4. Are heartbeats, commands, notifications, or business payloads dominating the link?
5. Is local communication or a small set of chatty plugins amplifying traffic disproportionately?

## Proposed Telemetry Model

### 1. Dimensions
Track metrics by the smallest useful keys below, then roll up to top-K tables:
- `transport_plugin`: `mqtt`, `amqp`, other
- `comm_instance`: `DEFAULT`, `HEARTBEATS`, `COMMAND_AND_CONTROL`, `NOTIFICATIONS`, and `L_*` variants
- `direction`: `ingress_raw`, `ingress_delivered`, `egress_enqueued`, `egress_sent`
- `topic_role`: `send`, `recv`, `device_specific_send`, `device_specific_recv`
- `resolved_topic`: actual MQTT topic string used by the wrapper
- `event_type`: `PAYLOAD`, `HEARTBEAT`, `COMMAND`, `NOTIFICATION`
- `stream_name`, `signature`, `instance_id` from `EE_PAYLOAD_PATH`
- `initiator_id`, `destination_id`, `destination_addr` for point-to-point traffic
- `formatter`, `encrypted`, `local_only`
- `message_size_bucket`: `0-4KB`, `4-16KB`, `16-64KB`, `64-256KB`, `256KB-1MB`, `1MB-2MB`, `>2MB`

### 2. Counter Families
For each window and each rollup key, collect:
- `msgs_attempted`
- `msgs_enqueued`
- `msgs_sent`
- `msgs_recv_raw`
- `msgs_recv_delivered`
- `bytes_enqueued`
- `bytes_sent`
- `bytes_recv_raw`
- `bytes_recv_delivered`
- `drops_send_queue_overwrite`
- `drops_recv_queue_overwrite`
- `drops_offline`
- `drops_oversize`
- `drops_duplicate`
- `drops_decode`
- `drops_signature`
- `drops_decrypt`
- `reconnect_attempts`
- `reconnect_failures`
- `send_queue_len_current`
- `send_queue_len_max`
- `recv_queue_len_current`
- `recv_queue_len_max`

### 3. Timing Metrics
Use rolling summaries rather than per-message verbose logs:
- `queue_wait_ms`: enqueue to actual send attempt
- `prepare_ms`: `_prepare_message()` or `_prepare_command()`
- `serialize_ms`: JSON formatting
- `sign_ms`: blockchain signing
- `encrypt_ms`: encryption when used
- `send_ms`: wrapper send call duration
- `ingress_to_delivery_ms`: broker receive to `get_message()` delivery
- `loop_ms`: communicator loop duration, logged next to traffic counters

Expose `avg`, `p50`, `p95`, and `max` per communicator window. Keep per-message timings behind a debug-only flag.

### 4. Stage Separation
The same message should be visible at four separate stages:
1. Raw broker receive or MQTT callback into `_recv_buff`
2. Application delivery when `get_message()` pops from `_recv_buff`
3. App enqueue when `send()` appends into `_send_buff`
4. Network egress when `_send()` returns

This separation is mandatory. Without it, high ingress with slow drain looks identical to low ingress with normal drain.

## Periodic Log Design
Reuse the existing 60-second `maybe_show_info()` cadence. Keep logs bounded and human-readable.

### Log Block A: Global Summary
One line with:
- window size
- total in/out raw and delivered kB
- total messages by event type
- total drops by cause
- total reconnect attempts and failures
- hottest communicator by bytes
- hottest plugin/signature by bytes

### Log Block B: Per Communicator
One line per communicator with:
- communicator name
- server, recv, send flags
- last activity
- address
- in/out raw kB
- in/out delivered kB
- messages sent and received
- send and recv queue current and high-water marks
- drops by cause
- p95 queue wait
- p95 send time
- last error time and message

### Log Block C: Top-K Egress Producers
Top 5 by bytes and top 5 by messages:
- `comm_instance`
- `resolved_topic`
- `event_type`
- `stream_name`
- `signature`
- `instance_id`
- msgs
- KB
- avg KB/msg
- p95 queue wait

### Log Block D: Top-K Ingress Consumers
Top 5 by raw ingress bytes and top 5 by delivered bytes:
- `comm_instance`
- `resolved_topic`
- `event_type`
- sender or initiator if available
- msgs
- KB raw
- KB delivered
- dequeue lag p95
- decode, decrypt, and signature failure counts

### Log Block E: Anomalies
Print only when non-zero:
- queue overwrite events
- oversize drops
- offline drops
- duplicate receives
- reconnect storms
- large-message spikes
- any communicator with queue high-water above 80% of maxlen

## Heartbeat-Safe Export Policy
Do not dump high-cardinality per-plugin or per-topic tables directly into `COMM_STATS` heartbeats.

Heartbeat should carry only:
- total in/out kB raw and delivered
- total sent and received counts
- total drops by cause
- top 1-3 offender IDs per direction as compact strings
- queue high-water marks
- reconnect counts

Detailed per-topic and per-plugin breakdowns should stay in local periodic logs and optional JSONL or CSV artifacts under `output/comm_telemetry/`.

## Recommended Config Surface
Plan for these flags:
- `COMM_TELEMETRY_ENABLED`
- `COMM_TELEMETRY_WINDOW_SEC` default `60`
- `COMM_TELEMETRY_TOPK` default `5`
- `COMM_TELEMETRY_INCLUDE_TOPICS`
- `COMM_TELEMETRY_INCLUDE_PLUGIN_BREAKDOWN`
- `COMM_TELEMETRY_PERSIST_JSONL`
- `COMM_TELEMETRY_PERSIST_CSV`
- `COMM_TELEMETRY_SIZE_BUCKETS`
- `COMM_TELEMETRY_CARDINALITY_LIMIT`
- `COMM_TELEMETRY_DEBUG_PER_MESSAGE` default `false`

Use bounded top-K structures or a space-saving heavy-hitter algorithm so the observability layer does not become another memory hotspot.

## Suggested Implementation Order
1. Add exact counters for enqueue, dequeue, send, receive, queue lengths, and all drop causes.
2. Separate raw ingress from delivered ingress and enqueued egress from actual sent egress.
3. Resolve and record actual MQTT topic names on subscribe and send so periodic logs can attribute traffic to concrete topics rather than only communicator names.
4. Add payload-path rollups for `stream_name`, `signature`, and `instance_id` on payload traffic.
5. Add command-specific rollups for `initiator_id`, `destination_id`, and `destination_addr`.
6. Extend `maybe_show_info()` to print bounded top-K tables from the active window.
7. Keep heartbeats low-cardinality and persist detailed snapshots to JSONL or CSV only when enabled.
8. Only after baseline visibility exists, consider exporter-based telemetry or broker-side metrics integration.

## Pre-Production Findings and Expected Optimizations
These optimizations can already be inferred from the current code and should be validated first once telemetry exists.

### 1. Heartbeats are likely a major traffic driver
Reasons:
- heartbeat cadence is up to every 10 seconds
- heartbeat payloads include `COMM_STATS`, capture stats, active plugins, disk and memory info, process info, and optionally timers and logs
- even compressed heartbeats still consume broker bandwidth and serialization and signing time

Expected optimization:
- split heartbeats into `slim` frequent status and `full` diagnostic heartbeat on a slower cadence or on-demand
- keep large diagnostic sections out of every regular heartbeat
- measure heartbeat size distribution before tuning

### 2. Commands share the heartbeat ingress transport path
Reasons:
- incoming command processing currently drains the `HEARTBEATS` communicator

Expected optimization:
- at minimum, log command traffic separately from actual heartbeat traffic even if they share transport today
- after measurement, consider moving receive-side command handling to the dedicated command communicator or topic so commands do not contend with heartbeat traffic or distort heartbeat observability

### 3. Silent queue eviction is currently invisible
Reasons:
- `_send_buff` and `_recv_buff` are bounded deques with `maxlen=1000`
- append past capacity silently drops the oldest entry unless it is explicitly counted

Expected optimization:
- add overwrite counters immediately
- if overflow is frequent, add backpressure, rate limits, or persistent spooling for critical traffic

### 4. Offline sending can lose traffic while looking idle
Reasons:
- send loops pop from `_send_buff`
- a message can be removed before a stable connection exists
- current periodic stats expose bandwidth but not attempted-vs-dropped deltas

Expected optimization:
- log `attempted`, `enqueued`, `sent`, and `offline_dropped` separately
- for critical channels, stop destructive pops while disconnected or persist to disk

### 5. QoS 2 is probably too expensive for all traffic classes
Reasons:
- MQTT config defaults to `QOS = 2`
- payloads and notifications are often idempotent or replay-tolerant compared with control traffic

Expected optimization:
- keep strict delivery only where required
- validate `QOS 1` for heartbeats, notifications, and many payload streams after telemetry confirms retransmission overhead or broker pressure

### 6. Payloads above 2MB are dropped, but size distribution is unknown
Reasons:
- `MAX_MESSAGE_LEN` is 2MB and oversize payloads are dropped before send

Expected optimization:
- measure size buckets and top oversize producers
- move large artifacts to object storage and publish pointers instead of full blobs
- compress large JSON payloads conditionally if CPU budget allows

### 7. Local and central communication can double traffic
Reasons:
- when local communication is enabled, the same logical message can be sent to both local and central communicators unless marked local-only

Expected optimization:
- report central and local egress separately
- quantify duplication before enabling local comm widely in production

### 8. Existing ingress kB is not broker ingress
Reasons:
- current `IN_KB` is measured when the app drains `_recv_buff`

Expected optimization:
- add raw callback ingress metrics first; otherwise operators will tune the wrong bottleneck

### 9. Throughput may be loop-bound, not broker-bound
Reasons:
- each communicator loop handles send work at loop cadence
- queue wait time is not currently surfaced

Expected optimization:
- log queue wait and drain rate before changing loop resolution, batching, or threading behavior

## Example Operator Questions This Plan Must Answer
After implementation, a single 60-second comm log should make these questions answerable without ad-hoc debugging:
- Which plugin signature is the top egress producer right now?
- Which MQTT topic is receiving the most raw traffic?
- Are we dropping because buffers overflow, because we are offline, or because messages are too large?
- Are heartbeats or business payloads dominating bytes?
- Is pressure concentrated in one stream or plugin, or spread across the node?
- Are local comms duplicating too much traffic?
- Is the bottleneck broker ingress, app dequeue, serialization and signing, or actual socket send?

## Non-Goals
- No runtime code changes in this document
- No broker reconfiguration
- No MQTT replacement
- No schema freeze for future metrics; the goal is to define the minimum observability needed to make the next optimization decisions defensible
