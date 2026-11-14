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
peer that was offline while other peers kept accepting writes. The hand-off
(below, `CSTORE_HANDOFF`) re-sends a writer's own writes to such peers once it
is `on`.

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
- each exported record carries only `key`, `value`, `owner`, `readonly` and, when
  stamped, `ver` (the fields a merge reads); a record with a token is never exported

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

An accepted write that changes the replica only marks the store dirty. The
SerializationManager (`naeural_core/main/serialization_manager.py`, created by the
Orchestrator) keeps one thread for this save and saves a changed store every
`CS_SAVE_INTERVAL` (5 s), so one save covers every write made in that window and
the plugin thread does not run the save; `on_close()` saves pending changes at
shutdown. That thread checks once a second, so an accepted write reaches the disk
after up to the cadence, plus up to one second until the thread's next check, plus
the chain store's own save. A save of another owner (on an oracle the
network-monitor save, 20 MB, measured at 1.5-1.9 s idle and 6.1-6.3 s busy under a
0.7 CPU limit, longer on a slower host) runs on its own thread and does not hold
this one back, but the two compete for the interpreter while both run. A crash
can lose the writes of that window. Do not reintroduce a full-store fsync per write:
on mainnet oracles it took 65% of the plugin loop and kept the input queue full.
Run `test_deployment_lifecycle.py` and `test_chain_store_hsync.py` for this path. For each save, records are copied shallowly under the
persistence lock, then the copy is pickled, fsynced and atomically replaced
outside it, so disk I/O does not block other writers or confirmations. A failed
save is logged and retried by the next flush; it never fails the write, so an
acknowledgment means the replica applied the write, not that it reached disk. A same-value remote retransmission is
acknowledged without another save; a same-value local write re-publishes and
collects confirmations again. Confirmation counts use distinct authenticated
transport senders from the actual target set. These are replica acknowledgments,
not consensus, CAS, or distributed locking.

Save back-off (`CSTORE_SAVE_BACKOFF`, `off` / `report` / `on`, wave 03 default
`report`; ceiling `CSTORE_SAVE_MAX_GAP`, 30 s): after a save of d seconds the next
save waits until `min(CSTORE_SAVE_MAX_GAP, 10 * d)` after that save started, on the
monotonic clock. The first save after a start and the save at an orderly stop are
immediate. A save of 0.26 s keeps the 5 s cadence; 1.93 s spaces the saves about
21 s apart; 3 s or more, 30 to 35 s. In `on` a crash can then lose the writes of up
to `CSTORE_SAVE_MAX_GAP` + 5 s + 1 s + d; `report` saves as `off` (wave 02) and only
counts what `on` would defer. The network monitor's own save is not affected.

The writer's local copy is applied before broadcast. Self is excluded from the
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
differences include both node and EVM signature metadata; the SDK signing-version
field is `BCct.SIGN_CANON_V` (`SIGN_CANON_V`), not `EE_SIGN_CANON_V`. These generations increase across successful deployment,
restoration and deletion commits; restored runtime configuration may retain an
older content revision. Receivers verify registry signer and job-field binding,
reject lower generations and divergent equal-generation payloads, and ignore
signature-only differences for idempotent retries. Once versioned, a field cannot
be replaced by an unversioned write or deletion. A signed higher generation
allows explicit reuse after deletion. Both direct writes and HSync enforce the
same rules, preventing stale cached pipeline discovery from reviving deletion.

## Writer revisions (`cs_rev`)

A writer that has its own source of order puts it in the value as an integer
`cs_rev`; Tunnels Manager stores the signed client time of a credential update
there. A record whose value carries an int `cs_rev` is not replaced by a live write
with a lower one (refused as `refused_cs_rev` and not confirmed), and an hsync copy
replaces it only when the copy carries an equal or higher `cs_rev`. A live write
without `cs_rev` (a writer that does not version, or a delete) is applied by
arrival as before, and records without `cs_rev` keep arrival order. In the Tunnels
Manager namespaces the oracle-only sender rule is checked first. A node on older
code applies these writes by arrival, so replicas can differ until every oracle
runs this rule.

## Version stamps and the order rule (`CSTORE_ORDER`)

Switch `CSTORE_ORDER` (`off`, `report` or `on`, read once at start; `report` by
default). With it `off` no stamp is made or read, as in the previous release.

