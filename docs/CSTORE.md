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
- a copy that takes more than 512 KiB on the wire, that no message could carry, or that
  is nested deeper than 64 levels, is skipped and counted (see "Record size cap")

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
  `hsync` during container boot (the store's own resync, below, covers only the five
  signed namespaces)

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

## Resync of the signed namespaces

No switch: it acts in every mode. The chain store of an oracle asks for the five signed
namespaces by itself, so an oracle that was away gets what it missed there without waiting
for a caller's sync. A round asks `DEEPLOY_OPERATIONS`, `DEEPLOY_NODE_OPERATIONS`,
`DEEPLOY_DEPLOYED_JOBS`, `DAUTH_JOB_SECRETS` and `NODE_KEY_ROTATIONS` in turn, one request
every 60 s, and the next round starts 1800 s after a round's last request (a round about
every 34 min). Each wait is stretched or shortened by up to a quarter at random, so the
oracles of a network do not ask together. The first request goes at the first plugin lap
that finds a replication peer.

- Who asks: a node in a registry (the oracles' or the dAuth servers'); `DAUTH_JOB_SECRETS`
  only a dAuth registry member. A node in neither registry never asks.
- Who is asked: 2 peers picked at random among the replication peers that are registry
  members; for `DAUTH_JOB_SECRETS` and `NODE_KEY_ROTATIONS`, which dAuth servers write,
  dAuth registry members only.
- The request is the `SHSYNC_REQ` of `hsync` (a request id and the namespace), so a node of
  any release answers it as it answers a caller's sync.
- No caller waits. The answer of each asked peer is merged on the plugin thread when it
  arrives, once per peer, until 60 s after the request; a later answer is dropped. Both
  answers are merged, not only the first.
- A request no answer of which was taken by the end of its wait (none came, or each was
  dropped) is asked again at the next turn, before the next namespace, and that turn comes
  at most 60 s later (after a round's last request it was a pause away): a namespace whose
  answers were lost is asked again 60 to 135 s after its first request instead of a round
  (about 30 minutes) later. A second request is not repeated, so a namespace is asked at most twice per
  round. This adds at most five requests per round, each to at most two peers, and none
  while the answers arrive.
- Only records whose value carries the namespace's signed field (`sequence`, `revision`,
  `deployment_revision`) are taken, and every record of the key rotations, whose merge
  checks each proof. A pointer of the old form (a plain CID without a revision) that was
  removed is not brought back, and no delete is applied.
- The merge is the one of `hsync`, with its rules.

Counters, under the namespace's bucket: `<bucket>/resync/asked` (requests sent),
`<bucket>/resync/answered` (answers taken, counted before their merge) and
`<bucket>/resync/merged` (records they inserted or replaced; present with 0 when the
answers changed nothing). Two answers per request are expected; fewer means a peer did not
answer within 60 s. `<bucket>/resync/unanswered` counts, when a request's wait ends, each
asked peer whose answer was not taken, and `<bucket>/resync/dropped_<word>` each answer the
handler dropped, by why: `no_sender`, `not_asked` (a node not asked, or a second answer of a
peer already taken), `other_namespace`, `not_member` (a registry rule of the namespace
refused the sender or this node) or `malformed` (a snapshot that is not a dict). An answer
dropped before it reached the chain store, or after its wait, shows only as `unanswered`.
Each change of a merge marks the replica for the save when it is made,
so an answer whose merge an exception ends has its earlier records saved; they are not in
`merged`.

It does not cover `DAUTH_JOB_SECRET_REVISIONS`, whose records carry no version to order
them by, nor any unsigned namespace. A node of the previous release never asks, and
answers these requests as today.

Cost, on the node image's interpreter under a 0.35 CPU limit: an idle lap of the step
takes under 1 microsecond. Merging one answer of 1,000 signed operations (2.7 MB of JSON),
with real signature checks, took 1.1 to 1.8 s of CPU and 3 to 5 s of wall time when the
replica lacks them all (one signature check per record: the first answers after an
absence), and 0.02 to 0.08 s of CPU and 0.1 to 0.3 s of wall time when it holds them all,
the higher figures when the copies carry other stamps than the held ones or none (no
signature check: every later answer). The merge runs on the plugin thread under the persistence lock: for that
time the store handles no other message, and local writers, the counters and the save's
copy wait. An oracle answers about two such requests per namespace and round.

One merge reads each oracle registry at most once, for all its copies, and does not repeat
a read that failed: the copies whose rule needs that registry are then skipped. The block
engine keeps a registry for ten minutes; what a failed read costs after that is the SDK's
(one that remembers no failure makes the remote call again at each question, about 20 s for
a call that hangs). Without the memo a merge would ask once per signed copy the replica
lacks. No registry is read under the persistence lock: a merge of `DEEPLOY_OPERATIONS`,
`DEEPLOY_DEPLOYED_JOBS` or `DAUTH_JOB_SECRETS`, and a write to one of them, read both
registries before taking it (any other namespace reads none), so a slow or failing read
holds only the thread that makes it, not every writer of the node.

