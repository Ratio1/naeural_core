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

### Same-host legacy worker inspection

An authenticated current protocol oracle can inspect a named untracked job with
one `WORKER_APP_RUNNER` instance. The transport addresses the worker by its
current identity. The versioned actions below require a coordinator-prefixed
operation ID, integer job ID, revision zero, exact `instance_id`, `node_addr`,
a 32-character lowercase hex `challenge` and an integer Unix `expires_at`.
The coordinator issues a 110-second challenge. A worker accepts it while the
deadline is in its future, up to 180 seconds from its local clock. The worker
throttles repeated running-worker inspections for the same job for five seconds.
The coordinator encrypts the full canonical configuration fingerprint
to this worker as `config_seal`. The worker decrypts it with the authenticated
sender identity and compares it locally on both commands. Only the ciphertext's
SHA-256 tag, `config_seal_hash`, appears in the journal and signed receipts.
The fingerprint preserves scalar values and all list order, excluding transport
and response metadata. Older command handlers reject these action names.

- `action=inspect_worker_v1`, `attempt=inspect` uses the existing parent
  operation ID. The worker compares its local stream and live runner and reads
  Git HEAD from the live container. It returns a node-signed observation at
  `<parent operation ID>|inspect|<node>` in `DEEPLOY_NODE_OPERATIONS` with
  `status=observed`, the challenge, deadline, job, app, revision, exact worker
  identity, Git commit and `order_digest`. The digest is SHA-256 of compact
  UTF-8 JSON for `[current_target_nodes, BUILD_AND_RUN_COMMANDS, CHAINSTORE_PEERS]`,
  preserving order and emitting Unicode literally. Only the coordinator receives
  the observation. An absent worker or expired challenge yields no new report.
  Reading the commit establishes what is checked out locally. The coordinator
  separately checks restoration source availability. The worker runs the Docker
  probe outside its shared lifecycle lock, then confirms that the runtime,
  config and recorded challenge still match under the lock.
  For a previously stopped worker the same request can use
  `confirm_prior_stop=true`. It verifies the matching local journal STOP
  and Git pin, absence of the stream and live instance, and no pending start.
  The signed receipt carries that flag and the pin without a config seal or
  order digest. The worker persists the legacy replay fence with this receipt
  before responding. An older direct parent STOP may lack a job ID. That
  compatibility path still binds the exact parent, coordinator, pipeline and
  instance. New conditional STOP records must include the job ID.
- `action=retire_worker_v1`, `attempt=retire` uses a new child operation ID and
  supplies `parent_operation_id`, the same challenge and deadline, accepted
  digest, ciphertext and `git_commit`. The worker rechecks its observation and live checkout
  before entering the existing durable retire path. The signed stop carries
  the parent ID, challenge, order digest and per-instance runtime binding.
  The inspected live Git commit remains authoritative when the runner
  attaches or reports cached artifact identifiers.
  A durable conditional abort also prevents the old untracked job from starting
  if its stream file survives a crash between journal and configuration writes.
  The reporter sees retirement only after the journal file and its directory
  entry are synced. A restart syncs the directory before using the recovered
  journal.
  Its tombstone survives admission of a later tracked revision. A tracked start
  encountering that obsolete file builds its replacement from the supplied
  pipeline so removed settings and credential references cannot carry forward.
- `action=start_repaired_worker_v1` starts the tracked `deploy` or `restore`
  attempt on either target. It requires this worker's matching local stop
  record, the parent operation, job ID and the child's immutable repair secret
  revision. The node persists that the legacy revision was retired, including on
  the target that had already stopped. Subsequent untracked `UPDATE_CONFIG`
  messages for the repaired job, including partial and dotted updates, are
  discarded before config merge. Single and batched instance updates address
  the current local instance. They can target the active tracked deployment
  or restoration and are rejected for a retired local configuration. The
  instance command format carries no deployment revision. The diagnostic
  names the pipeline and omits the payload.
  A restore that finds the failed deployment's config file after a restart
  replaces it with the complete restore config after verifying the stopped
  attempt in the journal.
  Retries against an absent pipeline leave the existing retirement receipt intact.
  A changed job, instance, command order, Git commit or expired authorization
  leaves the current pipeline running.

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
owning external workloads must declare `_deployment_external_workload = True` on the class
and call `set_deployment_stopped()` only after verifying actual shutdown. A
runner can implement static `cleanup_deployment_instance(app_id, instance_id)`
to reap its deterministic scoped workload after restart; it must use a bounded
timeout, preserve persistent volumes, and return True only after confirming
absence. Core invokes it without constructing or starting the plugin, while
holding the local admission lock. Superseded attempts cannot reap a newer
workload with the same name. Without such evidence cleanup remains unresolved.

