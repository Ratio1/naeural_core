"""Offline tests of the chain store's write counting, writes that change nothing, the start-up file check and hlist."""
import collections
import copy
import json
import os
import pickle
import tempfile
import threading
import unittest
from datetime import datetime as _datetime, timezone as _timezone
from types import SimpleNamespace

from naeural_core.main.serialization_manager import SerializationManager

from test_chain_store_hsync import (
  _ChainStoreRuntimeHarness, _PeerSelectionHarness, _SummaryControlNetmon, _WhitelistBlockEngine, _hset_index, _hset_key,
)

DIRTY = "_ChainStoreBasePlugin__chain_storage_dirty"
STATS = "_ChainStoreBasePlugin__cs_stats"
OPS = "_ChainStoreBasePlugin__ops"
BUCKET = _hset_index("players")  # "hs:" + 10 hex digits + ":", 14 characters


def make_store():
  store = _ChainStoreRuntimeHarness("node", default_peers=["peer1", "peer2"])
  setattr(store, OPS, collections.deque())
  return store


def make_signed_store():
  """A store whose block engine accepts records signed "valid", for the namespaces that check signatures."""
  store = make_store()
  store.get_instance_path = lambda: ["node", "admin_pipeline", "CHAIN_STORE", "instance"]
  store.bc = SimpleNamespace(
    node_address_to_eth_address=lambda address: address,
    get_eth_dauth_oracles=lambda: ["node", "peer1"],
    get_eth_oracles=lambda: ["oracle"],
    verify=lambda value, **kwargs: SimpleNamespace(valid=value.get("EE_SIGN") == "valid"),
  )
  return store


def seed(store, key, value, owner="node", readonly=False):
  store.storage[key] = {
    store.CS_KEY: key, store.CS_VALUE: value, store.CS_OWNER: owner, store.CS_READONLY: readonly,
    store.CS_TOKEN: None, store.CS_CONFIRMATIONS: 7, store.CS_MIN_CONFIRMATIONS: 9,
    "confirmed_peers": ["peer1"], "target_peers": ["old-peer"],
  }


class _CountedStr:
  """A value that counts how often it is turned into a string."""

  def __init__(self):
    self.calls = 0

  def __str__(self):
    self.calls += 1
    return "x" * 30


class _HlistHarness(_PeerSelectionHarness):
  """The plugin API over a store that gains a key while the caller walks it (as the chain-store thread does)."""

  get_hash = staticmethod(_ChainStoreRuntimeHarness.get_hash)

  def __init__(self, storage):
    super().__init__()
    self.plugins_shmem["__chain_storage"] = storage

  def base64_to_str(self, value, url_safe=True):
    storage = self.plugins_shmem["__chain_storage"]
    storage["written-meanwhile-{}".format(len(storage))] = {}
    return _ChainStoreRuntimeHarness.base64_to_str(value, url_safe=url_safe)


class _PluginBase:
  """Stands for the plugin classes above the network processor; their on_init does nothing here."""

  def on_init(self):
    return


class _StartHarness(_ChainStoreRuntimeHarness, _PluginBase):
  """The chain store with what on_init needs: a data folder, a cache folder, a pickle loader and plugin memory."""

  deque = collections.deque
  datetime = _datetime
  timezone = _timezone

  def __init__(self, data_folder):
    super().__init__("node")
    self.plugins_shmem = {}
    # on_init must create the counter state itself: drop what the shared harness prepared.
    for name in ("cs_stats", "cs_stats_last", "cs_stats_queue_peak", "cs_stats_lost"):
      delattr(self, "_ChainStoreBasePlugin__" + name)
    self.log = SimpleNamespace(get_data_folder=lambda: data_folder)
    self._persistence_fld = "/chain_store"
    self.replica_file = os.path.join(data_folder, "chain_store", "object.pickle")
    self.lines = []

  def P(self, msg, color=None, **kwargs):  # pylint: disable=unused-argument
    self.lines.append((msg, color))

  def cacheapi_load_pickle(self, default=None, verbose=True):  # pylint: disable=unused-argument
    # As the logger does: a file that cannot be read gives nothing, and the default replaces an empty result.
    try:
      with open(self.replica_file, "rb") as stream:
        return pickle.load(stream) or default
    except Exception:  # noqa: BLE001
      return default


