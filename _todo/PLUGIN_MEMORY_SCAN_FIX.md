# Plugin Memory Scan Fix - 2026-10-09

## Task Contract

- Concern: queue-overflow diagnostics traverse node-wide state instead of the
  plugin instance, adding work to an already unhealthy main loop.
- Owner: pipeline-owner; single-agent implementation with two recorded critique
  passes. No team backlog was requested.
- Production scope: `naeural_core/business/base/base_plugin_biz.py` only.
- Worktree: `/mnt/c/workspaces/r1_wrapper/worktrees/plugin-memory-scan-20261009/naeural_core`.
- Branch: `fix/plugin-memory-scan-20261009`.
- Base: freshly fetched `origin/main`, `a4b2ad07bc2881ba1b8f31f84d0e14dc26bad2ca`.
- Local artifacts: sibling `artifacts/` directory in the task directory.
- Remote staging: `/tmp/r1-plugin-memory-scan-20261009` on rsn1 and rsn2.
- Preserve the dirty original Core/Edge checkouts and all existing remote services.

## Findings

1. `BasePluginExecutor.__init__` stores shared state in
   `_BasePluginExecutor__global_shmem`. `get_plugin_used_memory()` currently
   excludes `global_shmem`, which is a property, not the stored key.
2. The SDK logger's `get_obj_size()` reads `obj.__dict__` and matches exclusions
   literally. A shared-memory reference reaches `business_manager`, whose
   `plugin_locations_cache` retains imported modules and plugin classes. The
   scanner walks their dictionaries and can expose a large graph.
3. Independent base-plugin references also retain shared plugin memory,
   blockchain/R1FS engines, the blockchain wrapper, the shared-memory facade,
   and the upload mixin's lazily cached file-system manager. Correcting only global shared memory
   leaves these routes available.
4. `add_inputs()` calls both queue and plugin scans in the main thread when a
   queue overflows. The queue already has separate accounting and should remain
   excluded from the plugin estimate.

## Design And Pre-Implementation Critique

Use exact stored attribute names in the existing plugin-local exclusion list:
`_BasePluginExecutor__global_shmem`, `_BasePluginExecutor__plugins_shmem`,
`_BasePluginExecutor__blockchain_manager`, `_BasePluginExecutor__bc`,
`_BasePluginExecutor__r1fs`, `_file_system_manager`, and `plugins_shared_mem`.
Retain existing exclusions.
Close the diagnostic timer in `finally` so scanner exceptions cannot leave it
running. Add a NumPy-style docstring defining this as a plugin-state estimate,
excluding shared infrastructure and the separately measured input queue.

Critique pass 1 (ownership and correctness): excluding only `global_shmem` is
insufficient; test each direct shared reference independently. Keep exact names
instead of broad fragments that would hide legitimate plugin data. Do not derive
private names from a subclass name, because Python mangles them using the class
that defines the attributes. Do not change the SDK scanner's global behavior or
return types for this fix.

Critique pass 2 (testing and operational risk): counting fewer bytes alone does
not prove traversal stopped; use objects that raise when visited and inspect
the returned tree. Ensure owned-state growth remains visible, queue accounting
still works, inherited plugins keep the boundary, and timers close on errors.
A synthetic unit fixture is insufficient runtime proof: start the real
orchestrator, capture manager, business manager and plugin thread, force a queue
overflow, and observe subsequent processing and heartbeats.

This remains an estimate, not exclusive ownership accounting or an RSS meter.
Arbitrary user aliases into shared objects and very large legitimate owned data
are not bounded by this name-based change. A generic traversal budget or
identity-based exclusion API would be a separate SDK contract change.

## Testbed Prepared Before Implementation

### Isolated Regression Tests

- Load the two unchanged production diagnostic methods into a minimal class
  through AST, preserving the defining class name. Use the installed SDK's real
  memory scanner, not a reimplementation.
- Cases: mangled shared-memory graph with manager/module cache; every independent
  shared reference; growing shared versus owned buffers; similar attribute names;
  inherited plugins; input queue separation; scalar/tree return contracts;
  existing infrastructure exclusions; timer cleanup on an exception.
