# Repository Guidelines
This file is "alive." `AGENTS.md` is the authoritative source for repository purpose, runtime constraints, module/file ownership, safe-edit boundaries, required verification commands, and agent handoff/escalation rules. Update it in the same change whenever any of those change. When you discover horizontal insights during prompts, append a brief note at the end so future agents can reuse it. Keep entries short, dated, and specific.

## Authority & Update Policy
- Follow `AGENTS.md` when other repo docs drift, then fix the stale doc.
- For ChainStore/CStore work, use `docs/CSTORE.md` as the quick-state review
  after reading `AGENTS.md`. It is a subsystem contract note, not a replacement
  for the repo rules here.
- Keep write scopes narrow; if a task spans more than one owner area, assign an `integrator-test-executor`.
- Prefer one responsible agent first. Split work only when there is a clear ownership boundary, hardware/tool boundary, or real parallel slack.
- Keep append-only sections append-only. Do not rewrite prior lessons to make a new task look cleaner.

## Repository Purpose & Runtime Constraints
- `naeural_core` is the runtime backbone of the Ratio1 Edge Protocol and is typically embedded into Edge Node deployments rather than used as a standalone end-user app.
- Supported Python is `3.10` to `3.11`.
- Standard launcher is `python start_nen.py`; treat `start_nen.py` as a frozen launcher boundary unless the operator explicitly asks to change it.
- Startup config resolves from `config_startup.json`, `EE_CONFIG`, and `EE_ID`; secrets belong in `.env` and are loaded through `ratio1.utils.load_dotenv`.
- Runtime artifacts, logs, downloaded models, and transient config copies land under `_local_cache/`; they are disposable and must stay untracked.
- Serving and some heavy ops require CUDA-capable hardware and may also require MinIO credentials or other external services.
- When `WORK_OFFLINE=false`, repeated comm failures can eventually stop the orchestrator after retry exhaustion; offline continuity depends on `WORK_OFFLINE=true`.
- Plugin lookup, config-handler reflection, and some long-horizon stats paths are cached or scale with runtime history; validate restart requirements and latency effects before claiming hot-reload or performance fixes.
- `xperimental/` is for experiments and proofs. It is not release verification and must not quietly become production runtime code.

## Project Structure & Ownership
| Area | Default owner role | Write scope | Notes |
| --- | --- | --- | --- |
| `naeural_core/main/`, `naeural_core/config/`, `start_nen.py` | `runtime-owner` | orchestrator, entrypoint, startup config | `start_nen.py` is read-only unless explicitly requested; startup semantics affect deployment safety |
| `naeural_core/comm/`, `naeural_core/remote_file_system/`, `naeural_core/ipfs/`, `_todo/COMMS.md` | `comm-owner` | transports, queues, ingress/egress, protocol notes | protocol or queue semantic changes require explicit verification notes |
| `naeural_core/business/`, `naeural_core/data/`, `extensions/` | `pipeline-owner` | plugins, data capture, business logic, plugin tests | watch queue pressure, plugin cache, and startup latency effects |
| `naeural_core/serving/`, `naeural_core/heavy_ops/`, `naeural_core/local_libraries/model_server*`, `naeural_core/utils/tracing/` | `serving-owner` | inference backends, serving harnesses, model tooling | GPU and external artifact services may be required to verify |
| `naeural_core/constants.py`, `naeural_core/core_logging/`, `naeural_core/utils/`, `pyproject.toml`, `requirements.txt` | `platform-owner` | shared constants, logging, utilities, packaging | cross-cutting changes require broader review and usually more than one verification row |
| `docs/`, `_todo/`, `_todo_archive/`, `README.md`, `AGENTS.md` | `docs-owner` | repo docs, runbooks, guidance | active planning docs live under `_todo/`; finalized or obsolete repo notes may move to `_todo_archive/`; docs must move with code, commands, and operator behavior |
| `xperimental/` | `platform-owner` | isolated experiments only | do not cite as production proof unless the task explicitly targets experiments |

## Safe-Edit Boundaries
- Do not modify `start_nen.py` unless the task is explicitly about launcher behavior and the operator accepts launcher-risk.
- Do not edit external site-packages or system paths such as `/usr/local/lib/python...`; treat them as read-only references.
- Do not treat `_local_cache/`, downloaded models, or generated logs as source of truth.
- Avoid casual edits to `naeural_core/constants.py`, shared logging, or startup/config plumbing; those are cross-cutting and require broader verification and `AGENTS.md` review if operator semantics change.
- Keep new production code out of `xperimental/`. Promote it intentionally into `naeural_core/` or `extensions/` with proper verification.

## Build, Test, and Development Commands
- Commands below assume an activated virtualenv where `python` resolves to the project interpreter; use `python3` to create the venv or when no `python` shim exists.
- `python3 -m venv .venv && source .venv/bin/activate`: provision a Python `3.10`-`3.11` virtual environment.
- `pip install -e . && pip install -r requirements.txt`: install the package in editable mode plus heavyweight plugin dependencies.
- `python start_nen.py`: boot the orchestrator; requires startup config plus `.env` secrets.
- `python -m unittest discover naeural_core/business/test_framework -p 'test_*.py'`: run the repo's unit-style test entry point.
- `python naeural_core/serving/model_testing/test_all/test_all_servings.py`: run the GPU-backed serving smoke tests; requires CUDA plus `EE_MINIO_*`.
- `python xperimental/minio/test_minio.py`: example targeted experiment command; document prerequisites beside the script.

