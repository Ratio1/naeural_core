# Plugin Caching Mechanics (End-to-End)

## 1) Scope
This document describes how plugin caching works across the runtime, end-to-end:
- Plugin discovery and class resolution caching.
- Runtime plugin instance reuse.
- Config-handler/validator reflection caching.
- Plugin-local persistence cache (`cacheapi_*`).
- Cache lifetimes, invalidation behavior, and practical implications.

All findings are based on the current code in this repository and the installed `ratio1` package used by it.


## 2) TL;DR
- The primary plugin lookup cache is `Manager.plugin_locations_cache`, keyed by `name.lower()`, stored per manager instance for the manager/process lifetime (`naeural_core/manager.py`).
- On cache hit, discovery/import/safety-check path in `ratio1._PluginsManagerMixin` is bypassed.
- On cache miss, `ratio1` performs filesystem/package discovery, import, optional safety checks, class matching, `_CONFIG` deepcopy, then the result is cached.
- Runtime plugin objects are also cached/reused in manager dictionaries (`_dct_current_instances`, `_servers`, `_dct_captures`, `_dct_comm_plugins`, etc.).
- `BM_CACHE_CONFIG_HANDLERS` and `BM_CACHE_VALIDATORS` are implemented in shared `_ConfigHandlerMixin`, so they affect all mixin consumers, not only business plugins.
- There is no runtime invalidation of `plugin_locations_cache`; restart is required to guarantee new plugin file pickup for already-cached signatures.


## 3) Cache Layers

## 3.1 Layer A: Manager Plugin Lookup Cache
### What it is
- In-memory cache owned by each `Manager` instance:
  - `self.plugin_locations_cache = {}` (`naeural_core/manager.py:12`).
- Used by `Manager._get_module_name_and_class(...)` (`naeural_core/manager.py:76`).

### Key and value
- Key: `cache_key = name.lower()` (`naeural_core/manager.py:80`).
- Value tuple: `(_module_name, _class_name, _cls_def, _config_dict)` (`naeural_core/manager.py:89`, `naeural_core/manager.py:106`).

### Hit path
- If key exists:
  - Returns cached tuple directly.
  - Logs "Attempting to load plugin ... from cache" (`naeural_core/manager.py:88` to `naeural_core/manager.py:90`).
- This bypasses discovery/import/safety checks from `ratio1`.

### Miss path
- Delegates to parent mixin (`ratio1._PluginsManagerMixin._get_module_name_and_class`) (`naeural_core/manager.py:94`).
- Stores returned tuple unconditionally in cache (`naeural_core/manager.py:106`).

### Important behavior
- Cached misses/failures are also stored because cache assignment is unconditional after delegate return.
- Cache key is only signature lowercase (not location/suffix/safety flags).


## 3.2 Layer B: `ratio1` Discovery/Import/Safety Path (used on cache miss)
Source: `/usr/local/lib/python3.10/dist-packages/ratio1/plugins_manager_mixin.py`.

### Discovery
- `_get_plugin_by_name(...)`:
  - Normalizes plugin name to snake case (`.../plugins_manager_mixin.py:71`).
  - Expands sub-locations from packages and local folders (`.../plugins_manager_mixin.py:79` to `.../plugins_manager_mixin.py:105`).
  - Tries candidates via `importlib.util.find_spec` (`.../plugins_manager_mixin.py:113` to `.../plugins_manager_mixin.py:123`).

### Safe vs user plugin search
- Safe locations are searched first if provided (`.../plugins_manager_mixin.py:195` to `.../plugins_manager_mixin.py:200`).
- If safe plugin found, module/class safety checks are disabled for that load (`.../plugins_manager_mixin.py:224` to `.../plugins_manager_mixin.py:225`).

### Import and class extraction
- If module exists in `sys.modules`, it is deleted before import (`.../plugins_manager_mixin.py:229` to `.../plugins_manager_mixin.py:231`).
- Imports module with `importlib.import_module` (`.../plugins_manager_mixin.py:231`).
- Optional module safety check via code scan and `inspect.getsource` (`.../plugins_manager_mixin.py:128` to `.../plugins_manager_mixin.py:158`, `.../plugins_manager_mixin.py:232` to `.../plugins_manager_mixin.py:236`).
- Finds class by name pattern: `simple_name + suffix` (`.../plugins_manager_mixin.py:238` to `.../plugins_manager_mixin.py:240`).

