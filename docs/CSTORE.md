# CStore Quick State

## Purpose

Use this document as the fast contract review for ChainStore/CStore work.
It is intentionally narrower than `AGENTS.md`: it explains how CStore works
end to end, what `hsync` does, what it does not do, and which follow-up issues
are still open.

## End-To-End Topology

The CStore stack spans three repositories:

- `core/naeural_core`
  Owns the runtime storage replica, peer broadcasts, confirmations, and the
  `chainstore_*` / `chainstore_h*` plugin helpers.
- `core/edge_node`
  Owns the thin FastAPI wrapper that exposes CStore operations over HTTP.
- `sdks/edge-sdk-ts`
  Owns the JavaScript app-facing client and service methods used by container
  apps and other edge consumers.

For the `cstore-hsync` workstream, the intended public surface is:

- runtime helper: `chainstore_hsync(hkey, ...)`
- edge endpoint: `POST /hsync`
- SDK methods: `cstore.hsync(...)` and `cstore.hsyncFull(...)`

## Storage Model

ChainStore is a replicated key-value map held in local runtime memory and
persisted through the runtime cache helpers.

- `set` writes one plain key
- `get` reads one plain key locally
- `hset` writes one field under a logical hash namespace
- `hget` reads one field locally
- `hgetall` reconstructs one hash namespace from local composed keys only
- `hsync` explicitly asks peers for one full hash namespace snapshot and merges
  that snapshot into the local replica

Hash namespaces are stored as composed keys. The namespace prefix is derived
from `hkey`, and each field name is base64-encoded into the final storage key.
That means `hgetall` is a local scan over the local replica, not a distributed
query.

## Write Propagation

Current write propagation is push-based:

1. a local caller uses `chainstore_set(...)` or `chainstore_hset(...)`
2. the runtime stores the value locally
3. the runtime broadcasts the write to target peers
4. peers apply the write locally and send confirmations back
5. the origin waits for the configured minimum confirmations or times out

This solves live replication for writes seen by currently running peers.
It does not, by itself, replay older writes to a late joiner or a restarted
peer that was offline while other peers kept accepting writes.

## Read Behavior

`get`, `hget`, and `hgetall` are local reads.

That boundary matters:

- `hgetall` does not fetch missing fields from peers
- `hgetall` does not repair stale local state
- local correctness after peer downtime depends on an explicit repair step

`hsync` is that explicit repair step for one hash namespace.

## `hsync` Contract

`hsync` is a merge-refresh for one logical HSET namespace.

Algorithm:

1. resolve the target peer set
2. send one `SHSYNC_REQ` request containing `request_id` and `hkey`
3. accept the first valid `SHSYNC_RESP` from an allowed peer
4. export or consume only the requested namespace snapshot
5. merge the snapshot into local storage without rebroadcasting it

Merge rules:

- remote fields missing locally are inserted
- remote fields that overlap local state overwrite the local field
- local fields absent from the remote snapshot are preserved
- delete pruning is out of scope

Result rules:

- success returns `{ hkey, source_peer, merged_fields }`
- an empty snapshot from a valid peer is still success
- timeout means no valid peer response was accepted

This is deliberately not an exact mirror operation. It repairs missing or stale
data for one namespace without trying to make the local replica identical to a
single peer.

## Boot-Time App Pattern

The platform does not own an automatic startup hook for `hsync`.
Boot-time refresh is an app-layer decision.

Recommended pattern:

1. the app reads one env variable listing the HSET namespaces it wants to
   refresh before serving traffic
2. the app calls SDK `hsync(...)` for each namespace during startup
3. the app chooses whether timeout is fatal, retryable, or best-effort for its
   own readiness policy

One possible app-owned env contract is:

```text
APP_CSTORE_HSYNC_HKEYS=players,characters,inventory
```

Example startup logic once the matching SDK branch is present:

```ts
const hkeys = (process.env.APP_CSTORE_HSYNC_HKEYS ?? '')
  .split(',')
  .map((value) => value.trim())
  .filter(Boolean)

for (const hkey of hkeys) {
  await ratio1.cstore.hsync({ hkey })
}
```

Interpretation:

- peer responds with data: app has live peer state for that namespace
- peer responds with `{ merged_fields: 0 }`: valid cold state, not an error
- no valid peer responds before timeout: app never confirmed peer state

## New Node vs Restarted Peer

`hsync` exists for both of these cases:

- a brand-new shard/world node starts with no local character data
- an existing peer restarts with a stale local replica after being offline

The repair need is the same in both cases: local `hgetall` must be refreshed
from live peer state before the app trusts its local namespace contents.

## Current Issue Triage

Fixed now:

- snapshot export freezes `self.__chain_storage.items()` into a list before
  filtering, so the export path no longer iterates a live mutable dict view

Open and documented:

- first valid peer response wins; there is no freshness arbitration across
  multiple peers yet