## Required Verification Commands
| Change scope | Minimum verification | Notes |
| --- | --- | --- |
| Docs-only (`README.md`, `AGENTS.md`, `docs/`, `_todo/`, runbooks) | manual diff review and link/path sanity check | no code command required unless the doc changes executable commands or config semantics |
| Any Python file change | `python -m compileall <touched_paths>` | substitute the real touched files or directories |
| `naeural_core/business/`, `naeural_core/data/`, `extensions/` | `python -m compileall <touched_paths>` and `python -m unittest discover naeural_core/business/test_framework -p 'test_*.py'` when relevant | if discovery is not relevant, state why |
| `naeural_core/main/`, `naeural_core/config/`, `naeural_core/comm/` | `python -m compileall <touched_paths>` and a targeted reproducer or sanitized startup check | if `python start_nen.py` is blocked by secrets, brokers, or unsafe side effects, record the blocker explicitly |
| `naeural_core/serving/`, `naeural_core/heavy_ops/`, serving backends | `python -m compileall <touched_paths>` and `python naeural_core/serving/model_testing/test_all/test_all_servings.py` when hardware/services are available | mark `blocked` if CUDA, MinIO, or datasets are unavailable |
| Shared cross-cutting files | run every affected row above | do not claim broad safety from a compile-only check |
- Every handoff, PR description, and final task report must state each verification command as `pass`, `fail`, or `blocked`, with one-line evidence.
- Do not use `xperimental/` scripts as the only verification for production runtime changes unless the task is explicitly experimental.

## Coding Style & Naming Conventions
- Match the repository's two-space indentation, `snake_case` functions, `PascalCase` classes, and all-caps constants imported as `naeural_core.constants as ct`.
- Treat production-grade Python quality as a hard requirement: touched Python code must keep clear control flow, extensive NumPy-style docstrings on modified classes and functions, and descriptive inline comments for non-trivial or contract-sensitive logic.
- Prefer double quotes for user-facing strings.
- Reuse the shared `Logger` for structured logs.
- Reference config keys through `ct` instead of raw string literals.
- When exposing HTTP endpoints, wrap plugin methods with `@BasePlugin.endpoint` and keep companion templates in the same package.

## Commit & Pull Request Guidelines
- Follow Conventional Commit prefixes such as `feat:`, `fix:`, and `chore:`.
- Reference issues with `(#123)` when applicable and keep subjects under 72 characters.
- Describe behavioral impact, verification commands, and any blocked verification in the PR body.
- Attach logs or screenshots for inference-facing changes.
- Call out new ports, env keys, deployment paths, or migrations.
- Keep `.env`, `_local_cache/`, model artifacts, and other generated files untracked.

## Configuration & Security Notes
- Runtime configuration resolves from `config_startup.json`, `EE_CONFIG`, and `EE_ID`.
- Keep secrets outside the repo and inject them through `.env`.
- Validate external payloads using the filters defined in `naeural_core.constants` when extending comms modules.
- Do not embed credential defaults in plugin code or docs.
- Treat new operator commands, deployment paths, auth flows, and incident semantics as `AGENTS.md` update triggers.
- `ADMIN_PIPELINE_ASYNC_DISPATCH` is default-on unless explicitly disabled; when enabled, `admin_pipeline` capture/dispatch runs on isolated collection/dispatch lanes, is primed before serving warmup during startup, and uses `ADMIN_PIPELINE_DISPATCH_POLL_SECONDS`, `ADMIN_PIPELINE_QUEUE_MAXLEN`, and `ADMIN_PIPELINE_STALL_WARNING_SECONDS` as the operator-facing controls.
- Structured training uses additive pipeline config keys `INPUT_FIELDS`, `OUTPUT_FIELDS`, `TASK_MATRIX`, `EXPORT_FORMATS`, `ALLOW_NESTED_INPUTS`, `ALLOW_NESTED_OUTPUTS`, and `TRACE_INPUT_MODE`; for the `structured` signature TorchScript export is required, while GGUF must be attempted and may finish `blocked` with an explicit capability reason.
- Standalone structured inference currently uses AI engine `th_structured` with local startup config keys `MODEL_PATH` plus either inline `INPUT_FIELDS` / `OUTPUT_FIELDS` / `TASK_MATRIX` or a local `MODEL_CONFIG_PATH`; this is the temporary same-node deployment path before object-storage-backed bootstrap.
- Heavy-op startup keeps `send_mail` and `send_sms` default-enabled even when `HEAVY_OPS_CONFIG.ACTIVE_COMM_ASYNC` is overridden; operators must set `DISABLE_DEFAULT_SEND_MAIL` or `DISABLE_DEFAULT_SEND_SMS` to opt out of default notification dispatch.
- `EE_DISABLE_ADDRESSED_PAYLOAD_SUBS=true` disables addressed payload-topic subscriptions and forces broadcast-only payload receive during rollout/rollback of targeted payload routing; `EE_DISABLE_ADDRESSED_PAYLOAD_SENDS=true` separately disables targeted payload fanout on send and downgrades addressed payloads to one broadcast publish without warning spam.

