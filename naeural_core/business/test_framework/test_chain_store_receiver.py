"""
Chain-store receiver, writer and snapshot-sync behaviour added to keep a slow
or restarted oracle in step with its peers:

- batch intake: acknowledgments first, newest store per sender and key;
- a confirmed local write of the same value is not re-broadcast at once;
- snapshot sync that merges every peer's answer and transfers only differences;
- revoked secret revisions are erased wherever their marker is known.
"""
import random
import unittest
from collections import deque
from pathlib import Path

from naeural_core.business.test_framework.test_chain_store_hsync import (
  ChainStoreBasePlugin, _ChainStoreRuntimeHarness, _FakeNetwork, _PayloadData,
  _build_hset_storage, _chain_store_record, _hset_key, _load_class_from_source, _BASE_DIR,
)

_NetworkProcessorMixin = _load_class_from_source(
  Path(_BASE_DIR) / "business" / "mixins_libs" / "network_processor_mixin.py", "_NetworkProcessorMixin",
)

CS = ChainStoreBasePlugin
REVISIONS = "DAUTH_JOB_SECRET_REVISIONS"


class _Registry:
  """Every simulated address is a registered dAuth oracle; signatures verify."""

  def __init__(self, peers):
    self.peers = list(peers)

  def get_whitelist(self, with_prefix=False):
    return list(self.peers)

  @staticmethod
  def node_address_to_eth_address(address):
    return address

  def get_eth_oracles(self):
    return list(self.peers)

  def get_eth_dauth_oracles(self):
    return list(self.peers)

  @staticmethod
  def verify(value, **kwargs):
    class _Result:
      valid = not (isinstance(value, dict) and value.get("forged"))
      message = "ok"
    return _Result()


