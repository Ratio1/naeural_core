"""
Offline tests of the chain store's write counting, writes that change nothing, the start-up file check and hlist,
and of the senders it takes the namespaces kept by oracles from.
"""
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

# The harness of the hsync tests, with its fake network and the helpers that compose hash keys and records.
from test_chain_store_hsync import (
  _ChainStoreRuntimeHarness, _PeerSelectionHarness, _SummaryControlNetmon, _WhitelistBlockEngine, _hset_index, _hset_key,
  _FakeNetwork, _hset_record,
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


# The two Tunnels Manager namespaces, kept by oracles only.
TUNNELS = ("tunnels_manager_secrets", "tunnels_manager_tcp_routes")
# The pointer of job 7 at revision 1, signed (EE_SIGN "valid") by its coordinator "oracle", which is in the oracle
# registry of make_signed_store.
SIGNED_POINTER = {
  "job_id": "7", "revision": 1, "operation_id": "op", "coordinator": "oracle", "EE_SENDER": "oracle",
  "EE_SIGN": "valid", "pipeline_cid": "cid-7", "deleted": False,
}


def make_signed_peer(address, records):
  """
  Build the chain store of another node, with the registry of ``make_signed_store`` and the given records.

  The store is the one ``make_signed_store`` builds (dAuth registry ``node`` and ``peer1``, oracle registry
  ``oracle``, chain peers ``peer1`` and ``peer2``), answering from ``address``.

  Parameters
  ----------
  address : str
    Address of the node: the fake network delivers to it and its answers are sent from it.
  records : dict
    Chain-store records (composed key -> record) the node holds and answers a sync request with.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store, ready to be registered on a ``_FakeNetwork``.

  Examples
  --------
  >>> network.register(make_signed_peer("oracle", {key: record}))
  """
  # The store and registry make_signed_store builds.
  store = make_signed_store()
  # The address the fake network delivers to and the answers are sent from.
  store.ee_addr = address
  # The records this node holds.
  store.storage.update(records)
  # The store, for the caller to register on its network.
  return store


def remote_store(store, key, value, sender):
  """
  Deliver one remote store of ``key`` = ``value`` from ``sender`` to ``store``, as its payload handler does.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The receiving chain store.
  key : str
    Composed chain-store key of the write.
  value : any
    Value of the write; ``None`` for a delete.
  sender : str
    Verified address of the sending node; also the owner the write names.

  Returns
  -------
  list
    The recipients of each confirmation the store sent back for this write, in order: ``[[sender]]`` when the
    write was applied, ``[]`` when it was refused.

  Examples
  --------
  >>> remote_store(make_signed_store(), _hset_key("players", "alpha"), "v", "peer2")
  [['peer2']]
  """
  # Payloads sent before this write, so only its own confirmations are returned.
  before = len(store.sent_payloads)
  # The store as default_handler passes it: the decrypted operation and the payload's sender.
  store._ChainStoreBasePlugin__exec_store(
    {store.CS_KEY: key, store.CS_VALUE: value, store.CS_OWNER: sender}, peers=sender,
  )
  # The recipients of what was sent since.
  return [sent["node_addr"] for sent in store.sent_payloads[before:]]


def share_index(store, alias, hkey):
  """
  Give ``alias`` the index of ``hkey`` on ``store``, as two names whose hashes start with the same 10 hex digits.

  Such a pair cannot be computed in a test, so the store's index hash (``get_hash``, which ``__hset_index`` calls)
  is patched on this store only: ``alias`` hashes as ``hkey``, every other name as before.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The chain store whose index hash is patched.
  alias : str
    The other hkey name, which gets the index of ``hkey``.
  hkey : str
    The namespace whose index ``alias`` gets.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The same store.

  Examples
  --------
  >>> store = share_index(make_signed_store(), "alias", "tunnels_manager_secrets")
  >>> store._ChainStoreBasePlugin__hset_index("alias") == _hset_index("tunnels_manager_secrets")
  True
  """
  # The harness's own hash, still used for every name.
  digest = store.get_hash
  # The alias hashed as hkey; set on the instance, so other stores keep the real hash.
  store.get_hash = lambda value, algorithm="sha256", length=10: digest(
    hkey if value == alias else value, algorithm, length,
  )
  # The store, patched.
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


class TestOracleOnlyNamespaces(unittest.TestCase):
  """The namespaces kept by oracles take writes, sync requests and sync answers only from registry members."""

  def test_tunnel_namespaces_apply_a_write_or_delete_only_from_a_registry_member(self):
    """A tunnel write or delete from a chain peer outside the registries is dropped, unconfirmed and counted."""
    # Both Tunnels Manager namespaces.
    for hkey in TUNNELS:
      # One sub-test each.
      with self.subTest(hkey=hkey):
        # The store of "node"; "peer2" is one of its chain peers but in neither registry.
        store = make_signed_store()
        # One field of the namespace.
        key = _hset_key(hkey, "0xabc")
        # The outsider's write: nothing stored, no confirmation sent back.
        self.assertEqual((remote_store(store, key, {"token": "t"}, "peer2"), key in store.storage), ([], False))
        # "oracle" is in the oracle registry only, not in the dAuth one: its write is stored and confirmed to it.
        self.assertEqual(remote_store(store, key, {"token": "t"}, "oracle"), [["oracle"]])
        # The outsider's delete: the record stays, no confirmation.
        self.assertEqual((remote_store(store, key, None, "peer2"), key in store.storage), ([], True))
        # The member's delete: the record goes, confirmed.
        self.assertEqual((remote_store(store, key, None, "oracle"), key in store.storage), ([["oracle"]], False))
        # The two refusals are counted as refused remote writes of the bucket ("oracle" is not a chain peer).
        self.assertEqual(getattr(store, STATS), {
          key[:14] + "/remote/refused": 2, key[:14] + "/remote/changed": 2, key[:14] + "/remote_non_peer": 2,
        })

  def test_tunnel_namespaces_answer_a_sync_request_only_to_a_registry_member(self):
    """A sync request for a tunnel namespace is answered to a registry member and not to another chain peer."""
    # Both Tunnels Manager namespaces.
    for hkey in TUNNELS:
      # One sub-test each.
      with self.subTest(hkey=hkey):
        # The store of "node".
        store = make_signed_store()
        # One field of the namespace.
        key, record = _hset_record(hkey, "0xabc", {"token": "t"}, "oracle")
        # Held by the store, as a write leaves it.
        store.storage[key] = record
        # The request of "peer2", a chain peer in neither registry, then the one of "oracle" (oracle registry only).
        for sender in ("peer2", "oracle"):
          # Each request as the network delivers it.
          store.default_handler(store.make_request_payload(sender=sender, hkey=hkey))
        # One answer only, to "oracle", carrying the field.
        self.assertEqual(
          [(sent["node_addr"], list(sent["data"][store.CS_DATA][store.CS_SNAPSHOT])) for sent in store.sent_payloads],
          [(["oracle"], [key])],
        )

  def test_tunnel_namespaces_merge_a_sync_answer_only_from_a_registry_member(self):
    """A sync of a tunnel namespace drops the answer of a chain peer outside the registries and merges a member's."""
    # Both Tunnels Manager namespaces.
    for hkey in TUNNELS:
      # One sub-test each.
      with self.subTest(hkey=hkey):
        # One field of the namespace, held by the answering nodes.
        key, record = _hset_record(hkey, "0xabc", {"token": "t"}, "oracle")
        # The requesting store of "node" and the network it asks on.
        requester, network = make_signed_store(), _FakeNetwork()
        # The requester, reachable for the answers.
        network.register(requester)
        # "peer2", in neither registry, holds the field and answers.
        network.register(make_signed_peer("peer2", {key: record}))
        # Its answer is dropped, so the sync asked of it alone times out.
        with self.assertRaisesRegex(ValueError, "timed out"):
          # Asked of "peer2" only.
          requester._hsync(hkey, peers=["peer2"], include_default_peers=False, timeout=0.5)
        # Nothing merged.
        self.assertNotIn(key, requester.storage)
        # "oracle", in the oracle registry only, holds the field and answers.
        network.register(make_signed_peer("oracle", {key: record}))
        # Its answer is merged.
        self.assertEqual(
          requester._hsync(hkey, peers=["oracle"], include_default_peers=False),
          {"hkey": hkey, "source_peer": "oracle", "merged_fields": 1},
        )

  def test_unversioned_job_pointer_is_written_or_deleted_only_by_a_registry_member(self):
    """A job pointer without a revision is stored or deleted only for a registry member; a signed one as before."""
    # The store of "node"; "peer2" is one of its chain peers but in neither registry.
    store = make_signed_store()
    # The pointer of job 7.
    key = _hset_key("DEEPLOY_DEPLOYED_JOBS", "7")
    # A plain CID and a dict without a revision from the outsider: neither confirmed.
    self.assertEqual([remote_store(store, key, value, "peer2") for value in ("cid-7", {"pipeline_cid": "c"})], [[], []])
    # Neither stored.
    self.assertNotIn(key, store.storage)
    # The CID from "oracle" (oracle registry only) is stored and confirmed.
    self.assertEqual(remote_store(store, key, "cid-7", "oracle"), [["oracle"]])
    # The outsider's delete leaves it, unconfirmed.
    self.assertEqual((remote_store(store, key, None, "peer2"), store.storage[key][store.CS_VALUE]), ([], "cid-7"))
    # The member's delete removes it, confirmed.
    self.assertEqual((remote_store(store, key, None, "oracle"), key in store.storage), ([["oracle"]], False))
    # A pointer signed by its coordinator keeps its own rule: relayed by the outsider, it is stored and confirmed.
    self.assertEqual(remote_store(store, key, SIGNED_POINTER, "peer2"), [["peer2"]])
    # The three refusals are counted as refused remote writes of the bucket.
    self.assertEqual(getattr(store, STATS)[key[:14] + "/remote/refused"], 3)

  def test_sync_answer_from_outside_the_registries_keeps_only_signed_job_pointers(self):
    """From a chain peer outside the registries a sync merges signed job pointers only; from a member, all."""
    # The pointer of job 7 without a revision, and the signed pointer of job 8.
    plain_key, plain = _hset_record("DEEPLOY_DEPLOYED_JOBS", "7", "cid-7", "oracle")
    # Signed by its coordinator "oracle".
    signed_key, signed = _hset_record("DEEPLOY_DEPLOYED_JOBS", "8", dict(SIGNED_POINTER, job_id="8"), "oracle")
    # The requesting store of "node" and the network it asks on.
    requester, network = make_signed_store(), _FakeNetwork()
    # The requester, reachable for the answers.
    network.register(requester)
    # "peer2" (in neither registry) and "oracle" (oracle registry only) both hold the two pointers.
    for address in ("peer2", "oracle"):
      # Each answering node.
      network.register(make_signed_peer(address, {plain_key: plain, signed_key: signed}))
    # The outsider's answer ends the sync, with only the signed pointer merged.
    result = requester._hsync("DEEPLOY_DEPLOYED_JOBS", peers=["peer2"], include_default_peers=False)
    # One field merged: the signed pointer, not the unversioned one.
    self.assertEqual((result["merged_fields"], plain_key in requester.storage, signed_key in requester.storage),
                     (1, False, True))
    # The member's answer brings the unversioned pointer too (the signed one is already held).
    result = requester._hsync("DEEPLOY_DEPLOYED_JOBS", peers=["oracle"], include_default_peers=False)
    # One field merged: the unversioned pointer.
    self.assertEqual((result["merged_fields"], requester.storage[plain_key][requester.CS_VALUE]), (1, "cid-7"))

  def test_other_namespaces_keep_their_rules(self):
    """The outsider refused in a tunnel namespace still writes an ordinary one, and the dAuth rules are unchanged."""
    # The store of "node" (a dAuth registry member); "peer2" is one of its chain peers but in neither registry.
    store = make_signed_store()
    # A tunnel field, an ordinary field and a dAuth secret revision.
    tunnel, ordinary, secret = (
      _hset_key("tunnels_manager_secrets", "0xabc"), _hset_key("players", "alpha"),
      _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|revision"),
    )
    # The outsider: refused in the tunnel namespace, applied and confirmed in the ordinary one, refused in dAuth.
    self.assertEqual([remote_store(store, key, {"v": 1}, "peer2") for key in (tunnel, ordinary, secret)],
                     [[], [["peer2"]], []])
    # "oracle" (oracle registry only) still writes a dAuth secret revision on a dAuth registry member.
    self.assertEqual(remote_store(store, secret, {"v": 1}, "oracle"), [["oracle"]])
    # The tunnel refusal is counted; the dAuth refusal returns before any count, as it did.
    self.assertEqual(getattr(store, STATS), {
      tunnel[:14] + "/remote/refused": 1, ordinary[:14] + "/remote/changed": 1,
      secret[:14] + "/remote_non_peer": 1, secret[:14] + "/remote/changed": 1,
    })
    # The payloads sent so far, to tell the sync answers below apart.
    sent = len(store.sent_payloads)
    # Sync requests: "oracle" for a tunnel and a dAuth namespace, "peer2" for an ordinary one and for job pointers.
    requests = (("oracle", "tunnels_manager_secrets"), ("oracle", "DAUTH_JOB_SECRET_REVISIONS"),
                ("peer2", "players"), ("peer2", "DEEPLOY_DEPLOYED_JOBS"))
    # Each request as the network delivers it.
    for sender, hkey in requests:
      # One request.
      store.default_handler(store.make_request_payload(sender=sender, hkey=hkey))
    # Answered: the tunnel namespace to the registry member, the ordinary namespace and the job pointers to anyone;
    # the dAuth namespace still only to a dAuth registry member, which "oracle" is not.
    self.assertEqual([payload["data"][store.CS_DATA][store.CS_HKEY] for payload in store.sent_payloads[sent:]],
                     ["tunnels_manager_secrets", "players", "DEEPLOY_DEPLOYED_JOBS"])

  def test_local_tunnel_write_is_sent_only_to_registry_members(self):
    """A local tunnel write or delete goes to the registry members among its targets; other namespaces to all."""
    # Targets of each write: chain peers "peer1" (dAuth registry) and "peer2" (no registry), extra peers "oracle"
    # (oracle registry) and "peer3" (no registry).
    for hkey, value, expected in (
      ("tunnels_manager_secrets", {"token": "t"}, ["peer1", "oracle"]),
      ("tunnels_manager_tcp_routes", None, ["peer1", "oracle"]),
      ("players", {"token": "t"}, ["peer1", "peer2", "oracle", "peer3"]),
    ):
      # One sub-test each.
      with self.subTest(hkey=hkey, value=value):
        # The store of "node".
        store = make_signed_store()
        # The local write, unconfirmed here: no peer answers in this test.
        store._set_value(_hset_key(hkey, "0xabc"), value, owner="node", peers=["oracle", "peer3"], timeout=.1,
                         max_retries=0)
        # The queued write, sent as process() sends it.
        store._ChainStoreBasePlugin__maybe_broadcast()
        # Sent once, to the expected targets.
        self.assertEqual([sent["node_addr"] for sent in store.sent_payloads], [expected])
    # With no registry member among the targets, a tunnel write ends as one with no target peers: the store of "node"
    # with "peer2" (no registry) as its only chain peer.
    store = make_signed_store()
    # Its only chain peer.
    setattr(store, "_ChainStoreBasePlugin__chain_peers", ["peer2"])
    # One field of a tunnel namespace.
    key = _hset_key("tunnels_manager_secrets", "0xabc")
    # Refused: not stored, nothing queued, counted as a refused local write.
    self.assertEqual(
      (store._set_value(key, {"token": "t"}, owner="node"), key in store.storage, len(getattr(store, OPS)),
       getattr(store, STATS)),
      (False, False, 0, {key[:14] + "/local/refused": 1}),
    )


class TestNamespaceIndexGates(unittest.TestCase):
  """The namespace rules apply to the namespace of the index an hkey name has, as the export and the merge do."""

  def test_sync_request_under_another_name_with_a_protected_index_gets_that_namespace_rule(self):
    """A sync request naming another hkey with a protected index is answered only to the namespace's senders."""
    # Per namespace: the sender its rule refuses and the sender it answers.
    cases = (("tunnels_manager_secrets", "peer2", "oracle"), ("DAUTH_JOB_SECRETS", "oracle", "peer1"))
    for hkey, refused, answered in cases:
      # One sub-test each.
      with self.subTest(hkey=hkey):
        # The store of "node" (dAuth registry), where "alias" has the index of the namespace.
        store = share_index(make_signed_store(), "alias", hkey)
        # One field of the namespace.
        key, record = _hset_record(hkey, "7", {"token": "t"}, "oracle")
        # Held by the store.
        store.storage[key] = record
        # A request for "alias" from each sender, as the network delivers it.
        for sender in (refused, answered):
          # One request.
          store.default_handler(store.make_request_payload(sender=sender, hkey="alias"))
        # One answer only, to the sender the namespace's rule allows, carrying the field.
        self.assertEqual(
          [(sent["node_addr"], list(sent["data"][store.CS_DATA][store.CS_SNAPSHOT])) for sent in store.sent_payloads],
          [([answered], [key])],
        )

  def test_sync_answer_under_another_name_with_a_protected_index_gets_that_namespace_rule(self):
    """A sync answer naming another hkey with a protected index is taken as for that namespace."""
    # Per namespace: the field's value, the sender its rule refuses, the sender it takes, and what is kept of the
    # first sender's answer (None: dropped; {}: taken without the unversioned job pointer).
    cases = (
      ("tunnels_manager_secrets", {"token": "t"}, "peer2", "oracle", None),
      ("DAUTH_JOB_SECRETS", {"secret": "s"}, "oracle", "peer1", None),
      ("NODE_KEY_ROTATIONS", {"proof": "p"}, "oracle", "peer1", None),
      ("DEEPLOY_DEPLOYED_JOBS", "cid-7", "peer2", "oracle", {}),
    )
    for hkey, value, refused, taken, kept in cases:
      # One sub-test each.
      with self.subTest(hkey=hkey):
        # The store of "node" (dAuth registry), where "alias" has the index of the namespace.
        store = share_index(make_signed_store(), "alias", hkey)
        # One field of the namespace, in each answer.
        key, record = _hset_record(hkey, "7", value, "oracle")
        # What each sync keeps of its answer.
        recorded = []
        # One sync of "alias" per sender, each waiting on that sender alone.
        for sender in (refused, taken):
          # The sync as _hsync leaves it while it waits, keyed by its request id.
          store.pending_hsync[sender] = {store.CS_HKEY: "alias", store.CS_PEERS: [sender], store.CS_RESPONSE: None}
          # The sender's answer, as the network delivers it.
          store.default_handler(
            store.make_response_payload(request_id=sender, sender=sender, hkey="alias", snapshot={key: record}),
          )
          # The answer kept for the merge, if any.
          response = store.pending_hsync[sender][store.CS_RESPONSE]
          # Its snapshot, or None when the answer was dropped.
          recorded.append(None if response is None else response[store.CS_SNAPSHOT])
        # The first answer dropped or filtered as for the namespace, the second taken whole.
        self.assertEqual(recorded, [kept, {key: record}])

  def test_local_dauth_gates_get_the_namespace_from_the_index(self):
    """Off the dAuth registry, another name with a dAuth index exports nothing, merges nothing and is not synced."""
    # The store of "oracle" (oracle registry only), where "alias" has the index of the dAuth secrets.
    store = share_index(make_signed_peer("oracle", {}), "alias", "DAUTH_JOB_SECRETS")
    # A secret the store holds.
    held, record = _hset_record("DAUTH_JOB_SECRETS", "7", {"secret": "s"}, "peer1")
    # Held by the store.
    store.storage[held] = record
    # A secret a peer would send.
    sent, other = _hset_record("DAUTH_JOB_SECRETS", "8", {"secret": "s"}, "peer1")
    # Nothing exported, nothing merged.
    self.assertEqual((store._ChainStoreBasePlugin__export_hset_snapshot("alias"),
                      store._ChainStoreBasePlugin__merge_hset_snapshot("alias", {sent: other})), ({}, 0))
    # No sync started: the dAuth rule refuses it before any request.
    with self.assertRaisesRegex(ValueError, "dAuth registry participant"):
      # Asked of the default peers.
      store._hsync("alias", timeout=0.5)


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