- **Stamp** (`ver`): `[time in ms, address of the writing node]`, a JSON list. Every
  local write takes a fresh one, identical rewrites included, from a clock that is the
  wall clock or one ms above the last stamp the node made or took, held within
  `CSTORE_VER_MAX_AHEAD` (60 s) of the wall clock. At start the clock begins at the
  newest stored stamp that is not more than 60 s ahead. The record keeps the stamp,
  the `SSTORE` message carries it, and the records of an hsync answer carry it as
  stored. A node of the previous release ignores the field and drops it when it
  rewrites the record.
- **Order rule**, for every local write, live store and hsync copy, after the
  signed-record rule and the `cs_rev` rule; the first step that decides wins:
  1. a stamp more than 60 s ahead of the wall clock: `future`;
  2. a live store stamped with another address than its verified sender: `origin`;
  3. nothing held, or a live write of the value held (an identical write): admitted;
  4. the signed sequence or revision of the namespace (`sequence` in the two Deeploy
     operation namespaces, `revision` in `DEEPLOY_DEPLOYED_JOBS`, `deployment_revision`
     in `DAUTH_JOB_SECRETS`), then `cs_rev`: a value that carries the integer field
     beats one that lacks it, else the higher wins (`older` for the loser);
  5. a held record without a stamp, or stamped more than 60 s ahead: the last arrival
     wins, as before;
  6. an unstamped write over a stamp: `unstamped`; an older stamp: `older`;
  7. an equal stamp with another value: the side with the smaller canonical JSON
     (a delete beats a value) loses as `collision`, so every replica keeps the same one.
- **Modes**: `report` counts each word as `<bucket>/<origin>/ver_<word>` and writes as
  the previous release (no write is refused or answered differently); `on` refuses a
  write on its word (`refused_<word>`, not confirmed) and skips such an hsync copy.
- A live write of the value held raises the held stamp when its own is newer; an hsync
  copy equal in value, owner, readonly and token does the same when the rule admits
  it. These stamp-only changes are not marked for the periodic save; the save at an
  orderly stop writes them.
- What this does not cover: the last writer by wall clock wins within the 60 s bound,
  and the losing write is gone except in the counters; a record written before stamps
  existed keeps arrival order until it is rewritten.

### Deletes that stick (tombstones)

With `CSTORE_ORDER` not `off`, a stamped delete leaves a **tombstone**: a record under
`dl:` plus the deleted key, with value `None`, the delete's owner and its stamp. A key
and its tombstone are never both held (storing a value removes the tombstone).

- Reads never see it: `get` and `hget` of the key return `None`; `hlist`, `hkeys` and
  `hgetall` match only `hs:` keys. No write may name a `dl:` key: a local one raises, a
  store from a peer is dropped and counted as `refused_invalid_key`.
- The order rule takes the tombstone as the held record, so a value stamped before the
  delete is `older_than_delete` (refused in `on`). A newer value is applied as to a key
  not held.
- An hsync answer carries each tombstone of the namespace under the deleted key (value
  `None`, its stamp). `on` applies it (the key removed, `ver_copy_delete`); `report`
  only counts what `on` would remove; `off` and the previous release skip it.
- A confirmation of a local delete (value `None`) counts on the tombstone.
- At each `CSTORE_STATS` line the sweep removes tombstones older than
  `CSTORE_TOMBSTONE_DAYS` (7) and the oldest past `CSTORE_TOMBSTONE_CAP` (570), or all
  of them with `CSTORE_ORDER` `off` or 0 days. A key whose tombstone was removed can come
  back from a peer that still holds it.
- At start a tombstone whose key is held again (rewritten under the previous release)
  is dropped. A tombstone of a key not held, or a newer stamp on one, is not marked for
  the periodic save; the save at an orderly stop writes it.
- With `CSTORE_ORDER` `on`, a delete of a record whose value carries `cs_rev`, or of a
  signed record, is refused (a side that carries the field beats one that lacks it).
- Previous release: a `dl:` record loads as a plain key no read lists or exports, and
  stays until a later release's sweep removes it.

### Refusal answers

