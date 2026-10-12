# Temporary serving SHM diagnostics

These probes observe the existing failure path. They do not repair failed
admission, resize/reclaim SHM, pad missing inference or change retries.

## Enablement

Diagnostics are off unless the process starts with `EE_SHM_DIAGNOSTICS` set to
a JSON object containing `targets` and `until`. `targets` contains one or two
exact canonical serving names (case-insensitive), for example `TH_YF8S`.
`until` is an absolute Unix timestamp in the next 600 seconds. Invalid, expired
or overly long windows disable collection. Select actual worker names, resolving
covered-server aliases beforehand; logical AI-engine names may differ.

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

`pid`, the shared `window_until`, target and existing `cmd_id` identify sampled
transport observations. No IDs are added to production envelopes. Consumer
records use the existing instance hash and explicitly report `correlated=false`,
because CMD_ID has already been removed before business dispatch. Timeouts and
receive exceptions without an envelope also remain uncorrelated.

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
The next eligible observation records error/recovery transitions and suppressed
observation counts. Suppression counters cover observations since the last
record, not exact per-category incident totals. Expiry can leave a final partial
interval unreported. Missing sampled records cannot prove a missing request.

State is bounded to eight target/event entries per interpreter. New serialized
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

No model, GPU, operating-system SHM or live transport is exercised by those unit
tests. Production-load budgets (at most 1% throughput loss and 5% p95 prediction
increase) remain a deployment gate. Existing timers supply performance context;
these records do not attribute elapsed time to GIL contention or prove the
October 9 allocation trigger. Moxa and backend diagnostics are separate work.
