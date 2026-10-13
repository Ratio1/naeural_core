# Temporary serving SHM diagnostics

These probes observe the existing failure path. They do not repair failed
admission, resize/reclaim SHM, pad missing inference or change retries.

## Enablement

Diagnostics are off unless the process starts with `EE_SHM_DIAGNOSTICS` set to
a JSON object containing `targets` and `until`. `targets` contains one or two
exact canonical serving names (case-insensitive), for example `TH_YF8S`.
`until` is an absolute Unix timestamp in the next 600 seconds. Invalid, expired
or overly long windows disable collection. Select actual worker names, resolving
covered-server aliases beforehand; logical AI-engine names may differ. Manager
and consumer diagnostics resolve aliases through the active worker registry,
while records retain `requested_server` and production routing remains unchanged.

Configuration is read once per interpreter. The same environment must reach
manager and worker processes. There is no hot-enable API. Expiry automatically
restores the cheap disabled path; a newly started worker cannot renew the
absolute deadline. Removing the setting for the next process start disables it.
Activation/restart/deployment are separate operator actions, not part of this
patch. A short window can expire during slow model startup.

## Records and interpretation

New lines begin with `[SHM_DIAG]` and contain bounded scalar JSON:

| Event | Meaning |
| --- | --- |
| `send` in manager | Original request demand, all traversed arrays/bytes, admission counts, first rejection ordinal/reason, non-atomic occupied flags before conversion and immediately before send, send completion or exception class |
| `receive` in worker | Receipt followed by restored-array count, missing references, usable image count and first read-failure reason; idle polls emit nothing |
| `send` in worker | Existing response send completed or raised; this does not prove manager receipt |
| `result` in manager | Existing result envelope received, outer entry count and empty per-stream entries; expected-response timeout/receive errors are separate categories |
| `consumer` | Configured detector result cardinality/type before metadata filtering and tracking; absent model, `[]` versus `[[]]`, wrong row types and original image count |
| `configuration` | Once per selected worker/segment lifecycle: actual worker PID, requested alias, transport kind, SHM segment, capacity, maximum elements/rank and dtype |
| `timeout` | Pre-cleanup worker liveness, pending-request and SHM occupancy snapshot; independent of result sampling |

Configuration describes the physical worker's segment, even when a covered
alias omits SHM in its registry entry and therefore sends arrays through the
pipe. Per-request `send.shm`, admission counters and `requested_server` retain
the actual handle's transport behavior; alternating direct and covered handles
must not repeatedly emit worker configuration.

`pid`, the shared `window_until`, target and existing `cmd_id` identify sampled
transport observations. No IDs are added to production envelopes. Consumer
records use the existing instance hash and explicitly report `correlated=false`,
because CMD_ID has already been removed before business dispatch. Timeouts and
receive exceptions without an envelope also remain uncorrelated. Manager
`correlated=true` now means the received ID matches the oldest tracked PREDICT
request for the requested handle. `expected_cmd_id` and `observed_cmd_id` expose
mismatches; existing response acceptance and retries are unchanged.

Manager tracking retains at most 64 scalar request summaries per actual target.
It records original entry counts and, for image-only structured requests with at
most 64 entries, per-entry image counts. Results are compared against these
expectations, and an `error_marker` boolean records presence of
`INFERENCES_META.ERROR` without logging its value. Raw/non-image/larger requests
keep outer counts only. `tracking_evictions` exposes bounded-history loss;
missing expectations cannot prove that a response was never requested.
Only PREDICT/RESULT has this request-ID contract; startup/config messages are not
invented into it. Tracking clears on failed send, worker cleanup, expiry, output
cap and fork. No input, array, inference, metadata or exception text is retained.

Timeout records capture pending count, expected ID, oldest pending age, worker
PID/alive/exit status and SHM initialized/occupied/free/capacity state before
termination and segment cleanup. They remain uncorrelated with any received
envelope. Occupancy and liveness are instantaneous observations, not ownership
or eventual-process-exit guarantees. The first timeout is independent of prior
result records. Repeats are limited to one per second; the first suppressed
snapshot is retained in `suppressed_timeout_*` scalar fields until the next
eligible timeout. Expiry/output caps can leave a final suppressed snapshot unreported.
For instance-specific detectors, consumer targeting and inference lookup retain
`MODEL_INSTANCE_ID` from `STARTUP_AI_ENGINE_PARAMS`, using the same flat/keyed
single-engine and per-engine list conventions as BusinessManager.