class TestHlist(unittest.TestCase):
  def test_hlist_walks_a_copy_of_the_keys_while_the_store_changes(self):
    storage = {_hset_key("players", "alpha"): {}, "plain": {}, _hset_key("players", "beta"): {}}
    harness = _HlistHarness(storage)
    self.assertEqual(sorted(harness.chainstore_hlist("players")), ["alpha", "beta"])
    self.assertEqual(len(storage), 5)  # two keys were added during the walk


class TestCheaperWrites(unittest.TestCase):
  def test_value_is_not_turned_into_a_string_when_debug_is_off(self):
    store = make_store()
    value = _CountedStr()
    self.assertTrue(store._set_value("key", value, owner="node", local_sync_storage_op=True))
    self.assertEqual(value.calls, 0)

  def test_debug_line_still_shows_the_shortened_value(self):
    store = make_store()
    self.assertTrue(store._set_value("key", "y" * 30, owner="node", local_sync_storage_op=True, debug=True))
    self.assertTrue(any("<key> = <{}...>".format("y" * 20) in msg for msg in store.messages), store.messages)

  def test_target_peers_are_a_new_list_made_without_a_deep_copy(self):
    store = make_store()
    copies = []
    store.deepcopy = lambda value: copies.append(value) or copy.deepcopy(value)
    peers = store._ChainStoreBasePlugin__get_target_peers()
    peers.append("peer3")
    self.assertEqual(peers, ["peer1", "peer2", "peer3"])
    self.assertEqual(store._ChainStoreBasePlugin__chain_peers, ["peer1", "peer2"])
    self.assertEqual(copies, [])


class TestWritesThatChangeNothing(unittest.TestCase):
  def test_identical_local_write_is_not_saved_but_is_broadcast_and_confirmed_again(self):
    store = make_store()
    seed(store, "key", "value")
    store._set_value("key", "value", owner="node", timeout=.1, max_retries=0)
    self.assertFalse(getattr(store, DIRTY))
    self.assertEqual(len(getattr(store, OPS)), 1)
    record = store.storage["key"]
    self.assertEqual(record[store.CS_CONFIRMATIONS], 0)
    self.assertEqual(record["confirmed_peers"], [])
    self.assertEqual(record["target_peers"], ["peer1", "peer2"])
    self.assertEqual(getattr(store, STATS), {"plain/local/identical": 1})

  def test_same_value_from_another_owner_or_with_another_readonly_is_saved(self):
    for owner, readonly in (("other", False), ("node", True)):
      with self.subTest(owner=owner, readonly=readonly):
        store = make_store()
        seed(store, "key", "value")
        store._set_value("key", "value", owner=owner, readonly=readonly, timeout=.1, max_retries=0)
        self.assertTrue(getattr(store, DIRTY))
        self.assertEqual(getattr(store, STATS), {"plain/local/changed": 1})

  def test_a_resent_signed_record_is_compared_by_the_owner_and_readonly_its_namespace_gives_it(self):
    # A coordinator sends an unchanged record again as a repair. The caller passes its own owner and readonly;
    # the namespace rules replace both before the comparison, and the comparison must use those.
    cases = (
      ("DEEPLOY_OPERATIONS", "op|1", False,
       {"operation_id": "op", "sequence": 9, "coordinator": "oracle", "EE_SENDER": "oracle", "EE_SIGN": "valid"}),
      ("DEEPLOY_NODE_OPERATIONS", "op|deploy|worker|1", False,
       {"operation_id": "op", "attempt": "deploy", "sequence": 9, "node_addr": "worker", "EE_SENDER": "worker",
        "EE_SIGN": "valid"}),
      ("DEEPLOY_DEPLOYED_JOBS", "7", True,
       {"job_id": "7", "revision": 2, "operation_id": "op", "coordinator": "oracle", "EE_SENDER": "oracle",
        "EE_SIGN": "valid", "pipeline_cid": "cid", "deleted": False}),
    )
    for namespace, field, caller_readonly, value in cases:
      with self.subTest(namespace=namespace):
        store = make_signed_store()
        key = _hset_key(namespace, field)
        self.assertTrue(store._set_value(key, dict(value), owner="x", local_sync_storage_op=True))
        setattr(store, DIRTY, False)
        store._set_value(key, dict(value), readonly=caller_readonly, timeout=.1, max_retries=0)
        self.assertFalse(getattr(store, DIRTY))
        self.assertEqual(len(getattr(store, OPS)), 1)  # still sent to the peers

  def test_changed_value_is_saved(self):
    store = make_store()
    seed(store, "key", "value")
    store._set_value("key", "other", owner="node", timeout=.1, max_retries=0)
    self.assertTrue(getattr(store, DIRTY))
    self.assertEqual(getattr(store, STATS), {"plain/local/changed": 1})

  def test_remote_delete_of_an_absent_key_is_confirmed_and_not_saved(self):
    store = make_store()
    self.assertTrue(store._set_value("absent", None, owner="peer1", local_sync_storage_op=True))
    self.assertFalse(getattr(store, DIRTY))
    self.assertEqual(getattr(store, STATS), {"plain/remote/absent_delete": 1})

  def test_local_delete_of_an_absent_key_is_broadcast_and_not_saved(self):
    store = make_store()
    self.assertTrue(store._set_value("absent", None, owner="node", timeout=.1, max_retries=0))
    self.assertFalse(getattr(store, DIRTY))
    self.assertEqual([op[store.CS_VALUE] for op in getattr(store, OPS)], [None])
    self.assertEqual(getattr(store, STATS), {"plain/local/absent_delete": 1})

  def test_delete_of_a_held_key_is_saved(self):
    store = make_store()
    seed(store, "key", "value")
    self.assertTrue(store._set_value("key", None, owner="node", timeout=.1, max_retries=0))
    self.assertNotIn("key", store.storage)
    self.assertTrue(getattr(store, DIRTY))
    self.assertEqual(getattr(store, STATS), {"plain/local/changed": 1})