Before construction, the journal records the resolved class's native/external
classification and the owning interpreter's process identity. A native instance
with no runtime object can be confirmed stopped after that process exits. A new
journal reader in the same process is insufficient. Unknown classifications,
same-process constructor/cleanup failures, and external workloads still require
cleanup evidence. Retirement preserves this classification and process identity;
both fields remain private to the node journal. Custom code that owns external
workloads must honor the runner declaration and cleanup contract above.

The signing node writes HSET `DEEPLOY_NODE_OPERATIONS`, fields:

- `<operation_id>|<attempt>|<node_addr>`: current snapshot.
- `<operation_id>|<attempt>|<node_addr>|<sequence modulo 8>`: bounded recent slots.

Snapshots contain operation/attempt/revision/coordinator, node/app identity,
status, per-instance signature/ID/status/safe error, monotonically increasing
sequence, and Unix `updated_at`. They are signed with
`sign(record, add_data=True, use_digest=True)`. A snapshot is published only
when its observable state changes, with a `PUBLISH_TIMEOUT` (15 s) confirmation
wait, and is retried on the next pass until a write is confirmed. Stop and failure
snapshots are written first in each pass, and a snapshot's second field is
skipped once its first write is unconfirmed. Each updated Deeploy app runner reports its
`source_mode` (`following`, `pinned` or `service`) from admission. Saved
Deeploy pipelines use the same source policy after node restart. Direct SDK
pipelines keep the runner's regular source and pull settings. A
following runner continues to report changes to its status and actual commit or
image after readiness. A branch lookup failure can coexist with readiness and
appears as `source_error: SOURCE_UNREACHABLE`. A new start can clear a workload
start, health, image pull or source access failure. Infrastructure failures
remain terminal for the affected runner. A new runner on node restart observes
its own health after a previously confirmed deployment. Bound instances retain
their confirmed ready snapshot until retirement or stop.

The node replays the latest signed current field to an intended oracle that
missed publication, came back online, or whose reported process uptime fell.
The replay preserves the original sequence, timestamp and history slots.
It waits for an acknowledgement from that specific oracle and makes up to
three attempts per snapshot and oracle session. A new observation or a
returning oracle starts a fresh retry budget.
The node accepts process uptime from direct heartbeats and fresh authorized
network summaries. If an older summary omits uptime and the oracle restarts
after acknowledging but before its disk flush, a restart that never appears
offline can leave its replica behind until the next observation. Consumers must verify signatures and
expected node/operation identity and use node liveness to detect stale reports.

These are bounded recent snapshots, not a permanent event log. Tombstones and
operation journal records are retained; operator retention must not discard aborts
while delayed commands can still arrive.

## Revision secrets

`DEEPLOY_SPECS.secret_revision` participates in runtime secret cache identity and
is forwarded as `revision` to the SDK's signed dAuth request. An in-flight request
for another revision cannot satisfy a restored configuration. Definitive secret
validation/rejection errors publish `SECRET_RESOLUTION_FAILED`; transport,
registry, and replica synchronization failures remain retryable.

Existing persisted pipelines without lifecycle metadata may omit
`secret_revision`, including after restart with an empty runtime cache. The SDK
sends an unversioned request; dAuth authorizes it only against the existing raw
CID, current placement, and matching current secret bundle. A tracked pipeline,
nonzero revision, or explicit null secret revision cannot use this path. New
deployments and both target/restoration plans of the first update always carry
immutable secret revisions; they never fall back to unversioned secrets.

## Limits

The receiving oracle owns coordination. There is no distributed election,
failover, or cross-oracle admission lock. Node tombstones and local active-attempt
tracking prevent scoped replay and cleanup of superseded attempts; they do not
provide global concurrent mutation serialization.

Tracked plugin construction is serialized with lifecycle admission and reporting;
the lock is acquired after prior-instance shutdown and is never held across that
thread join. Stop acknowledgment cannot race a constructor creating an external
workload. Runner `get_deployment_runtime_artifacts()` must return cached values
without I/O. The node journals the actual `image_id` (local Docker identity),
the immutable registry `image_ref` when available, and the worker's checked-out
`git_commit`. Retirement keeps this evidence. Restoring a bound instance
requires its recorded artifact. A following instance resolves its source again.

## Stateless replacement

A validated command from an enrolled oracle can use
`start_stateless_replacement_v1` to send a complete configuration without a
prior retirement receipt. The job, owner and runtime component identities must
match any local occupant. Native and runner plugins go through normal plugin
validation and the replacement configuration checks. The new
attempt is journaled before the config changes. The business manager uses the
ordinary tracked restart path, including local shutdown before reconstructing a
component with the same identity. The journal fences older starts and
unversioned pipeline writes for the job across a worker restart. The journal
orders new starts against same-job retirements even after the config file is
removed. Historical per-node overlays are checked for local mounts. Instance
commands still address the active tracked attempt.
