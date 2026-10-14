# Node deployment lifecycle

The full cross-repository reference is
[the Edge deployment lifecycle specification](../../edge_node/docs/deeploy-deployment-lifecycle-spec.md).
This note describes the Core wire/runtime contract.

## Commands

Use authenticated `UPDATE_CONFIG` with stable pipeline `NAME` and top-level
`DEEPLOY_OPERATION`: `operation_id`, `attempt`, `revision`, `coordinator`,
`peers`, `action`, and `job_id`. The transport sender must equal `coordinator`.
A start carries the complete frozen canonical pipeline configuration. Attempts
are `deploy`, `retire`, or `restore`. Restore has its own attempt identity even
when its configuration revision is older.

`action: stop` persists an abort before removing only a matching operation and
attempt configuration. A delayed start of that attempt is rejected after restart.
It never removes another operation or restoration attempt by pipeline name alone.

Before an update, `action: retire`, `attempt: retire` binds the old placement to
`revision` and `expected_previous_operation_id` (null for an untracked revision
zero). Current canonical `DEEPLOY_SPECS.job_id`, `revision` (default zero), and
operation identity must match. Mismatch reports `CONFIGURATION_REJECTED` and
leaves the current pipeline untouched. A successful retirement records the prior
attempt's abort, adopts existing runtime objects for cleanup evidence, and
removes the canonical configuration. No new revision may start until the
controller observes retirement completion on every previous target. If the
canonical configuration is already absent, any active matching journal record
still supplies workload identities for cleanup; absence of a configuration alone
cannot acknowledge a journaled workload as stopped.

## Durable observations and cleanup

`DeploymentLifecycle` is owned by ConfigManager and publishes on an independent
node worker. It registers accepted instance identities before BusinessManager
loads code or constructs plugins. The journal is
`<data folder>/deeploy_node_operations.json`, atomically replaced with file and
directory fsync. Corrupt journals fail closed. No plaintext configuration or
secret bundle is stored there.

BusinessManager observes load/constructor failures. The plugin loop records
initialization/execution/cleanup failures without copying exception messages
into the public channel. `report_deployment_status(status, error_code=None)`
accepts the fixed vocabulary in `deployment_lifecycle.py`; readiness still uses
`is_plugin_ready()` and semaphore semantics are unchanged.

A dead plugin thread is insufficient for external workload cleanup. Runners
owning external workloads must declare `_deployment_external_workload = True`
and call `set_deployment_stopped()` only after verifying actual shutdown. A
runner can implement static `cleanup_deployment_instance(app_id, instance_id)`
to reap its deterministic scoped workload after restart; it must use a bounded
timeout, preserve persistent volumes, and return True only after confirming
absence. Core invokes it without constructing or starting the plugin, while
holding the local admission lock. Superseded attempts cannot reap a newer
workload with the same name. Without such evidence cleanup remains unresolved.

The signing node writes HSET `DEEPLOY_NODE_OPERATIONS`, fields:

- `<operation_id>|<attempt>|<node_addr>`: current snapshot.
- `<operation_id>|<attempt>|<node_addr>|<sequence modulo 8>`: bounded recent slots.

Snapshots contain operation/attempt/revision/coordinator, node/app identity,
status, per-instance signature/ID/status/safe error, monotonically increasing
sequence, and Unix `updated_at`. They are signed with
`sign(record, add_data=True, use_digest=True)`. Ready observations continue at
approximately ten-second intervals; stopped/failed unchanged observations stop
after acknowledged publication. Sequence is persisted before publication.
Consumers must verify signatures, expected node/operation identity, and freshness.

These are bounded recent snapshots, not a permanent event log. Tombstones and
operation journal records are retained; operator retention must not discard aborts
while delayed commands can still arrive.

## Revision secrets and transaction wrappers

`DEEPLOY_SPECS.secret_revision` participates in runtime secret cache identity and
is forwarded as `revision` to the SDK's signed dAuth request. An in-flight request
for another revision cannot satisfy a restored configuration. Definitive secret
validation/rejection errors publish `SECRET_RESOLUTION_FAILED`; transport,
registry, and replica synchronization failures remain retryable.

BCWrapper exposes `prepare_node_update`, `send_prepared_transaction`, and
`get_transaction_receipt`. The controller persists prepared signed bytes before
broadcast and reconciles the same transaction across retries; Core does not
allocate a second transaction as part of these wrappers.

## Limits

The receiving oracle owns coordination. There is no distributed election,
failover, or cross-oracle admission lock. Node tombstones and local active-attempt
tracking prevent scoped replay and cleanup of superseded attempts; they do not
provide global concurrent mutation serialization.

All plugin node-update writers share `bc.node_update_transaction_lock`, stored on
the shared blockchain engine. The coordinator must hold this lock across prepare,
durable CStore reservation, and broadcast. Legacy `submit_node_update` acquires
the lock and defers with a retryable error while the newest verified snapshot of
any locally coordinated operation retains `settlement.transaction`. Old ring
slots are ignored when a newer snapshot exists. This reservation survives process
restart through CStore and must remain present until a receipt proves mining,
even if membership already matches. This is local signer coordination only.

Tracked plugin construction is serialized with lifecycle admission and reporting;
the lock is acquired after prior-instance shutdown and is never held across that
thread join. Stop acknowledgment cannot race a constructor creating an external
workload. Runner `get_deployment_runtime_artifacts()` must return cached values
without I/O. The node journals and reports only `image_id` (full sha256 Docker
identity) and optional `git_commit` (40 hexadecimal characters), including after
retirement/restart. Missing bindings remain absent so restoration can fail closed.