### Config extraction
- Reads module `_CONFIG` and deep-copies it (`.../plugins_manager_mixin.py:246`, `.../plugins_manager_mixin.py:253` to `.../plugins_manager_mixin.py:256`).
- Adds `MODULE_VERSION` (`.../plugins_manager_mixin.py:258`).


## 3.3 Layer C: Runtime Plugin Object Caches (Instance Reuse)
These are distinct from class lookup cache.

### Business plugins
- Stored in `_dct_current_instances` (`naeural_core/business/business_manager.py:44`).
- New instance only when hash absent (`naeural_core/business/business_manager.py:274`).
- Existing instances reused and reconfigured via `maybe_update_instance_config(...)` (`naeural_core/business/business_manager.py:410` to `naeural_core/business/business_manager.py:424`).

### Serving processes
- Stored in `_servers` (`naeural_core/serving/serving_manager.py:180`).
- `maybe_start_server` only creates when missing (`naeural_core/serving/serving_manager.py:887` to `naeural_core/serving/serving_manager.py:896`).

### Capture plugins
- Stored in `_dct_captures` (`naeural_core/data/capture_manager.py:27`, `naeural_core/data/capture_manager.py:44`).
- Existing captures are updated instead of recreated (`naeural_core/data/capture_manager.py:48` to `naeural_core/data/capture_manager.py:55`).

### Communication plugins
- Stored in `_dct_comm_plugins = _dct_subalterns` (`naeural_core/comm/communication_manager.py:34`, `naeural_core/comm/communication_manager.py:52`).

### Heavy ops plugins
- Stored in `_dct_ops = _dct_subalterns` (`naeural_core/heavy_ops/heavy_ops_manager.py:35`, `naeural_core/heavy_ops/heavy_ops_manager.py:42`).

### File system plugin
- Stored in `_dct_file_systems` and `_file_system` (`naeural_core/remote_file_system/file_system_manager.py:10` to `naeural_core/remote_file_system/file_system_manager.py:12`, `naeural_core/remote_file_system/file_system_manager.py:64` to `naeural_core/remote_file_system/file_system_manager.py:65`).

### Config retrievers
- Stored in `_dct_retrievers` (`naeural_core/config/config_manager.py:44`, `naeural_core/config/config_manager.py:58`, `naeural_core/config/config_manager.py:379`).

### Testing framework plugins
- Testers cached in `_dct_testers` (`naeural_core/business/test_framework/testing_manager.py:7`, `naeural_core/business/test_framework/testing_manager.py:35`).
- Scorers cached in `_dct_scorers` keyed by scorer+source hash (`naeural_core/business/test_framework/scoring_manager.py:8`, `naeural_core/business/test_framework/scoring_manager.py:31` to `naeural_core/business/test_framework/scoring_manager.py:44`).


## 3.4 Layer D: Config Handler and Validator Reflection Caches
Implemented in shared `_ConfigHandlerMixin` (`naeural_core/local_libraries/config_handler_mixin.py`).

### `BM_CACHE_CONFIG_HANDLERS`
- Flag read from `self.log.config_data` (`naeural_core/local_libraries/config_handler_mixin.py:216`).
- Caches generated `cfg_*` properties per class using `_cfg_keys_created` (`naeural_core/local_libraries/config_handler_mixin.py:243` to `naeural_core/local_libraries/config_handler_mixin.py:261`).
- Also pre-warms base `BasePluginExecutor` keys once (`naeural_core/local_libraries/config_handler_mixin.py:223` to `naeural_core/local_libraries/config_handler_mixin.py:240`).

### `BM_CACHE_VALIDATORS`
- Flag read from `self.log.config_data` (`naeural_core/local_libraries/config_handler_mixin.py:535`).
- Caches `validate_*` method list in class attribute `_cfg_validators` (`naeural_core/local_libraries/config_handler_mixin.py:539` to `naeural_core/local_libraries/config_handler_mixin.py:545`).

### Where this matters
- `setup_config_and_validate(...)` is called widely (`naeural_core/local_libraries/config_handler_mixin.py:583`).
- Consumers include business, capture, comm, serving paths:
  - Business: `naeural_core/business/base/base_plugin_biz.py:1989`.
  - Capture: `naeural_core/data/base/base_data.py:415`.
  - Comm: `naeural_core/comm/base/base_comm_thread.py:158`.
  - Serving: `naeural_core/serving/base/base_serving_process.py:827`.