In every merge, a caller's `hsync` included, a copy whose value is a dict equal to the one
held under its key is not put through the signed-record rule while it leaves everything
but the held stamp as it is: a sync of a signed namespace that already agrees verifies no
signature, where it verified one per record (about 3 ms each at 0.35 core). An equal copy
that would be stored, because its owner, readonly mark or token differ from the held ones,
is put through the rule right before the store and skipped when the rule refuses it. Equal
is not verified: `5.0` equals `5` and is another text, whose signature fails, and a record
held from before the rule, or whose coordinator has left the registry, does not pass it
today. A copy with another value, or of a held value that is not a dict, goes through the
rule first.

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

## Record size cap

No switch: it acts in every mode. One record may take at most `CS_MAX_RECORD_BYTES`
(524,288 bytes, 512 KiB) on the wire, counted as a namespace-sync answer carries it plus
the token a store carries: `{key: {key, value, owner, readonly, token, ver}}` as compact
JSON with non-ASCII characters as themselves, in UTF-8 bytes. Every field a store or a
sync answer can fill counts, not only the value. The record is measured with the owner,
readonly mark, token and stamp it would be stored with: after the signed-record, `cs_rev`
and namespace rules, which can replace the owner, mark and token a sender gave, and before
the order rule. So a writer and its receivers measure the same bytes, on every path that
stores a record, and a record those earlier rules refuse is not measured (a signed one has
its signature checked before its size):

- a local write returns `False` at once with `refused_too_large`; nothing is stored or
  sent;
- a store from a peer (a hand-off re-send included) is not applied and not confirmed
  (`refused_too_large`); with `CSTORE_ORDER` `on` a stamped one gets the refusal answer
  `too_large`;
- a copy in a sync answer is skipped and counted `<bucket>/hsync/refused_too_large`; the
  other copies of the answer are merged. A copy that would be stored as the record already
  held, field for field, is not measured: nothing of it is stored.

