# Business Manager Findings

## Scope
This note focuses on `BusinessManager._check_instances()` and the critical path for plugin discovery, loading, and configuration. It summarizes current bottlenecks observed during stress testing and the improvement ideas discussed so far. It also records recently added optional optimizations (flag‑gated) for config handler creation and validation.

## Critical Path (New Instance)
`BusinessManager._check_instances()` → `Manager._get_module_name_and_class()` → `ratio1._PluginsManagerMixin._get_module_name_and_class()` → plugin class `__init__` → `BasePluginExecutor.startup()` → `_instance_init()` → `_update_instance_config()` → `setup_config_and_validate()` → `create_config_handlers()` + `validate()` → `_create_alert_state_machine()` + `timebins_create_bin()` + `init_plugins_shared_memory()`.

## Observed Timing Behavior
- First instance: `get_class` dominates (~1–2s) for cold discovery.
- Later instances (e.g., 71st/79): `get_class` and `init` can grow significantly (8–9s + 15–17s), with total cumulative time exceeding 10 minutes.
- The `(ALL=...)` in logs is cumulative time since start of `_check_instances()`.

## Primary Bottlenecks
1. **Plugin discovery and import**
   - `_get_plugin_by_name()` walks filesystem and uses `find_spec` for each candidate.
   - Safety check (`inspect.getsource` + code scan) is executed per unique signature even on unsecured nodes.
   - `sys.modules` is forcibly cleared on load in ratio1, preventing reuse of import cache.

2. **Plugin initialization**
   - `BasePluginExecutor.startup()` invokes `_instance_init()` and `_update_instance_config()` in the main loop.
   - Config handling involves deep merge + dynamic property creation + reflection for validation.

3. **Shared memory initialization**
   - `SharedMemoryManager.reset()` and link checks can add per‑instance overhead.

4. **Logging overhead**
   - Per‑instance logs add measurable time when many instances are created.

## Implemented Improvements (Flag‑Gated)
### 1) Cache config handlers
File: `naeural_core/local_libraries/config_handler_mixin.py`
- Flag: `BM_CACHE_CONFIG_HANDLERS` (global config)
- Behavior: caches already created config keys per class and pre‑creates BasePluginExecutor base keys once using MRO lookup.
- Default: off (rollback by setting flag false).

### 2) Cache validation method list
File: `naeural_core/local_libraries/config_handler_mixin.py`
- Flag: `BM_CACHE_VALIDATORS` (global config)
- Behavior: caches `validate_*` methods per class; avoids repeated reflection.
- Default: off (rollback by setting flag false).

## Proposed Improvements (Drafts)
### A) Pre‑index plugin discovery
- Flag: `BM_PREINDEX_PLUGINS` (global config)
- Idea: one `os.walk()` per plugin location to build `{signature_snake: module_path}` map. Fall back to old search on miss.
- Benefit: remove repeated filesystem traversal per signature.

### B) Cache safety checks
- Flag: `BM_CACHE_SAFETY_CHECK`
- Idea: cache `(is_good, msg)` per module name to skip repeated `inspect.getsource` and code scan.

### C) Skip forced module reload
- Flag: `BM_SKIP_MODULE_RELOAD`
- Idea: avoid `del sys.modules[_module_name]` for repeat loads.

### D) Limit new instance creation per loop
- Flag: `BM_MAX_NEW_INSTANCES_PER_TICK` (0 = unlimited)
- Idea: reduce long stalls by processing new instances in batches.

### E) Reduce verbosity
- Flag: `BM_VERBOSE_START_LOGS` (default true)
- Idea: summary logging rather than per‑instance logs.

## Notes
- Safety checks run even on unsecured nodes; they simply do not block loading.
- Circular import issues in test scripts can be avoided by pre‑importing `naeural_core.serving` and `naeural_core.serving.ai_engines` before loading `BasePluginExecutor` subclasses.

## Configuration
All new optimizations are intended to be controlled from global config (`self.log.config_data`) for easy rollback.