## Agent Cards
These are repo-local role cards inspired by current A2A agent-card practice. They are lightweight operating contracts for collaboration inside this repository.

### `platform-owner`
- Objective: maintain shared platform behavior, packaging, logging, and cross-cutting utilities without destabilizing downstream subsystems.
- Owned files / write scope: `naeural_core/constants.py`, `naeural_core/core_logging/`, `naeural_core/utils/`, `pyproject.toml`, `requirements.txt`.
- Required inputs and context: impacted subsystems, config keys, dependency/runtime assumptions, affected verification rows.
- Expected outputs / artifacts: narrow patch, compatibility notes, verification summary, any required `AGENTS.md` update.
- Allowed tools: repo search, targeted edits, compile/test commands, local docs review, web review for external protocol or dependency changes.
- Escalation triggers: more than one owner area affected, new env keys, logging/metric semantic changes, dependency upgrades with runtime risk.

### `runtime-owner`
- Objective: maintain startup, orchestration, and runtime control flow without breaking deployment boot or shutdown behavior.
- Owned files / write scope: `naeural_core/main/`, `naeural_core/config/`; `start_nen.py` is read-only unless explicitly assigned.
- Required inputs and context: startup config source, env assumptions, repro steps, offline/online behavior expectations.
- Expected outputs / artifacts: bounded runtime patch, startup notes, verification logs or explicit blockers.
- Allowed tools: repo search, targeted edits, compile checks, sanitized startup checks, targeted runtime reproducers.
- Escalation triggers: any need to touch `start_nen.py`, exit behavior changes, multiprocessing changes, broker/secret dependency blocks, deployment-path changes.

### `comm-owner`
- Objective: maintain transport correctness, queue behavior, ingress/egress semantics, and comm observability.
- Owned files / write scope: `naeural_core/comm/`, `naeural_core/remote_file_system/`, `naeural_core/ipfs/`, `_todo/COMMS.md`.
- Required inputs and context: payload examples, queue/retry semantics, broker assumptions, backward-compatibility constraints.
- Expected outputs / artifacts: transport patch, protocol notes, verification evidence, open risk note if live infra is unavailable.
- Allowed tools: repo search, targeted edits, compile checks, local repro scripts, protocol/source review.
- Escalation triggers: wire or topic semantic change, incompatible message shape, blocked live-broker verification, queue-loss or shutdown-risk changes.

### `pipeline-owner`
- Objective: maintain data capture, pipeline plugins, and business logic while preserving queue safety and plugin loading expectations.
- Owned files / write scope: `naeural_core/business/`, `naeural_core/data/`, `extensions/`.
- Required inputs and context: plugin signature/config, pipeline topology, queue/backpressure expectations, operator-visible behavior.
- Expected outputs / artifacts: plugin or capture patch, relevant unit tests or blockers, config/docs updates when behavior changes.
- Allowed tools: repo search, targeted edits, compile checks, `unittest` discovery, documented experiment scripts when explicitly needed.
- Escalation triggers: plugin cache or discovery changes, queue overflow behavior changes, cross-subsystem config changes, no safe reproduction path.

### `serving-owner`
- Objective: maintain inference backends, model-serving flows, and serving test harnesses with explicit hardware/runtime assumptions.
- Owned files / write scope: `naeural_core/serving/`, `naeural_core/heavy_ops/`, `naeural_core/local_libraries/model_server*`, `naeural_core/utils/tracing/`.
- Required inputs and context: model/backend target, CUDA availability, MinIO credentials, artifact locations, latency/correctness expectations.
- Expected outputs / artifacts: serving patch, smoke-test output or blocker, performance or compatibility note if relevant.
- Allowed tools: repo search, targeted edits, compile checks, serving smoke tests, GPU-specific repro commands when available.
- Escalation triggers: missing CUDA/MinIO/datasets, model artifact migration, unexplained latency regression, rollback uncertainty for backend changes.

### `docs-owner`
- Objective: keep repository docs, runbooks, and guidance synchronized with actual code and operator workflows.
- Owned files / write scope: `docs/`, `_todo/`, `_todo_archive/`, `README.md`, `AGENTS.md`.
- Required inputs and context: implemented behavior, exact commands, changed boundaries, operator impact, source references when policy changes.
- Expected outputs / artifacts: doc patch, worked examples, updated commands, reference list.
- Allowed tools: repo search, targeted edits, diff review, link/path sanity checks, web review for external guidance.
- Escalation triggers: command drift from code, unclear owner boundary, policy ambiguity, missing executable evidence from implementing agents.

### `critic`
- Objective: independently test and review an actor's result for correctness, safety, alert-noise risk, rollback risk, and missing tests.
- Owned files / write scope: none by default; may add review notes or a failing regression test only if explicitly assigned.
- Required inputs and context: diff, write scope, rubric, executed tests, logs, and any claimed assumptions.
- Expected outputs / artifacts: concrete defects with evidence or a clear pass.
- Allowed tools: read-only repo search, diff inspection, targeted test runs, spec/doc review.
- Escalation triggers: insufficient evidence, out-of-scope changes, unverifiable safety claims, repeated disagreement without executable repro.

