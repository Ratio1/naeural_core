# Always-on serving failure diagnostics

This revision replaces the opt-in trace described in
[the earlier guide](20261012_shm_diagnostics.md). It observes the existing
transport and inference contract; it does not resize/reclaim SHM, retry failed
requests, repair missing results or change business processing.

## Startup and configuration

No environment setting enables these diagnostics. Manager and worker startup
each initialize one process-local daemon logger. One `[SHM_DIAG]` startup record
states `always_on=true`, history/context/output-queue limits and `expiry=null`.
A leftover `EE_SHM_DIAGNOSTICS` value is ignored, including malformed or expired
JSON, and `legacy_env_ignored=true` reports its presence without printing it.
There is no replacement diagnostic configuration or activation deadline to
validate. Ten-second history retention expires evidence, not failure reporting.

The deployed image must contain this revision. Installing a new package into a
different checkout does not change a running engine. Publication, rollout and
an E2 restart are separate operator actions; this change performs none.

## Evidence and bounds

Each interpreter retains a deque of at most 128 successful `send`, `receive`
and `result` observations from the most recent ten seconds of activity.
Observations contain allowlisted scalar counts, existing command IDs and worker
identity. No image arrays, payloads, metadata objects, detections, raw exception
messages or image hashes are retained. A failure summary includes at most eight
recent observations for that worker. Heavy traffic can shorten the available
history below ten seconds; absent history does not establish missing transport.

Failures include rejected SHM admission, failed reference restoration, IPC
exceptions, expected-response timeouts, known image/result cardinality errors
and explicit serving error markers. The first admitted failure is queued
promptly; continuing failures aggregate every ten seconds, with a one-second
floor between transition records and a recovery summary. Counts describe
observed boundaries, not distinct end-to-end incidents.

Known structured image requests establish the result cardinality contract.
An empty detection list for an existing image is valid. Streams with zero
expected images may return empty image rows. Arbitrary non-image serving
results are not required to be image-result lists. Lost/evicted diagnostic
expectations report incomplete tracking; they do not prove a transport failure.
After tracking contention or eviction, directly known command IDs can still
establish their own image counts, but incomplete history cannot establish FIFO
ordering errors. Full ordering confidence returns only at explicit worker
lifecycle cleanup. An unverified result receipt cannot clear an earlier result
failure or produce a recovery summary.
Image-contract failures remain unresolved across suppressed and emitted
summaries until a valid, verified image result arrives. An intervening IPC
failure or successful non-image result cannot erase that requirement.
CV consumer checks run only when images exist, preserve per-engine model
instances, and remain explicitly uncorrelated because business inputs no longer
carry the original transport command ID.

Request state is bounded to 64 workers, 64 requests per worker and 256 requests
globally. Per-request image counts inspect at most 64 entries and 256 image
items; larger/non-image requests have unavailable image expectations. Error
states are limited to 128 worker/event keys. Configuration records describe the
physical worker's segment, even when a covered alias uses a pipe payload.

Admission/restoration counters reuse the existing conversion traversal.
Healthy requests do not scan the slot-occupancy array; failure and timeout
boundaries may take a non-atomic snapshot. The snapshot does not prove slot
ownership, pixel integrity or an orphan allocation.

## Execution and output behavior

Inference callers use nonblocking diagnostic lock admission and a bounded
32-record output deque. They never wait for the diagnostic logger, perform its
JSON serialization or execute its log I/O. A single daemon per interpreter
drains output. Output budgets renew each second: at most 32 records and 64 KiB
of serialized diagnostic content; logger prefixes/storage overhead are extra.
There is no lifetime byte cap that permanently disables failure reporting.

Counter snapshots expose history/state/tracking eviction, lock-contention
loss, full output queues, output-budget loss, logger errors and startup errors.
When the sink is unavailable or pressure exceeds the bounds, diagnostics lose
completeness rather than blocking inference. Recently queued records and final
suppressed aggregates are best effort at process exit. A startup record cannot
be guaranteed when the logger or diagnostic thread itself cannot start.

`observed_at` and history `time` are process-local monotonic observations;
`queue_delay_ms` shows delayed emission. Use PID, worker/alias and existing
command IDs for transport correlation. No cross-process ordering or delivery
guarantee is invented by the asynchronous logger.

This design has bounded CPU/memory cost, not literally zero overhead. Focused
tests and a repeatable synthetic benchmark measure it. Live GPU/camera workload
qualification remains necessary before claiming production throughput or GIL
improvement.

## Verification

From the core repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 naeural_core/utils/test_shm_diagnostics.py -v
PYTHONDONTWRITEBYTECODE=1 python3 naeural_core/utils/test_shm_diagnostic_evidence.py -v
PYTHONDONTWRITEBYTECODE=1 python3 naeural_core/utils/test_shm_diagnostic_transport.py -v
PYTHONDONTWRITEBYTECODE=1 python3 naeural_core/utils/test_shm_always_on.py -v
```

These exercise actual extracted manager/worker/CV methods, original exception
identity, real spawned Pipe/OS-SHM transport, aliases and model instances,
bounded history/state, privacy, suppressed failures/recovery, valid non-image
results, fork reset and blocked/broken diagnostic logging. Test fixtures may
raise output budgets for deterministic acknowledgments; the dedicated always-on
suite checks production bounds.

The isolated full Edge harness remains available with its existing pinned
images and network isolation. It now runs with always-on diagnostics; the old
`--diagnostics on` spelling is accepted for compatibility, while `off` fails
explicitly. See the earlier testbed isolation instructions, and run one fresh
test directory without setting any diagnostic environment variable:

```bash
python3 naeural_core/business/test_framework/shm_testbed/run_e2e.py \
  --root /tmp/r1-pr225-shm-e2e-20261014/always-on \
  --core-source "$PWD"
```

Docker access, the pinned images and spare resources are required. This harness
tests a synthetic CPU model and loopback MQTT; it is not a live nuvo-a2 rollout
or a production detector-performance measurement.
