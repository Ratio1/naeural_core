# Plugin Memory Diagnostic Testbed

This fixture validates the real Edge orchestrator, capture thread,
BusinessManager delivery, plugin execution, diagnostic scanner, and MQTT
heartbeats. It has no inference/model, external broker, public network, or
production-cache dependency.

## Isolated Tests

Use an environment with the Ratio1 SDK installed, preferably the pinned Edge
image below. Run from the Core source root:

```bash
python3 -m unittest discover -s naeural_core/business/test_framework -p 'test_plugin_memory_scan.py' -v
python3 -m compileall naeural_core/business/base/base_plugin_biz.py naeural_core/business/test_framework/test_plugin_memory_scan.py naeural_core/business/test_framework/plugin_memory_testbed
```

The focused tests execute the production diagnostic methods through AST without
loading heavyweight plugin imports, and call the real installed SDK scanner.
The full-node test imports the entire real plugin class and verifies its loaded
source hash. Both scalar/tree return contracts and inherited private names are
covered. The regression contains eleven test methods.

## Full Edge Run

Stage this Core tree in a dedicated directory on a Docker host, for example
`/tmp/r1-plugin-memory-scan-20261009/source`. Preserve an unmodified source tree
separately to run the negative control. Obtain the exact dependency images:

```bash
docker pull ratio1/edge_node@sha256:d62da776263eea1f3e186d40fa95e05b0724c7c0381c570ec99130f605bd2037
docker pull eclipse-mosquitto@sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21
python3 /tmp/r1-plugin-memory-scan-20261009/source/naeural_core/business/test_framework/plugin_memory_testbed/run_e2e.py --root /tmp/r1-plugin-memory-scan-20261009/r1-plugin-memory-scan-fixed
```

The controller is standard-library Python. Its Docker processes are capped at
3 GiB/two CPUs for the boundary node (4 GiB for pressure, no swap) and
128 MiB/one-quarter CPU for the broker. Reserve
that capacity on the host. Container names are checked before launch, and an
existing runtime cache or evidence directory is refused.

The node uses `--network none`. The broker shares its network namespace and
listens on `127.0.0.1:1883`; anonymous access is confined to that namespace.
Before any runtime import, the launcher proves that only loopback exists,
there are no IPv4 routes, and an external socket fails. Neither container has
published ports, privileged mode, host networking, a host Docker socket, live
environment files, or a production cache. The image is a dependency image;
its default launcher/network configuration is not used.

The launcher calls production `main(additional_packages=[])` with the staged
Core tree and the image's real Edge extensions. It uses fresh keys, disables EVM
calls, pauses admin processing and tunnels, and enables offline continuity.
The pinned image runs Edge 2.10.480, SDK 3.5.57 and Python 3.13.11. Older Python
3.10-3.11 environments were not separately qualified in this run.

Normal CaptureManager backpressure prevents overflow. The fixture fills only
the test plugin's queue once at BusinessManager delivery, after capture
availability was checked, and delegates to production `add_inputs()`. This
models a missed-pressure handoff and exercises the real main-thread diagnostic,
queue loss counter and notification. No production component is mocked.

Acceptance requires shared-reference traversal guards to remain unvisited,
an empty manager/module leak list, unchanged estimates after 16 MiB of shared
growth, visible owned-buffer growth, a real overflow/main-thread scan, at least
12 processing cycles and three broker-observed heartbeats including
one after the overflow. The scan must finish within two seconds; the test has
a 180-second readiness/acceptance deadline. Source SHA-256, no OOM/restarts, and
network containment are checked.

For the negative control, use a new root and append
`--core-source /path/to/unmodified/core`. An assertion naming a visited shared
reference is the expected failure. Guards stop this scan before it can walk the
large runtime graph.

## Relevant Sustained Memory Load

The default `boundary` scenario above is a small correctness check, not memory-
pressure qualification. The opt-in `memory-growth` scenario grows retained
plugin state in 128 MiB buffer batches and adds 2,048 nested cache records per
batch. Every payload byte is nonzero and touched; both RSS and anonymous cgroup
growth must confirm physical residency.

It requires at least 1.5 GiB plugin-owned payload and at least 80% of the 4 GiB
hard container limit. Shared runtime data independently grows by 256 MiB and
must not contaminate the estimate. Allocation caps and 192 MiB scanner headroom
prevent an intentional OOM. Pressure lasts at least 30 seconds, with at least
three full-tree diagnostics, the real main-thread overflow scan at pressure,
accounting error below 5%, continued processing, at least ten heartbeats during
pressure, heartbeat gaps below eight seconds, and at least three heartbeats
after release. Owned estimates and RSS must recover; OOM/restarts are failures.
The entire `add_inputs()` overflow handler is also timed, including separate
queue accounting, lock waits and notification work. Its eight-second liveness
ceiling is separate from the unchanged two-second plugin-scan target.

Run with a fresh root:

```bash
python3 /tmp/r1-plugin-memory-scan-20261009/source/naeural_core/business/test_framework/plugin_memory_testbed/run_e2e.py --root /tmp/r1-plugin-memory-scan-20261009/r1-plugin-memory-scan-pressure --scenario memory-growth
```

Pressure scans are test-triggered from actual kernel container metrics. The
production plugin diagnostic is unmodified. The native AppMonitor low-RAM alert
uses host memory and scans the whole orchestrator; this test does not falsify
its readings or exhaust the shared host to fire that separate alert. The one
test-only queue refill is delayed until pressure, as normal capture backpressure
prevents overflow.

The isolated opt-in runner uses the production method bodies and real SDK
scanner with the same resident load, three large-tree scans, an independently
measured 64 MiB input queue, and release recovery. It is intentionally not a
`test_*.py` file, so ordinary unittest discovery does not allocate GiBs. Create
an empty evidence directory, then run from the dedicated staging root:

```bash
docker run --rm --name r1-memory-isolated-load-20261009 --network none --cap-drop ALL --security-opt no-new-privileges --memory 3g --memory-swap 3g --cpus 2 --entrypoint python3 -e PYTHONPATH=/opt/core -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD/source:/opt/core:ro" -v "$PWD/isolated-evidence:/evidence" -w /opt/core ratio1/edge_node@sha256:d62da776263eea1f3e186d40fa95e05b0724c7c0381c570ec99130f605bd2037 /opt/core/naeural_core/business/test_framework/plugin_memory_testbed/run_load.py --report /evidence/load.json
```

These commands require cgroup v2, 3 GiB for the isolated container or 4 GiB for
the full pressure node, and safe host headroom. `memory_load.py` fails closed if
the actual limit differs from `R1_MEMORY_LIMIT_BYTES` (default 3 GiB) or swap is
enabled. Allow 300 seconds for the full pressure scenario. Preserve its
growth/pressure/recovery samples and fixture hashes alongside the node evidence.
Timing violations are retained while the scenario continues through pressure
and recovery; any scan exceeding two seconds still fails final acceptance.
This defers failure reporting, not the acceptance threshold.

## Evidence And Cleanup

`<root>/evidence/result.json` records acceptance and cleanup. Companion files
contain sanitized counters, loaded-source provenance, isolation evidence,
node/broker logs, and existing-service identities before/after. Startup success
alone is not a passing result. The controller removes its exact node and broker,
then deletes its runtime directory including disposable private keys. It
retains evidence and verifies existing services have the same identities/start
times. It does not prune Docker images, networks or unrelated files.

After reviewing/downloading evidence, remove only the run's staging directory
and any dependency images that were downloaded solely for that run and have no
other consumers. Keep the source worktree and evidence needed for review.