### `integrator-test-executor`
- Objective: resolve cross-owner or actor-critic disagreements using executable evidence, then produce the final integrated result.
- Owned files / write scope: only the already-touched integration surface unless explicitly expanded.
- Required inputs and context: actor artifact, critic findings, branch state, test plan, blockers, and handoff envelopes.
- Expected outputs / artifacts: merged patch, final verification table, acceptance or escalation decision.
- Allowed tools: targeted repo edits, compile/test commands, diff review, log capture, source review.
- Escalation triggers: conflicting owner claims, repeated failed loops, unsafe rollback story, or unresolved blocker after evidence-driven retest.

## Single-Agent Loop
- Required loop: `plan -> implement -> test -> critique -> revise -> verify`.
- Keep each loop focused on one primary concern. If a second concern appears, record it and finish or stop the current loop first.
- Evaluate environment feedback before stylistic feedback: syntax errors, runtime failures, bad logs, failed tests, broken configs, and latency regressions outrank naming or formatting opinions.
- `plan`: state the concern, intended write scope, and minimum verification command before editing.
- `implement`: change only the files owned by the acting role unless an integrator expands scope.
- `test`: run the minimum verification row for the touched area before asking whether the change is "good enough."
- `critique`: check correctness, safety, operator impact, rollback path, and whether the tests actually exercise the claim.
- `revise`: change only what the critique or environment evidence proves is wrong; preserve already-good parts.
- `verify`: rerun the relevant verification after the last revision and record pass/fail/blocked.
- Stop and escalate after `2` revise cycles on the same concern without new executable evidence, or after `3` environment attempts that leave the system in materially the same failing state.

## Actor-Critic Workflow
- Use actor-critic only for higher-risk changes, low-confidence results, or work that crosses a meaningful review boundary.
- The actor owns implementation and tests inside a bounded write scope and must ship a patch plus a verification record.
- The critic is independent and focuses on correctness, safety, alert-noise risk, rollback safety, observability gaps, and missing tests.
- Critic requests must be evidence-backed: failing command, incompatible diff behavior, spec mismatch, missing telemetry, or unsupported assumption.
- The actor revises only the cited defects, keeps already-good behavior intact, and records what changed between cycles.
- The `integrator-test-executor` resolves disagreements using executable evidence, not preference. If the evidence cannot be produced safely, escalate instead of looping.
- Prefer one actor and one critic. Do not create review swarms.
- Stop when checks pass, critiques stop being materially new, or the iteration budget is exhausted.

## A2A-Style Task Contract
- Every delegated task must use a structured payload, not free-form prose alone.
- Preferred status values align with current A2A task states: `submitted`, `working`, `input-required`, `auth-required`, `completed`, `canceled`, `failed`, `rejected`.
- Long-running tasks, meaning more than `15` minutes or more than `3` tool/test cycles, require checkpoints at each phase boundary and at least every `15` minutes.
- Retries should be idempotent where possible: reuse the same `task_id`, add or increment `attempt`, and avoid duplicate external side effects if a prior attempt may have partially succeeded.
- Cancellations must be explicit and safe: set status to `canceled`, list partial artifacts, stop background work, and never revert unrelated user changes.

```yaml
task_id: COMM-20260317-001
attempt: 1
owner_role: comm-owner
goal: Separate command ingress metrics from heartbeat ingress metrics.
write_scope:
  - naeural_core/comm/communication_manager.py
constraints:
  - Preserve public payload shape.
  - Do not rename broker topics in this task.
required_inputs:
  - reproducer or failing log
  - relevant config or env assumptions
expected_artifacts:
  - patch
  - verification notes
  - handoff envelope
success_criteria:
  - behavior matches goal
  - required verification is pass or explicitly blocked
checkpoints:
  - reproducer confirmed
  - patch ready
terminal_state: submitted|working|input-required|auth-required|completed|canceled|failed|rejected
```

## Required Handoff Envelope
- Every handoff or escalation must include the following fields at minimum: `task_id`, `current_status`, `changed_files`, `tests_run`, `evidence_reviewed`, `open_risks`, `next_recommended_action`.
- Include `goal`, `owner_role`, and `attempt` whenever possible.

```yaml
task_id: COMM-20260317-001
attempt: 1
owner_role: comm-owner
goal: Separate command ingress metrics from heartbeat ingress metrics.
current_status: working
changed_files:
  - naeural_core/comm/communication_manager.py
tests_run:
  - command: python -m compileall naeural_core/comm
    result: pass
  - command: python start_nen.py
    result: blocked
    evidence: missing safe broker and startup secrets
evidence_reviewed:
  - broker log sample from 2026-03-17
  - current comm note in AGENTS.md
open_risks:
  - no live broker verification in this environment
next_recommended_action: run targeted MQTT integration in staging and confirm ingress counters
```

