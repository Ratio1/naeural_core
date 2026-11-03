# Runtime notes

Non-obvious behavior and contracts that are easy to break, grouped by subsystem. Read the section
for the area you change. Keep each entry current: edit or delete it when the code changes, and put
long contracts in a dedicated doc linked from here.

Dedicated docs: [CStore](CSTORE.md), [Deeploy node lifecycle](DEEPLOY_NODE_LIFECYCLE.md),
[serving SHM lifecycle](20261014_serving_shm_lifecycle.md),
[SHM diagnostics](20261014_shm_diagnostics.md), [BusinessManager](business_manager.md).

## Main loop and offline behavior

- Data delivery from capture to plugins runs every main-loop pass regardless of comm health.
  Comm failure stops the orchestrator only when `WORK_OFFLINE` is false, after
  `_nr_conn_retry_iters` exceeds `CONN_MAX_RETRY_ITERS`; comm threads sleep
  `CONNECTION_FAIL_SLEEP_TIME` between retries, so the stop is delayed (`main/orchestrator.py`,
  `comm/base/base_comm_thread.py`).
- Business plugin instantiation runs inside the main loop (`BusinessManager._check_instances`):
  heavy imports, config validation and code safety checks show up as main-loop stalls.
- Node-oracle refresh reads the blockchain on a background thread and applies whitelist additions
  on the main loop. `get_oracles(wait_interval=0)` answers from NetMon/epoch history, so a brand-new
  node may need several passes.
- `entrypoint.py` installs additional packages into `_bin/<cache_tag>` per interpreter, with an
  `install_manifest.json`; the cache rebuilds when the runtime or the package list changes.

## Comms

- Send buffers hold `ct.COMM_SEND_BUFFER` messages. The command/control buffer rejects the newest
  message when full; the others are deques that drop the oldest. While disconnected, one popped
  message is held for retry. Delivery is best effort, not durable.
- Naming is misleading: `CommunicationManager.maybe_process_incoming()` drains the `HEARTBEATS`
  communicator, while the `COMMANDCONTROL` thread consumes its own receive path and also registers
  heartbeats.
- Legacy crossed receive layouts may keep DEFAULT's passive CONFIG copy. Resolve the command
  consumer before applying receive policy, drain only `HEARTBEATS`/`L_HEARTBEATS`, and do not rewrite
  configured paths or add subscriptions. MQTT installs the passive post-message hook only for
  DEFAULT, not `L_DEFAULT`.
- Existing policy booleans go through `Logger.str_to_bool` and warn once per key on unrecognized
  strings; QoS keeps int-plus-range coercion. New heartbeat worker/auth settings use strict guards.
  Run `test_comm_legacy_compatibility.py` with the comm policy/ingress tests when touching these.
- Command authorization uses only the envelope `EE_SENDER` whose key verified the signature. The
  encrypted body contributes only `ACTION` and `PAYLOAD`, and unsigned or invalid commands pass only
  through `whitelist_commands.json`. Nodes are always secured (`node_is_secured`); the `SECURED`
  startup value is honored only on VPN deployments (`EE_VPN_IMPL`). See
  `test_command_sender_authorization.py`.
- `IN_KB` is measured when `_recv_buff` is drained in `get_message()`, not on MQTT arrival, so broker
  ingress and app consumption pressure look the same.
- Broker failover: `CommunicationManager` copies dAuth's `EE_MQTT_HOSTS` into runtime `HOSTS` only
  when `HOST` equals `EE_MQTT_HOST`; an explicit `HOSTS` (including `[]`) wins. The SDK `MQTTWrapper`
  does the failover, so deploy SDK updates first. Verify with `test_comm_heartbeat_policy.py`.
- Addressed payloads fan out only when the payload channel has an addressed topic template
  (`TARGETED_TOPIC` or a templated `TOPIC`); otherwise core and SDK send one broadcast.
  `EE_DISABLE_ADDRESSED_PAYLOAD_SUBS` and `EE_DISABLE_ADDRESSED_PAYLOAD_SENDS` turn off addressed
  receive and send for rollout or rollback.