- Run on the unmodified base and retain expected failures, then run the same tests
  after the implementation in a Docker container with `--network none`.
- Run compile checks and repository business-test discovery. Compare any broad
  suite failures with the untouched base in the same image/environment.

### Full Edge Node E2E

- Obtain the public `ratio1/edge_node:mainnet` image as a dependency image only;
  pin its digest and record Python/Core/SDK identities before execution.
- Node container uses `--network none`, no host ports, host sockets, live caches,
  production env files, privileged mode, or host namespaces.
- A test-only MQTT broker shares the node's network namespace and listens only
  on `127.0.0.1`. The only interface is loopback; prove no default route and
  external connection failure before booting the node process.
- Inject this worktree read-only through `PYTHONPATH`, fresh startup/app/pipeline
  configuration, a short unique `EE_ID`, fresh disposable keys and cache, disabled
  EVM calls and update/restart actions, and `WORK_OFFLINE=true`.
- Use the full production entrypoint with no optional package installation when
  the image supplies that launcher. If absent, a test-only launcher calls the
  production `main(additional_packages=[])`; production launchers remain intact.
- Test fixture: a real plugin with owned data, a deliberately small queue, and
  slow processing, driven by a test-only source using the real capture-thread
  base and input builder. Refill the test queue once at BusinessManager delivery
  to model pressure after CaptureManager's availability check; its normal
  backpressure otherwise prevents the diagnostic branch. Check scanner
  trees for manager/module traversal, shared versus owned growth, inherited
  stored attributes, and an actual `add_inputs()` overflow diagnostic.
- Require continued plugin processing and MQTT heartbeats after overflow, a
  bounded test deadline, no OOM/restarts, and matching loaded-source SHA-256.
- Capture sanitized structured reports, logs, image/source provenance and
  isolation proofs under the task directory.

### Cleanup

Use exact task-owned container names. Stop the node and broker and remove only
their test state. Compare existing EMQX container ID/start time before and after.
Remove remote staging after downloading evidence; retain the local worktree,
tests, plan and sanitized artifacts for review. Record any downloaded image left
in Docker's reusable cache separately from test containers/state.

## Atomic Steps And Acceptance

- [x] Confirm owner, call path and scanner behavior; read local guidance.
- [x] Create isolated worktree; fetch and record integration base.
- [x] Review proposed fix and network containment before implementation.
- [x] Establish regression and full-runtime test fixtures; capture red baseline.
- [x] Implement exact shared-reference exclusions and timer cleanup.
- [x] Pass focused regressions/compilation; record baseline discovery failures.
- [x] Pass full Edge E2E with runtime source and network isolation evidence.
- [x] Review final diff; address evidence-backed findings.
- [x] Clean remote task state; preserve sanitized evidence and indexed context.

## Execution Record

- Linux Git fetch initially failed on DNS resolution; Windows Git fetched the
  same configured remote successfully. No authentication settings changed.
- Read-only preflight: rsn1/rsn2 each have approximately 15 GiB available RAM and
  more than 180 GiB free disk; each currently runs only its existing EMQX.
- Initial preparation downloaded the same public Edge image on both hosts
  before starting any test runtime.
- Isolated red baseline: 10 tests, 10 failures including six subtest failures;
  exact shared references were visited and shared growth inflated the estimate.
- First implementation: all 10 focused tests passed. Broad discovery ran 407
  tests before/after with the same nine unrelated failures (one assertion,
  eight errors); the ten memory failures disappeared.
- Review found the shared-memory facade's `_shm` alias; two additional failing
  assertions against the first implementation proved it bypassed the new private
  exclusion. Added `plugins_shared_mem` as an exact seventh exclusion.
- Full-runtime setup exposed fixture issues: module-level `_CONFIG` is required
  for plugin discovery, `AI_ENGINE` must be an empty list rather than `None`, and
  this base's built-in CounterStream does not implement `connect()`. Corrected
  test fixtures; these were not production memory-fix failures.