## Handoff & Escalation Rules
- Prefer supervisor-worker or router-specialist topologies over free-form peer swarms.
- Handoff only when the receiving role owns the remaining write scope, has the required hardware/credentials, or can run in parallel without blocking the next local step.
- Share only the minimum context needed: task contract, current evidence, blockers, and expected artifact shape.
- Treat agents as opaque execution units. Do not assume shared hidden memory, tools, or reasoning state.
- Keep A2A handoffs separate from MCP/tool/resource access; delegation is control-plane, tools are data-plane.
- Fail closed on blockers: return the partial artifact, exact blocker, and the missing input or authority needed to proceed.
- Escalate immediately for boundary changes, new operator commands, deployment-path changes, irreversible actions, auth/secret handling, or repeated failed loops.

## Worked Examples
### Single-Agent Task
1. `runtime-owner` takes `RUNTIME-20260317-001` to fix a race in `naeural_core/main/net_mon.py`.
2. `plan`: narrow scope to `net_mon.py`, choose `python -m compileall naeural_core/main/net_mon.py`, and prepare a targeted reproducer.
3. `implement`: add the smallest lock/snapshot fix inside the affected method only.
4. `test`: run compile plus the reproducer.
5. `critique`: check first that the race is gone and logs stay clean; only then consider style.
6. `revise`: if the reproducer still fails, adjust the lock scope once; escalate after two evidence-free loops.
7. `verify`: rerun compile and reproducer, then record the result in the final report.

### Actor-Critic Task
1. `comm-owner` is the actor for `COMM-20260317-002` and may edit only `naeural_core/comm/communication_manager.py`.
2. The actor ships a patch plus `tests_run` and a short rollback note.
3. `critic` reviews for incorrect ingress attribution, alert-noise risk, missed rollback hazards, and missing tests.
4. If the critic claims a defect, it must cite executable evidence such as a failed reproducer or a spec mismatch.
5. `integrator-test-executor` reruns the reproducer and decides based on the evidence, not preference.
6. The actor revises only the cited defect set and resubmits a new envelope.

### A2A-Style Cross-Agent Handoff
```yaml
task_id: SERV-20260317-003
attempt: 1
owner_role: serving-owner
goal: Add a serving-backend fallback required by a pipeline change.
write_scope:
  - naeural_core/serving/default_inference/
  - naeural_core/serving/model_testing/
constraints:
  - Keep existing config keys stable.
  - Do not change CUDA-primary behavior.
expected_artifacts:
  - bounded patch
  - smoke-test output or blocker
terminal_state: working
```

```yaml
task_id: SERV-20260317-003
current_status: completed
changed_files:
  - naeural_core/serving/default_inference/example_backend.py
  - naeural_core/serving/model_testing/test_all/test_all_servings.py
tests_run:
  - command: python -m compileall naeural_core/serving
    result: pass
  - command: python naeural_core/serving/model_testing/test_all/test_all_servings.py
    result: blocked
    evidence: CUDA unavailable on runner
evidence_reviewed:
  - backend load trace
  - fallback path diff
open_risks:
  - smoke path still needs GPU validation
next_recommended_action: run serving smoke in a CUDA-enabled environment with EE_MINIO credentials
```

## AGENTS.md Review Triggers
- Review and update `AGENTS.md` whenever module boundaries change.
- Review and update `AGENTS.md` whenever verification commands change.
- Review and update `AGENTS.md` whenever new incident semantics are introduced.
- Review and update `AGENTS.md` whenever new operator commands or deployment paths are added.
- Also update `AGENTS.md` when new owner roles, new auth flows, or new required environment keys are introduced.

## Online References
Reviewed on `2026-03-17` against these current public sources for agent interoperability, agent loops, and actor-critic/eval guidance:
- A2A Protocol Specification `v0.3.0`: `https://a2a-protocol.org/v0.3.0/specification/`
- OpenAI, "A practical guide to building agents": `https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/`
- OpenAI, "Agents SDK": `https://developers.openai.com/api/docs/guides/agents-sdk`
- OpenAI Agents SDK, "Handoffs": `https://openai.github.io/openai-agents-python/handoffs/`
- OpenAI, "Trace grading": `https://platform.openai.com/docs/guides/trace-grading`
- OpenAI, "Graders": `https://platform.openai.com/docs/guides/graders/`
- OpenAI, "Unrolling the Codex agent loop": `https://openai.com/index/unrolling-the-codex-agent-loop/`
- OpenAI, "Harness engineering: leveraging Codex in an agent-first world": `https://openai.com/index/harness-engineering/`
- Anthropic, "Building effective agents": `https://www.anthropic.com/engineering/building-effective-agents`
- Anthropic, "How we built our multi-agent research system": `https://www.anthropic.com/engineering/built-multi-agent-research-system`
- Anthropic, "Building agents with the Claude Agent SDK": `https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk/`
- Anthropic, "Demystifying evals for AI agents": `https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents`
- Self-Refine: `https://arxiv.org/abs/2303.17651`
- Reflexion: `https://arxiv.org/abs/2303.11366`
- ReVISE: `https://arxiv.org/abs/2502.14565`
- ProActive Self-Refinement (PASR): `https://arxiv.org/abs/2508.12903`