The same paths refuse a record no message can carry, as `refused_unencodable`: a key,
value, owner, readonly mark, token or stamp that holds a string UTF-8 cannot encode (a
lone surrogate, which a JSON escape in a peer's message or a local caller can deliver), a
value of a type or with a dict key the wire's encoder does not take, a circular value, or
one nested deeper than the encoder goes (about 10,000 levels on the node image's
interpreter). One such record made every sync answer of its namespace fail to be
prepared for sending. A refused store of this kind is never answered: the answer would
echo the key and the stamp. Values the encoder converts (numpy numbers and arrays,
datetimes) are stored as before.

The same paths refuse a record nested too deep, as `refused_too_deep`: one whose value,
owner, readonly mark or token holds dicts and lists inside each other more than
`CS_MAX_DEPTH` (64) levels deep, the outermost counted as the first. The wire's encoder and
decoder take about 10,000 levels, but the steps after the store copy a value level by
level and stop far earlier: the export of a sync answer and a read from about 445 levels,
the save's pickle of the replica from 5,000. So one deeper record, which any sender could
store, broke every sync answer and read of its namespace, or every later save. The
deepest value known in use, a deployment's secret record with one pipeline configuration
per node, is nested 10 levels. Such a record can be carried, so unlike an unencodable one
its stamped store is answered (`too_deep`) with `CSTORE_ORDER` `on`. A record held from
before the bound is not removed by it; its delete is taken. The bound is checked by a walk
of the record's dicts and lists, after the encoding and the size, that stops at the bound;
a record over the cap is not walked.

A delete is a record with value `None`, so a key that holds a larger record, written
before the cap existed, can still be deleted. Such a record is not removed by the cap; a
sync answer or a hand-off re-send that carries it is refused by its receivers. The
largest record found on the production network when the cap was set took 125 KB, a
quarter of the cap: that record holds one configuration per node of a deployment and
grows with it, so the cap leaves room for a deployment four times that size.

One record at the cap fits one message. Sent alone through the node's encryption for
several receivers (compressed, encrypted, written as hex inside JSON, compressed again,
base64), it filled 63 % of the broker's 1 MiB packet with a value of random printable
ASCII, 72 % with content chosen to compress worst sent to 100 receivers, and stays under
80 % for content that does not compress at all. A namespace-sync answer carries every
record of its namespace and is not bounded by this cap.

A node of the previous release has no cap and no depth bound: it applies and confirms a
larger or deeper record, and a sync answer from it can carry a larger one (skipped here).
Its own larger or deeper store is refused here without an answer, as it carries no stamp,
so its write returns `False` after its retries unless enough of its other peers confirm
it.

Cost: one JSON encoding of the record and one walk of its dicts and lists per write call,
under the persistence lock. On the node image's interpreter under a 0.35 CPU limit, in
wall time with the limit's throttling: 0.06 ms for a 0.4 KB value (0.05 without the walk),
0.5 to 0.6 ms for a 14 KB value, 4.9 to 5.1 ms for a 122 KB record of nested configuration
(the kind the largest production value is; 4.6 without the walk, 1.6 to 1.9 ms of CPU),
10 ms for 125 KB of small nested dicts, where the walk costs most, 20 ms for 125 KB of
floats, and 19 to 21 ms (6 to 7 ms of CPU) for a 511 KB record of nested configuration,
just under the cap; a record of twice the cap is refused after 30 ms, not walked. At full
speed: 0.012 ms, 0.11 ms, 0.96 ms for the 122 KB record and 3.9 ms for the one at the cap.
A sync answer costs one check (the encoding and the walk) per copy that would change the
record held, and none for a copy that would be stored as that record field for field: its
fields are compared instead, its value up to three times (for this skip, for the
signed-record rule's and for the order rule's identical case). A copy's value is copied
only when it is stored. Merging one answer of 1,000 signed operations (2.7 MB) that all are
the records held takes 0.02 to 0.04 s of CPU at 0.35 CPU, the comparisons included; when
their stamps differ from the held ones each copy is checked once, and the same merge takes
0.06 to 0.08 s.

### New keys per sender

No switch; by default it only counts. A store from a peer that creates a key this replica
did not hold, outside the protected namespaces (the five signed ones and
`DAUTH_JOB_SECRET_REVISIONS`), counts `new_keys/<sender>` when its verified sender is
neither one of this node's replication peers nor in an oracle registry. An overwrite, a
delete and a refused store count nothing. The registries are read only for such a store
from a sender outside the replication peers, of a key not held, once per store and before
the persistence lock is taken: while a registry's remote call hangs after the block
engine's copy expired, each such store waits for that call (about 20 s) on the plugin
thread, without the lock. A key another thread deletes between that question and the lock
is not counted. The per-sender counts of a period show how
many keys a node outside the oracles adds, which is what a limit is set from.

The limit is the number `CSTORE_NEW_KEYS_MAX` of the plugin config, read once at start
(default 0):

- 0: no store is refused; the keys are only counted, as above.
- above 0: such a store is refused as `refused_new_keys` once its sender's
  `new_keys/<sender>` count of the current period has reached the number. It is not
  stored, not counted as a new key and not confirmed; a stamped one is answered like any
  other refusal (`CSTORE_ORDER` `on`, or a hand-off re-send), so its writer stops waiting
  and does not re-send it.
- The period is the one of the `CSTORE_STATS` line: the counts restart at each line, every
  `CS_STATS_INTERVAL` (300 s) of the wall clock, so a sender creates at most that many keys
  on this replica between two lines. Each replica counts for itself, and a restart of the
  plugin starts a new period.
- Never refused by it: an overwrite or a delete of a key held, a key of a protected
  namespace, a store from a replication peer or from a registry member, a local write and
  a sync copy.

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
and its tombstone are never both held (storing a value, live or from a sync answer,
removes the tombstone; with `CSTORE_ORDER` `off` it stays until the next sweep). A `dl:`
record that holds a value (one a caller of the previous release could write, as that
release's API did not refuse the prefix) is not a tombstone: a write of the key never
removes it, and a delete of the key leaves no tombstone over it (the key is only removed,
as the previous release removes it; a sync answer's delete copy of the key is skipped).

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
`origin`, `collision`, `too_large`, `too_deep`, or a rule of the previous release: `versioned`,
`cs_rev`, `secret_rule`, `token`, `readonly`) and its own stamp for the key, never the
value. A `future` stamp is counted and not answered, so the writer can deliver it once the
clocks agree; a record that is not encodable (`unencodable`) is not answered either.
Unstamped stores and stores dropped for their sender's membership or a `dl:` key
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
  node started, or when it left or joined the replication peers since its last such
  message (it may come back on the previous release). A dAuth secret goes only to a target still in
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
  changes. `off`: nothing runs. `on` needs `CSTORE_ORDER` `on`, `CSTORE_TOMBSTONE_DAYS`
  and `CSTORE_TOMBSTONE_CAP` above 0, and `CSTORE_LEAN_CONFIRM` not `off` (a confirmation
  without a stamp withdraws its sender's proof); otherwise the plugin does not start.

A receiver admits a store that carries `resend` by the order rule of `on` in `report`
too (signed field, `cs_rev`, then the stamp; a stamped re-send replaces an unstamped
copy), and answers a refused one. A node of the previous release would apply it in
arrival order, which is why re-sends go only to peers proven to stamp their stores. A
peer that restarts within one peer refresh (60 s) is never seen leaving, so it keeps its
proof until its first store or confirmation without a stamp. So does an explicit target
of a write that is never in the replication peers (for example a single peer named with
`include_default_peers=False`), for as long as it sends nothing unstamped. A stamped
message from a peer that stopped proves it again only if this node processes it after
the refresh that adds the peer back, which takes an input queue that lags by more than
the peer's absence.

Records: an own record also carries `default_peers`; a remote record carries
`confirms` -1 and `min_confirms` 0 and no peer lists (the previous release gave it
this node's lists, which nothing read). A `False` from a blocking write with the
hand-off `on` means "not confirmed yet": the store keeps delivering it.

### Catch-up after a start (`CSTORE_PULL_PEERS`)

A node that was away misses the records written meanwhile. The hand-off brings what live
writers still hold as their own; the catch-up asks other oracles for everything stamped after
what the node held when it started, also the writes of a writer that is gone. It follows the
switch `CSTORE_HANDOFF` (`off`: a node neither asks nor answers; `report`: it asks, answers
and counts what it would change; `on`: it applies it) and the number `CSTORE_PULL_PEERS`
(default 2, read once at start; 0: the node never asks, and still answers).

**What the replica keeps.** One record of its own under the reserved key
`hs:cstore_pull`, with the value `{"floor": <ms>, "owed": {<writer>: <ms>} | null}`:

- `floor` is the tombstone horizon: the newest stamp of a tombstone this node removed at a
  sweep (expired, or past the cap), in every mode of every switch. No record stamped below
  it may come back by a catch-up, as the delete that removed it is forgotten here. The
  sweep writes the record in the lock hold of its removals, so a save never holds a removal
  without the horizon it raised, and a start reads it back. A removed tombstone stamped
  more than `CSTORE_VER_MAX_AHEAD` ahead of the wall clock does not raise it and is counted
  `pull_h_future`.
- `owed` are the marks of a start whose catch-up has not finished: per writer, the newest
  stamp time the replica held at that start (stamps more than `CSTORE_VER_MAX_AHEAD` ahead
  left out), taken before any write of the run can raise one. A start takes them with
  `CSTORE_HANDOFF` not `off` and `CSTORE_PULL_PEERS` (default 2, read once at start) above
  0. While marks are still owed from an earlier start, the new ones are the lower of the
  two per writer; a writer either side lacks is left out, which means all its records are
  owed. With the switch `off` or the number 0 a start takes none and writes nothing.

The key is under `hs:` but not of a namespace's form (`hs:`, 10 hex digits, `:`), so no
namespace sync, list or census reaches the record; a read of that exact key returns it.
No write may name it: a local one raises, and a peer's store or delete is dropped and
counted `hs:cstore_pull/remote/refused_invalid_key`. A start repairs a record of another
form, with one red line per field: a horizon that is not an int, or lies past the bound of
a stamp that is not a future one, becomes that bound, and marks that are not a map of
writer to int are dropped. The owner field is not read, so the record survives a change of
the node's address. One red line also says when the newest mark of a start is older than
`CSTORE_TOMBSTONE_DAYS`: keys deleted on the network meanwhile are still held on that node.

A node of the previous release keeps the record in its file, reads it nowhere, and takes a
peer's store to that key like any other; the repair above is for that case.

**The request and its answer.** A catch-up request is the namespace sync's request message
with a `pull` field and no `hkey`: `SHSYNC_REQ {op, request_id, pull: {since, floor, after}}`.
`since` maps a writer to the stamp time from which its records are wanted (a writer not
named: from 0), `floor` is a stamp time below which nothing may be sent, and `after` is the
cursor `[ms, stored key]` of the last record of the page before (`null` for the first). With
`CSTORE_HANDOFF` not `off`, a node answers a sender that is in an oracle registry with one
page, `SHSYNC_RESP {op, request_id, snapshot, pull: {after, more, partial, wait}}`, sent to
that sender alone:

- The page holds the records that carry a stamp and no token, lie outside the six protected
  namespaces (never paged: their rules read a whole namespace), are stamped at or above the
  floor and their writer's `since`, and come after the cursor, ordered by stamp time and then
  stored key. Each travels with the five fields of a sync answer; a tombstone travels under
  the key it deleted, with value `None`. The values are not copied for the page.
- Records are added while the page stays within `CS_PULL_PAGE_BYTES` (262,144), each counted
  as the size cap counts a record (compact JSON, non-ASCII characters as themselves, UTF-8
  bytes). The first record is always taken, so a record larger than a page travels alone
  and a page holds at most the larger of that bound and the size cap of one record
  (`CS_MAX_RECORD_BYTES`, 524,288). That worst page, one record at the cap of content that
  compresses worst, took 735 KB on the wire through the SDK's
  encryption: 70 % of a broker packet of 1 MiB (77 % is the bound for a plaintext that does
  not compress at all).
- `after` is the cursor of the page's last record, `more` says records are left, `partial`
  says the answering node still owes a catch-up itself (its replica holds marks), and `wait`
  is the seconds before it sends another page.
- A node sends one page per `CS_PULL_GAP` (8 s), whoever asks, and none while more inputs
  wait in its queue than one lap takes: the answer is then a busy reply,
  `pull: {busy: true, wait: 8}` with no record. It keeps no state per asking node.
- A request whose fields have another form, or whose sender is in neither registry, gets no
  answer and counts `pull_refused`; each page counts `pull_answered`. With `CSTORE_HANDOFF`
  `off` nothing is answered or counted.

A node of the previous release drops such a request at its `hkey` check: no answer, no log
line, no change.

**Asking.** A start that took marks runs one catch-up, a step of each plugin lap:

- Only a node in an oracle registry asks, and it asks registry members among its
  replication peers, one at a time. A series is a chain of requests to one oracle, picked
  at random among those not tried in this round, each for the page after the cursor of the
  page before, until a page says no more follows. A series that changes its oracle for any
  reason starts again from the beginning.
- Each request carries, per writer, the owed mark less 120 s (twice
  `CSTORE_VER_MAX_AHEAD`), so a writer's stamps are compared only with its own mark, never
  with a wall clock or another writer's stamps; a writer without a mark is asked for from
  the floor. The floor is computed for each request: the larger of the tombstone horizon
  and now less `CSTORE_TOMBSTONE_DAYS` plus 120 s. Nothing below it is asked for, so a key
  whose delete this node no longer remembers cannot come back.
- An answer is waited for 10 s (`pull_no_answer`, and the series ends with that oracle).
  While more inputs wait in the queue than one lap takes, nothing is sent and the wait of a
  request in flight starts again. A busy reply is asked again after its wait; the third in
  a row ends the series.
- Of a page, a record is dropped and counted `pull_refused` when it has no well-formed
  stamp, is stamped below the floor, its writer's mark less 120 s or the cursor asked after,
  has a key of a protected namespace, a `dl:` key or the reserved key, or carries a token.
  A copy of a record held with a token, or readonly for another owner, is skipped and
  counted `<bucket>/pull<n>/held`. An answer of another form, or a page that says more
  follows without a cursor past the one asked after, counts `pull_refused` and ends the
  series.
- The rest goes through the merge of `hsync`, with no namespace and every rule of it (the
  size cap, the depth bound, the signed-record and `cs_rev` rules, the order rule), counted
  under the origin `pull<n>`, n being the number of the series. `on` applies it; `report`
  runs the same rules and counts and changes nothing. A copy the order rule admits is
  counted by what it changes: `missing` (nothing held under its key), `newer` (a stamped
  record or tombstone held), `over_legacy` (a record without a stamp held: the stamped copy
  replaces it, also when the unstamped one was written later), or `same` (the record held:
  sent for nothing). A delete copy is counted as in a sync answer (`ver_copy_delete` when it
  removes, or would remove, a value held); only with `CSTORE_ORDER` `on`, where a sync
  applies deletes, is it also counted by what it changes.
- After a page with more to follow the next request waits `max(8 s, the answer's wait,
  10 x d)`, at most 120 s, d being the last save's wall time, so the saves the merged pages
  cause stay near a tenth of the wall time. d is measured only with `CSTORE_SAVE_BACKOFF`
  not `off`; with it `off` the wait is `max(8 s, the answer's wait)`.
- A series ends at a page with no more to follow; the next one starts 120 s later, and so
  does a new round when every oracle was tried. The catch-up is complete after
  `CSTORE_PULL_PEERS` series without a `partial` page, or, once more oracles than that have
  been tried, after that many series of any kind. Then the marks leave the reserved record
  (held without a dirty mark of their own: they reach the file with the next save or the
  save of an orderly stop; a crash before it only makes the next start ask again).
- 3600 s after the start an unfinished catch-up stops asking for that run, with one red
  line when it had asked anyone. Its marks stay in the record, so the next start asks from
  them again, within what its own floor still allows.

Counters: `pull_no_answer`, `pull_refused`, `<bucket>/pull<n>/<outcome>` (the merge's counts
under the catch-up's origins, and `held`), and `pull<n>_cut_s`: the largest number of
seconds, in the period, by which the floor of a request of series n lay above the lowest
mark it asked from (present with 0 when nothing was cut). A cut above 0 means part of what
a mark asks for could no longer be asked for; a writer whose newest record is older than the
floor gives one too, although nothing may be missing from it.

What it does not reach: records without a stamp, the six protected namespaces (the signed
five have the resync above), records with a token, records stamped below the floor (older
than the tombstone life, or than a tombstone this node removed at its cap), records a writer
stamped more than 120 s below its own newer ones (a wall clock set back while it ran), and
records no oracle asked holds. A node that is in no registry never asks, so the marks of its
starts stay owed (`pull_owed` 1 in its `CSTORE_STATS` lines).

Cost, on the node image's interpreter under a 0.35 CPU limit, with a replica of 5,700
records (5.8 MB): a lap with nothing owed, under a microsecond; the marks of a start, 2 to
3 ms of CPU; building a full page, 10 to 16 ms; taking one, 19 to 24 ms judged only and 29
to 33 ms applied, about 0.1 s of wall time, under the persistence lock. A page judged only
(`report`) copies no value and causes no save.

### Non-blocking write (`wait=False`)

`chainstore_set(..., wait=False)` and `chainstore_hset(..., wait=False)` (no switch; a
caller that does not pass `wait` sees no change) run every check and the local apply of
a blocking write, queue the same `SSTORE` and return without waiting for any
confirmation: `True` with the write's own outcome (`changed`, `identical` or
`absent_delete`) when it was stored here and queued (not confirmed), `False` with the
blocking write's refusal word otherwise. The plugin thread re-sends a value while it is
unconfirmed on the blocking wait's schedule (with the defaults at 10 s and 20 s) and
counts how it ended as a blocking write does (`unconfirmed`, `overwritten`,
`refused_by_peers`; nothing when confirmed); a newer `wait=False` write of the key
replaces the pending re-sends, and a blocking write of another value or a delete ends
them at the next lap (`overwritten`); a delete is queued once. It is refused (`error`) in the protected
namespaces (the signed ones and `DAUTH_JOB_SECRET_REVISIONS`), whose callers rely on the
confirmation. It never waits for the chain store to start: before the store started, and
after the chain-store plugin's loop ended (a stop, or a restart before the next instance
publishes its setter), it stores nothing and returns `False` with the outcome
`not_ready`. The record and the message are those of a blocking write.

It waits at most `CS_NOWAIT_LOCK_S` (1 ms) for the chain store's persistence lock, which
other threads hold for whole-store walks: a namespace sync's merge holds it for its whole
answer, with one signature check per signed record whose value is not the one held (3 to
4 ms each at 0.35 core, so 4 to 5 s for 1,000 records a replica lacks), and the save holds
it while it copies the replica. Held longer, the write stores nothing and returns `False`
with the outcome `not_ready`, and the caller writes again later; before, it waited for the
whole hold. Once it has the lock it keeps it to its end (the apply, the queueing, the
registration of its re-sends and its count), so it never waits for it twice. A `not_ready`
write is not counted in `CSTORE_STATS`. Measured on the node image's interpreter under a
0.35 CPU limit, while a merge of 1,000 signed records the replica lacked held the lock: the
write took 2 ms (median) and 6 to 45 ms (99th percentile, three runs), where it waited
4.9 s (median); in an idle store 0.1 to 0.2 ms. A node where a save or a merge holds the lock for more than 1 ms about
1 % of the time sees about 1 % of such writes end `not_ready`.

Web apps get a hook, `on_request_monitor_tick()`, run on the request monitor thread
before each read of the server queue (about every 0.25 s while idle, and after each
request); it does nothing in the base web app, and an exception in it is logged and does
not stop the admission of requests. A non-blocking chain-store write from it waits neither
for the plugin thread, which a hung endpoint can hold, nor for more than 1 ms for the
persistence lock (above).

## Cost line in the node log (`CSTORE_STATS`)

Every `CS_STATS_INTERVAL` (300 s) the chain-store plugin loop writes one line to
the node log, one JSON object with sorted keys:

```text
CSTORE_STATS {"counts":{"hs:<hash>:/remote/changed":<n>,"hs:<hash>:/remote_non_peer":<n>,"plain/local/identical":<n>,"saves":<n>},"handoff_pending":<n>,"lost_inputs":<n>,"modes":{"handoff":"report","lean_confirm":"report","order":"report","pull_peers":2,"save_backoff":"report"},"period_s":300,"pull_owed":<n>,"queue_peak":<n>,"stamp_lead_max_s":<s>}
```

The line is written also when nothing was counted, so a missing line means the
plugin loop is not running (the interval is timed on the wall clock, so a clock
set back also delays the line). The counters restart at zero after each line.

Fields:

- `period_s`: seconds since the previous line, rounded.
- `counts`: the counters of that period, by label (below); only labels seen in the period appear.
- `queue_peak`: the largest number of inputs still waiting in the plugin's input queue when a loop lap reached the chain-store step in the period. The lap has taken its inputs by then, so a queue that filled up shows in `lost_inputs`, not here.
- `lost_inputs`: how much the plugin's `lost_inputs_count` grew: inputs that arrived while the input queue was full, when the queue drops its oldest input.
- `modes`: the mode of each wave 03 switch of the plugin config (`CSTORE_ORDER`, `CSTORE_LEAN_CONFIRM`, `CSTORE_HANDOFF`, `CSTORE_SAVE_BACKOFF`), read once at start: `off` (wave 02 behaviour), `report` (the rule runs and is counted, nothing acts on it) or `on` (the rule acts). Any other value stops the plugin's start. With them `pull_peers`, the number `CSTORE_PULL_PEERS` as read at start. The same table is in every line until the next start.
- `stamp_lead_max_s`: the largest lead over this node's wall clock, in seconds, of a version stamp the order rule took in the period (a peer's clock ahead of this one, plus transit); 0 when none led, and always with `CSTORE_ORDER` `off`.
- `handoff_pending`: the hand-off's pairs (own record, target) not settled at its last sweep; 0 with `CSTORE_HANDOFF` `off`.
- `pull_owed`: 1 while the replica holds marks a catch-up still owes (above), 0 when it holds none.

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
    - `refused_too_large`: a record over the size cap of 512 KiB on the wire (above); `refused_too_deep`: a record nested deeper than 64 levels (above); `refused_unencodable`: a record no message can carry (above). A copy of a sync answer skipped for one of them is counted under the same words with the origin `hsync`.
    - `refused_new_keys`: only with `CSTORE_NEW_KEYS_MAX` above 0, a peer's store that would create a key after its sender reached that number of new keys in the period (above).
    - with `CSTORE_ORDER` `on` (or `report`, for a hand-off re-send) only: `refused_future`, `refused_origin`, `refused_older`, `refused_older_than_delete`, `refused_unstamped` and `refused_collision`, the order rule's words (above).
  - `error`: the call raised before the replica changed (for example a bad `timeout`).
- `<bucket>/local/unconfirmed`, `<bucket>/local/overwritten` and `<bucket>/local/refused_by_peers`: counted in addition to `changed` or `identical` when a local write's wait failed: too few confirmations after the retries, another write replaced the key during the wait, or every target settled with at least one refusal answer and too few confirmations. For a `wait=False` write they are counted later, by the plugin thread's re-sends.
- `<bucket>/<origin>/ver_<word>` (`CSTORE_ORDER` `report` or `on`): a write or copy the order rule gives a word (`future`, `origin`, `older`, `older_than_delete`, `unstamped`, `collision`), counted whether or not it is refused; `<origin>` is `local`, `remote` or `hsync` (a record of a sync answer). `ver_invalid`: a stamp that is not `[int, str]`, treated as none. `<bucket>/hsync/ver_copy_delete`: a tombstone copy that removes (`on`) or would remove (`report`) a value held.
- `<bucket>/<origin>/tombstone_new`: a delete that left a new tombstone; `<origin>` also `hsync` in `on`.
- `<bucket>/confirm/disagree_own`, `<bucket>/confirm/disagree_other` (`CSTORE_LEAN_CONFIRM` `report` on the receivers): a full confirmation with a stamp that the value rule and the stamp rule judge apart (above).
- `<bucket>/<origin>/perm_readonly`, `<bucket>/<origin>/delete_foreign` (`CSTORE_ORDER` `report` or `on`): the write-permission count (above); counts only.
- `<bucket>/remote/refused_invalid_key`: a store for a `dl:` key, dropped; the bucket is that of the key after `dl:`. Under `hs:cstore_pull`, a store or delete that names the store's own catch-up record, dropped. Under the bucket of `DAUTH_JOB_SECRET_REVISIONS`, a store or delete from a registry member whose key's field part does not decode (base64 without its padding, bytes that are not UTF-8, a lone surrogate), dropped unconfirmed: the revocation rules decode that part. Under the bucket `plain`, a message dropped because a peer put something unusable where a key belongs, which no node of this release sends: `plain/remote/refused_invalid_key` for a store or delete whose key is not a non-empty str, `plain/confirm/refused_invalid_key` for a confirmation or refusal answer whose key is not a str, `plain/hsync/refused_invalid_key` for a namespace-sync request whose `hkey` holds a surrogate code point (U+D800 to U+DFFF), which UTF-8 does not encode.
- `plain/hsync/refused_malformed`, `plain/remote/refused_malformed`: a namespace-sync answer whose `request_id` is not a str, and a message whose `CHAIN_STORE_DATA` is not a dict; both dropped. Each of these six inputs used to raise out of the payload handler, which ends the plugin's lap; any count of them points at a sender that forges or corrupts messages.
- `tombstones_held`, `tombstones_dropped`: tombstones left and removed by the sweep before the line.
- `pull_h_future`: removed tombstones stamped too far ahead of the wall clock to raise the tombstone horizon (above); expected 0.
- `pull_answered`: pages this node sent in answer to catch-up requests. `pull_refused`: catch-up requests it did not answer for their form or their sender, answers of another form, and records of a page it was not asked for or may not take (above); expected 0.
- `pull_no_answer`: catch-up requests not answered within 10 s (an oracle that is away, runs the previous release, or has `CSTORE_HANDOFF` `off`).
- `pull<n>_cut_s`: seconds by which the floor of a request of series n lay above the lowest mark it asked from, the largest of the period (above).
- `<bucket>/pull<n>/missing`, `newer`, `over_legacy`, `same`, `held`, and the merge's own labels (`ver_<word>`, `refused_too_large`, `refused_too_deep`, `refused_unencodable`, `ver_copy_delete`, `tombstone_new`, `perm_readonly`, `delete_foreign`) under the origin `pull<n>`: what the pages of series n of a catch-up changed, or in `report` would change (above).
- `handoff/send/<peer>`, `handoff/resend/<peer>`, `handoff/gave_up/<peer>`, `handoff/window_full/<peer>` (`CSTORE_HANDOFF` `report` or `on`): first re-sends of a pair, later re-sends, give-ups, and (in `on` only) due pairs held back by the window, at each sweep, per target peer (in `report`, each due pair is counted at its due time, as if every re-send went unanswered). `<bucket>/handoff/answered`: a confirmation of a pair already re-sent (in `report`, a re-send `on` would have wasted).
- `<bucket>/resync/asked`, `<bucket>/resync/answered`, `<bucket>/resync/merged`: the store's own resync of a signed namespace (above): requests sent, answers taken, and records those answers inserted or replaced. `<bucket>/resync/unanswered`: asked peers whose answer was not taken when a request's wait ended (a request with none taken is asked again once). `<bucket>/resync/dropped_<word>` (`no_sender`, `not_asked`, `other_namespace`, `not_member`, `malformed`): answers to these requests the answer handler dropped, by why.
- `<bucket>/remote_non_peer`: a store received from a sender that is not in this node's replication peers, counted before any check of the write.
- `new_keys/<sender>`: keys this replica did not hold, created outside the protected namespaces by stores from a verified sender that is neither a replication peer nor in an oracle registry (above); with `CSTORE_NEW_KEYS_MAX` above 0 it is also the count the limit reads.
- `saves`: full-replica saves started in the period (a failed write is counted too, and retried by the next save). A hash-sync merge that changed the replica also leads to a save, without a write label.
- `saves_deferred` (save back-off `report` or `on`): periodic saves of a changed replica inside the back-off pause; skipped in `on`, made anyway in `report`, where `saves - saves_deferred` is what `on` would have saved.
- `save_cpu_ms` (save back-off `report` or `on`): CPU milliseconds the periodic saves took on the save thread.

A caller of `chainstore_set` or `chainstore_hset` can pass `result_info={}` to read the same word as `result_info["outcome"]` (the wait end when there is one, `not_ready` for a `wait=False` call before the store started, after its loop ended or while the persistence lock stayed held longer than 1 ms, `error` when the call raised); the returned bool is unchanged. A `not_ready` call is not counted in `counts`.

## Census line in the node log (`CSTORE_CENSUS`)

The first plugin lap after each full UTC hour of the wall clock writes one
`CSTORE_CENSUS` line (none before the first full hour after start), so every node takes
its census within a lap of the same moment whenever it started, and the digests compare
one moment's state, except for writes still in transit then. The line is a JSON object with sorted keys
and one row per bucket:

```text
CSTORE_CENSUS {"hs:<hash>:":[<records>,<unstamped>,<tombstones>,<readonly>,<size>,"<digest>",<unencodable>],"plain":[...]}
```

- `records`: live records; `unstamped`: those without a stamp (written before stamps or
  by a node of the previous release); `readonly`: those marked readonly.
- `tombstones`: tombstones of deleted keys, in the bucket of the key they deleted.
- `size`: characters of the live values' canonical JSON (sorted keys), made from each
  value as the wire delivers it: a copy from a peer already is, and the node's own record
  goes through the wire's encoder and back first (numpy and datetime values, int dict
  keys), so a writer and its receivers give equal records the same text.
- `digest`: 8 hex digits, the XOR over the live records of a 4-byte BLAKE2b hash of the
  key and the value's canonical JSON (for a record with a token, or in
  `DAUTH_JOB_SECRETS` or `DAUTH_JOB_SECRET_REVISIONS`, of the key and the stamp instead,
  so no such value is hashed into the log; other values are, the tunnel secrets
  included). Replicas that hold the same live records show the same digest whatever
  order the records arrived in; tombstones and the bookkeeping fields (confirmations,
  peer lists, owner) are left out. For records with a token and in the two dAuth secret
  namespaces the digest compares keys and stamps only: two unstamped records (or two
  with one stamp) show the same digest whatever their values, only `size` still reflects
  the values, and an equal value whose stamp was raised on one replica shows a different
  digest. Read digest and size together there.