- Runtime image is Edge 2.10.480, installed Core 7.7.326, SDK 3.5.57, Python
  3.13.11. The public container therefore runs newer Python than the declared
  3.10-3.11 guidance; record this explicitly rather than claiming older-runtime
  verification.

## Final Verification

Commands ran inside the pinned Edge Docker image on rsn1/rsn2, with network
mode `none`; host Python ran only the standard-library Docker controller.
Evidence paths below are relative to the Core repo root.

| Command / scenario | Status | Evidence |
| --- | --- | --- |
| `python3 -m unittest discover -s naeural_core/business/test_framework -p 'test_plugin_memory_scan.py' -v` | PASS | 11 tests, 0.012 seconds; `../artifacts/isolated-final.log` |
| `python3 -m compileall naeural_core/business/base/base_plugin_biz.py naeural_core/business/test_framework/test_plugin_memory_scan.py naeural_core/business/test_framework/plugin_memory_testbed` | PASS | All six Python files compile; `../artifacts/compile-reviewed.log` |
| `python3 -m unittest discover naeural_core/business/test_framework -p 'test_*.py'` | FAIL (baseline) | 408 tests: one failure, eight errors, one skip. All nine defects also occur before this fix; `../artifacts/discovery-before.log` and `discovery-final.log` |
| Full Edge negative control using the unmodified Core source | FAIL (expected) | Shared-runtime traversal guard raised before a large scan; `../artifacts/rsn1/r1-plugin-memory-scan-negative/evidence/failure.json` |
| Full Edge `run_e2e.py --root /tmp/r1-plugin-memory-scan-20261009/r1-plugin-memory-scan-verified` | PASS | Real queue overflow/main-thread scan, continued processing and broker-observed heartbeats; `../artifacts/rsn1/r1-plugin-memory-scan-verified/evidence/result.json` |
| `git diff --check` | PASS | No whitespace defects |

The broad baseline defects are six stale oracle-refresh test fixtures, a missing
structured-training module, a Torch stub interaction in TensorRT discovery,
and an existing epoch-rollover assertion. They are outside this fix's scope.

Final runtime evidence: 187 tree objects; 17 real BusinessManager cache entries
not traversed; 16,777,216 shared bytes produce zero estimate growth;
1,835,008 bytes of owned growth remain visible. One actual overflow loses one
input, as expected for the injected pressure. Its main-thread memory scan takes
0.002081 seconds; the probe completes 15 processing cycles and MQTT observes five
heartbeats, including later heartbeats after the overflow. No OOM/restart.
Only `lo` exists, routes are empty, external socket failure is `ENETUNREACH` (101),
there are no published ports or privileged mode, and the exact loaded source
hash is `c83e7f3d7cb95f22a268d4cccc247ac57d9d0df22c8860b7dab619738bea28f4`.

## Final Critique

- Correctness: tests fail on the original code and pass on the final code. The
  separate shared-memory facade regression also fails on the first, incomplete
  patch and passes after adding its exclusion. Full class initialization and the
  actual overflow call path are covered by the container test.
- Scope: one production method changes; SDK/general scans and queue-accounting
  code stay as before. Exact names preserve similarly named owned fields.
- Safety: containment is checked before runtime imports. Only task-created
  containers/cache are removed; all captured rsn1 existing-service comparisons
  pass. No live env files, keys, Docker socket or network membership are reused.
- Review: two design critique passes and a final evidence/diff critique were
  performed sequentially by one agent; no independent second-agent audit is
  claimed. The review found and repaired the facade alias.
- Limits: no identity-based exclusion of arbitrary user aliases, generic scan
  budget, RSS accuracy, GPU/model qualification or older-Python qualification
  is claimed. Those are separate concerns, not hidden by these tests.

## Cleanup And Adoption

All run-owned containers and fresh runtime caches/keys were removed. Both remote
`/tmp/r1-plugin-memory-scan-20261009` staging directories and the Edge/broker
images downloaded only for this run were removed after downloading evidence.
No Docker prune command or operation on existing EMQX was used. Both hosts
retain only their original EMQX container and image, and rsn1 service identity
and start-time comparisons pass for every test run.