## Lessons Learned Template
Record reusable failures, false positives, rollback hazards, and validated fixes using this format:

- `YYYY-MM-DD` `[failure|false-positive|rollback-hazard|validated-fix]`: `scope=<path or subsystem>`; `trigger=<what happened>`; `evidence=<command/log/spec>`; `guidance=<what later agents should repeat or avoid>`.

Promote broad operational facts to `Living Notes` below; use the lessons format when the entry should prevent repeated mistakes.

## Living Notes (append-only)
Format:
- `YYYY-MM-DD`: one-sentence insight (include file/path or subsystem if relevant).

Latest:
- `2026-02-02`: Initialized living notes; append new cross-cutting insights here.
- `2026-02-02`: Comms send/recv queues are bounded deques (1000); when MQTT is down, one message is popped and retried while queues fill/drop, and the main loop will shut down once retries exceed `CONN_MAX_RETRY_ITERS` unless bypassed (see `naeural_core/comm` + `naeural_core/main/orchestrator.py`).
- `2026-02-02`: Correction: comm send loops pop from `_send_buff` even when disconnected, so messages may be dropped while offline; buffering is best-effort (see `naeural_core/comm/mixins`).
- `2026-02-04`: Business plugin instantiation runs inside the main loop (`naeural_core/business/business_manager.py` `_check_instances`), so `_get_module_name_and_class` plus plugin config/validation/inspect checks can stall the loop; heavy imports or safety checks show up as startup latency.
- `2026-02-04`: Plugin discovery in `ratio1._PluginsManagerMixin._get_plugin_by_name` walks the filesystem (`Logger.get_all_subfolders`) and safety checks call `inspect.getsource` + code scanning; first load per signature can be expensive if many distinct plugins.
- `2026-02-05`: Comm failure triggers main-loop shutdown only when `WORK_OFFLINE` is false; failure is `comm._nr_conn_retry_iters > CONN_MAX_RETRY_ITERS` (incremented on server connect failures, reset on success) and comm threads sleep `CONNECTION_FAIL_SLEEP_TIME` between retries, so shutdown is delayed until retries exceed the threshold (see `naeural_core/comm/default/mqtt.py`, `naeural_core/comm/base/base_comm_thread.py`, `naeural_core/main/orchestrator.py`).
- `2026-02-06`: DCT->plugin delivery is independent of comm health in each main-loop pass (collect/update/append/run at stages 4-8), and comm failures are enforced only at stage 10 (`has_failed_comms`) so offline processing continuity requires `WORK_OFFLINE=true` (see `naeural_core/main/orchestrator.py`, `naeural_core/main/main_loop_data_handler.py`, `naeural_core/business/business_manager.py`).
- `2026-02-09`: `Manager.plugin_locations_cache` stores plugin lookup results by lowercased signature for the manager lifetime (including failed lookups), so hot-added plugins or on-disk code changes for an already-cached signature are not picked up until process/manager restart (`naeural_core/manager.py`).
- `2026-02-09`: `BM_CACHE_CONFIG_HANDLERS` and `BM_CACHE_VALIDATORS` are implemented in shared `_ConfigHandlerMixin`, so enabling them affects config handler/validator reflection across biz, comm, capture, and serving plugins, not only BusinessManager (`naeural_core/local_libraries/config_handler_mixin.py`).
- `2026-02-18`: NetMon reads last heartbeat without a read lock; `get_network_node_tags` deep-copies the shared heartbeat dict and can raise "dictionary changed size during iteration" if `register_heartbeat` mutates it concurrently (see `naeural_core/main/net_mon.py`).
- `2026-02-19`: `EpochsManager.get_node_epochs` autocompletes missing epochs up to current, mutating cached epoch maps and scaling O(nodes * epochs) inside stats paths; repeated calls can be expensive for long-lived nodes (`naeural_core/main/epochs_manager.py`).
- `2026-03-04`: `EpochsManager` now tracks local per-epoch availability in `LOCAL_EPOCHS` to avoid re-sorting heartbeats in stats; debug local vs consensus can use stored values (`naeural_core/main/epochs_manager.py`).
- `2026-02-28`: CaptureManager stops data acquisition for all DCTs when any biz plugin queue overflows; for IoT listeners this can stall DCT deque drain and let the network receive deque (maxlen=1000) drop messages (see `naeural_core/data/capture_manager.py`, `naeural_core/data/base/base_iot_queue_listener.py`, `naeural_core/business/base/base_plugin_biz.py`).
- `2026-03-04`: BusinessManager handler routing (`NETWORK_ROUTE_BY_HANDLER`) filters inputs for any plugin instance; loopback-captured payloads lacking `EE_PAYLOAD_PATH` get dropped when enabled (see `naeural_core/business/business_manager.py`, `naeural_core/data/default/loopback.py`).
- `2026-03-10`: Node-oracle refresh now uses a background refresh thread for blockchain reads and applies append-only whitelist additions at main-loop stage 9, avoiding cross-thread whitelist writes; the fast `get_oracles(wait_interval=0)` path usually resolves from `NetworkMonitor`/`EpochsManager` cached node history, while brand-new nodes with empty netmon state may need later passes after heartbeats arrive (`naeural_core/main/orchestrator.py`, `naeural_core/main/net_mon.py`, `naeural_core/main/epochs_manager.py`).
- `2026-03-10`: Correction: comm failure enforcement in the main loop now occurs at stage 11, not stage 10, because stage 9 is node-oracle refresh and logs/timers shifted to stage 10 (`naeural_core/main/orchestrator.py`).
- `2026-03-17`: `CommunicationManager.maybe_process_incoming` drains incoming commands from the `HEARTBEATS` communicator, so logical command ingress and heartbeat ingress currently share the same transport path unless the receive flow is refactored (`naeural_core/comm/communication_manager.py`).
- `2026-03-17`: Current comm `IN_KB` stats are measured when `_recv_buff` is drained in `get_message()`, not when MQTT traffic first arrives, so broker-side ingress pressure and app-side consumption pressure are currently conflated (`naeural_core/comm/base/base_comm_thread.py`).
- `2026-03-17`: Correction: command/heartbeat ingress attribution is split and naming is misleading; `maybe_process_incoming()` drains the `HEARTBEATS` communicator, but the `COMMANDCONTROL` thread also consumes its own recv path and even registers heartbeats, while the SDK `MqttSession` “heartbeats” communicator is wired to config send + control recv channels (`naeural_core/comm/communication_manager.py`, `naeural_core/comm/mixins/commandcontrol_comm_mixin.py`, `/usr/local/lib/python3.10/dist-packages/ratio1/default/session/mqtt_session.py`).
- `2026-03-17`: `start_nen.py` is an intentionally frozen launcher boundary, sets multiprocessing start method to `spawn`, and exits via `os._exit`, so startup-path fixes should usually land in `naeural_core/main/entrypoint.py` or deeper runtime modules instead of the launcher.
- `2026-03-17`: Periodic heartbeats with `HEARTBEAT_TIMERS=false` still include broad summary sections such as `ACTIVE_PLUGINS`, `CONFIG_STREAMS`, `DCT_STATS`, `COMM_STATS`, and whitelist data; the regular/non-full heartbeat path omits timers and logs, not most diagnostic summaries (`naeural_core/main/app_monitor.py`, `naeural_core/main/orchestrator.py`).
- `2026-03-17`: `GeneralPayload` copies most capture metadata into `_C_*` fields for every payload and also adds tags plus multiple `_P_*` runtime fields, so capture-metadata growth directly increases wire payload size (`naeural_core/data_structures.py`).
- `2026-03-17`: Correction: the prior heartbeat-size note overstated `ACTIVE_PLUGINS` and `CONFIG_STREAMS`; those sections are gated by `EE_HB_CONTAINS_ACTIVE_PLUGINS` and `EE_HB_CONTAINS_PIPELINES` (default true if unset), usually travel inside compressed `ENCODED_DATA`, and `NetworkMonitor` pipeline cache is now refreshed directly through `NET_CONFIG_MONITOR` payloads rather than depending only on heartbeat parsing (`naeural_core/main/app_monitor.py`, `naeural_core/main/orchestrator.py`, `naeural_core/business/default/admin/net_config_monitor.py`, `naeural_core/main/net_mon.py`).
- `2026-03-18`: SDK mainnet capture (`1957` msgs / `65.7s`) was dominated by `heartbeat` (`58.1%`) and `NET_MON_01` snapshots, with `CURRENT_NETWORK` alone accounting for `8.6MB` (`27.0%`) across only `15` messages; admin/control-plane payloads can outweigh business payloads in real traffic (`PAYLOADS_SDK_RESULTS.md`, `naeural_core/business/default/admin/net_mon_01.py`).
- `2026-03-18`: SDK callback views can expose both decoded heartbeat fields and `ENCODED_DATA`; treat `ENCODED_DATA` there as analysis and archival overhead, not proof of raw-wire duplication without lower-level transport evidence (`PAYLOADS_SDK_RESULTS.md`, `naeural_core/main/app_monitor.py`).
- `2026-03-18`: Ratio1 SDK `Session` decompresses heartbeat `ENCODED_DATA` before `on_heartbeat`, and the default session may auto-request peer net-config after `NET_MON_01`; 10-minute bandwidth tests should therefore measure raw MQTT payload bytes at the communicator callback and disable auto net-config requests to stay passive (`/usr/local/lib/python3.10/dist-packages/ratio1/base/generic_session.py`, `/usr/local/lib/python3.10/dist-packages/ratio1/comm/mqtt_wrapper.py`).
- `2026-03-18`: Mainnet SDK traffic drivers in the sampled window were distributed across many `r1s-*` senders with similar totals and concentrated in `admin_pipeline` control-plane flows (`NET_MON_01`, `NET_CONFIG_MONITOR`, `CHAIN_STORE_BASE`) plus heartbeats, which suggests fleet-wide periodic admin chatter rather than one runaway business stream (`PAYLOADS_SDK_RESULTS.md`).
- `2026-03-18`: Root planning and analysis markdown files now live under `_todo/`; keep only `README.md` and `AGENTS.md` at repo root and update path references there when moving or adding planning docs.
- `2026-03-18`: Correction: the raw 10-minute mainnet SDK capture measured `NET_MON_01` at `49.0%` of raw MQTT payload bytes and heartbeats at `46.7%`; callback-level heartbeat expansion can overstate heartbeat dominance, so use `xperimental/payloads_tests/evidence/raw_bandwidth/*_mainnet_bandwidth_summary.json` or `_todo/PAYLOADS_SDK_RESULTS.md` for raw-basis claims (`xperimental/payloads_tests/sdk_bandwidth_capture.py`, `_todo/PAYLOADS_SDK_RESULTS.md`).
- `2026-03-18`: Measured `NET_MON_01` payloads compress extremely well with the heartbeat codec: across `30` mainnet samples, compressing `CURRENT_NETWORK` alone cut payload bytes by `80.2%`, while hb-style compression of all non-`EE_*` NET_MON fields cut them by `81.8%`, projecting about `50.4MB` saved or `40.1%` off the full 10-minute raw sample (`xperimental/payloads_tests/netmon_compression_probe.py`, `xperimental/payloads_tests/evidence/netmon_compression/20260318T200033+0000_netmon_compression_results.md`).
- `2026-03-19`: Additional packages installed by `naeural_core/main/entrypoint.py` now use interpreter-scoped caches under `_bin/<cache_tag>` and intentionally ignore the legacy flat `_bin` cache so Python upgrades do not reuse stale package artifacts.
- `2026-03-19`: Additional package caches in `naeural_core/main/entrypoint.py` now persist an `install_manifest.json` per `_bin/<cache_tag>` and rebuild that interpreter cache when the runtime identity or requested package list changes.
- `2026-03-19`: Additional package manifest matching now normalizes package-list order, and `naeural_core/main/entrypoint.py` initializes `lock` before startup so exception cleanup cannot reference it before assignment.
- `2026-03-19`: NET_MON sender-side compression cannot move routing/meta fields such as `STREAM_NAME`, `SIGNATURE`, `INSTANCE_ID`, `SESSION_ID`, or initiator/modifier addresses into `ENCODED_DATA`, because the comm layer still reads those top-level keys to build `EE_PAYLOAD_PATH`, session metadata, and destination routing before transport packaging (`naeural_core/comm/base/base_comm_thread.py`, `naeural_core/business/default/admin/net_mon_01.py`, `ratio1_sdk/ratio1/const/payload.py`).
- `2026-03-19`: Passive rollout scripts under `xperimental/payloads_tests/` still require a valid Ratio1 SDK network user in env or config; without that, `sdk_bandwidth_capture.py` and `netmon_compression_probe.py` fail during `GenericSession.__fill_config` before any baseline capture begins.
- `2026-03-30`: `EpochsManager.maybe_update_cached_data` should stay a compact dirty-epoch-history refresh (`EPCT.EPOCHS` + optional `EPCT.NAME`) rather than a full `__data` deepcopy, and callers needing full debug state should use `get_full_node_state()` so heartbeat registration does not contend on long cache copies (`naeural_core/main/epochs_manager.py`, `naeural_core/business/default/admin/net_mon_01.py`).
- `2026-03-30`: `EpochsManager.save_status` now keeps detached per-epoch caches for `SYNC_SIGNATURES`, `SYNC_AGREEMENT_CID`, and `SYNC_SIGNATURES_CID` and refreshes only dirty epochs before persistence, so unbounded signature retention no longer implies full signature-history deepcopy on every save (`naeural_core/main/epochs_manager.py`).
- `2026-03-31`: `Dockerfile_core` now expects the docker build context to be the parent directory containing both `naeural_core/` and `ratio1/`, installs both packages from those local trees, and starts the container with `python3 start_nen.py`.