## 3.5 Layer E: Plugin Local Persistence Cache (`cacheapi_*`)
Implemented by `_PersistenceSerializationMixin` (`naeural_core/utils/plugins_base/persistence_serialization_mixin.py`), inherited via `_UtilsBaseMixin` (`naeural_core/utils/plugins_base/plugin_base_utils.py:379` to `naeural_core/utils/plugins_base/plugin_base_utils.py:381`).

### Storage path
- Base folder is built once per instance:
  - `self.__persistence_fld = '{}/{}'.format(self._cache_folder, self.plugin_id.lower())`
  - (`naeural_core/utils/plugins_base/persistence_serialization_mixin.py:50`).

### Category folders
- Business plugins: `cache_plugins` (`naeural_core/business/base/base_plugin_biz.py:899` to `naeural_core/business/base/base_plugin_biz.py:900`).
- Capture plugins: `cache_data_capture` (`naeural_core/data/base/base_plugin_dct.py:113` to `naeural_core/data/base/base_plugin_dct.py:114`).
- Serving plugins: `cache_serving` (`naeural_core/serving/base/base_serving_process.py:144` to `naeural_core/serving/base/base_serving_process.py:145`).
- Constants defined in `naeural_core/constants.py:649` to `naeural_core/constants.py:651`.

### IDs used in path
- Business: `stream__signature__instance_id` (`naeural_core/business/base/base_plugin_biz.py:917` to `naeural_core/business/base/base_plugin_biz.py:921`).
- Capture: `type__name` (`naeural_core/data/base/base_plugin_dct.py:117` to `naeural_core/data/base/base_plugin_dct.py:121`).
- Serving: `server_name` (`naeural_core/serving/base/base_serving_process.py:148` to `naeural_core/serving/base/base_serving_process.py:149`).

### Signature guard
- `metadata.json` stores `SERIALIZATION_SIGNATURE`; load rejects mismatched signature (`naeural_core/utils/plugins_base/persistence_serialization_mixin.py:68` to `naeural_core/utils/plugins_base/persistence_serialization_mixin.py:92`).

### Usage note
- `LOAD_PREVIOUS_SERIALIZATION` and `SERIALIZATION_SIGNATURE` are present in default configs (`naeural_core/business/base/base_plugin_biz.py:222` to `naeural_core/business/base/base_plugin_biz.py:223`, `naeural_core/data/base/base_plugin_dct.py:45` to `naeural_core/data/base/base_plugin_dct.py:46`, `naeural_core/serving/base/base_serving_process.py:46` to `naeural_core/serving/base/base_serving_process.py:47`).
- Their effect depends on plugin code explicitly calling `persistence_serialization_*` or `cacheapi_*`.


## 4) End-to-End Runtime Flow

## 4.1 Startup lifetime context
- Managers are initialized once during orchestrator startup (`naeural_core/main/orchestrator.py:366`, `naeural_core/main/orchestrator.py:389` to `naeural_core/main/orchestrator.py:402`).
- Therefore, manager-owned lookup caches are long-lived for node/process lifetime.

## 4.2 Business plugin creation and warm path
### Cold path for a new instance hash
1. `BusinessManager._check_instances()` computes instance hash and sees it is missing (`naeural_core/business/business_manager.py:270` to `naeural_core/business/business_manager.py:275`).
2. Calls `self._get_module_name_and_class(...)` (`naeural_core/business/business_manager.py:295`).
3. Manager lookup cache hit/miss decides whether `ratio1` discovery/import executes (`naeural_core/manager.py:81`, `naeural_core/manager.py:94`).
4. Instantiates plugin with default config from resolver (`naeural_core/business/business_manager.py:338` to `naeural_core/business/business_manager.py:360`).
5. Plugin startup path applies config merge + handlers + validation (`naeural_core/business/base/base_plugin_biz.py:1935` onward, `naeural_core/business/base/base_plugin_biz.py:1989`).
6. Plugin object stored in `_dct_current_instances` (`naeural_core/business/business_manager.py:394`).

### Warm path (same instance hash exists)
1. Reuses object from `_dct_current_instances` (`naeural_core/business/business_manager.py:410`).
2. Runs `maybe_update_instance_config(...)` only (`naeural_core/business/business_manager.py:416`).
3. No class resolution call is made on this path.