class TestCounters(unittest.TestCase):
  def test_each_write_is_counted_once_by_bucket_origin_and_outcome(self):
    store = make_store()
    hkey = _hset_key("players", "alpha")
    self.assertTrue(store._set_value(hkey, "v", owner="peer1", local_sync_storage_op=True))
    self.assertTrue(store._set_value(hkey, "v", owner="peer1", local_sync_storage_op=True))
    store._set_value("key", "v", owner="node", timeout=.1, max_retries=0)
    store._set_value("key", "v", owner="node", timeout=.1, max_retries=0)
    store._set_value("gone", None, owner="node", timeout=.1, max_retries=0)
    store._set_value("gone", None, owner="peer1", local_sync_storage_op=True)
    self.assertFalse(store._set_value("key", "w", owner="peer1", token="secret", local_sync_storage_op=True))
    self.assertFalse(store._set_value("key", "w", owner="node", include_default_peers=False))
    self.assertEqual(getattr(store, STATS), {
      BUCKET + "/remote/changed": 1, BUCKET + "/remote/identical": 1,
      "plain/local/changed": 1, "plain/local/identical": 1, "plain/local/absent_delete": 1,
      "plain/remote/absent_delete": 1, "plain/remote/refused": 1, "plain/local/refused": 1,
    })

  def test_a_write_that_raises_is_counted_once_by_what_it_did_to_the_replica(self):
    store = make_store()
    with self.assertRaises(ValueError):
      store._set_value("key", "v", owner="node", timeout=-1)  # raises before the store: refused
    self.assertEqual(getattr(store, STATS), {"plain/local/refused": 1})
    def failing_sleep(seconds):
      raise RuntimeError("stopped")
    store.sleep = failing_sleep
    with self.assertRaises(ValueError):
      store._set_value("key", "v", owner="node")  # raises after the store, while waiting: the replica changed
    self.assertEqual(getattr(store, STATS), {"plain/local/refused": 1, "plain/local/changed": 1})

  def test_stores_from_senders_outside_the_peers_are_counted(self):
    store = make_store()
    store.get_instance_path = lambda: "node-instance"  # named in the confirmation sent back
    data = {store.CS_KEY: _hset_key("players", "alpha"), store.CS_VALUE: "v", store.CS_OWNER: "x"}
    store._ChainStoreBasePlugin__exec_store(dict(data, value="w"), peers="peer1")
    self.assertEqual(getattr(store, STATS), {BUCKET + "/remote/changed": 1})  # a peer is not counted apart
    store._ChainStoreBasePlugin__exec_store(dict(data), peers="stranger")
    self.assertEqual(getattr(store, STATS), {
      BUCKET + "/remote_non_peer": 1, BUCKET + "/remote/changed": 2,
    })
    self.assertEqual([sent["node_addr"] for sent in store.sent_payloads], [["peer1"], ["stranger"]])

  def test_each_written_save_is_counted(self):
    store = make_store()
    flush = store._ChainStoreBasePlugin__maybe_flush_chain_storage
    store._set_value("key", "v", owner="node", local_sync_storage_op=True)
    flush()
    flush()  # nothing changed: nothing written, nothing counted
    self.assertEqual(getattr(store, STATS).get("saves"), 1)
    store._set_value("key2", "v", owner="node", local_sync_storage_op=True)
    flush()
    self.assertEqual(getattr(store, STATS).get("saves"), 2)
    self.assertEqual(len(store.saved_storage), 2)

  def test_stats_line_is_written_once_per_interval_and_resets_the_counters(self):
    store = make_store()
    store.bc = _WhitelistBlockEngine(["peer1", "peer2"])
    store.netmon = _SummaryControlNetmon(online_for_control={"peer1", "peer2"})
    store._set_value("key", "v", owner="peer1", local_sync_storage_op=True)
    store.input_queue_size, store._now = 7, 100
    store.process()
    store.input_queue_size, store.lost_inputs_count = 3, 4
    store._now = store.CS_STATS_INTERVAL - 0.1
    store.process()
    self.assertEqual([msg for msg in store.messages if msg.startswith("CSTORE_STATS ")], [])
    store._now = store.CS_STATS_INTERVAL
    store.process()
    store.process()
    lines = [msg for msg in store.messages if msg.startswith("CSTORE_STATS ")]
    self.assertEqual(len(lines), 1)
    self.assertNotIn("\n", lines[0])
    stats = json.loads(lines[0][len("CSTORE_STATS "):])
    self.assertEqual(list(stats), sorted(stats))
    self.assertEqual(stats, {
      "period_s": 300, "counts": {"plain/remote/changed": 1}, "queue_peak": 7, "lost_inputs": 4,
    })
    self.assertEqual(getattr(store, STATS), {})
    # The next interval: a line also when nothing was counted, measured from the previous line.
    store._now = 2 * store.CS_STATS_INTERVAL
    store.process()
    lines = [msg for msg in store.messages if msg.startswith("CSTORE_STATS ")]
    self.assertEqual(len(lines), 2)
    self.assertEqual(json.loads(lines[1][len("CSTORE_STATS "):]), {
      "period_s": 300, "counts": {}, "queue_peak": 3, "lost_inputs": 0,
    })