With `CSTORE_ORDER` `on`, a receiver that refuses a stamped store answers its sender
with one `SCONFIRM` message `{op, key, ver, refused, held_ver}`: the store's stamp
echoed, the refusal word without its `refused_` prefix (`older`, `older_than_delete`,
`origin`, `collision`, or a rule of the previous release: `versioned`, `cs_rev`,
`secret_rule`, `token`, `readonly`) and its own stamp for the key, never the value. A
`future` stamp is counted and not answered, so the writer can deliver it once the clocks
agree. Unstamped stores and stores dropped for their sender's membership or a `dl:` key
are never answered. In `report` nothing is sent, except for a refused hand-off re-send.

The writer handles answers in every mode: an answer that echoes the stamp of its own
record, from a target not yet settled, adds the sender to the record's
`confirmed_peers` (now meaning settled: confirmed or refused) without counting a
confirmation, and moves the writer's stamp clock up to `held_ver` when that is not a
future stamp. The blocking wait ends at once with `False` when every target is
settled, one at least by a refusal, and too few confirmed it; `result_info["outcome"]`
is then `refused_by_peers`. A writer of the previous release sends no stamp, so it is
never answered.

### Lean confirmations (`CSTORE_LEAN_CONFIRM`)

Switch `CSTORE_LEAN_CONFIRM` (`off`, `report` or `on`, read once at start; `report` by
default). A receiver confirms a stamped store it applied (changed, identical or a delete
of a key not held) with `{op, key, ver}` in `on`, instead of echoing the value, and with
the previous release's full form plus `ver` in `report`. An unstamped store, and every
store with the switch `off`, get the previous release's full form.