## 4.3 Serving path
- `maybe_start_server(...)` checks `_servers` first (`naeural_core/serving/serving_manager.py:879`, `naeural_core/serving/serving_manager.py:887`).
- If missing, `_create_server(...)` resolves plugin class via manager method (`naeural_core/serving/serving_manager.py:392` to `naeural_core/serving/serving_manager.py:410`).
- On success, process object cached in `_servers` (`naeural_core/serving/serving_manager.py:525`, `naeural_core/serving/serving_manager.py:573`).

## 4.4 Capture path
- `CaptureManager._get_plugin_class(...)` resolves via manager cache layer (`naeural_core/data/capture_manager.py:270` to `naeural_core/data/capture_manager.py:279`).
- Instance stored in `_dct_captures` (`naeural_core/data/capture_manager.py:266`).
- Stream updates call `maybe_update_config(...)` on existing capture object (`naeural_core/data/capture_manager.py:48` to `naeural_core/data/capture_manager.py:55`).

## 4.5 Comm path
- Comm class resolved once per communication type via manager cache path (`naeural_core/comm/communication_manager.py:171` to `naeural_core/comm/communication_manager.py:178`).
- Instances stored in `_dct_comm_plugins` (`naeural_core/comm/communication_manager.py:52`).


## 5) Direct `_PluginsManagerMixin` Users That Bypass `Manager.plugin_locations_cache`
These classes call mixin resolver directly and do not use `Manager.plugin_locations_cache`:
- `THTraining` (`naeural_core/serving/training/th_training.py:37`, `naeural_core/serving/training/th_training.py:89`).
- `VideoStreamDataCapture` (`naeural_core/data/default/video/video_stream.py:35`, `naeural_core/data/default/video/video_stream.py:53`).
- `FlaskModelServer` (`naeural_core/local_libraries/model_server_v2/server.py:19`, `naeural_core/local_libraries/model_server_v2/server.py:171`).
- `BaseTrainingPipeline` (`naeural_core/local_libraries/nn/th/training/pipelines/base.py:22`, `naeural_core/local_libraries/nn/th/training/pipelines/base.py:116`).


## 6) Invalidation and Reload Behavior
- No explicit invalidation/clear method for `plugin_locations_cache` exists in repo code.
- Cache is initialized in manager constructor and then reused (`naeural_core/manager.py:12`).
- With long-lived managers, already-cached signatures keep returning cached tuple until restart.
- `ratio1` does `del sys.modules[...]` only in miss path; hit path does not touch import state (`.../plugins_manager_mixin.py:229` to `.../plugins_manager_mixin.py:231`).


## 7) Practical Performance Findings
- Cold loads are dominated by discovery + import + safety-check path in `ratio1`.
- Warm loads for same signature in same manager are cheap due to `plugin_locations_cache` hit.
- Business manager can still spend time in per-instance init/config validation even if class resolution is cached (`naeural_core/business/base/base_plugin_biz.py:1935` onward).
- Existing doc (`docs/business_manager.md`) matches this behavior and lists `BM_CACHE_CONFIG_HANDLERS` / `BM_CACHE_VALIDATORS` as implemented accelerations (`docs/business_manager.md:30` to `docs/business_manager.md:41`).


## 8) Risks and Gotchas
- Sticky miss behavior:
  - If a plugin signature lookup fails once and is cached as failed tuple, newly added module under same signature will not be picked up without restart.
- Cache key granularity:
  - Key is only `name.lower()` in manager cache.  
  - [Inference] If the same manager resolves the same signature name under different locations/suffix expectations, key collision can return a previously cached tuple.
- Global flag scope:
  - `BM_CACHE_CONFIG_HANDLERS` and `BM_CACHE_VALIDATORS` names suggest business-only scope, but implementation is in shared mixin used by multiple subsystems.


## 9) Quick Source Index
- Manager cache wrapper: `naeural_core/manager.py`.
- Upstream resolver implementation: `/usr/local/lib/python3.10/dist-packages/ratio1/plugins_manager_mixin.py`.
- Manager initialization lifetime: `naeural_core/main/orchestrator.py`, `naeural_core/main/orchestrator_mixins/managers_init.py`.
- Business runtime reuse: `naeural_core/business/business_manager.py`.
- Config reflection caches: `naeural_core/local_libraries/config_handler_mixin.py`.
- Persistence cache API: `naeural_core/utils/plugins_base/persistence_serialization_mixin.py`.
- Cache constants: `naeural_core/constants.py`.
