# PR225 Full Edge SHM E2E

- Owner: integrator-test-executor; test-only serving/pipeline fixture scope.
- Branch: `test/pr225-shm-e2e`; worktree: `/mnt/c/workspaces/r1_wrapper/worktrees/pr225-shm-e2e`.
- Base: PR head `1bcf47ca00acd24ab982acf999282511b97743aa`.
- Remote: rsn2; dedicated `/tmp/r1-pr225-shm-e2e-20261013` directory.
- Real: production entrypoint, capture/business managers and threads, serving manager,
  spawned model process, OS shared memory, pipe IPC, CPU Torch inference, MQTT.
- Test-only: generated images, deterministic CPU model, injected mismatch/delay.
- Isolation: node `--network none`; broker shares node namespace, loopback listener;
  no published ports, host keys/cache/env/socket, privileged mode, or public peers.
- Before runtime imports, assert loopback-only interfaces, no routes and external
  connection fails. Compare live container IDs/start times before/after.
- Gates: focused diagnostics/transport/poll/memory tests; broad discovery with
  compatible SDK and unpatched baseline control; full-runtime normal/capacity/
  alias/mismatch/timeout/recovery, independent heartbeats and cleanup.
- Compile all added Python, critique fixtures and acceptance checks, preserve
  sanitized evidence locally, remove only test-owned remote resources.
- GPU serving/MinIO qualification and supported Python 3.10-3.11 are not available
  in the pinned Python 3.13 Edge image and must not be claimed as covered.

## Verification Results

- PASS: source SHA-256 checked after transport; real image SDK 3.5.57 and CPU
  Torch 2.12.0, no old SDK checkout mounted.
- PASS: initial 48 focused checks, then 49 after the configuration regression.
- FAIL (unchanged baseline): 415 business-discovery tests, one failure, eight
  errors, one skipped; identical on original-PR-plus-main baseline `1ba7cce`.
  Six oracle fixtures omit `evm_network`/`config_data`, structured training source
  is absent, a Torch stub breaks TensorRT import, and epoch rollover asserts zero.
  Older-SDK comm/dAuth/heartbeat errors disappear in this compatible environment.
- Found/fixed: alternating direct and covered handles relogged physical worker
  configuration because real aliases omit SHM. Only configuration observation
  now resolves the root registry entry; per-handle serialization stays unchanged.
  The new regression fails on `1bcf47c` (four configurations instead of one).
- PASS: independent adversarial review, 29 evidence tests and actual probe
  negative checks for missing boxes/incorrect computed values.
- PASS: strengthened diagnostics-on session: all functional phases, 36 records,
  two worker PIDs, 26 independent heartbeats, 523 MQTT payloads, graceful exit 0,
  no OOM/restart, loopback-only/no routes/ENETUNREACH, existing EMQX unchanged.
- PASS: sequential diagnostics-off control: all identical functional gates,
  zero diagnostic records, two worker PIDs, 25 heartbeats, 534 MQTT payloads,
  graceful exit 0, no OOM/restart, isolation and existing-service checks.
- PASS: compileall for added/touched Python; `git diff --check`; downloaded
  source/fixture SHA-256 matches the local tree; broad failure identities match
  baseline exactly. Adversarial probe checks reject missing boxes/wrong scores.
- PASS: evidence downloaded to workspace
  `worktrees/pr225-review-artifacts/e2e-rsn2-20261013`; no private-key blocks or
  bearer tokens in full-node artifacts. Test-owned containers, remote directory,
  fresh runtime keys/caches and downloaded-only pinned images removed. EMQX ID
  and original 2026-09-05 startup timestamp unchanged; only EMQX image remains.
- Initial fixture-setup/alias-expectation/concurrent-test-container failures are
  retained as evidence. The final acceptance requires every steady-state sample
  to have the correct cardinality and computed values, not one passing sample.
- End-to-end normal medians: diagnostics on 63.8 ms, off 67.2 ms; p95 154.1 ms
  versus 152.8 ms. Container memory snapshots 1.208 GiB versus 1.199 GiB.
  These sequential single-session figures include capture/thread scheduling,
  startup/backlog and all admin runtime memory; they are not causal overhead
  measurements or production throughput qualification.