- `hsync` is namespace-scoped and merge-only; it is not a whole-cluster
  consistency pass
- local-only fields are intentionally preserved, so exact delete reconciliation
  remains out of scope
- startup orchestration remains app-owned; the platform still does not auto-run
  `hsync` during container boot

Improved in this workstream:

- runtime `chainstore_hsync(...)` accepts a caller-provided `timeout`, so the
  wait budget is no longer hardcoded inside the runtime path

## Next Steps

Small follow-ups:

- expose the runtime `timeout` control consistently through the edge API and
  SDK if apps need that knob
- add a small freshness token or version hint so `hsync` can reject obviously
  stale peer responses
- add thin edge and SDK tests that lock the `/hsync` request and response shape

Larger follow-ups:

- cluster-level reconciliation for multiple namespaces
- delete/tombstone reconciliation when exact mirror behavior is required
- automatic app bootstrap wrappers if the platform later decides to own startup
  hydration instead of leaving it to apps

## Deployment lifecycle persistence and protected namespaces

Each accepted write still saves the full store before returning (and, for remote
writes, before the acknowledgment is sent): records are copied shallowly under the
persistence lock, then the copy is pickled, fsynced and atomically replaced
outside it, so disk I/O no longer blocks other writers or confirmations. A failed
save is logged and retried from `process()`; it never fails the write, so an
acknowledgment means the replica applied the write, not that it reached disk. A same-value remote retransmission is
acknowledged without another save; a same-value local write re-publishes and
collects confirmations again. Confirmation counts use distinct authenticated
transport senders from the actual target set. These are replica acknowledgments,
not consensus, CAS, or distributed locking.

The writer's local copy is applied and saved before broadcast. Self is excluded from the
remote target set. A positive configured `MIN_CONFIRMATIONS` takes precedence;
otherwise the threshold is `floor(remote target count / 2) + 1`. With no remote
targets a local-origin write returns False before persistence; there is no
local-only successful acceptance mode. A configured minimum above the reachable
target count times out. Explicit targets are exclusive only when
`include_default_peers=False`. The same rules apply after a same-value retry.
Records saved to disk survive process restarts with the same data volume; these
acknowledgments do not replicate R1FS content or elect a replacement
coordinator. Generic `value=None` deletes retain the historical asynchronous
confirmation behavior; lifecycle secret erasure uses a retained non-null
revocation marker and redacted tombstone, which receive normal confirmations.

`DEEPLOY_OPERATIONS` and `DEEPLOY_NODE_OPERATIONS` contain signed sequence records.
Writes and HSync reject invalid signatures, mismatched field identities, and
lower per-field sequences. Record ownership is derived from the signed principal,
not an untrusted snapshot wrapper. Operation signers must be registered oracles
or dAuth registry members. Current fields and eight sequence-modulo slots provide
bounded recent history; generic HSync namespaces retain their existing semantics.

Both `DAUTH_JOB_SECRETS` and `DAUTH_JOB_SECRET_REVISIONS` are protected. Remote
writes require an authenticated registered oracle/dAuth sender and a dAuth
recipient. Local secret writes may originate on an oracle, but network targets
must be dAuth registry participants. Protected HSync request, response, export,
and merge require dAuth registry membership, failing closed on registry lookup
failure. HSync never exports token-bearing records because that API supplies no
token capability.

Revision fields ending in `|revoked` are immutable once populated. Identical
retransmission is allowed; deletion/change is rejected. Revocation wins over
stale payload snapshots. Marker lookups preserve the writer's URL-safe Base64
padding so direct writes and snapshot repair find the same persisted keys.
Only deletion or the exact redacted
`{job_id, revision, revoked: true}` tombstone matching the revocation marker is
accepted for a revoked payload field. No plaintext secret can be restored by
late direct writes or merge repair.

Canonical `DEEPLOY_DEPLOYED_JOBS` pointers are signed envelopes containing
`job_id`, `revision`, `operation_id`, `coordinator`, `pipeline_cid`, and `deleted`.
Current `DAUTH_JOB_SECRETS` bundles carry equivalent signed writer metadata and
`deployment_revision`. A registered dAuth custodian may sign these bundles for
the registered protocol-oracle coordinator after verifying its lifecycle request;
public pointers must still be signed by that coordinator. Signature-only retry
differences include both node and EVM signature metadata. These generations increase across successful deployment,
restoration and deletion commits; restored runtime configuration may retain an
older content revision. Receivers verify registry signer and job-field binding,
reject lower generations and divergent equal-generation payloads, and ignore
signature-only differences for idempotent retries. Once versioned, a field cannot
be replaced by an unversioned write or deletion. A signed higher generation
allows explicit reuse after deletion. Both direct writes and HSync enforce the
same rules, preventing stale cached pipeline discovery from reviving deletion.