class TestStart(unittest.TestCase):
  def setUp(self):
    folder = tempfile.TemporaryDirectory()
    self.addCleanup(folder.cleanup)
    self.data_folder = folder.name
    self.cache_folder = os.path.join(self.data_folder, "chain_store")
    os.makedirs(self.cache_folder)
    self.registered = []
    previous = SerializationManager.instance
    SerializationManager.instance = SimpleNamespace(register=lambda save, every: self.registered.append(every))
    self.addCleanup(setattr, SerializationManager, "instance", previous)

  def start(self):
    store = _StartHarness(self.data_folder)
    store.on_init()
    return store

  def test_an_unreadable_file_is_kept_aside_and_the_store_starts_empty(self):
    with open(os.path.join(self.cache_folder, "object.pickle"), "wb") as stream:
      stream.write(b"not a pickle")
    store = self.start()
    self.assertEqual(store.storage, {})
    names = os.listdir(self.cache_folder)
    self.assertEqual(len(names), 1)
    self.assertRegex(names[0], r"^object\.pickle\.unreadable-\d{14}$")
    with open(os.path.join(self.cache_folder, names[0]), "rb") as stream:
      self.assertEqual(stream.read(), b"not a pickle")
    red = [msg for msg, color in store.lines if color == "r"]
    self.assertEqual(len(red), 1)
    self.assertIn(names[0], red[0])
    self.assertEqual(self.registered, [store.CS_SAVE_INTERVAL])

  def test_a_file_holding_an_empty_store_is_kept_aside_too(self):
    with open(os.path.join(self.cache_folder, "object.pickle"), "wb") as stream:
      pickle.dump({}, stream)
    self.start()
    names = os.listdir(self.cache_folder)
    self.assertEqual(len(names), 1)
    self.assertRegex(names[0], r"^object\.pickle\.unreadable-\d{14}$")

  def test_a_store_taken_over_from_a_previous_instance_keeps_the_file(self):
    # A restart of the plugin inside the same process: the live store is in the shared memory. Whatever the
    # load gave, the file is not moved aside while that store holds records; its next save writes the file.
    with open(os.path.join(self.cache_folder, "object.pickle"), "wb") as stream:
      stream.write(b"not a pickle")
    live = {"key": {"key": "key", "value": "v", "owner": "node", "readonly": False, "token": None}}
    store = _StartHarness(self.data_folder)
    store.plugins_shmem[store.CS_STORAGE_MEM] = live
    store.on_init()
    self.assertIs(store.storage, live)
    self.assertEqual(os.listdir(self.cache_folder), ["object.pickle"])
    self.assertEqual([msg for msg, _ in store.lines if "unreadable" in msg], [])

  def test_a_started_store_counts_its_writes_and_writes_its_line(self):
    # The counter state comes from on_init here, not from the test harness.
    store = self.start()
    self.assertTrue(store._set_value("key", "v", owner="peer1", local_sync_storage_op=True))
    self.assertEqual(getattr(store, STATS), {"plain/remote/changed": 1})
    store.bc = _WhitelistBlockEngine(["peer1"])
    store.netmon = _SummaryControlNetmon(online_for_control={"peer1"})
    store._now = store.CS_STATS_INTERVAL
    store.process()
    lines = [msg for msg, _ in store.lines if msg.startswith("CSTORE_STATS ")]
    self.assertEqual(json.loads(lines[0][len("CSTORE_STATS "):]), {
      "period_s": 300, "counts": {"plain/remote/changed": 1}, "queue_peak": 0, "lost_inputs": 0,
    })

  def test_a_save_of_a_restarted_instance_waits_for_a_save_of_the_closed_one(self):
    # Two started instances write the same file, each save on its own thread. The older snapshot must not
    # land after the newer one.
    old, new = self.start(), self.start()
    writing, release, order = threading.Event(), threading.Event(), []

    def slow_write(snapshot, **kwargs):  # pylint: disable=unused-argument
      writing.set()
      release.wait(5)
      order.append("old")

    old.cacheapi_save_pickle = slow_write
    new.cacheapi_save_pickle = lambda snapshot, **kwargs: order.append("new")
    for store in (old, new):
      setattr(store, DIRTY, True)
    late = threading.Thread(target=old._ChainStoreBasePlugin__maybe_flush_chain_storage)
    late.start()
    self.assertTrue(writing.wait(5))
    newer = threading.Thread(target=new._ChainStoreBasePlugin__maybe_flush_chain_storage)
    newer.start()
    newer.join(0.2)
    self.assertTrue(newer.is_alive())  # it waits for the older save instead of writing past it
    release.set()
    late.join(5)
    newer.join(5)
    self.assertEqual(order, ["old", "new"])

  def test_no_file_renames_nothing_and_logs_nothing_red(self):
    store = self.start()
    self.assertEqual(store.storage, {})
    self.assertEqual(os.listdir(self.cache_folder), [])
    self.assertEqual([msg for msg, color in store.lines if color == "r"], [])

  def test_a_good_file_is_loaded_and_kept(self):
    record = {"key": "key", "value": "v", "owner": "node", "readonly": False, "token": None}
    with open(os.path.join(self.cache_folder, "object.pickle"), "wb") as stream:
      pickle.dump({"key": record}, stream)
    store = self.start()
    self.assertEqual(store.storage, {"key": record})
    self.assertEqual(os.listdir(self.cache_folder), ["object.pickle"])
    self.assertEqual([msg for msg, color in store.lines if color == "r"], [])


if __name__ == "__main__":
  unittest.main()