- `2026-03-31`: Targeted payload fanout now requires an addressed topic template on the active payload channel (`TARGETED_TOPIC` or a templated `TOPIC`); otherwise both `naeural_core` and `ratio1` intentionally downgrade addressed payloads to one broadcast publish to avoid sending the same payload once per destination onto the broadcast topic.
- `2026-04-14`: The `structured` training signature now derives shared vocab/schema metadata from the train split and reuses it for dev/test plus export, so categorical/text compatibility across splits follows the train schema selected at startup (`naeural_core/local_libraries/nn/th/training/data/structured.py`, `naeural_core/local_libraries/nn/th/training/pipelines/structured.py`).
- `2026-04-14`: Structured training now emits `METADATA.SERVING_MODEL_CONFIG`, and same-node standalone inference can load the exported TorchScript through AI engine `th_structured` using local startup config rather than MinIO-backed bootstrap (`naeural_core/local_libraries/nn/th/training/pipelines/structured.py`, `naeural_core/serving/default_inference/th_structured.py`).
- `2026-04-23`: Serving routing now preserves `MODEL_INSTANCE_ID` in serving handles, and `MainLoopDataHandler` no longer overwrites aggregated inputs for repeated use of the same serving class; same-node deployments can therefore target distinct model instances of one serving process (for example multiple `text_classifier` models) as separate servers (`naeural_core/serving/ai_engines/utils.py`, `naeural_core/main/main_loop_data_handler.py`).
