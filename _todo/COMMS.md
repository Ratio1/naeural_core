# MQTT Logging and Stats Optimization Plan
This document is planning-only. It covers transport observability, logging, and statistics only for the current comms stack. Any mention of message size here is for measurement and reporting only, not for reducing outgoing bytes. Payload-byte reduction proposals live in `PAYLOADS.md`.


## Scope
- `naeural_core/comm/communication_manager.py`
- `naeural_core/comm/base/base_comm_thread.py`
- `naeural_core/comm/default/mqtt.py`
- periodic comm logs emitted by `CommunicationManager.maybe_show_info()`
- optional heartbeat-safe comm summaries via `COMM_STATS`

## Current State
- `CommunicationManager.maybe_show_info()` emits one coarse status block every `COMM_SECS_SHOW_INFO` seconds.
- Per-communicator bandwidth comes from `BaseCommThread._incoming_lens/_outgoing_lens`.
- Outgoing bytes are counted after `send_wrapper()` succeeds.
- Incoming bytes are counted in `get_message()` when `_recv_buff` is drained, so current `IN_KB` is delivery rate, not raw broker ingress.
- `_send_buff` and `_recv_buff` are bounded deques and overwrite silently once full unless code adds explicit counters.
- Command receive handling is currently drained from the `HEARTBEATS` communicator in `CommunicationManager.maybe_process_incoming()`, so heartbeat and command ingress attribution are mixed.
- Local and central communicators can both carry the same logical message, but periodic stats do not quantify that duplication today.

## Observability Objectives
Within one log window, operators should be able to answer:
1. Which communicator, topic, and logical traffic class produced the most ingress and egress?
2. Are drops caused by queue overwrite, offline send attempts, oversize payloads, decode failures, decrypt failures, duplicates, or reconnect churn?
3. Is pressure at raw broker ingress, internal queueing, application dequeue, or actual network egress?
4. Are local communicators duplicating significant traffic relative to central communicators?
5. Which streams, signatures, or instances are the top talkers right now?

## Proposed Telemetry Model

### Dimensions
Track counters by the smallest useful keys below, then roll up to bounded top-K tables:
- `transport_plugin`: `mqtt`, `amqp`, other
- `comm_instance`: `DEFAULT`, `HEARTBEATS`, `COMMAND_AND_CONTROL`, `NOTIFICATIONS`, plus `L_*`
- `direction`: `ingress_raw`, `ingress_delivered`, `egress_enqueued`, `egress_sent`
- `topic_role`: send topic, receive topic, device-specific send, device-specific receive
- `resolved_topic`: concrete topic string
- `event_type`: `PAYLOAD`, `HEARTBEAT`, `COMMAND`, `NOTIFICATION`
- `stream_name`, `signature`, `instance_id` when available
- `formatter`, `encrypted`, `local_only`
- `message_size_bucket`: `0-4KB`, `4-16KB`, `16-64KB`, `64-256KB`, `256KB-1MB`, `1MB-2MB`, `>2MB` for observability only

### Counter Families
Per window and rollup key, collect:
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

### Timing Metrics
Keep timing summaries windowed and bounded:
- `queue_wait_ms`
- `prepare_ms`
- `serialize_ms`
- `sign_ms`
- `encrypt_ms`
- `send_ms`
- `ingress_to_delivery_ms`
- `loop_ms`

Expose `avg`, `p50`, `p95`, and `max` per communicator window. Keep per-message timings behind a debug-only flag.

## Logging Design
Reuse the existing periodic cadence from `maybe_show_info()` and keep output human-readable.

### Block A: Global Summary
One line with:
- window size
- total ingress raw and delivered KB
- total egress enqueued and sent KB
- total messages by event type
- total drops by cause
- total reconnect attempts and failures

### Block B: Per Communicator
One line per communicator with:
- communicator name
- server, recv, send flags
- last activity
- server address
- ingress raw and delivered KB
- egress enqueued and sent KB
- current and peak queue lengths
- drops by cause
- p95 queue wait
- p95 send time
- last error time and last error

### Block C: Top-K Producers
Bounded top-K by bytes and messages:
- communicator
- topic
- event type
- stream, signature, instance
- msgs
- KB
- avg KB/msg

### Block D: Anomalies
Print only when non-zero:
- queue overwrites
- oversize drops
- offline drops
- duplicate receives
- reconnect storms
- communicators above 80 percent queue occupancy

## Heartbeat-Safe Export Policy
Do not push high-cardinality per-topic or per-plugin tables into `COMM_STATS`.

Heartbeat comm summary should stay low-cardinality:
- total ingress raw and delivered KB
- total egress enqueued and sent KB
- total sent and received counts
- total drops by cause
- queue high-water marks
- reconnect counts
- compact top offender identifiers only when needed

Detailed breakdowns should stay in periodic logs and optional local JSONL or CSV snapshots.

## COMMS Todo List
1. Split ingress metrics into raw callback ingress and app-delivered ingress.
2. Split egress metrics into app-enqueued and actual network-sent stages.
3. Add explicit counters for send-buffer overwrite, recv-buffer overwrite, offline drops, oversize drops, decode failures, decrypt failures, duplicate receives, and signature failures.
4. Attribute command ingress separately from heartbeat ingress even while they share the same receive communicator.
5. Track central versus local communicator traffic separately so duplicated sends are visible.
6. Resolve actual topic strings on subscribe and send, then include them in bounded top-K log tables.
7. Add queue current and high-water metrics for every communicator.
8. Add message-size observability buckets to logs and local telemetry artifacts.
9. Keep heartbeat-exported comm stats compact and bounded.
10. Add optional JSONL or CSV persistence for detailed comm windows under local output only when enabled.

## Suggested Implementation Order
1. Add exact stage counters and queue high-water marks.
2. Separate raw ingress from delivered ingress and enqueued egress from sent egress.
3. Count all drop causes explicitly.
4. Record resolved topic names and event types.
5. Extend `maybe_show_info()` with bounded per-communicator and top-K summaries.
6. Keep heartbeat comm stats low-cardinality and push detailed views to local artifacts only when enabled.

## Non-Goals
- No runtime code changes in this document
- No broker reconfiguration
- No payload-shape changes or outgoing-byte reduction proposals here
- No MQTT replacement
