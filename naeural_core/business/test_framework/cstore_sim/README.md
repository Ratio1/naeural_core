# Chain-store fleet simulator

Runs a small fleet of simulated oracles to measure how the chain store behaves under the conditions
that broke it in production: a runaway writer, a saturated interpreter, a host that withholds CPU, a
slow disk, message loss, and crashes.

## What is real and what is simulated

Each oracle is its own OS process, so each has its own interpreter lock, as in production.

| Real (loaded from the source tree under test) | Simulated |
|---|---|
| `ChainStoreBasePlugin` (all of it) | the plugin base class: queue, loop, helpers (`node.py`, `SimBase`) |
| `_NetworkProcessorMixin` (input intake, signature check, dispatch) | the transport (`driver.py`, `Hub`): delay, jitter, loss, isolation |
| `_PersistenceSerializationMixin` and the source checkout's `SerializationManager` | the oracle registry (every simulated node is a registered dAuth oracle by default) |
| signatures, encryption, decryption (SDK block engine, one key per node) | other plugins: `hogs` threads of pure Python work |

The loop follows the base plugin with a 20 ms minimum sleep and a bounded queue (1,024) that evicts
the oldest input when full. It deep-copies each input on the plugin thread. On checkouts with input
draining, the real network processor can take more inputs from the queue during the same iteration.
Signature checks and decryption run on the plugin thread; encryption and signing run on a separate
communication thread.

## Calibration

The profiles in `driver.py` are fitted so that the **old** code shows the symptoms measured in
production. They are a model, not a measurement of what loads a real oracle:

| Profile | Setting | Old code in the simulator | Production measurement |
|---|---|---|---|
| `HEALTHY` | one other busy thread at 60 % | full save 0.17-0.28 s | 0.3-1.9 s (the profile is lighter than a real healthy oracle) |
| `STARVED` | eight busy threads, 25 ms added per fsync | save 5-6 s on average, 8-11 s worst; loop every 3-5 s; queue grows; no local write confirmed | save 5.7-11.2 s; loop 0.1-0.7 per second; queue refilled; about one local write in seven confirmed |
| `STOLEN` | five busy threads and the process frozen 40 % of the time | save 3.6 s on average, 5.2 s worst | 5.7-11.2 s on the host that withholds 40 % of CPU time (the profile is lighter) |

The stuck Deeploy operation is replayed at its measured lifetime rate: a new signed 20 KB snapshot
every 6.2 s and a re-send of the unchanged snapshot every 2 s. Both code variants receive that same
stream, so the results isolate the chain store; they do not include the gain of a fixed Deeploy engine.

Which threads saturate the interpreter on a degraded oracle has not been established. `STARVED`
models the cause as time lost per loop iteration; `blocking_store` models the alternative, time lost
per message. Use the results to compare old and new code under the same conditions. Do not read
the absolute latencies or capacity margins as predictions for a real oracle.

## Running

Run the driver by path, so the node package is not imported:

```bash
python naeural_core/business/test_framework/cstore_sim/driver.py --list
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario healthy --variant new --scale 0.12
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario incident --variant new
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario incident --variant base --base-src /path/to/older/checkout
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario healthy --variant mixed --base-src /path/to/older/checkout
```

`--variant base` loads the Core source files from `--base-src` (an older checkout), `new` from this
repository, and `mixed` alternates them across the fleet. `--scale 0.25` shortens a run. `--workdir`
keeps each node's log and replica.

The JSON result contains each node's loop rate, queue depth, inputs lost and handled, save duration,
local write and snapshot-sync outcomes, messages and bytes sent, and persistence counters. It reports
convergence when every oracle reports the same workload record digests. The comparison excludes
preloaded records and the store's node-local catch-up metadata.

The current manager runs periodic saves on a dedicated thread per save. Its report records completed
background saves and write failures. If the selected source has the older worker-process manager,
`CSTORE_SIM_MANAGER=thread` or `off` selects its thread-only mode or disables that older manager.

`test_cstore_sim.py` runs short scenarios as unit tests when `EE_RUN_CSTORE_SIM=1` is set (about
five minutes); add `EE_CSTORE_SIM_BASE_SRC=/path/to/older/checkout` for the mixed-fleet test.

## Limits

- The plugin base class, the capture and dispatch threads, the communication layer, the broker
  and the oracle registry are simulated; only the chain store, its two mixins and the
  SerializationManager run. Nothing from edge_node (Deeploy, dAuth, the local API) is loaded.
  A run inside real nodes is still needed before production.
- The `crash` and `restart_sync` scenarios explicitly refresh namespaces to exercise recovery from
  missing records. Stores with a one-response refresh API rotate requests across available peers.
  The `crash` scenario enables record ordering on stores that support it. In report mode, a peer
  snapshot that was taken before a local update can still replace that update.
  The `kill_worker` events in `crash` apply to sources with a separate serialization worker process.
- Give `--workdir` a folder on a real disk: on tmpfs an fsync costs nothing.
- Each result records the hash of the source files it ran (`sources`), so a table can be tied to a commit.
- A saturated interpreter is modelled with busy threads. Above a few messages per second such a
  node cannot keep up with either code: every signature check and decryption gives up the
  interpreter lock and waits to get it back.
- With loss or isolation, convergence depends on which records reached peers and which namespaces
  the scenario refreshes before it ends.