Keep this worktree/branch and the sibling local `artifacts/` directory for review.
The latter retains sanitized positive/negative reports, logs and a source
snapshot; no private keys or live credentials are retained. At initial verification
completion, no commit, push or PR had been requested or performed; the user
requested PR publication separately after the load-test review. No shared-checkout
change or live-node deployment was performed.
Restart a node/plugin process after installing the fix; Python's already-loaded
plugin classes are not changed by editing files on disk.

Durable context: workspace `docs/context/plugin-memory-scan-boundary-2026-10-09.md`.

## Follow-Up: Relevant Sustained Memory Pressure

The first E2E run covered a 2 MiB owned buffer and injected queue pressure, not
sustained high-memory operation. Do not interpret that result as pressure-load
qualification. The user requested this missing scenario explicitly.

Scope: extend only the opt-in test fixtures and their documentation, in the same
worktree and branch. The production fix is unchanged. Keep prior evidence and
use fresh `r1-plugin-memory-scan-*` run directories under the task staging root.

### Prepared Test Plan And Acceptance

- Use the same pinned Edge/SDK image, a 3 GiB hard memory limit, equal
  `--memory-swap` limit (no swap), two CPUs, and the existing interface-free
  node/loopback-broker isolation. Both hosts have approximately 15 GiB available;
  do not exhaust host RAM or interfere with existing EMQX.
- Read actual cgroup v2 memory usage/limit, anonymous usage, swap and OOM events,
  and process RSS. Touch all allocated pages; virtual reservations do not count.
- Grow real plugin-owned buffers in approximately 128 MiB batches, adding 2,048
  nested records per batch. Require at least 1.5 GiB owned payload and at least
  80% real cgroup utilization, with a 2.25 GiB owned-buffer cap and allocation
  headroom checks. Shared runtime data independently grows by 256 MiB.
- Isolated opt-in load run on rsn2: use the real SDK scanner and production
  diagnostic methods, compare estimates at every batch, inspect the large tree,
  verify shared-growth exclusion and separate 64 MiB queue accounting, release
  the load, and require estimate/RSS recovery. Keep ordinary unit tests small.
- Full Edge run on rsn1: use the real initialized plugin, capture/business
  managers and MQTT. Let retained state grow across processing cycles. Invoke
  the unmodified diagnostic when measured container pressure reaches the target,
  and exercise production `add_inputs()` on the main thread at that pressure.
  The one test-only queue refill remains explicit: capture backpressure normally
  prevents queue overflow. Require accounting error below 5%, tree containment,
  a scan duration below two seconds, at least three pressure diagnostics,
  30 seconds of pressure, heartbeats during/after pressure, heartbeat gaps below
  eight seconds, continued processing, release recovery, and no OOM/restart.
- The native application low-RAM monitor reads host memory via psutil, not the
  container limit, and its alert scans the entire orchestrator rather than this
  plugin method. Do not fake its readings or claim that alert was exercised.
  Pressure-triggered plugin scans are initiated by the test fixture using real
  kernel metrics; diagnostic implementation and runtime components are real.
- Verification commands: focused unittest discovery, opt-in
  `plugin_memory_testbed/run_load.py`, `run_e2e.py --scenario memory-growth`,
  compilation, normal business discovery and `git diff --check`.
- Preserve structured growth/pressure/recovery samples, source/fixture hashes,
  logs, isolation and existing-service evidence, then remove exact task-owned
  containers, caches/keys, staging and newly downloaded dependency images.

### Pre-Implementation Critique

Pass 1, validity: bytes alone do not load traversal, so combine large resident
buffers with a growing nested object graph. Require owned accounting and RSS/
cgroup evidence, not an allocation counter. Trigger overflow only after genuine
pressure; otherwise the previous small-buffer gap would persist. Distinguish a
test-driven pressure diagnosis from the native host-memory alert.

