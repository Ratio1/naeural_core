# naeural_core agent guide

This is the root contract for coding agents in `naeural_core`. It holds only stable,
repository-specific rules. Read the linked references when your task touches their area:

- [Runtime notes](docs/runtime-notes.md): non-obvious behavior and contracts, by subsystem.
- [Agent workflows](docs/agent-workflows.md): roles, the single-agent loop and actor-critic review.
- Subsystem contracts: [CStore](docs/CSTORE.md), [Deeploy node lifecycle](docs/DEEPLOY_NODE_LIFECYCLE.md),
  [serving SHM lifecycle](docs/20261014_serving_shm_lifecycle.md) and
  [SHM diagnostics](docs/20261014_shm_diagnostics.md).

## Core context

`naeural_core` is the runtime engine of the Ratio1 Edge Node: orchestrator, comms, data capture,
business plugins, serving, NetMon, epochs and CStore. It runs embedded in `edge_node` deployments,
not as a standalone app. `ratio1_sdk` provides the `ratio1` package (blockchain engine, logger,
MQTT wrapper); `edge_node` provides extensions and the operational APIs. Sibling directories with
those names are independent checkouts: change upstream behavior there and commit separately.

- Python 3.10 to 3.11.
- `python start_nen.py` boots the node. It is a frozen launcher (sets the `spawn` start method and
  exits through `os._exit`); put startup changes in `naeural_core/main/entrypoint.py` or deeper.
- Startup config comes from `config_startup.json`, `EE_CONFIG` and `EE_ID`; secrets come from `.env`
  through `ratio1.utils.load_dotenv`.
- `_local_cache/` holds runtime state (logs, models, pickles, config copies). It is disposable and
  never source.

## Repository map

- `naeural_core/main/`: orchestrator, entrypoint, NetMon (`net_mon.py`), epochs, app monitor.
- `naeural_core/comm/`: MQTT communicators, heartbeat ingress, command/control.
- `naeural_core/business/`: plugin base classes and default/admin plugins (NetMon, CStore,
  Deeploy lifecycle); tests live in `business/test_framework/`.
- `naeural_core/data/`: capture managers and data plugins.
- `naeural_core/serving/`, `heavy_ops/`, `local_libraries/`: inference backends and model tooling.
- `naeural_core/constants.py`, `core_logging/`, `utils/`: shared constants, logging and utilities.
- `docs/`: durable references. `_todo/`: active plans; finished ones move to `_todo_archive/`.
- `xperimental/`: experiments. It is never production code or release proof.

## Verification

Run checks with the project interpreter and the sibling `ratio1_sdk` checkout importable (for
example `PYTHONPATH=../ratio1_sdk:.`); an older installed `ratio1` breaks `constants.py` imports.

```bash
python -m compileall <touched_paths>
python -m unittest naeural_core.business.test_framework.<test_module>
python -m unittest discover -s naeural_core/business/test_framework -p 'test_*.py'
```

| Change | Minimum proof |
| --- | --- |
| Any Python file | `compileall` on the touched paths |
| `business/`, `data/`, `main/`, `comm/` | the focused test modules for the changed contract; a targeted reproducer when no test covers it |
| Startup, config or comm wiring | add a sanitized startup check; `python start_nen.py` needs secrets and a broker, so record it as blocked when they are unavailable |
| `serving/`, `heavy_ops/` | `python naeural_core/serving/model_testing/test_all/test_all_servings.py`; needs CUDA and `EE_MINIO_*` |
| Docs only | check links, paths and any command you changed |

- Report each check as pass, fail or blocked, with one line of evidence, in handoffs and PR bodies.
- Add a regression test for a bug fix when practical. Never weaken a test or production behavior
  to get a pass.
- If a broad suite was already failing, report the exact failure and prove the change narrowly.
- On macOS, `test_netmon_summary_status` needs `PATH` passed through its `extra_env`, because the
  SDK logger reads it on Darwin.

## Conventions

- Two-space indentation, `snake_case` functions, `PascalCase` classes.
- Use constants through `import naeural_core.constants as ct` instead of string literals.
- NumPy-style docstrings on new or modified functions and classes; comment non-obvious contracts.
- Log through the shared `Logger` (`self.P(...)` in plugins).
- HTTP endpoints are plugin methods decorated with `@BasePlugin.endpoint`.
- Conventional Commit subjects (`feat:`, `fix:`, `perf:`, `docs:`, `chore:`) under 72 characters.

## Safety boundaries

- Do not edit `start_nen.py`, installed site-packages or `_local_cache/` as part of other work.
- `constants.py`, logging and startup/config plumbing affect every subsystem: change them only with
  verification for each affected area.
- Keep secrets in `.env` or deployment config. Never log or commit keys, tokens, wallets or
  populated `.env` files, and never add credential defaults to code or docs.
- Promote code out of `xperimental/` deliberately, with tests, instead of importing it.

## Git

- Branch from an up-to-date `main`; PRs target `main`. Do not push to `main` directly.
- Stage exact paths; never commit runtime data, models, caches or unrelated changes.
- Do not force-push shared branches or rewrite pushed history without approval.
- PR bodies state behavior impact, new env keys, ports or migrations, rollout order and verification.

## Keeping this guidance useful

This file and the references are loaded into agents' context, so every line has a cost.

- Add a fact only when it is durable, not obvious from the code, and likely to prevent a mistake.
- Put it in the matching section of [runtime notes](docs/runtime-notes.md) or the subsystem doc,
  not here. Edit or delete the entry it replaces; do not append corrections or dated logs.
- Keep task progress, measurements and investigations in the PR, `_todo/` or the issue tracker.
- Update this file when commands, boundaries or the repository layout change.
