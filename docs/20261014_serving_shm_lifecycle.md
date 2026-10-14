# Serving SHM lifecycle and diagnostics

ServingManager now allocates a fresh data/control pair for each physical worker
generation. Logical model identities and inference routing do not change.

## Ownership and capacity

- `create=True` exclusively creates the exact requested names. A collision is
  reported; it never turns a Creator into a User. `create=False` only attaches.
- Serving allocation names include `r1s_`, a manager-session UUID, a generation
  counter and a bounded model label. The worker receives that exact name through
  its existing startup arguments. Only collisions receive up to three attempts;
  other allocation failures use the existing pipe fallback before child startup.
- Each successfully created object is owned independently. Partial initialization
  rolls back only owned objects. Both objects receive cleanup attempts despite
  close/unlink failures. Failed closes remain retryable; attachers never unlink.
  Ordinary failure-reporting exceptions cannot hide the original allocation
  classification. Python resource tracking keeps its existing defaults.
- In-process and pipe workers allocate no SHM. Constructor, process start, READY,
  configuration and teardown failures attempt owned cleanup. A worker whose
  attachment fails cannot reach READY. Cleanup preserves the original failure.
  All-worker shutdown attempts the remaining workers before rethrowing the first
  teardown failure; one failed STOP cannot strand the next worker's allocation.
- Effective serving capacity is `max(100, configured SHM_MAX_LEN)`; omitted means
  100. The generic utility's default remains unchanged for non-serving callers.
  External capture producers keep their explicitly assigned names.

At `(1520, 2688, 3)` uint8, 100 slots reserve 1,225,728,000 data bytes plus 4,400
control bytes, about 1.142 GiB per physical worker. Four such workers declare
about 4.566 GiB. These are fixed-stride logical capacities, not RSS or locked RAM.
Smaller images use part of a slot. Returning a slot clears metadata, not storage.
One hundred slots do not guarantee throughput for 100 cameras; extra arrays in a
batch can exceed capacity. Covered aliases do not create extra allocations.

## What the logs show

Immediately before starting a worker, the manager verifies zero occupied slots
and the expected data/control sizes. POSIX lengths must match exactly; Windows
may round mappings up to page size. The generic external-producer attachment API
accepts larger segments, but rejects undersized segments before constructing views.

SHM rows follow the worker details under
`[SMGR] ==== List of active serving processes ====`. They report initialization,
Creator/User role, exact names, capacity, occupied/free slots, actual/expected
bytes and observation context. Pipe, in-process and covered handles are explicit.
The existing orchestrator timer dump supplies `context=after_collection`; a
nonzero count then appears as `suspected_residual_slots`. Other callers use
`snapshot` and do not classify in-flight slots as leaks. No slots are cleared by
diagnostics. Heartbeat return fields and `show=False` behavior are unchanged.

The same summary ends with lifecycle counters, bounded recent failures and a
metadata-only `/dev/shm` inventory. It also runs during manager startup, including
zero-worker startup, and before allocations. Inspection visits at most 256
directory entries, reports at most 16 candidate names, and retains at most 64
known legacy model names. Candidates cover the `r1s_` namespace and exact known
legacy data/control names; this is not a complete host-wide orphan inventory.
Truncation, stat errors and unavailable access are explicit. Unavailable counts
are `unknown`. No discovery attaches, registers or deletes a foreign object.

Failure history retains 32 scalar records, shows the most recent eight and counts
dropped history. Immediate manager lifecycle reports are limited to one per ten
seconds; utility allocation/cleanup failures retain their existing error logging.
The E2 template uses the existing 654-second extended timer-dump cadence (core
fallback: 650 seconds). No new timer, per-frame scan, image read or IPC is added.
Startup and printed-summary work has a small bounded cost, not zero overhead.

Possible leftovers may belong to another live manager. Occupancy and namespace
observations establish suspicion, not object ownership or the cause of the
original 32 stuck slots. Fresh identities prevent stale reuse but do not reclaim
objects left by hard kills. Stale-object deletion requires separate ownership
verification; this patch never performs it.

## Verification and rollout

Run the two lifecycle suites directly, plus the existing diagnostics regressions:

```sh
python naeural_core/utils/test_system_shared_memory_lifecycle.py
python naeural_core/utils/test_serving_shm_lifecycle.py
python naeural_core/utils/test_shm_diagnostics.py
python naeural_core/utils/test_shm_diagnostic_transport.py
python naeural_core/utils/test_shm_diagnostic_evidence.py
python naeural_core/utils/test_shm_always_on.py
```

Utility tests use tiny real OS allocations and injected failures. Manager tests
execute production method bodies with model/runtime boundaries replaced. A real
spawned child receives the exact assigned pair, consumes 100 arrays correctly,
rejects the 101st admission and returns occupancy to zero. This does not replace
GPU-model or 100-camera load acceptance. The retained POSIX backend is exercised
through an API adapter with real shared-memory file descriptors and mmap when
`posix_ipc` is unavailable.

Publish core before building E2 with the 100-slot template. Check the effective
configuration and aggregate SHM budget, then observe an authorized E2-only
restart: fresh Creator names, zero startup occupancy, exact child attachment and
the next normal summary. Confirm ordinary shutdown removes owned names. A
rollback to an old core restores its old fixed-name behavior; changing capacity
alone is not a lifecycle rollback or repair. No package/image publication, live
restart, foreign cleanup or release promotion is part of these local changes.