Pass 2, safety and liveness: no shared-host exhaustion, swap, unlimited allocator,
live credentials, or network joining. Leave headroom for scanner scratch space,
guard each allocation and bound run time. Measure heartbeat gaps and recovery,
not startup alone. Separate the heavy runner from default unit discovery.
Reviews remain sequential single-agent passes, not independent agent audits.

### Follow-Up Progress

- [x] Inspect the actual diagnostic and native memory-monitor paths.
- [x] Check host capacity and define load, safety and acceptance before edits.
- [x] Implement and review opt-in resident-load and full-runtime scenarios.
- [x] Run isolated relevant-load tests and focused regressions; retain timing failures as well as the passing repeat.
- [x] Pass full Edge sustained-pressure and recovery scenario, including whole-handler timing.
- [x] Record compilation/discovery/diff results and critique evidence.
- [x] Update reproducible instructions/context and clean remote task state.

### First Heavy-Load Feedback

The first isolated load run failed the two-second scan target after fourteen
successful growth samples (1,806 MiB retained, 28,672 nested records). This is
legitimate owned-graph work, not shared-runtime traversal. Its fail-fast reporter
did not retain the violating sample's exact duration or finish pressure/recovery.
Keep `../artifacts/pressure-20261009/isolated-load-first.json` and its log as a
failed result, not a passing qualification.

Revised only observability/validation timing: collect timing violations while
continuing growth, pressure and recovery, then fail final acceptance on any
violation. The two-second target, accounting, safety and liveness checks are
unchanged. No GC disabling, CPU quota relaxation, or production scanner change
was made to hide the first failure. The full-node run uses this reviewed flow.

The first full-node pressure run used the planned 3 GiB limit and stopped at
`Insufficient scan headroom`: 1,419 MiB owned, 22,528 records, and 86.95% cgroup
utilization. It did not reach the minimum-owned target and is a failed testbed
setup, not a passing pressure result. The controller cleaned up normally.
Use a fresh root with a 4 GiB full-node limit to cover its real runtime baseline
plus at least 1.5 GiB owned data and scanner scratch. Keep the isolated limit at
3 GiB; the 80% pressure target, two CPU quota and two-second acceptance stay
unchanged. Both hosts still have ample headroom. This increases meaningful
absolute load rather than lowering the required pressure fraction.

Final fixture critique found a timing gap: the plugin-scan stopwatch starts
after the separate queue scan, so it cannot describe the entire main-thread
diagnostic. Added a separate stopwatch around production `add_inputs()`, plus
an eight-second whole-handler liveness ceiling. The plugin-scan target remains
two seconds. Preserve the passing first 4 GiB run and repeat the full scenario
with this complete timing evidence rather than claiming its 0.956-second plugin
measurement was the entire handler.

### Follow-Up Verification And Results

All paths below are relative to `../artifacts/pressure-20261009/`. The production
method remains unchanged from the previously reviewed fix (SHA-256 `c83e7f3d7cb95f22a268d4cccc247ac57d9d0df22c8860b7dab619738bea28f4`).

| Command / scenario | Status | Evidence |
| --- | --- | --- |
| Focused `python3 -m unittest discover -s naeural_core/business/test_framework -p test_plugin_memory_scan.py -v` | PASS | 11 tests; `isolated-focused.log` |
| Opt-in `run_load.py --report /evidence/load-complete.json` | FAIL (latency) | Reached/recovered full load; one 3.293-second growth scan exceeded the two-second target; `isolated-load-complete.json` |
| Repeat `run_load.py --report /evidence/load-final.json` | PASS | Same 2.016 GiB payload and 32,768 records, 82.10% of 3 GiB, 64 MiB queue, 98,440 tree objects; `isolated-load-final.json` |
| Full `run_e2e.py --scenario memory-growth`, first 3 GiB configuration | FAIL (testbed setup) | Safety headroom guard stopped before minimum owned target; `rsn1/r1-plugin-memory-scan-pressure/evidence/result.json` |
| Full `run_e2e.py --scenario memory-growth`, first 4 GiB configuration | PASS | Sustained pressure/recovery and actual main-thread diagnostic; `rsn1/r1-plugin-memory-scan-pressure-4g/evidence/result.json` |
| Reviewed 4 GiB `run_e2e.py --scenario memory-growth` including whole-handler timing | PASS | All acceptance and cleanup checks; `rsn1/r1-plugin-memory-scan-pressure-reviewed/evidence/result.json` |
| `python3 -m compileall naeural_core/business/base/base_plugin_biz.py naeural_core/business/test_framework/test_plugin_memory_scan.py naeural_core/business/test_framework/plugin_memory_testbed` | PASS | All eight Python files; `compile-pressure-reviewed.log` |
| Normal `python3 -m unittest discover naeural_core/business/test_framework -p test_*.py` | FAIL (baseline) | 408 tests, one failure/eight errors/one skip, same nine defects as before; `discovery-pressure.log` |
| `git diff --check` and fixture SHA-256 comparison against loaded runtime provenance | PASS | Clean whitespace and all six fixture files match the reviewed runtime |