The writer counts both forms in every mode: a message that carries a value (the full
form) by the echoed owner and value, as before; a lean one by its stamp, which must be
the record's `sent_ver`, the stamp of the writer's last own store (own records and
tombstones keep it, so a confirmation still counts after a peer's identical write raised
the record's stamp). A full form with a stamp that the two rules judge apart counts
`disagree_own` (an echo of an earlier own stamp of the same value: the value was
rewritten since) or `disagree_other` (any other case, where the lean rule would decide
differently). A writer of the previous release sends no stamp, so it never gets a lean
confirmation.

### Write-permission count

With `CSTORE_ORDER` not `off`, every write the order rule admits (and every hsync copy
applied) is checked against the held record outside the protected namespaces (the
five signed ones and `DAUTH_JOB_SECRET_REVISIONS`), when the write carries the record's
token. It only counts, in `report` and `on` alike, and never refuses:

- `perm_readonly`: a readonly record deleted, taken over with the same value, or
  rewritten under the same owner by another node than the one that stamped it (a sync
  copy also with another value and owner, which the merge applies);
- `delete_foreign`: a delete of a record that is not readonly, stamped by another node.

The node that wrote a record is the address in its stamp: the verified sender for a
live store, unverified for a sync copy. A record without a stamp is judged by its owner.

### Hand-off (`CSTORE_HANDOFF`)

Switch `CSTORE_HANDOFF` (`off`, `report` or `on`, read once at start; `report` by
default). The writer finishes its own writes: every 15 s (the first sweep
`CSTORE_HANDOFF_FIRST_S` after start) a sweep on the plugin thread walks the replica for
the node's own records whose stamp is still its own last store's (`ver` equal to
`sent_ver`; tombstones of local deletes included, re-sent as a store of value `None`). A
pair (record, target) is settled when the target confirmed it, refused it with an
answer, or the writer gave up; `confirmed_peers` holds the settled targets.

- A pair is first due `CSTORE_HANDOFF_FIRST_S` (300 s) after the write, then after a gap
  that doubles up to 3600 s: re-sends at the record's age of 300, 600, 1200, 2400, 4800
  and 8400 s, at most 6, then the pair is given up.
- A target that is offline, or not proven to stamp its stores, is skipped and uses none
  of the 6: an oracle away for any length gets the record once back. A peer is proven
  when its last store or confirmation to this node carried a well-formed stamp (so a
  worker, which gets few stores from oracles, proves its oracles by their confirmations).
  It is not proven when that message carried no stamp (a node of the previous release,
  or with `CSTORE_ORDER` or `CSTORE_LEAN_CONFIRM` `off`), when it sent nothing since this
  node started, or when it left the replication peers since its last such message (it
  may come back on the previous release). A dAuth secret goes only to a target still in
  the dAuth registry, and the payload of a secret revision is not re-sent once the writer
  holds the revision's revocation marker (its marker and redacted erasure still are).
- In `on`, at most `CSTORE_HANDOFF_WINDOW` (8) re-sends wait per peer (1 on a node in
  neither registry, a worker); the registry is read for it only when a due pair reaches
  this check, so a sweep with nothing to re-send reads none. At most 32 re-sends per
  sweep in both modes.
- A peer that joins becomes a target of the own records meant for every default peer
  (not of secret records); a peer the refresh dropped as `LOST STATUS` and that comes
  back is unsettled on the records written at most 300 s before it was dropped. An
  orderly stop re-opens nothing.
- `on`: each record goes as one `SSTORE` to its due peers with its own stamp and
  `resend: true`; widening, re-confirm and give-up change `target_peers` and
  `confirmed_peers`. `report`: the same bookkeeping in memory only, without the window
  (nothing is answered there, while in `on` a healthy peer frees its window within
  seconds), so each due pair is counted at its due time; nothing is sent and no record
  changes. `off`: nothing runs. `on` needs `CSTORE_ORDER` `on` and
  `CSTORE_TOMBSTONE_DAYS` above 0; otherwise the plugin does not start.

A receiver admits a store that carries `resend` by the order rule of `on` in `report`
too (signed field, `cs_rev`, then the stamp; a stamped re-send replaces an unstamped
copy), and answers a refused one. A node of the previous release would apply it in
arrival order, which is why re-sends go only to peers proven to stamp their stores. A
peer that restarts within one peer refresh (60 s) is never seen leaving, so it keeps its
proof until its first store or confirmation without a stamp.

Records: an own record also carries `default_peers`; a remote record carries
`confirms` -1 and `min_confirms` 0 and no peer lists (the previous release gave it
this node's lists, which nothing read). A `False` from a blocking write with the
hand-off `on` means "not confirmed yet": the store keeps delivering it.

### Non-blocking write (`wait=False`)

`chainstore_set(..., wait=False)` and `chainstore_hset(..., wait=False)` (no switch; a
caller that does not pass `wait` sees no change) run every check and the local apply of
a blocking write, queue the same `SSTORE` and return at once: `True` with the outcome
`queued` when the write was stored here and queued (not confirmed), `False` with the
blocking write's refusal word otherwise. The plugin thread re-sends a value while it is
unconfirmed on the blocking wait's schedule (with the defaults at 10 s and 20 s) and
counts how it ended as a blocking write does (`unconfirmed`, `overwritten`,
`refused_by_peers`; nothing when confirmed); a newer write of the key replaces the
pending re-sends; a delete is queued once. It is refused (`error`) in the protected
namespaces (the signed ones and `DAUTH_JOB_SECRET_REVISIONS`), whose callers rely on the
confirmation. It never waits for the chain store to start: before the store started, and
after the chain-store plugin's loop ended (a stop, or a restart before the next instance
publishes its setter), it stores nothing and returns `False` with the outcome
`not_ready`. The record and the message are those of a blocking write.

Web apps get a hook, `on_request_monitor_tick()`, run on the request monitor thread
before each read of the server queue (about every 0.25 s while idle, and after each
request); it does nothing in the base web app, and an exception in it is logged and does
not stop the admission of requests. A non-blocking chain-store write from it does not
depend on the plugin thread, which a hung endpoint can hold.

## Cost line in the node log (`CSTORE_STATS`)

Every `CS_STATS_INTERVAL` (300 s) the chain-store plugin loop writes one line to
the node log, one JSON object with sorted keys:

```text
CSTORE_STATS {"counts":{"hs:<hash>:/remote/changed":<n>,"hs:<hash>:/remote_non_peer":<n>,"plain/local/identical":<n>,"saves":<n>},"handoff_pending":<n>,"lost_inputs":<n>,"modes":{"handoff":"report","lean_confirm":"report","order":"report","save_backoff":"report"},"period_s":300,"queue_peak":<n>,"stamp_lead_max_s":<s>}
```

The line is written also when nothing was counted, so a missing line means the
plugin loop is not running (the interval is timed on the wall clock, so a clock
set back also delays the line). The counters restart at zero after each line.

Fields:

- `period_s`: seconds since the previous line, rounded.
- `counts`: the counters of that period, by label (below); only labels seen in the period appear.
- `queue_peak`: the largest number of inputs still waiting in the plugin's input queue when a loop lap reached the chain-store step in the period. The lap has taken its inputs by then, so a queue that filled up shows in `lost_inputs`, not here.
- `lost_inputs`: how much the plugin's `lost_inputs_count` grew: inputs that arrived while the input queue was full, when the queue drops its oldest input.
- `modes`: the mode of each wave 03 switch of the plugin config (`CSTORE_ORDER`, `CSTORE_LEAN_CONFIRM`, `CSTORE_HANDOFF`, `CSTORE_SAVE_BACKOFF`), read once at start: `off` (wave 02 behaviour), `report` (the rule runs and is counted, nothing acts on it) or `on` (the rule acts). Any other value stops the plugin's start. The same table is in every line until the next start.
- `stamp_lead_max_s`: the largest lead over this node's wall clock, in seconds, of a version stamp the order rule took in the period (a peer's clock ahead of this one, plus transit); 0 when none led, and always with `CSTORE_ORDER` `off`.
- `handoff_pending`: the hand-off's pairs (own record, target) not settled at its last sweep; 0 with `CSTORE_HANDOFF` `off`.

