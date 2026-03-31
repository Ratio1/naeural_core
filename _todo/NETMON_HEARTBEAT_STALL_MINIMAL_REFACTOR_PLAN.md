# NetMon Heartbeat Stall Minimal Refactor Plan

Date: `2026-03-27`

## Goal
Minimally refactor `NetworkMonitor` and `EpochsManager` so recurrent maintenance work does not stall heartbeat consumption on the edge node.

Target operations to de-risk:
- `NetworkMonitor.network_save_status()`
- `EpochsManager.save_status()`
- `EpochsManager.maybe_update_cached_data()`

Non-goal:
- redesigning the heartbeat data model
- changing heartbeat payload shape
- changing `NetworkMonitor.register_heartbeat(...)` semantics
- changing broker topics, QoS, or transport behavior

## Problem Summary
Heartbeat consumption currently runs through the command/control comm thread:
- [commandcontrol_comm_mixin.py](/workspaces/naeural_core/naeural_core/comm/mixins/commandcontrol_comm_mixin.py#L58) pulls one message from the recv buffer
- if the decoded payload is a heartbeat, it calls [NetworkMonitor.register_heartbeat(...)](/workspaces/naeural_core/naeural_core/main/net_mon.py#L949)

That registration path blocks on two mutexes:
- `NETMON_MUTEX` inside [NetworkMonitor.__register_heartbeat(...)](/workspaces/naeural_core/naeural_core/main/net_mon.py#L360)
- `EPOCHMON_MUTEX` inside [EpochsManager.register_data(...)](/workspaces/naeural_core/naeural_core/main/epochs_manager.py#L1199)

Recurrent maintenance code also uses those same mutexes:
- [NetworkMonitor.network_save_status()](/workspaces/naeural_core/naeural_core/main/net_mon.py#L1410) holds `NETMON_MUTEX` while pickling `db.pkl`, then calls `epoch_manager.save_status()`
- [EpochsManager.save_status()](/workspaces/naeural_core/naeural_core/main/epochs_manager.py#L379) holds `EPOCHMON_MUTEX` while deep-copying and pickling `epochs_status.pkl`
- [EpochsManager.maybe_update_cached_data()](/workspaces/naeural_core/naeural_core/main/epochs_manager.py#L1877) can hold `EPOCHMON_MUTEX` while deep-copying the full epoch dataset

This means long-running save/cache work stalls the same comm thread that consumes heartbeats.

## Current High-Confidence Findings

### `NETMON_MUTEX` stalls heartbeats before netmon append
- [NetworkMonitor.__register_heartbeat(...)](/workspaces/naeural_core/naeural_core/main/net_mon.py#L360) acquires `NETMON_MUTEX` before appending the heartbeat to `__network_heartbeats`
- [NetworkMonitor.network_save_status()](/workspaces/naeural_core/naeural_core/main/net_mon.py#L1412) holds that same mutex during the network status pickle save

Effect:
- while `network_save_status()` runs, the current heartbeat cannot enter netmon yet
- the command/control thread is blocked in `register_heartbeat(...)`
- subsequent control-topic messages are not drained from `_recv_buff`

### `EPOCHMON_MUTEX` stalls heartbeats after netmon append
- [NetworkMonitor.register_heartbeat(...)](/workspaces/naeural_core/naeural_core/main/net_mon.py#L949) calls `epoch_manager.register_data(...)` only after `__register_heartbeat(...)` returns
- [EpochsManager.register_data(...)](/workspaces/naeural_core/naeural_core/main/epochs_manager.py#L1199) then acquires `EPOCHMON_MUTEX`

Effect:
- the current heartbeat is already visible in netmon
- but the same comm thread still blocks before it can process later messages

### Supervisor and non-supervisor nodes stall differently
- supervisors call [netmon.network_save_status()](/workspaces/naeural_core/naeural_core/business/default/admin/net_mon_01.py#L330)
- non-supervisors call [epoch_manager.save_status()](/workspaces/naeural_core/naeural_core/business/default/admin/net_mon_01.py#L323)

Effect:
- supervisor save path blocks both `NETMON_MUTEX` and `EPOCHMON_MUTEX`
- non-supervisor save path blocks only `EPOCHMON_MUTEX`

### Current lock ordering is stall-prone but not obviously deadlocking
Observed ordering:
- normal heartbeat path: `NETMON_MUTEX` first, then later `EPOCHMON_MUTEX`
- supervisor save path: `NETMON_MUTEX`, then nested `epoch_manager.save_status()` which takes `EPOCHMON_MUTEX`

I did not find the opposite live path `EPOCHMON_MUTEX -> NETMON_MUTEX` in heartbeat processing, so the current risk is primarily long stall, not an obvious mutex deadlock.

## Minimal Refactor Strategy
Do not move heartbeat registration off-thread.

Instead:
1. keep the write-side critical sections small
2. capture immutable snapshots under lock
3. perform expensive `deepcopy(...)`, pickle serialization, and cache rebuild work outside the heartbeat-critical lock

This is the smallest change that addresses the stall without altering message flow or public API semantics.

## Proposed Changes

### 1. Refactor `NetworkMonitor.network_save_status()`
Current behavior:
- take `NETMON_MUTEX`
- pickle `self.__network_heartbeats`
- call `epoch_manager.save_status()` while still inside the netmon save flow

Recommended minimal change:
1. under `NETMON_MUTEX`, capture a shallow snapshot of the current heartbeat map structure
2. release `NETMON_MUTEX`
3. outside the lock, build the serializable copy and save `db.pkl`
4. call `epoch_manager.save_status()` outside `NETMON_MUTEX`

Recommended implementation detail:
- add a helper like `NetworkMonitor._snapshot_network_heartbeats_for_save()`
- that helper should:
  - acquire `NETMON_MUTEX`
  - build a lightweight snapshot such as:
    - `dict[str, list[dict]]` using `list(deque_obj)` per node
  - release `NETMON_MUTEX`
- the expensive serialization and file write happen after the helper returns

Why this is enough:
- converting each node deque to a list is much cheaper than performing the full pickle write under lock
- the heartbeat append path only waits for the snapshot copy, not the whole disk write

Acceptable semantic tradeoff:
- saved `db.pkl` may lag a few heartbeats behind reality
- that is already acceptable for persistence; it is not the source of truth for the live node

### 2. Refactor `EpochsManager.save_status()`
Current behavior:
- take `EPOCHMON_MUTEX`
- mutate save metadata
- trim history
- `deepcopy(self.__full_data)`
- pickle the copy while still in the save flow

Recommended minimal change:
1. under `EPOCHMON_MUTEX`, update save metadata and build a bounded snapshot object
2. release `EPOCHMON_MUTEX`
3. serialize and write `epochs_status.pkl` outside the lock

Recommended implementation detail:
- add a helper like `EpochsManager._snapshot_full_data_for_save()`
- inside that helper:
  - acquire `EPOCHMON_MUTEX`
  - update `SYNC_SAVES_TS` / `SYNC_SAVES_EP`
  - call `__trim_history()`
  - create the copy that should be persisted
  - release `EPOCHMON_MUTEX`
- file write happens afterward

Why this is still minimal:
- no change to epoch math
- no change to save cadence
- no change to file format
- only lock scope is reduced

### 3. Refactor `EpochsManager.maybe_update_cached_data()`
Current behavior:
- optionally acquires `EPOCHMON_MUTEX`
- deep-copies the whole epoch data while holding the lock

Recommended minimal change:
1. treat cache refresh as best-effort secondary work
2. stop treating normal heartbeat churn as cache-relevant invalidation
3. track only cache-relevant dirty state
4. under `EPOCHMON_MUTEX`, snapshot only the compact cache view for the dirty entries
5. release `EPOCHMON_MUTEX`
6. atomically merge the refreshed entries into `self.cached_data`

Recommended implementation detail:
- add a compact cache representation for each node containing only:
  - `EPCT.EPOCHS`
  - optionally `EPCT.NAME`
- do not copy hot heartbeat-path fields into `cached_data`, including:
  - `CURRENT_EPOCH`
  - `LAST_EPOCH`
  - `LOCAL_EPOCHS`
  - `FIRST_SEEN` / `LAST_SEEN`
  - `CURR_*`
  - alert counters and gap-error deques
- add a dirty set keyed by node address for cache-relevant mutations
- mark a node dirty only when data used by cached readers changes, mainly:
  - `EPCT.EPOCHS`
  - optionally first node initialization / visible name introduction
- do not mark nodes dirty for ordinary heartbeat updates that only change current-epoch live state
- if lock acquisition for cache refresh would still block, skip the refresh cycle and try again later
- `force=False`: use a non-blocking or immediate-fail lock attempt
- `force=True`: allow only a very short bounded wait or bounded retry before skipping; heartbeat ingestion still has priority

Important note:
- this cache is secondary
- staleness by one refresh interval is preferable to blocking heartbeat ingestion
- with 10-second heartbeats, marking every active node dirty on each heartbeat would erase most of the benefit
- the dirty signal must follow epoch-history mutations, not heartbeat arrival itself

Additional follow-up note:
- `get_node_epochs(..., autocomplete=True)` currently mutates the cached epoch mapping
- if incremental cache refresh is implemented, that read-side mutation should be removed or contained to avoid cache drift outside the refresh path

### 4. Keep `register_heartbeat(...)` synchronous
Do not change:
- [NetworkMonitor.register_heartbeat(...)](/workspaces/naeural_core/naeural_core/main/net_mon.py#L949)
- [EpochsManager.register_data(...)](/workspaces/naeural_core/naeural_core/main/epochs_manager.py#L1166)

Reason:
- this is the smallest safe patch
- it avoids introducing worker threads, task queues, or shutdown ordering changes
- it preserves existing netmon and epoch semantics

The point of this plan is not to remove all heartbeat-path work.
It is to remove avoidable long lock holds from non-heartbeat maintenance paths.

## Locking Rules After Refactor

### Rule 1
`NETMON_MUTEX` should protect:
- mutation of `__network_heartbeats`
- mutation of other netmon-owned write-side structures tightly coupled to heartbeat registration

It should not cover:
- pickle serialization
- disk I/O
- epoch manager save

### Rule 2
`EPOCHMON_MUTEX` should protect:
- mutation of epoch state
- extraction of a consistent save/cache snapshot

It should not cover:
- disk I/O
- large cache-copy work when that work can be done from a stable extracted snapshot

### Rule 3
Do not introduce new nested locking in the heartbeat path.

Preferred heartbeat ordering remains:
1. short `NETMON_MUTEX` section
2. no netmon lock held
3. short `EPOCHMON_MUTEX` section

## Suggested Implementation Steps
1. Add `NetworkMonitor._snapshot_network_heartbeats_for_save()`
2. Rewrite `network_save_status()` to:
   - snapshot under `NETMON_MUTEX`
   - save `db.pkl` outside the lock
   - call `epoch_manager.save_status()` outside the netmon lock
3. Add `EpochsManager._snapshot_full_data_for_save()`
4. Rewrite `save_status()` to save from the snapshot outside `EPOCHMON_MUTEX`
5. Add `EpochsManager._snapshot_data_for_cache_refresh()`
6. Rewrite `maybe_update_cached_data()` to refresh cache from a snapshot outside `EPOCHMON_MUTEX`
7. Add targeted tests for lock-scope behavior and heartbeat-progress behavior

## Verification Plan

### Required minimum
- `python -m compileall naeural_core/main/net_mon.py naeural_core/main/epochs_manager.py naeural_core/business/default/admin/net_mon_01.py`

### Targeted evidence to add
- a repro or test showing that heartbeat registration can progress while save/cache work is running
- supervisor path:
  - one thread repeatedly calls `register_heartbeat(...)`
  - another triggers `network_save_status()`
  - verify heartbeat count continues increasing during save
- non-supervisor path:
  - one thread repeatedly calls `register_heartbeat(...)`
  - another triggers `epoch_manager.save_status()` and `maybe_update_cached_data(force=True)`
  - verify heartbeat count continues increasing with at most short snapshot-copy pauses

### Optional but valuable
- record before/after timings for:
  - time spent inside `NETMON_MUTEX`
  - time spent inside `EPOCHMON_MUTEX`
  - time to persist `db.pkl`
  - time to persist `epochs_status.pkl`

## Risks and Non-goals
- snapshotting still costs time; this plan reduces lock hold time, not total CPU cost
- shallow snapshotting must not leak mutable references that later mutate during serialization
- if the snapshot helper is implemented with a full `deepcopy(...)` under lock, the plan fails its goal
- this plan does not fix:
  - bounded recv-buffer overflow under extreme traffic
  - unlocked read-side netmon iteration races
  - long epoch-close recomputation stalls inside `maybe_close_epoch()`
  - cache reader mutation of cached epoch maps

## Follow-up Work Explicitly Deferred
- actor-style async persistence worker for netmon and epoch saves
- read/write lock or snapshot-reader model for netmon queries
- pruning stale nodes from `__network_heartbeats` / `__nodes_pipelines`
- decoupling epoch registration from the comm thread
- changing heartbeat transport topology

## Acceptance Criteria
This refactor is successful if:
- `register_heartbeat(...)` no longer waits for the full duration of `db.pkl` writes
- `register_heartbeat(...)` no longer waits for the full duration of `epochs_status.pkl` writes
- `maybe_update_cached_data()` no longer holds `EPOCHMON_MUTEX` for the full deep-copy duration
- file formats and public netmon/epoch APIs remain unchanged
- supervisor and non-supervisor runtime behavior remains semantically equivalent apart from reduced heartbeat-consumption stalls
