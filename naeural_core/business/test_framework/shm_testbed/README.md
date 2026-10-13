# Full Edge SHM Testbed

This test runs the production Edge entrypoint, capture and business managers,
threaded CV plugins, serving manager, spawned model worker, pipe IPC, kernel
shared memory and loopback MQTT. Generated images and a deterministic CPU Torch
convolution replace external cameras and downloadable trained models. This
validates transport/lifecycle and computed output, not detector quality or GPU
throughput. Production code is not monkeypatched.

## Setup And Run

Use a Docker host with at least 4 GiB spare RAM, two spare CPUs and room for the
pinned image. Stage the exact Core source in a dedicated directory. Dependencies
come from the real Edge image (SDK 3.5.57, Python 3.13.11, CPU Torch); do not mount
an older SDK checkout over it. Python 3.10-3.11 and GPU/MinIO serving remain
separate qualification rows.

```bash
docker pull ratio1/edge_node@sha256:d62da776263eea1f3e186d40fa95e05b0724c7c0381c570ec99130f605bd2037
docker pull eclipse-mosquitto@sha256:199ea8ef2e35ec2b1b37e59cfd1dbae538ed4dfa4a2251a121a52215a6248a21
python3 /tmp/r1-pr225-shm-e2e-20261013/source/naeural_core/business/test_framework/shm_testbed/run_e2e.py --root /tmp/r1-pr225-shm-e2e-20261013/enabled --core-source /tmp/r1-pr225-shm-e2e-20261013/source --diagnostics on
python3 /tmp/r1-pr225-shm-e2e-20261013/source/naeural_core/business/test_framework/shm_testbed/run_e2e.py --root /tmp/r1-pr225-shm-e2e-20261013/disabled --core-source /tmp/r1-pr225-shm-e2e-20261013/source --diagnostics off
```

Run sequentially with no other task containers active. Each root must be fresh,
and its parent name must start with `r1-pr225-shm-e2e-`. The controller records all
existing container identities/start times and requires them to stay unchanged.
It refuses container name reuse. Docker operations and phase readiness have
bounded deadlines. Node limits are 3 GiB, two CPUs and 256 PIDs; broker limits
are 128 MiB and one-quarter CPU. No swap is allowed for the node.

## Isolation

The node has `--network none`. Its broker shares that namespace and listens only
on `127.0.0.1:1883`. Before runtime imports the launcher requires loopback only,
no IPv4 routes and `ENETUNREACH` for an external socket. No ports, host networking,
privileged mode, Docker socket, production cache, live env files or host keys
are mounted. Fresh keys are generated in a private runtime directory. EVM calls
and tunnels are disabled, admin plugins paused, and offline continuity enabled.
Host-side image downloads occur before node startup; the node cannot join
testnet, devnet or mainnet.

## Acceptance

- Normal inference: at least 100 valid consumer samples for each handle.
- Capacity: 51 images with 50 SHM slots; direct handle returns 50, while the
  existing covered alias uses pipe payloads and returns 51. This is existing
  production routing, not a fixture override.
- Capacity recovery: two images per handle, with all results restored.
- Mismatch: test worker deliberately removes one returned image result; manager
  and consumer cardinality diagnostics must expose it.
- Timeout: test worker delays eight seconds against its two-second deadline;
  diagnostics must retain the pending CMD_ID and live worker before cleanup.
- Recovery: a new worker PID and at least 30 valid consumer samples per handle.
- Every steady-state sample after a 500 ms phase handoff must have the exact
  result count and one computed box per image. Scores must equal the known
  convolution output within tolerance. Timing summaries are end-to-end samples,
  not a statistically controlled diagnostics overhead benchmark.
- Independently observed MQTT payloads and at least ten heartbeats, including
  recovery traffic. No container restart or OOM, matching production file hashes.
- Diagnostics on: all six boundary events, canonical target and alias, capacity
  and cardinality failures, exactly one configuration per actual worker.
- Diagnostics off: no diagnostic records, identical functional acceptance.
- Graceful plugin-issued local STOP: serving cleanup logged and node exit zero.

## Evidence And Cleanup

Each run keeps incremental scalar evidence, source/fixture hashes, isolation,
worker identities, consumer results, broker observations, logs and `result.json`
under its `evidence` directory. The controller's `finally` removes only its named
node/broker and private runtime/key/cache directory, including on failure.
Acceptance is false on cleanup or existing-service mismatch. Download evidence
before removing the dedicated remote directory; remove downloaded-only pinned
images only after checking no other containers use them. Never use Docker prune.

Compile the fixture and run the focused tests from the Core source root:

```bash
python3 -m compileall naeural_core/business/test_framework/shm_testbed naeural_core/serving/serving_manager.py naeural_core/utils/test_shm_diagnostic_evidence.py
python3 -m unittest -v naeural_core.utils.test_shm_diagnostic_evidence naeural_core.utils.test_shm_diagnostic_transport naeural_core.business.test_framework.test_plugin_loop_waits naeural_core.business.test_framework.test_plugin_memory_scan
```