Reviewed full-node measurements: 2,164,260,864 owned payload bytes (2.016 GiB),
32,768 nested records, approximately 98,700 tree objects, 81.07% of a 4 GiB hard
limit, and 30.515 seconds held at pressure. Maximum full-tree scan 1.325 seconds;
actual main-thread plugin scan 0.831 seconds; entire overflow/delivery handler
0.831 seconds. MQTT independently observed 15 heartbeats during pressure and
four after release, maximum pressure heartbeat gap 2.302 seconds. 84 processing
cycles and no OOM, swap or restart. Large shared growth remained excluded;
owned accounting error stayed approximately 0.7%.

After release, node RSS fell by 1.885 GiB and owned estimates returned within
1 MiB of baseline. RSS did not return completely to its startup value (about
1.092 GiB after recovery); allocator retention versus additional runtime state
was not separated. Do not claim complete physical-memory return. The isolated
repeat reclaimed 2.311 GiB RSS and restored its exact 1,793-byte plugin estimate.

### Residual Risk And Cleanup

The earlier 3.293-second isolated scan is a real failed acceptance result, not
erased by the repeat or the two passing full-node runs. The boundary fix is
validated under relevant load, but deterministic two-second latency is not
qualified. No causal claim about GC, CPU scheduling or allocator behavior is
made without additional profiling. A separate change should consider bounded
or coalesced background diagnostics instead of synchronous full traversal in
the critical main loop; preserve exclusions and separate queue accounting.
No additional production behavior was changed for this test request.

Both hosts retain their original EMQX service; IDs/start times match. Test
containers, runtime keys/caches, remote task staging and dependency images
downloaded only for this round were removed. No prune or live-service action.
Preserve the local worktree and this sanitized evidence directory, including
failed runs, passing repeats and the reviewed source snapshot. Neither live
credentials nor raw existing-service logs are retained. Native host low-RAM
alerting, GPU/model workloads and older Python remain unqualified as stated.

## PR Preparation - 2026-10-09

The user authorized committing this task branch, pushing it and creating a PR.
The target remains `main`; GitHub confirms the repository's canonical name is
`Ratio1/naeural_core` and its current base is still `a4b2ad07bc2881ba1b8f31f84d0e14dc26bad2ca`.
Only the production method, focused tests, opt-in testbed and task documentation
are included. Generated artifacts, keys, source archives and workspace context
remain outside the commit.

- PASS: fresh local `python3 -m compileall -q` of the same eight Python files,
  with generated bytecode redirected outside the source tree; Python 3.12.3.
- PASS: pre-commit `git diff --check` and manual production/testbed diff review.
- PASS: production source and all six fixture hashes still match the reviewed
  full-node run; previous container verification applies to these exact files.
- BLOCKED: a fresh local SDK-test run, because local Python lacks `ratio1`.
  The recorded eleven-test container run remains PASS; no dependency install
  or new remote/container load run is performed solely to publish the PR.

The PR must retain the baseline discovery failures, the failed 3.293-second
isolated scan, and the Python/native-alert qualification limits. Publishing is
not a deployment, merge, or claim of deterministic scan latency.