- `EE_HEARTBEAT_TARGETED_MIRROR_ENABLED=true` also publishes each signed heartbeat to the node's
  `CTRL_CHANNEL.TARGETED_TOPIC`; without a targeted topic it falls back to the global publish.
- Routing fields (`STREAM_NAME`, `SIGNATURE`, `INSTANCE_ID`, `SESSION_ID`, initiator/modifier
  addresses) must stay top-level in payloads: the comm layer reads them before packaging, so they
  cannot move into `ENCODED_DATA`.

## Heartbeats and ingress

- Heartbeats carry `ACTIVE_PLUGINS` and `CONFIG_STREAMS` unless `EE_HB_CONTAINS_ACTIVE_PLUGINS` /
  `EE_HB_CONTAINS_PIPELINES` are false; these usually travel inside compressed `ENCODED_DATA`.
  `HEARTBEAT_TIMERS=false` omits only timers and logs.
- Remote heartbeat ingress (`comm/heartbeat_ingress.py`) uses one bounded reject-newest queue,
  parallel raw-envelope signature checks, and one ordered owner for decode, identity, dedup,
  NetMon and epoch commits. `HEARTBEAT_AUTH_MODE` is `off`/`shadow`/`enforce`;
  `HEARTBEAT_INGRESS_WORKER_ENABLED=false` restores the legacy loop. The queue is in memory, not a WAL.
- Identity validation must inspect compressed data that formatter decoding newly exposes or
  replaces (including Aixp1 `DATA.ENCODED_DATA`) before NetMon/epoch commit.
- Signature canonicalization counters stay in memory and flush after the worker drains; shutdown
  joins with a timeout, so final counters are best effort.
- CommandControl snapshots commands at queue admission and again before preparation, so retries
  resend the exact source. Admission rejects the newest command when full; no retry, TTL or
  idempotency guarantee exists beyond transport handoff.
- `register_local_heartbeat` receives the node's own heartbeat before the comm layer adds
  `EE_TIMESTAMP`/`EE_TIMEZONE`; local epoch paths canonicalize `CURRENT_TIME` with the logger offset.

## NetMon and epochs

- NetMon keeps each node's newest heartbeat complete and reduces older ones to `HISTORY_HB_KEYS`
  in `main/net_mon.py`. A field missing from that tuple exists only in the newest heartbeat, so a
  new field read from history must be added there. Nodes silent for over 24 hours keep one
  heartbeat, trimmed on load and before each save.
- Stored heartbeats cannot be signature-verified: signatures cover the raw envelope with
  `ENCODED_DATA`, which NetMon decodes and discards. Signatures are checked on arrival only.
- `EpochsManager.get_node_epochs` fills missing epochs up to the current one, mutating cached maps;
  repeated calls cost O(nodes × epochs). Local per-epoch availability is stored in `LOCAL_EPOCHS`.
- `EpochsManager.maybe_update_cached_data` refreshes only dirty epoch history; use
  `get_full_node_state()` for full debug state so heartbeat registration does not wait on copies.
- `save_status` keeps detached per-epoch caches of `SYNC_SIGNATURES`, `SYNC_AGREEMENT_CID` and
  `SYNC_SIGNATURES_CID`, refreshing only dirty epochs before saving.
- NetMon's pipeline cache is refreshed by `NET_CONFIG_MONITOR` payloads, not only by heartbeats.

## Plugins and business manager

- Plugin lookup walks the filesystem and runs code safety checks on first use per signature
  (SDK `_PluginsManagerMixin._get_plugin_by_name`). `Manager.plugin_locations_cache` keeps results,
  including failures, for the process lifetime, so new or changed plugin files need a restart.
- `BM_CACHE_CONFIG_HANDLERS` and `BM_CACHE_VALIDATORS` live in the shared `_ConfigHandlerMixin` and
  affect business, comm, capture and serving plugins alike.
