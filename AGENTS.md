# Repository Guidelines
This file is "alive." When you discover horizontal insights during prompts (cross-cutting behavior, gotchas, performance hotspots, shared workflows), append a brief note at the end so future agents can reuse it. Keep entries short, dated, and specific.

## Project Structure & Module Organization
`naeural_core/` houses the runtime: `main/` orchestrates startup, `business/` contains pipeline plugins and the testing harness, `comm/` handles transports, and `serving/` wraps model execution. Runtime artifacts land in `_local_cache/`, created on demand. Optional packages live under `extensions/`, docs under `docs/`, and hardware spikes stay in `xperimental/`. Use `start_nen.py` as the launcher when embedding the core into Edge Node deployments.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: provision a Python 3.10–3.11 virtual environment.
- `pip install -e .` followed by `pip install -r requirements.txt`: install the package in editable mode plus heavyweight plugin dependencies.
- `python start_nen.py`: boot the orchestrator; expects `config_startup.json` or an `EE_CONFIG` JSON plus `.env` secrets loaded via `ratio1.utils.load_dotenv`.
- `python naeural_core/serving/model_testing/test_all/test_all_servings.py`: run the GPU-backed serving smoke tests; set `EE_MINIO_*` credentials and ensure CUDA availability.
- `python xperimental/minio/test_minio.py` (pattern): execute targeted experiments without affecting release modules; document prerequisites alongside the script.

## Coding Style & Naming Conventions
Match the repository’s two-space indentation, `snake_case` functions, `PascalCase` classes, and all-caps constants (imported as `naeural_core.constants as ct`). Prefer double quotes for user-facing strings. Reuse the shared `Logger` for structured logs and reference config keys through `ct` instead of raw literals. When exposing HTTP endpoints, wrap plugin methods with `@BasePlugin.endpoint` and keep companion templates in the same package (see `docs/FastApiWebApp.md`).

## Testing Guidelines
Keep fast checks close to their modules—`naeural_core/business/test_framework/` is the staging ground for unit-style tests. Name files `test_<feature>.py` so `python -m unittest discover` remains viable. Integration suites under `naeural_core/serving/model_testing/` rely on CUDA hardware, MinIO access, and populated `.env` files; record the environment, datasets, and command output in the PR. Experimental proofs should stay in `xperimental/` with a module-level docstring describing required GPUs or services.

## Commit & Pull Request Guidelines
Follow the Conventional Commit voice observed in history (`feat:`, `fix:`, `chore:`). Reference issues with `(#123)` when applicable, cap subjects at 72 characters, and detail behavioral impact plus commands executed in the description. Attach logs or screenshots for inference-facing changes, and call out any new ports, environment keys, or migrations. Verify that `.env` files and cached artifacts stay untracked.

## Configuration & Security Notes
Runtime configuration resolves from `config_startup.json`, `EE_CONFIG`, and `EE_ID`; keep secrets outside the repo and feed them through `.env`. Validate external payloads using the filters defined in `naeural_core.constants` when extending comms modules, and avoid embedding credential defaults inside plugin code.

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
