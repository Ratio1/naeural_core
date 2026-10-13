# PR225 SHM Diagnostic Evidence

Owner: integrator-test-executor (parent agent). No team backlog was requested.
Worktree: `/mnt/c/workspaces/r1_wrapper/worktrees/pr225-shm-hardening`.
Branch: `fix/pr225-shm-diagnostic-evidence`.
Base: PR225 `00df8854968c9913b76383fd4990f6e8e4b8b894`.
Destination: `Ratio1/naeural_core`, `threads-and-shm-issues`.

## Authorized Scope

- Preserve suppressed failure counts/reasons and first/last failed command IDs.
- Match results to bounded pending request summaries and compare cardinality.
- Snapshot worker/SHM state before timeout cleanup.
- Resolve actual worker aliases and emit bounded configuration context.
- Keep inference, IPC envelopes, retry/cleanup behavior and polling sleeps intact.
- Test first; adversarial subagent review after tests pass; check branch freshness
  and push only after the review passes. No per-request phase timing work.

## Acceptance And Test Bed

1. A brief suppressed failure survives in the next eligible summary.
2. Wrong/unknown IDs and wrong result cardinality are observable without changing
   the returned result; valid IDs/counts match and pending state is bounded.
3. Timeout evidence contains pending ID/age, worker liveness/exit and SHM occupancy
   captured before original termination/segment cleanup.
4. Selecting an actual covered worker captures manager, worker and CV records;
   logical inference keys stay unchanged. Configuration is scalar and bounded.
5. Expiry, caps, fork resets, logger/inspection failures, exceptions and disabled
   operation retain the original transport behavior. No frames are retained.

Prepared test bed: existing isolated Docker image
`r1-llcpp-five-edge-20261010t072554z-7b879aa5:candidate`, Python 3.13.11/NumPy.
Read-only source, no network or live keys/services. Focused unit/differential
tests plus actual spawned-child/Pipe/OS-SHM test; compile and diff checks.
Business discovery will be compared with unchanged PR head because known SDK
compatibility failures exist. Full serving/model smoke is BLOCKED: no GPU,
model artifacts or MinIO test bed. Python 3.10-3.11 qualification remains separate.

## Execution

- Guidance/skills read; PR head fetched and dedicated worktree created.
- Regression tests are prepared before production changes.
- Adversarial review is deliberately deferred until tests pass, per user order.
- PASS: initial 24 focused/differential tests, 2 spawn/Pipe/OS-SHM tests and
  7 unchanged polling tests; compilation and whitespace checks.
- Adversarial subagent started after those checks passed. It found a P2 timeout
  sampling collision: a recent result could suppress the pre-cleanup snapshot.
- Fixed with an independent one-second timeout event and bounded first suppressed
  timeout context. PASS: focused suite now 26 tests, including both new regressions.
- PASS: imports of changed modules using the local SDK in isolated Docker.
- FAIL, unchanged baseline: business discovery runs 346 tests on both the original
  PR head and the patch, with identical 4 failures, 11 errors and 1 skipped replay.
  Known older local SDK contracts/missing structured-training source/Torch-stub
  interaction; no added discovery defects. This is not a green repository suite.
- Freshness: fetched main is `cdef608` (merged PR224), one commit ahead of PR225;
  remote PR head is still the original `00df885`. Integration/checks remain pending.
- PASS: final 28-test evidence suite, 2 cross-process transport tests and 7 polling
  tests. Reviewer found and reproduced a P3 cap race as well; locked publication
  guards plus two deterministic tests prevent in-flight work restoring cleared state.
- PASS: adversarial reviewer `pr225_adversarial_review` after both fixes. Independent
  28+2 test reruns, 18-test additional scratch suite, original timeout/cap repros,
  actual cleanup/logger failures/out-of-order IDs and whitespace review passed.
  No blocking findings. Full model/load and Python 3.10-3.11 remain unqualified.
- Integrated `main` at `cdef608` with no history rewrite. The only conflict was
  append-only AGENTS notes; all main and diagnostic notes were retained.
- PASS after integration: 28 evidence tests, 2 spawn/Pipe/OS-SHM tests,
  7 polling tests, 11 real-SDK memory-scan tests and compilation/whitespace checks.
- FAIL, unchanged integrated baseline: both the patch and original-PR-plus-main
  baseline run 357 business tests with the same 4 failures, 11 errors and 1 skip.
  Comparison baseline is local commit `1ba7ccee006e5da293cc02263f68e5b6e0119088`.
- PASS: second adversarial integration review confirms the incoming main delta
  and exact preservation of diagnostic code/tests from `f12c9f8`.
- Implementation and review are complete; publication is gated by one final
  remote-head/base freshness and ancestry check. No deployment is authorized.