Labels in `counts`:

- `<bucket>/<origin>/<outcome>`: one count per write call (`_set_value`) with a valid key.
  - `<bucket>` is `plain` for a plain key, or the first 14 characters of an `hs:` key, which is the prefix shared by every field of one hash namespace.
  - `<origin>` is `local` for a write made on this node and `remote` for a write received from a peer.
  - `changed`: the write was stored and changed this replica, so the replica will be saved. It says what happened here: a local write that changed the replica and then was not confirmed by the peers is still counted as `changed`.
  - `identical`: the value was already stored (a remote copy of it, or a local write with the same value, owner and readonly); the replica is not saved for it, but a local one is still broadcast and confirmed.
  - `absent_delete`: a delete of a key this replica does not hold; the replica is not saved for it.
  - `refused_<reason>`: a refused write, one word per rule (before wave 03 all of them, and errors, were one `refused` label; a parser sums the labels that start with `refused_`):
    - `refused_no_peers`: a local write with no target peer. The node's own address is never a peer, so on a seed whose whitelist holds only itself every write ends here at once (it used to wait 3 x 10 s).
    - `refused_oracle_only`: a Tunnels Manager write, or an unversioned deployed-job pointer, from a sender outside the oracle registries (remote), or a local Tunnels Manager write with no oracle registry member among its targets.
    - `refused_not_member`: a dAuth or key-rotation write refused by the registry-membership rules: locally (this node, or a target, outside the registry the namespace needs) or remotely (the sender outside it, or this node outside the dAuth registry; these remote drops were not counted before).
    - `refused_versioned`: the signed-record rule of the deployment and dAuth namespaces.
    - `refused_cs_rev`: a value whose integer `cs_rev` is lower than the stored value's (below).
    - `refused_secret_rule`: a revocation marker emptied or changed, or a payload for a revoked secret revision.
    - `refused_token`: the stored record has another token.
    - `refused_readonly`: the stored record is readonly for another owner.
    - with `CSTORE_ORDER` `on` (or `report`, for a hand-off re-send) only: `refused_future`, `refused_origin`, `refused_older`, `refused_older_than_delete`, `refused_unstamped` and `refused_collision`, the order rule's words (above).
  - `error`: the call raised before the replica changed (for example a bad `timeout`).