Every recursively visited ndarray occurrence counts, including metadata and
duplicate references. `admitted` is a sender count; `read_ok` is a receiver count.
Receiver bytes count only successfully restored arrays. `images` counts input
image positions before conversion and `images_read` counts non-None image
positions afterward. Only the first failed array ordinal/slot/reason is retained;
`missing` counts all failures. No pixels, array hashes or extra image copies occur.
An `observation_error` flag means diagnostic inspection itself failed and its
counts may be partial; the original transport result/exception remains in charge.

`no_free_slot` records the allocator's unsuccessful attempt; it does not prove
orphan ownership. Occupancy snapshots are non-atomic. Empty per-stream entries
alone do not prove an error when that entry had no input image. A consumer
cardinality mismatch supplies stronger context. A successful send does not prove
receipt; receipt does not prove every image was usable. Parallel transport probes
do not execute in in-process mode; absence of their records is not loss evidence.

Each target/event has a one-second detail ceiling and ten-second repeat cadence.
An eligible observation records its current error/recovery state and suppressed
observation count. A short-lived anomaly during suppression may be represented
in fixed failure-reason counters plus `first_failed_cmd_id`,
`last_failed_cmd_id`, `first_failed_reason` and `missing_total`. A healthy call
with retained failure evidence can emit after one second; otherwise healthy/error
repeats retain the ten-second cadence. `anomaly` describes the current observation,
while `failures` describes the whole interval. These are local boundary observations,
not distinct end-to-end incident totals. Expiry can leave a final interval unreported.
Missing sampled records cannot prove a missing request or absence of failures.

State is bounded to twelve target/event entries per interpreter, plus two bounded
request maps and two configuration identities. New serialized
record bytes are capped at one MiB per interpreter/window, ending with a cap
record; log prefixes/storage overhead are additional. This is not a shared
16-MiB fleet/window guarantee: restarted interpreters have fresh local counters.
An observation deployment must enforce its aggregate collection budget and
measure overhead under representative load. Legacy logs are unchanged and are
not certified secret-free by the new-record tests.

## Verification and limits

Run `python naeural_core/utils/test_shm_diagnostics.py -v` from the core root with
NumPy installed. Tests execute actual conversion and manager/worker/CV methods
with isolated collaborators, ordinary NumPy backing and synthetic frames. They
compare enabled/disabled results, slot mutations, envelope keys, call ordering and
exception identities; cover capacity/read/IPC/result loss, empty idle polls,
consumer shapes, secret canaries, target selection, expiry, suppression and caps.

Also run `python naeural_core/utils/test_shm_diagnostic_evidence.py -v` for failure
summaries, request bounds/matching, aliases and timeout snapshots, and
`python naeural_core/utils/test_shm_diagnostic_transport.py -v` for real spawned
processes, Pipe IPC and separately attached OS SHM. The transport test keeps model
responses and lifecycle initialization synthetic; it is not full model serving.

No model or GPU is exercised by these tests. Production-load budgets
(at most 1% throughput loss and 5% p95 prediction
increase) remain a deployment gate. Existing timers supply performance context;
these records do not attribute elapsed time to GIL contention or prove the
October 9 allocation trigger. Moxa and backend diagnostics are separate work.

The companion polling-wait commit adds at least 20 ms after normal plugin work,
including an iteration already over its time budget. A 50 ms execution therefore
takes at least 70 ms per iteration, about 14.3 Hz; a 100 ms execution takes at
least 120 ms, about 8.3 Hz. This is the agreed sleep floor, not a period floor.
It deliberately trades per-plugin throughput/latency for fewer GIL reacquisitions.
Measure the wait-only build against baseline separately from diagnostics off/on;
check busy-plugin queues, dropped inputs and command latency before deployment.
