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
| `_PersistenceSerializationMixin` and `SerializationManager` | the oracle registry (every simulated node is a registered dAuth oracle) |
| signatures, encryption, decryption (SDK block engine, one key per node) | other plugins: `hogs` threads of pure Python work |

The loop follows the base plugin: one queued input per iteration, a 20 ms minimum sleep, a bounded
queue (1,024) that evicts the oldest input when full, a deep copy of each input on the plugin thread.
Signature checks and decryption run on the plugin thread; encryption and signing run on a separate
communication thread.

## Calibration

The profiles in `driver.py` are set so the **old** code reproduces what was measured in production:

| Profile | Setting | Production measurement it reproduces |
|---|---|---|
| `HEALTHY` | one other busy thread at 60 % | a full save of the 5.5 MB, 3,657-key replica takes 0.3-1.9 s |
| `STARVED` | eight busy threads, 25 ms added per fsync | the plugin loops about once every few seconds and a save takes 5-11 s |
| `STOLEN` | five busy threads and the process frozen 40 % of the time | 40 % of CPU time withheld by the host without being reported as steal |

The stuck Deeploy operation is replayed at its measured rate: a new signed 20 KB snapshot every 6.2 s
and a re-send of the unchanged snapshot every 2 s.

Which threads saturate the interpreter on a degraded oracle has not been measured yet. `STARVED`
models the cause as time lost per loop iteration; `blocking_store` models the alternative, time lost
per message. Read the results with that in mind.

## Running

Run the driver by path, so the node package is not imported:

```bash
python naeural_core/business/test_framework/cstore_sim/driver.py --list
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario incident --variant new
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario incident --variant base --base-src /path/to/older/checkout
python naeural_core/business/test_framework/cstore_sim/driver.py --scenario healthy --variant mixed --base-src /path/to/older/checkout
```

`--variant base` loads the three source files from `--base-src` (an older checkout), `new` from this
repository, and `mixed` alternates them across the fleet. `--scale 0.25` shortens a run. `--workdir`
keeps each node's log and replica.

The result is JSON: per node the loop rate, queue depth, inputs lost and handled, time the plugin
thread spent saving, local write and snapshot-sync outcomes, messages and bytes sent; plus whether
all replicas ended identical.

Set `CSTORE_SIM_MANAGER=thread` (or `off`) to run the new code without the serialization worker
process (or without the manager).

`test_cstore_sim.py` runs short scenarios as unit tests when `EE_RUN_CSTORE_SIM=1` is set (about
five minutes); add `EE_CSTORE_SIM_BASE_SRC=/path/to/older/checkout` for the mixed-fleet test.

## Limits

- The plugin base class, the capture and dispatch threads and the communication layer are
  simulated. A run inside a real node (devnet) is still needed before production.
- A saturated interpreter is modelled with busy threads. Above a few messages per second such a
  node cannot keep up with either code: every signature check and decryption gives up the
  interpreter lock and waits to get it back.
- Lost messages are not repaired unless a namespace is refreshed (`lossy` ends with different
  replicas on old and new code alike).