- `<bucket>/local/unconfirmed`, `<bucket>/local/overwritten` and `<bucket>/local/refused_by_peers`: counted in addition to `changed` or `identical` when a local write's wait failed: too few confirmations after the retries, another write replaced the key during the wait, or every target settled with at least one refusal answer and too few confirmations. For a `wait=False` write they are counted later, by the plugin thread's re-sends.
- `<bucket>/local/queued`: counted in addition to the outcome for a `wait=False` write stored here and queued.
- `<bucket>/<origin>/ver_<word>` (`CSTORE_ORDER` `report` or `on`): a write or copy the order rule gives a word (`future`, `origin`, `older`, `older_than_delete`, `unstamped`, `collision`), counted whether or not it is refused; `<origin>` is `local`, `remote` or `hsync` (a record of a sync answer). `ver_invalid`: a stamp that is not `[int, str]`, treated as none. `<bucket>/hsync/ver_copy_delete`: a tombstone copy that removes (`on`) or would remove (`report`) a value held.
- `<bucket>/<origin>/tombstone_new`: a delete that left a new tombstone; `<origin>` also `hsync` in `on`.
- `<bucket>/confirm/disagree_own`, `<bucket>/confirm/disagree_other` (`CSTORE_LEAN_CONFIRM` `report` on the receivers): a full confirmation with a stamp that the value rule and the stamp rule judge apart (above).
- `<bucket>/<origin>/perm_readonly`, `<bucket>/<origin>/delete_foreign` (`CSTORE_ORDER` `report` or `on`): the write-permission count (above); counts only.
- `<bucket>/remote/refused_invalid_key`: a store for a `dl:` key, dropped; the bucket is that of the key after `dl:`.
- `tombstones_held`, `tombstones_dropped`: tombstones left and removed by the sweep before the line.
- `handoff/send/<peer>`, `handoff/resend/<peer>`, `handoff/gave_up/<peer>`, `handoff/window_full/<peer>` (`CSTORE_HANDOFF` `report` or `on`): first re-sends of a pair, later re-sends, give-ups, and (in `on` only) due pairs held back by the window, at each sweep, per target peer (in `report`, each due pair is counted at its due time, as if every re-send went unanswered). `<bucket>/handoff/answered`: a confirmation of a pair already re-sent (in `report`, a re-send `on` would have wasted).
- `<bucket>/remote_non_peer`: a store received from a sender that is not in this node's replication peers, counted before any check of the write.
- `saves`: full-replica saves started in the period (a failed write is counted too, and retried by the next save). A hash-sync merge that changed the replica also leads to a save, without a write label.
- `saves_deferred` (save back-off `report` or `on`): periodic saves of a changed replica inside the back-off pause; skipped in `on`, made anyway in `report`, where `saves - saves_deferred` is what `on` would have saved.
- `save_cpu_ms` (save back-off `report` or `on`): CPU milliseconds the periodic saves took on the save thread.

A caller of `chainstore_set` or `chainstore_hset` can pass `result_info={}` to read the same word as `result_info["outcome"]` (the wait end when there is one, `queued` for a `wait=False` write, `not_ready` for a `wait=False` call before the store started or after its loop ended, `error` when the call raised); the returned bool is unchanged.

## Census line in the node log (`CSTORE_CENSUS`)

The first `CSTORE_STATS` line after start, and every 12th after it (about hourly), is
followed by one `CSTORE_CENSUS` line, a JSON object with sorted keys and one row per
bucket:

```text
CSTORE_CENSUS {"hs:<hash>:":[<records>,<unstamped>,<tombstones>,<readonly>,<size>,"<digest>"],"plain":[...]}
```

- `records`: live records; `unstamped`: those without a stamp (written before stamps or
  by a node of the previous release); `readonly`: those marked readonly.
- `tombstones`: tombstones of deleted keys, in the bucket of the key they deleted.
- `size`: characters of the live values' canonical JSON (sorted keys).
- `digest`: 8 hex digits, the XOR over the live records of a 4-byte BLAKE2b hash of the
  key and the value's canonical JSON (for a record with a token, or in
  `DAUTH_JOB_SECRETS` or `DAUTH_JOB_SECRET_REVISIONS`, of the key and the stamp instead,
  so no such value is hashed into the log). Replicas that hold the same live records
  show the same digest whatever order the records arrived in; tombstones and the
  bookkeeping fields (confirmations, peer lists, owner) are left out.

The line holds no key and no value. Keys under `hs:` whose 14th character is not `:`
(a namespace's keys start with `hs:`, 10 hex digits and `:`) are left out. The walk runs on the plugin
thread without the persistence lock; on a 5,700-record, 5.2 MB replica it took 125 ms of
CPU and 0.38 s of wall time at 0.35 CPU.

To find the hash namespace of a bucket, compute its prefix: `"hs:"` followed by the
first 10 hex digits of the SHA-256 of the namespace name, followed by `":"`, as
`__hset_index` does. For example, in Python:

```python
import hashlib
bucket = "hs:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10] + ":"
```