- `unencodable`: live records whose value cannot be put in canonical form (a type or a
  dict key no encoder takes, a circular value or one nested deeper than the encoder goes,
  about 10,000 levels, which the wire could not send either, as an old own record can
  hold; the store itself now refuses a value nested deeper than 64 levels, so only a
  record held from before can be that deep); they are left out of `size` and
  `digest`, and the line is still written.
  The census does not raise on what a replica holds: records of an older shape (no
  `readonly` or `token`) and keys holding a lone surrogate are counted too.

The line holds no key and no value. Keys under `hs:` whose 14th character is not `:`
(a namespace's keys start with `hs:`, 10 hex digits and `:`) are left out. The walk runs on the plugin
thread without the persistence lock, over a copy of the replica's items, and reads every
field of a row from the record copied, so a record deleted or replaced meanwhile is still
read whole (a token record's value is never hashed). On a synthetic 5,700-record, 5.0 MB replica with
float-heavy values, half of them own records, one census took about 320 ms of CPU on the
node image's interpreter, about 200 ms before own records went through the wire's
encoder; the wall time at 0.35 CPU is about three times the CPU time.

To find the hash namespace of a bucket, compute its prefix: `"hs:"` followed by the
first 10 hex digits of the SHA-256 of the namespace name, followed by `":"`, as
`__hset_index` does. For example, in Python:

```python
import hashlib
bucket = "hs:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10] + ":"
```