class _Node(_ChainStoreRuntimeHarness):
  """Harness with the state on_init creates and a clock that also runs the plugin loop."""

  def __init__(self, ee_addr, default_peers=None, storage=None, batching=False, pump=()):
    super().__init__(ee_addr, default_peers=default_peers, storage=storage)
    self._ChainStoreBasePlugin__ops = deque()
    self._ChainStoreBasePlugin__inbox = []
    self._ChainStoreBasePlugin__batching = batching
    self._ChainStoreBasePlugin__stats = {}
    self.bc = _Registry([ee_addr] + list(default_peers or []))
    self.netmon = type("Netmon", (), {"network_node_is_online": staticmethod(lambda peer: True)})()
    self.pump = list(pump)  # plugins whose loop runs while this one waits

  def sleep(self, seconds):
    self._now += seconds
    for plugin in [self] + self.pump:
      plugin._now = self._now
      plugin.process()

  def get_instance_path(self):
    return [self.ee_addr, self._stream_id, self._signature, self.cfg_instance_id]

  @property
  def stats(self):
    return self._ChainStoreBasePlugin__stats

  @property
  def inbox(self):
    return self._ChainStoreBasePlugin__inbox

  def payload(self, sender, **data):
    return {
      _PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
      _PayloadData.EE_DESTINATION: [self.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
      "decoded": {CS.CS_DATA: data},
    }

  def sent(self, operation):
    return [item for item in self.sent_payloads if item["data"][CS.CS_DATA][CS.CS_OP] == operation]


def _fleet(count=3, batching=False):
  names = ["n%d" % idx for idx in range(count)]
  nodes = [_Node(name, default_peers=[peer for peer in names if peer != name], batching=batching) for name in names]
  network = _FakeNetwork()
  for node in nodes:
    network.register(node)
    node.pump = [peer for peer in nodes if peer is not node]
  return nodes


def _store(key, value, owner="writer", **fields):
  return dict({CS.CS_OP: CS.CS_STORE, CS.CS_KEY: key, CS.CS_VALUE: value, CS.CS_OWNER: owner}, **fields)


def _visible(node):
  """What a reader of the replica can observe."""
  return {key: (record[CS.CS_VALUE], record[CS.CS_OWNER], record.get(CS.CS_READONLY, False), record.get(CS.CS_TOKEN))
          for key, record in node.storage.items()}


class TestBatchIntake(unittest.TestCase):

  def test_handler_runs_at_once_when_batching_is_off(self):
    node = _Node("n0", default_peers=["n1"])
    node.default_handler(node.payload("n1", **_store("k", "v")))
    self.assertEqual(node.storage["k"][CS.CS_VALUE], "v")
    self.assertEqual(len(node.sent(CS.CS_CONFIRM)), 1)

  def test_batch_is_deferred_until_process(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.default_handler(node.payload("n1", **_store("k", "v")))
    self.assertNotIn("k", node.storage)
    self.assertEqual(len(node.inbox), 1)
    node.process()
    self.assertEqual(node.storage["k"][CS.CS_VALUE], "v")
    self.assertEqual(len(node.inbox), 0)

  def test_newest_store_per_sender_and_key_wins(self):
    node = _Node("n0", default_peers=["n1", "n2"], batching=True)
    for value in ("v1", "v2", "v3"):
      node.default_handler(node.payload("n1", **_store("k", value)))
    node.default_handler(node.payload("n2", **_store("other", "x")))
    node.process()
    self.assertEqual(node.storage["k"][CS.CS_VALUE], "v3")
    self.assertEqual(node.storage["other"][CS.CS_VALUE], "x")
    confirms = node.sent(CS.CS_CONFIRM)
    # one acknowledgment for the newest value of k, one for the other key
    self.assertEqual([item["data"][CS.CS_DATA][CS.CS_VALUE] for item in confirms], ["v3", "x"])
    self.assertEqual(node.stats["superseded"], 2)

  def test_same_key_from_two_senders_is_not_merged(self):
    node = _Node("n0", default_peers=["n1", "n2"], batching=True)
    node.default_handler(node.payload("n1", **_store("k", "from-n1")))
    node.default_handler(node.payload("n2", **_store("k", "from-n2")))
    node.process()
    self.assertEqual(node.stats.get("superseded", 0), 0)
    self.assertEqual(len(node.sent(CS.CS_CONFIRM)), 2)
    self.assertEqual(node.storage["k"][CS.CS_VALUE], "from-n2")

  def test_out_of_order_sequence_keeps_the_higher_one(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.default_handler(node.payload("n1", **_store("k", {"sequence": 10, "body": "new"})))
    node.default_handler(node.payload("n1", **_store("k", {"sequence": 9, "body": "late"})))
    node.process()
    self.assertEqual(node.storage["k"][CS.CS_VALUE]["sequence"], 10)

  def test_delete_after_store_is_applied(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.default_handler(node.payload("n1", **_store("k", "v")))
    node.default_handler(node.payload("n1", **_store("k", None)))
    node.process()
    self.assertNotIn("k", node.storage)

  def test_acknowledgments_run_before_stores(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    record = _chain_store_record("mine", "local", owner="me")
    record.update({"target_peers": ["n1"], "confirmed_peers": []})
    node.storage["mine"] = record
    order = []
    original = node._ChainStoreBasePlugin__exec_operation
    node._ChainStoreBasePlugin__exec_operation = lambda operation, data, sender: (order.append(operation), original(operation, data, sender))
    for idx in range(5):
      node.default_handler(node.payload("n1", **_store("k%d" % idx, "v")))
    node.default_handler(node.payload("n1", **{CS.CS_OP: CS.CS_HSYNC_REQ, CS.CS_REQUEST_ID: "r", CS.CS_HKEY: "h"}))
    node.default_handler(node.payload("n1", **{CS.CS_OP: CS.CS_CONFIRM, CS.CS_KEY: "mine", CS.CS_VALUE: "local", CS.CS_OWNER: "me"}))
    node.process()
    self.assertEqual(order[0], CS.CS_CONFIRM)
    self.assertEqual(order[-1], CS.CS_HSYNC_REQ)
    self.assertEqual(node.storage["mine"][CS.CS_CONFIRMATIONS], 1)

  def test_one_failing_operation_does_not_drop_the_batch(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.default_handler(node.payload("n1", **_store("", "bad key")))  # _set_value raises on an empty key
    node.default_handler(node.payload("n1", **_store("good", "v")))
    node.process()
    self.assertEqual(node.storage["good"][CS.CS_VALUE], "v")
    self.assertEqual(node.stats["failed"], 1)

  def test_undecryptable_payload_is_ignored(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    payload = node.payload("n1", **_store("k", "v"))
    payload["decoded"] = {}
    node.default_handler(payload)
    node.process()
    self.assertNotIn("k", node.storage)

  def test_stores_beyond_the_time_budget_wait_but_acknowledgments_do_not(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.cfg_inputs_batch_seconds = 0.25
    record = _chain_store_record("mine", "local", owner="me")
    record.update({"target_peers": ["n1"], "confirmed_peers": []})
    node.storage["mine"] = record
    original = node._ChainStoreBasePlugin__exec_store

    def slow_store(data, peers=None):
      node._now += 0.1  # every store costs 100 ms on this starved node
      return original(data, peers=peers)
    node._ChainStoreBasePlugin__exec_store = slow_store
    for idx in range(10):
      node.default_handler(node.payload("n1", **_store("k%d" % idx, "v")))
    node.default_handler(node.payload("n1", **{CS.CS_OP: CS.CS_CONFIRM, CS.CS_KEY: "mine", CS.CS_VALUE: "local", CS.CS_OWNER: "me"}))
    node.process()
    self.assertEqual(node.storage["mine"][CS.CS_CONFIRMATIONS], 1)
    done = [idx for idx in range(10) if "k%d" % idx in node.storage]
    self.assertEqual(done, [0, 1, 2])            # 0.25 s budget, 0.1 s each, in arrival order
    self.assertEqual(len(node.inbox), 7)
    while node.inbox:
      node.process()
    self.assertEqual(len([idx for idx in range(10) if "k%d" % idx in node.storage]), 10)

  def test_waiting_stores_collapse_with_newer_ones_and_are_bounded(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.cfg_inputs_batch_seconds = 0.25
    node.CS_BACKLOG_MAX = 5
    original = node._ChainStoreBasePlugin__exec_store

    def slow_store(data, peers=None):
      node._now += 1.0
      return original(data, peers=peers)
    node._ChainStoreBasePlugin__exec_store = slow_store
    for idx in range(20):
      node.default_handler(node.payload("n1", **_store("k%d" % idx, "v1")))
    node.process()                               # one store fits, 19 wait, the 14 oldest of them are dropped
    self.assertEqual(len(node.inbox), 5)
    self.assertEqual(node.stats["backlog_dropped"], 14)
    for idx in range(15, 20):
      node.default_handler(node.payload("n1", **_store("k%d" % idx, "v2")))
    while node.inbox:
      node.process()
    # the newest write of every key survives the overflow; only stores it stands for are let go
    self.assertEqual([node.storage["k%d" % idx][CS.CS_VALUE] for idx in range(15, 20)], ["v2"] * 5)
    self.assertEqual(node.stats["superseded"] + node.stats["backlog_dropped"] - 14, 5)
    self.assertEqual(len([item for item in node.sent(CS.CS_CONFIRM) if item["data"][CS.CS_DATA][CS.CS_VALUE] == "v1"]), 1)

  def test_close_runs_everything_that_waits(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.cfg_inputs_batch_seconds = 0.25
    original = node._ChainStoreBasePlugin__exec_store

    def slow_store(data, peers=None):
      node._now += 1.0
      return original(data, peers=peers)
    node._ChainStoreBasePlugin__exec_store = slow_store
    for idx in range(4):
      node.default_handler(node.payload("n1", **_store("k%d" % idx, "v")))
    node.on_close()
    self.assertEqual(len(node.storage), 4)

  def test_malformed_operations_are_ignored_without_touching_the_batch(self):
    node = _Node("n0", default_peers=["n1", "n2"], batching=True)
    record = _chain_store_record("mine", "local", owner="me")
    record.update({"target_peers": ["n2"], "confirmed_peers": []})
    node.storage["mine"] = record
    node.default_handler(node.payload("n2", **{CS.CS_OP: CS.CS_CONFIRM, CS.CS_KEY: "mine", CS.CS_VALUE: "local", CS.CS_OWNER: "me"}))
    node.default_handler(node.payload("n1", **{CS.CS_OP: ["x"], CS.CS_KEY: "k"}))
    node.default_handler(node.payload("n1", **{CS.CS_OP: {"a": 1}}))
    node.default_handler(node.payload("n1", **{CS.CS_OP: "SOMETHING_ELSE"}))
    bad = node.payload("n1")
    bad["decoded"] = {CS.CS_DATA: ["not", "a", "dict"]}
    node.default_handler(bad)
    node.default_handler(node.payload("n1", **_store("good", "v")))
    self.assertEqual(len(node.inbox), 2)
    node.process()
    self.assertEqual(node.storage["good"][CS.CS_VALUE], "v")
    self.assertEqual(node.storage["mine"][CS.CS_CONFIRMATIONS], 1)
    self.assertEqual(node.stats.get("failed", 0), 0)
    plain = _Node("n3", default_peers=["n1"])            # the same payloads with batching off
    plain.default_handler(plain.payload("n1", **{CS.CS_OP: ["x"], CS.CS_KEY: "k"}))
    self.assertEqual(plain.storage, {})

  def test_delete_then_recreate_by_another_owner_is_not_collapsed(self):
    for batching in (False, True):
      node = _Node("n0", default_peers=["n1"], batching=batching)
      node.storage["k"] = _chain_store_record("k", "orig", owner="plugin-a", readonly=True)
      node.default_handler(node.payload("n1", **_store("k", None, owner="plugin-a")))
      node.default_handler(node.payload("n1", **_store("k", "new", owner="plugin-b")))
      node.process()
      self.assertEqual(node.storage["k"][CS.CS_VALUE], "new", batching)
      self.assertEqual(node.sent(CS.CS_CONFIRM)[-1]["data"][CS.CS_DATA][CS.CS_VALUE], "new")

  def test_rejected_newest_store_falls_back_to_the_older_ones(self):
    key = _hset_key("DEEPLOY_OPERATIONS", "op1|5")
    good = {"operation_id": "op1", "sequence": 5, "coordinator": "n1", "EE_SENDER": "n1", "body": "valid"}
    forged = {"operation_id": "op1", "sequence": 13, "coordinator": "n1", "EE_SENDER": "n1", "body": "x", "forged": True}
    for batching in (False, True):
      node = _Node("n0", default_peers=["n1"], batching=batching)
      node.default_handler(node.payload("n1", **_store(key, good, owner="n1")))
      node.default_handler(node.payload("n1", **_store(key, forged, owner="n1")))
      node.process()
      self.assertEqual(node.storage[key][CS.CS_VALUE]["body"], "valid", batching)
      self.assertEqual(len(node.sent(CS.CS_CONFIRM)), 1)

  def test_store_from_another_sender_on_the_same_key_keeps_the_order(self):
    for batching in (False, True):
      node = _Node("n0", default_peers=["n1", "n2"], batching=batching)
      node.default_handler(node.payload("n1", **_store("k", "a", owner="o1", **{CS.CS_READONLY: True})))
      node.default_handler(node.payload("n2", **_store("k", "b", owner="o2", **{CS.CS_READONLY: True})))
      node.default_handler(node.payload("n1", **_store("k", "c", owner="o1", **{CS.CS_READONLY: True})))
      node.process()
      self.assertEqual(_visible(node)["k"], ("c", "o1", True, None), batching)
      self.assertEqual([item["data"][CS.CS_DATA][CS.CS_VALUE] for item in node.sent(CS.CS_CONFIRM)], ["a", "c"])

  def test_snapshot_requests_keep_their_turn_and_are_not_dropped(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.CS_BACKLOG_MAX = 3
    original = node._ChainStoreBasePlugin__exec_store

    def slow_store(data, peers=None):
      node._now += 1.0
      return original(data, peers=peers)
    node._ChainStoreBasePlugin__exec_store = slow_store
    node.default_handler(node.payload("n1", **_store("k0", "v")))
    node.default_handler(node.payload("n1", **{CS.CS_OP: CS.CS_HSYNC_REQ, CS.CS_REQUEST_ID: "r1", CS.CS_HKEY: "players"}))
    for idx in range(1, 12):
      node.default_handler(node.payload("n1", **_store("k%d" % idx, "v")))
    node.process()                      # k0 fits; the request and 11 stores wait; 9 of the stores are dropped
    self.assertEqual([item[0] for item in node.inbox], [CS.CS_HSYNC_REQ, CS.CS_STORE, CS.CS_STORE])
    node.process()                      # the request is first in line
    self.assertEqual(len(node.sent(CS.CS_HSYNC_RESP)), 1)
    self.assertEqual(node.stats["backlog_dropped"], 9)

  def test_batching_gives_the_same_replica_as_one_by_one_processing(self):
    """Random streams (several owners per sender, deletes, read-only, tokens, forged and stale records)."""
    rnd = random.Random(20261110)
    senders, owners = ["n1", "n2"], ["o1", "o2"]
    op_key = lambda slot: _hset_key("DEEPLOY_OPERATIONS", "op1|%d" % slot)
    for trial in range(300):
      stream = []
      for _ in range(rnd.randrange(2, 40)):
        sender = rnd.choice(senders)
        kind = rnd.random()
        if kind < 0.25:
          sequence = rnd.randrange(1, 40)
          value = {"operation_id": "op1", "sequence": sequence, "coordinator": sender, "EE_SENDER": sender,
                   "n": rnd.randrange(3), "forged": rnd.random() < 0.15}
          stream.append((sender, _store(op_key(sequence % 8), value, owner=sender)))
        else:
          fields = {CS.CS_READONLY: rnd.random() < 0.3}
          if rnd.random() < 0.2:
            fields[CS.CS_TOKEN] = rnd.choice(["t1", "t2"])
          value = None if kind < 0.4 else rnd.choice(["a", "b", "c", {"x": rnd.randrange(3)}])
          stream.append((sender, _store(rnd.choice(["k1", "k2", "k3"]), value, owner=rnd.choice(owners), **fields)))
      cuts = sorted(rnd.sample(range(1, len(stream) + 1), k=min(len(stream), rnd.randrange(1, 4))))
      plain = _Node("n0", default_peers=senders)
      batched = _Node("n0", default_peers=senders, batching=True)
      batched.CS_STORE_SECONDS = 10 ** 6
      for position, (sender, data) in enumerate(stream, start=1):
        plain.default_handler(plain.payload(sender, **data))
        batched.default_handler(batched.payload(sender, **data))
        if position in cuts:
          batched.process()
      batched.process()
      self.assertEqual(_visible(batched), _visible(plain), "trial %d: %r" % (trial, stream))
      last = lambda node: {(str(item["node_addr"]), item["data"][CS.CS_DATA][CS.CS_KEY]): item["data"][CS.CS_DATA][CS.CS_VALUE]
                           for item in node.sent(CS.CS_CONFIRM)}
      # every key a sender last got confirmed in one mode, it also got confirmed in the other, with the same value
      for slot, value in last(plain).items():
        if _visible(plain).get(slot[1], (None,))[0] == value:
          self.assertEqual(last(batched).get(slot), value, "trial %d" % trial)
      self.assertEqual(batched.stats.get("failed", 0), 0)

  def test_stats_line(self):
    node = _Node("n0", default_peers=["n1"], batching=True)
    node.CS_STATS_INTERVAL = 60
    node.default_handler(node.payload("n1", **_store("k", "v")))
    node.process()
    node._now += 61
    node.process()
    line = [msg for msg in node.messages if "CSTORE STATS" in msg][-1]
    self.assertRegex(line, r"CSTORE STATS \d+s: loop=\S+Hz queue=\d+/1024 lost=\d+ keys=1 store=1 confirm=0 ")
    self.assertEqual(node.stats, {})


class TestSameValueRepublish(unittest.TestCase):

  def test_confirmed_same_value_is_not_rebroadcast_within_the_interval(self):
    writer, peer, _ = _fleet()
    self.assertTrue(writer._set_value("k", {"a": 1}))
    self.assertEqual(len(writer.sent(CS.CS_STORE)), 1)
    self.assertTrue(writer._set_value("k", {"a": 1}))
    self.assertEqual(len(writer.sent(CS.CS_STORE)), 1)
    self.assertEqual(writer.stats["same_value_skipped"], 1)
    self.assertEqual(peer.storage["k"][CS.CS_VALUE], {"a": 1})

  def test_same_value_is_rebroadcast_after_the_interval(self):
    writer, _, _ = _fleet()
    self.assertTrue(writer._set_value("k", "v"))
    writer._now += CS.CS_REPUBLISH_INTERVAL + 1
    self.assertTrue(writer._set_value("k", "v"))
    self.assertEqual(len(writer.sent(CS.CS_STORE)), 2)

  def test_same_value_for_an_additional_peer_is_broadcast(self):
    writer, _, _ = _fleet()
    extra = _Node("extra", default_peers=["n0"])
    writer.network.register(extra)
    writer.bc.peers.append("extra")
    writer.pump.append(extra)
    self.assertTrue(writer._set_value("k", "v"))
    self.assertTrue(writer._set_value("k", "v", peers=["extra"]))
    self.assertEqual(len(writer.sent(CS.CS_STORE)), 2)
    self.assertEqual(extra.storage["k"][CS.CS_VALUE], "v")

  def test_changed_value_is_always_broadcast(self):
    writer, peer, _ = _fleet()
    self.assertTrue(writer._set_value("k", "v1"))
    self.assertTrue(writer._set_value("k", "v2"))
    self.assertEqual(len(writer.sent(CS.CS_STORE)), 2)
    self.assertEqual(peer.storage["k"][CS.CS_VALUE], "v2")

  def test_unconfirmed_write_is_rebroadcast(self):
    writer = _Node("n0", default_peers=["n1"])  # n1 never answers
    _FakeNetwork().register(writer)
    self.assertFalse(writer._set_value("k", "v", timeout=1, max_retries=0))
    before = len(writer.sent(CS.CS_STORE))
    self.assertFalse(writer._set_value("k", "v", timeout=1, max_retries=0))
    self.assertGreater(len(writer.sent(CS.CS_STORE)), before)

  def test_value_received_from_a_peer_is_republished_by_a_local_write(self):
    writer, peer, _ = _fleet()
    self.assertTrue(writer._set_value("k", "v"))
    # the peer holds the value as a remote copy; its own first local write must still go out
    self.assertTrue(peer._set_value("k", "v", owner=peer.storage["k"][CS.CS_OWNER]))
    self.assertEqual(len(peer.sent(CS.CS_STORE)), 1)


class TestSnapshotSync(unittest.TestCase):

  def _pair(self, local, *remotes):
    requester = _Node("req", default_peers=["p%d" % idx for idx in range(len(remotes))],
                      storage=_build_hset_storage("players", local, owner="x"))
    peers = [_Node("p%d" % idx, default_peers=["req"], storage=_build_hset_storage("players", fields, owner="x"))
             for idx, fields in enumerate(remotes)]
    network = _FakeNetwork()
    for node in [requester] + peers:
      network.register(node)
    return requester, peers

  def test_first_answer_mode_keeps_its_result_shape(self):
    requester, _ = self._pair({}, {"a": 1})
    self.assertEqual(requester._hsync("players"), {"hkey": "players", "source_peer": "p0", "merged_fields": 1})

  def test_all_peers_merges_every_answer(self):
    requester, _ = self._pair({}, {"a": 1}, {"b": 2}, {"c": 3})
    result = requester._hsync("players", all_peers=True)
    self.assertEqual(result["responses"], 3)
    self.assertEqual(sorted(result["source_peers"]), ["p0", "p1", "p2"])
    self.assertEqual(result["merged_fields"], 3)
    for field in "abc":
      self.assertIn(_hset_key("players", field), requester.storage)

  def test_first_answer_mode_sees_only_one_peer(self):
    requester, _ = self._pair({}, {"a": 1}, {"b": 2})
    requester._hsync("players")
    self.assertNotIn(_hset_key("players", "b"), requester.storage)

  def test_all_peers_does_not_wait_out_the_timeout_for_a_silent_peer(self):
    requester, peers = self._pair({}, {"a": 1}, {"b": 2})
    requester.network.nodes.pop(peers[1].ee_addr)  # p1 is unreachable
    result = requester._hsync("players", all_peers=True, timeout=30)
    self.assertEqual(result["responses"], 1)
    self.assertGreaterEqual(requester._now, CS.CS_HSYNC_GRACE)
    self.assertLess(requester._now, CS.CS_HSYNC_GRACE + 1)

  def test_all_peers_never_waits_longer_than_the_timeout(self):
    requester, peers = self._pair({}, {"a": 1}, {"b": 2})
    requester.network.nodes.pop(peers[1].ee_addr)
    result = requester._hsync("players", all_peers=True, timeout=1)
    self.assertEqual(result["responses"], 1)
    self.assertLess(requester._now, 1.3)

  def test_plugin_api_passes_all_peers_only_when_asked(self):
    from naeural_core.business.test_framework.test_chain_store_hsync import _PeerSelectionHarness
    plugin, calls = _PeerSelectionHarness(), []
    plugin.plugins_shmem["__chain_storage_hsync"] = lambda hkey, **kwargs: calls.append(kwargs) or {"hkey": hkey}
    plugin.plugins_shmem["__chain_storage_get"] = lambda *args, **kwargs: None
    plugin.chainstore_hsync("players", timeout=7, all_peers=True)
    plugin.chainstore_hsync("players", timeout=7)
    self.assertEqual(calls[0].get("all_peers"), True)
    self.assertEqual(calls[0]["timeout"], 7)
    self.assertNotIn("all_peers", calls[1])          # an older chain store never sees the new keyword

  def test_all_peers_times_out_when_nobody_answers(self):
    requester, peers = self._pair({}, {"a": 1})
    requester.network.nodes.pop(peers[0].ee_addr)
    with self.assertRaises(ValueError):
      requester._hsync("players", all_peers=True, timeout=2)

  def test_duplicate_answer_from_one_peer_counts_once(self):
    requester, peers = self._pair({}, {"a": 1}, {"b": 2})
    original = peers[0].send_encrypted_payload
    peers[0].send_encrypted_payload = lambda node_addr=None, **data: (original(node_addr=node_addr, **data), original(node_addr=node_addr, **data))
    result = requester._hsync("players", all_peers=True, timeout=2)
    self.assertEqual(sorted(result["source_peers"]), ["p0", "p1"])

  def test_peer_sends_only_what_differs(self):
    same = {"f%d" % idx: "value-%d" % idx for idx in range(50)}
    requester, peers = self._pair(dict(same, stale="old"), dict(same, stale="new", extra="x"))
    result = requester._hsync("players")
    snapshot = peers[0].sent_payloads[-1]["data"][CS.CS_DATA][CS.CS_SNAPSHOT]
    self.assertEqual(sorted(snapshot), sorted([_hset_key("players", "stale"), _hset_key("players", "extra")]))
    self.assertEqual(result["merged_fields"], 2)
    self.assertEqual(requester.storage[_hset_key("players", "stale")][CS.CS_VALUE], "new")

  def test_request_without_digest_gets_the_full_snapshot(self):
    _, peers = self._pair({}, {"a": 1, "b": 2})
    peers[0].default_handler(peers[0].payload("req", **{CS.CS_OP: CS.CS_HSYNC_REQ, CS.CS_REQUEST_ID: "r1", CS.CS_HKEY: "players"}))
    self.assertEqual(len(peers[0].sent_payloads[-1]["data"][CS.CS_DATA][CS.CS_SNAPSHOT]), 2)

  def test_record_without_a_computable_digest_is_still_synced(self):
    requester, peers = self._pair({"a": 1}, {"a": 1, "odd": "remote"})
    key = _hset_key("players", "odd")
    requester.storage[key] = _chain_store_record(key, {1: "x", "b": 2}, owner="x")   # cannot be sorted for a digest
    result = requester._hsync("players")
    self.assertNotIn(key, requester.sent_payloads[0]["data"][CS.CS_DATA][CS.CS_HAVE])
    self.assertEqual(requester.storage[key][CS.CS_VALUE], "remote")
    self.assertEqual(result["merged_fields"], 1)
    self.assertEqual(requester.pending_hsync, {})

  def test_request_carries_no_token_protected_digest(self):
    requester, peers = self._pair({"a": 1}, {"a": 1})
    key = _hset_key("players", "secret")
    requester.storage[key] = _chain_store_record(key, "s", owner="x", token="t")
    requester._hsync("players")
    have = requester.sent_payloads[0]["data"][CS.CS_DATA][CS.CS_HAVE]
    self.assertEqual(list(have), [_hset_key("players", "a")])


def _revision(job, operation, revoked=False):
  field = "{}|{}:deploy".format(job, operation)
  return field, ({"job_id": job, "revision": operation + ":deploy", "revoked": True} if revoked
                 else {"job_id": job, "revision": operation + ":deploy", "bundle": {"API_KEY": "plaintext"}})


def _marker(job, operation):
  return "{}|{}:deploy|revoked".format(job, operation), {"job_id": job, "revision": operation + ":deploy"}


class TestRevokedSecrets(unittest.TestCase):

  def _oracle(self, name, peers, fields):
    return _Node(name, default_peers=peers, storage=_build_hset_storage(REVISIONS, fields, owner="dauth"))

  def test_stale_oracle_converges_from_peers(self):
    """The frozen replica of a stopped oracle: full revisions that peers revoked, newer ones missing."""
    stale_fields = dict([_revision("26", "opA"), _revision("66", "opB"), _revision("68", "opC"), _revision("9", "keep")])
    healthy = dict([
      _revision("26", "opA", revoked=True), _marker("26", "opA"),
      _revision("66", "opB", revoked=True), _marker("66", "opB"),
      _marker("68", "opC"),                       # this peer dropped the revision and kept only the marker
      _revision("9", "keep"), _revision("34", "new1"), _revision("52", "new2"),
    ])
    second = dict(healthy)
    second.update(dict([_revision("67", "new3")]))
    stale = self._oracle("stale", ["p0", "p1"], stale_fields)
    peers = [self._oracle("p0", ["stale"], healthy), self._oracle("p1", ["stale"], second)]
    network = _FakeNetwork()
    for node in [stale] + peers:
      network.register(node)

    result = stale._hsync(REVISIONS, all_peers=True)

    self.assertEqual(result["responses"], 2)
    value = lambda field: stale.storage.get(_hset_key(REVISIONS, field), {}).get(CS.CS_VALUE)
    for job, operation in (("26", "opA"), ("66", "opB")):
      self.assertEqual(value(_revision(job, operation)[0]), {"job_id": job, "revision": operation + ":deploy", "revoked": True})
      self.assertTrue(value(_marker(job, operation)[0]))
    self.assertIsNone(value(_revision("68", "opC")[0]), "a revision whose marker arrived must not stay in full")
    self.assertTrue(value(_marker("68", "opC")[0]))
    self.assertIn("bundle", value(_revision("9", "keep")[0]))
    for job, operation in (("34", "new1"), ("52", "new2"), ("67", "new3")):
      self.assertIn("bundle", value(_revision(job, operation)[0]))
    self.assertNotIn("plaintext", str([value(_revision(j, o)[0]) for j, o in (("26", "opA"), ("66", "opB"), ("68", "opC"))]))

  def test_full_revision_next_to_its_marker_is_erased(self):
    fields = dict([_revision("68", "opC"), _marker("68", "opC"), _revision("9", "keep")])
    node = self._oracle("n0", ["n1"], fields)
    self.assertEqual(node._ChainStoreBasePlugin__erase_revoked_secrets(), 1)
    self.assertNotIn(_hset_key(REVISIONS, _revision("68", "opC")[0]), node.storage)
    self.assertIn(_hset_key(REVISIONS, _revision("9", "keep")[0]), node.storage)
    self.assertIn(_hset_key(REVISIONS, _marker("68", "opC")[0]), node.storage)

  def test_tombstone_is_kept(self):
    fields = dict([_revision("26", "opA", revoked=True), _marker("26", "opA")])
    node = self._oracle("n0", ["n1"], fields)
    self.assertEqual(node._ChainStoreBasePlugin__erase_revoked_secrets(), 0)
    self.assertEqual(len(node.storage), 2)

  def test_marker_received_as_a_store_erases_the_revision(self):
    node = self._oracle("n0", ["n1"], dict([_revision("68", "opC")]))
    field, marker = _marker("68", "opC")
    node.default_handler(node.payload("n1", **_store(_hset_key(REVISIONS, field), marker, owner="dauth")))
    self.assertNotIn(_hset_key(REVISIONS, _revision("68", "opC")[0]), node.storage)
    self.assertTrue(node.storage[_hset_key(REVISIONS, field)][CS.CS_VALUE])

  def test_revoked_revision_cannot_come_back(self):
    node = self._oracle("n0", ["n1"], dict([_marker("68", "opC")]))
    field, full = _revision("68", "opC")
    node.default_handler(node.payload("n1", **_store(_hset_key(REVISIONS, field), full, owner="dauth")))
    self.assertNotIn(_hset_key(REVISIONS, field), node.storage)

  def test_sync_result_does_not_depend_on_which_peer_answers_first(self):
    stale_fields = dict([_revision("66", "opB")])
    partial = dict([_revision("66", "opB")])                                   # a peer that is itself stale
    healthy = dict([_revision("66", "opB", revoked=True), _marker("66", "opB")])
    for order in ((partial, healthy), (healthy, partial)):
      stale = self._oracle("stale", ["p0", "p1"], stale_fields)
      peers = [self._oracle("p%d" % idx, ["stale"], fields) for idx, fields in enumerate(order)]
      network = _FakeNetwork()
      for node in [stale] + peers:
        network.register(node)
      stale._hsync(REVISIONS, all_peers=True)
      value = stale.storage[_hset_key(REVISIONS, _revision("66", "opB")[0])][CS.CS_VALUE]
      self.assertEqual(value.get("revoked"), True)


class TestBackgroundSave(unittest.TestCase):

  def test_failed_save_marks_the_store_dirty_again(self):
    node = _Node("n0", default_peers=["n1"])
    node._ChainStoreBasePlugin__chain_storage_dirty = True

    def failing(value, **kwargs):
      raise OSError("disk full")
    node.cacheapi_save_pickle = failing
    node._ChainStoreBasePlugin__save_chain_storage()
    self.assertTrue(node._ChainStoreBasePlugin__chain_storage_dirty)

  def test_background_error_callback_marks_the_store_dirty_again(self):
    node = _Node("n0", default_peers=["n1"])
    captured = {}
    node.cacheapi_save_pickle = lambda value, **kwargs: captured.update(kwargs) or "path"
    node._ChainStoreBasePlugin__chain_storage_dirty = True
    self.assertEqual(node._ChainStoreBasePlugin__save_chain_storage(), "path")
    self.assertFalse(node._ChainStoreBasePlugin__chain_storage_dirty)
    self.assertTrue(captured["background"] and captured["strict"])
    captured["on_error"]("worker: disk full")
    self.assertTrue(node._ChainStoreBasePlugin__chain_storage_dirty)

  def test_flush_interval(self):
    node = _Node("n0", default_peers=["n1"])
    node.default_handler(node.payload("n1", **_store("k", "v")))
    node._now = CS.CS_SAVE_INTERVAL - 1
    node.process()
    self.assertEqual(len(node.saved_storage), 0)
    node._now = CS.CS_SAVE_INTERVAL + 1
    node.process()
    self.assertEqual(len(node.saved_storage), 1)



class _Inputs(_NetworkProcessorMixin):
  """The real network-processor intake with a minimal plugin around it."""

  def __init__(self, per_iteration):
    self.cfg_max_inputs_per_iteration = per_iteration
    self.cfg_inputs_batch_seconds = 1.0
    self.cfg_skip_message_verify = True
    self.cfg_accept_self = False
    self.cfg_full_debug_payloads = False
    self.cfg_log_stats_period = None
    self.cfg_instance_id, self._stream_id, self._signature, self.ee_addr = "inst", "stream", "SIG", "me"
    self.const = type("Const", (), {"PAYLOAD_DATA": type("PD", (), {"EE_PAYLOAD_PATH": "EE_PAYLOAD_PATH", "EE_SENDER": "EE_SENDER"})})
    self.queue, self.inputs, self.handled, self.messages, self._now = deque(), None, [], [], 0.0
    from collections import defaultdict
    self.defaultdict = defaultdict
    self.network_processor_init()

  def P(self, msg, color=None, **kwargs):
    self.messages.append(msg)

  def time(self):
    return self._now

  @property
  def input_queue_size(self):
    return len(self.queue)

  def get_next_avail_input(self):
    return self.queue.popleft()

  def dataapi_struct_datas(self, full=False, as_list=False):
    return list(self.inputs or [])

  @_NetworkProcessorMixin.payload_handler()
  def handle(self, payload):
    self._now += payload.get("cost", 0)
    if payload.get("boom"):
      raise RuntimeError("bad payload")
    self.handled.append(payload["n"])

  def iteration(self):
    self.inputs = self.queue.popleft() if self.queue else None
    self.network_processor_loop()

  @staticmethod
  def payload(n, **extra):
    return dict({"EE_PAYLOAD_PATH": ["other", "stream", "SIG", "inst"], "EE_SENDER": "other", "n": n}, **extra)


class TestInputIntake(unittest.TestCase):

  def test_default_takes_one_input_per_iteration(self):
    plugin = _Inputs(per_iteration=1)
    plugin.queue.extend([[plugin.payload(idx)] for idx in range(5)])
    plugin.iteration()
    self.assertEqual(plugin.handled, [0])
    self.assertEqual(len(plugin.queue), 4)

  def test_default_lets_a_handler_error_propagate(self):
    plugin = _Inputs(per_iteration=1)
    plugin.queue.append([plugin.payload(0, boom=True)])
    with self.assertRaises(RuntimeError):
      plugin.iteration()

  def test_batch_takes_inputs_up_to_the_limit(self):
    plugin = _Inputs(per_iteration=64)
    plugin.queue.extend([[plugin.payload(idx)] for idx in range(100)])
    plugin.iteration()
    self.assertEqual(plugin.handled, list(range(64)))
    plugin.iteration()
    self.assertEqual(len(plugin.handled), 100)

  def test_batch_stops_at_the_time_budget(self):
    plugin = _Inputs(per_iteration=64)
    plugin.queue.extend([[plugin.payload(idx, cost=0.3)] for idx in range(20)])
    plugin.iteration()
    self.assertEqual(plugin.handled, [0, 1, 2, 3])        # 4 x 0.3 s reaches the 1 s budget

  def test_failing_payload_costs_one_input_not_the_batch(self):
    plugin = _Inputs(per_iteration=64)
    plugin.queue.extend([[plugin.payload(0)], [plugin.payload(1, boom=True), plugin.payload(2)], [plugin.payload(3)], ["not a dict"]])
    plugin.iteration()
    self.assertEqual(plugin.handled, [0, 3])
    self.assertEqual(len([msg for msg in plugin.messages if "skipped after an error" in msg]), 1)
    self.assertEqual(len(plugin.queue), 0)


if __name__ == "__main__":
  unittest.main()