- When any business plugin queue overflows, CaptureManager stops acquisition for all DCTs; IoT
  listeners then stop draining and the network receive deque can drop messages.
- `BasePluginExecutor.add_inputs` records overflow with counters only, logged at most once a minute.
  Never run recursive memory scans on the delivery thread. Run `test_plugin_queue_overflow.py` and
  `test_plugin_memory_scan.py` for this path.
- Plugin memory diagnostics must exclude base-owned shared references
  (`_BasePluginExecutor__global_shmem`, the `plugins_shared_mem` facade). GiB-scale checks are
  opt-in; see `business/test_framework/plugin_memory_testbed/README.md`.
- `NETWORK_ROUTE_BY_HANDLER` filters inputs for every plugin instance; loopback payloads without
  `EE_PAYLOAD_PATH` are dropped when it is on.
- `PER_NODE_CONFIG` is materialized before config validation, so per-node `AI_ENGINE` and serving
  parameters apply before serving starts.
- Plugins get `BCWrapper`, not the raw SDK engine: forward every new blockchain method or
  verification option in `utils/plugins_base/bc_wrapper.py` and test it with
  `test_bc_wrapper_dauth`.
- `GeneralPayload` copies capture metadata into `_C_*` fields and adds `_P_*` fields to every
  payload, so capture metadata growth increases wire size.
- `ADMIN_PIPELINE_ASYNC_DISPATCH` is on by default: `admin_pipeline` capture and dispatch run on
  their own lanes, primed before serving warmup, controlled by `ADMIN_PIPELINE_DISPATCH_POLL_SECONDS`,
  `ADMIN_PIPELINE_QUEUE_MAXLEN` and `ADMIN_PIPELINE_STALL_WARNING_SECONDS`.
- Heavy ops keep `send_mail` and `send_sms` enabled even when `HEAVY_OPS_CONFIG.ACTIVE_COMM_ASYNC`
  is overridden; `DISABLE_DEFAULT_SEND_MAIL` / `DISABLE_DEFAULT_SEND_SMS` opt out.

## Pipeline config and secrets

- Pipelines in ConfigManager, `_current_dct_config_streams`, persistence and `get_pipelines_view`
  are canonical and redacted. Only BusinessManager receives `_runtime_dct_config_streams`, where
  `$EE_*` references and dAuth placeholders are resolved in memory. CaptureManager gets only
  runtime-eligible pipelines with redacted `PLUGINS`.
- Pipelines with unresolved dAuth secrets are withheld and retried by one tracked worker. The cache
  key is pipeline name, job ID, `DEEPLOY_SPECS.date_updated` and the canonical `PLUGINS` hash; a
  fetch result applies only if the canonical snapshot is unchanged.
- Persistence barriers restore canonical references on direct saves and command deltas (including
  dotted instance keys and batches) and reject unsafe structural changes to protected lists.
  Config-merge diagnostics log keys and change state, never values.
- `cmdapi_build_pipeline_config()` lets callers stage exact command metadata before dispatch.

## Serving and training

- Serving handles keep `MODEL_INSTANCE_ID`, so one serving class can run several model instances
  as separate servers on the same node.
- The `structured` training signature takes `INPUT_FIELDS`, `OUTPUT_FIELDS`, `TASK_MATRIX`,
  `EXPORT_FORMATS`, `ALLOW_NESTED_INPUTS` and `ALLOW_NESTED_OUTPUTS`. It derives vocab and schema from
  the train split, requires TorchScript export, attempts GGUF (which may end blocked) and emits
  `METADATA.SERVING_MODEL_CONFIG`.
- AI engine `th_structured` serves that TorchScript on the same node from `MODEL_PATH` plus inline
  fields or `MODEL_CONFIG_PATH`, without MinIO.
- SHM diagnostics (`EE_SHM_DIAGNOSTICS`) are opt-in, bounded and time-limited; see the SHM docs and
  run `utils/test_shm_diagnostic_evidence.py` and `utils/test_shm_diagnostic_transport.py`.
