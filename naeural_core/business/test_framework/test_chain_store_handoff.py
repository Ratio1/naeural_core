"""
Offline tests of the chain store's hand-off (switch CSTORE_HANDOFF): the writer re-sends its own writes to the target
peers that have not settled them, and the receiver admits a re-send by the order rule of 'on'.
"""
# The data folder of a started store.
import os
# Its temporary place.
import tempfile
# The test framework.
import unittest
# The block engine, NetMon and SerializationManager stand-ins.
from types import SimpleNamespace

# The manager on_init registers its save with.
from naeural_core.main.serialization_manager import SerializationManager

# The counter attribute, the harness that runs the real on_init, and the signed store's registries.
from test_chain_store_accounting import STATS, _StartHarness, make_signed_store
# The network that delivers at once, and the composer of a hash key.
from test_chain_store_hsync import _FakeNetwork, _hset_key
# The order tests' store builder, deliveries, records, local write, refusal and confirmation helpers and constants.
from test_chain_store_order import (BUCKET, EXPORT, KEY, MERGE, NOW, NOW_MS, SWEEP, TOMB, answers, confirm, deliver,
                                    local_write, make, record, refusal)

# The peer refresh and the side table, by their private names.
REFRESH, TABLE = "_ChainStoreBasePlugin__maybe_refresh_chain_peers", "_ChainStoreBasePlugin__handoff"


def writer(mode, peers=("peer1", "peer2"), oracle=True, stamped=None):
  """
  Build a writing chain store "node" for the hand-off tests, with a block engine, a NetMon and proven peers.

  Parameters
  ----------
  mode : str
    The mode of every switch (``'off'``, ``'report'`` or ``'on'``).
  peers : sequence of str, optional
    Its replication peers, all online and in the oracle registry; ``("peer1", "peer2")`` by default.
  oracle : bool, optional
    True (the default) puts the node in the oracle registry (window ``CSTORE_HANDOFF_WINDOW``); False makes it a
    worker (window 1).
  stamped : sequence of str, optional
    The peers that sent it a stamped store (the only ones a re-send goes to); all of ``peers`` by default.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store; its ``online`` set says which addresses NetMon reports ONLINE (the others LOST STATUS) and its
    ``whitelist`` list what the block engine whitelists.

  Examples
  --------
  >>> store = writer("on")
  >>> store.online
  {'peer1', 'peer2'}
  """
  # The order tests' store in this mode.
  store = make(mode, peers=peers)
  # Online and whitelisted: the peers.
  store.online, store.whitelist = set(peers), list(peers)
  # Registries: the oracle registry holds the peers (and the node unless it is a worker); no dAuth registry.
  store.bc = SimpleNamespace(
    node_address_to_eth_address=lambda address: address, get_eth_dauth_oracles=lambda: [],
    get_eth_oracles=lambda: (["node"] if oracle else []) + list(peers),
    get_whitelist=lambda with_prefix=False: list(store.whitelist),
  )
  # NetMon: ONLINE for the online set, LOST STATUS for the others.
  store.netmon = SimpleNamespace(
    network_node_simple_status=lambda peer, **kwargs: "ONLINE" if peer in store.online else "LOST STATUS")
  # The peers proven to stamp their stores.
  store._ChainStoreBasePlugin__stamped_peers.update(peers if stamped is None else stamped)
  # Ready.
  return store


def sweep(store, at):
  """
  Run one hand-off sweep of ``store`` at ``at`` seconds after the tests' wall clock, then send what it queued.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The writer.
  at : float
    Seconds after ``NOW``; a sweep less than ``CS_HANDOFF_INTERVAL`` after the last one does not run.

  Returns
  -------
  list of tuple
    One ``(recipients, body)`` per store the sweep sent.

  Examples
  --------
  >>> sweep(writer("on"), 300)
  []
  """
  # The sweep's wall clock.
  store._now = NOW + at
  # Only what this sweep sends.
  before = len(store.sent_payloads)
  # The sweep.
  getattr(store, SWEEP)()
  # The plugin loop sends what it queued.
  store._ChainStoreBasePlugin__maybe_broadcast()
  # Recipients and body of each store sent.
  return [(sent["node_addr"], sent["data"]["CHAIN_STORE_DATA"]) for sent in store.sent_payloads[before:]]


def handoff_counts(store):
  """
  Give the hand-off's counts of a store's CSTORE_STATS counters.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store.

  Returns
  -------
  dict
    The labels that name the hand-off, with their counts.

  Examples
  --------
  >>> handoff_counts(writer("on"))
  {}
  """
  # The labels of the hand-off.
  return {label: n for label, n in getattr(store, STATS).items() if "handoff" in label}


class TestResends(unittest.TestCase):
  """Which records are re-sent, to whom, when, and how often."""

  def test_an_unsettled_target_gets_the_record_after_the_first_delay_with_its_own_stamp(self):
    """In 'on' a write confirmed by peer1 only is re-sent to peer2 alone 300 s after it, with its own stamp and the
    resend mark, counted handoff/send/peer2; nothing goes before 300 s."""
    # The writer in 'on'.
    store = writer("on")
    # Its write, which no peer confirms in its wait.
    local_write(store, KEY, "v")
    # peer1 confirms it later, in the full form.
    confirm(store, KEY, "peer1", value="v", owner="node")
    # Nothing at 285 s; at 300 s the store, to peer2 alone.
    self.assertEqual((sweep(store, 285), sweep(store, 300)), ([], [(["peer2"], {
      "op": "SSTORE", "key": KEY, "value": "v", "owner": "node", "token": None, "readonly": False,
      "PEERS": ["peer2"], "INCLUDE_DEFAULT_PEERS": False, "ver": [NOW_MS, "node"], "resend": True})]))
    # Counted as the first re-send of the pair; one pair pending.
    self.assertEqual((handoff_counts(store), store._ChainStoreBasePlugin__handoff_pending),
                     ({"handoff/send/peer2": 1}, 1))

  def test_remote_legacy_and_raised_records_are_never_resent(self):
    """A remote record, an own record of the previous release (no stamp) and an own record whose stamp a peer's
    identical write raised are not re-sent; neither is a confirmed own record."""
    # The writer in 'on', holding a remote record and an own record of the previous release, both unconfirmed.
    store = writer("on")
    # A remote record.
    deliver(store, _hset_key("players", "remote"), "r", "peer1", [NOW_MS, "peer1"])
    # The previous release's own record: confirmation fields, no stamp.
    store.storage["legacy"] = {**record("legacy", "l", "node"), "confirms": 0, "min_confirms": 1,
                               "target_peers": ["peer1", "peer2"], "confirmed_peers": []}
    # Its own write of KEY, whose stamp peer2's identical write then raises.
    local_write(store, KEY, "v")
    # peer2's identical write with a newer stamp.
    deliver(store, KEY, "v", "peer2", [NOW_MS + 1000, "peer2"])
    # Its own write of another key, confirmed by both peers.
    local_write(store, _hset_key("players", "done"), "d")
    # Both confirm it.
    for peer in ("peer1", "peer2"):
      # In the full form.
      confirm(store, _hset_key("players", "done"), peer, value="d", owner="node")
    # A sweep long after: nothing to re-send, nothing pending.
    self.assertEqual((sweep(store, 4000), store._ChainStoreBasePlugin__handoff_pending), ([], 0))

  def test_the_back_off_schedule_then_the_give_up_in_on_and_in_report(self):
    """With no answer, a pair is re-sent at the record's age of 300, 600, 1200, 2400, 4800 and 8400 s and given up at
    12,000 s: in 'on' the stores go out and the give-up settles the peer in the record; in 'report' the same counts
    and table, but no store and no record change."""
    # The re-send ages and counts each mode gives.
    seen = {}
    # Each mode.
    for mode in ("on", "report"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The writer with one peer, which never answers.
        store = writer(mode, peers=("peer1",))
        # Its write.
        local_write(store, KEY, "v")
        # Sweeps every 15 s for 12,015 s; the ages at which a store went out.
        ages = [at for at in range(0, 12_030, 15) if sweep(store, at)]
        # The record as stored after the give-up.
        held = store.storage[KEY]
        # 'on' sent at the schedule and settled peer1; 'report' sent nothing and left the record as written.
        self.assertEqual((ages, held["confirmed_peers"]), ([300, 600, 1200, 2400, 4800, 8400], ["peer1"])
                         if mode == "on" else ([], []))
        # The counts, the same in both modes.
        seen[mode] = handoff_counts(store)
    # One first re-send, five later ones and one give-up, in both modes.
    self.assertEqual(seen, dict.fromkeys(("on", "report"), {
      "handoff/send/peer1": 1, "handoff/resend/peer1": 5, "handoff/gave_up/peer1": 1}))

  def test_an_offline_peer_uses_none_of_its_resends(self):
    """A target offline for 20,000 s (out of the peer list and LOST for NetMon) gets nothing and is not given up; once
    online it gets the record at the next sweep, as its first re-send."""
    # The writer in 'on', whose peer2 then goes offline.
    store = writer("on")
    # Its write.
    local_write(store, KEY, "v")
    # peer1 confirms.
    confirm(store, KEY, "peer1", value="v", owner="node")
    # peer2 offline: out of the peer list and LOST for NetMon.
    store.online, store._ChainStoreBasePlugin__chain_peers = {"peer1"}, ["peer1"]
    # Sweeps every 15 s for 20,000 s: nothing sent.
    self.assertEqual([at for at in range(0, 20_000, 15) if sweep(store, at)], [])
    # peer2 online again (NetMon reports it, before the refresh puts it back in the list).
    store.online.add("peer2")
    # The next sweep sends the store to it.
    self.assertEqual([peers for peers, _ in sweep(store, 20_010)], [["peer2"]])
    # Its first re-send, nothing given up.
    self.assertEqual(handoff_counts(store), {"handoff/send/peer2": 1})

  def test_a_peer_that_sent_no_stamped_store_gets_no_resend(self):
    """A re-send goes only to a peer whose last store (or confirmation) to this node carried a stamp: none before such
    a store, one after it, none again after an unstamped store (a node of the previous release)."""
    # The writer in 'on', with no peer proven yet.
    store = writer("on", peers=("peer1",), stamped=())
    # Its write, unconfirmed.
    local_write(store, KEY, "v")
    # Due at 300 s, but peer1 is not proven.
    self.assertEqual(sweep(store, 300), [])
    # peer1 sends a stamped store of another key.
    deliver(store, _hset_key("players", "p1"), "x", "peer1", [NOW_MS + 300_000, "peer1"])
    # Sent at the next sweep.
    self.assertEqual(len(sweep(store, 315)), 1)
    # peer1 then sends an unstamped store.
    deliver(store, _hset_key("players", "p2"), "y", "peer1")
    # Its next re-send is due 300 s later, but peer1 is no longer proven.
    self.assertEqual(sweep(store, 615), [])

  def test_a_worker_proves_its_oracles_by_their_stamped_confirmations(self):
    """A worker, which gets no store from the oracles, re-sends a write to an oracle once that oracle confirmed one
    of its writes with a stamp; a later confirmation without a stamp (the oracle back on the previous release)
    withdraws the proof, and the write's next re-send does not go to it."""
    # A worker with two oracles as peers, neither proven.
    store = writer("on", oracle=False, stamped=())
    # Its write, which peer1 confirms by stamp and peer2 misses.
    local_write(store, KEY, "v")
    # peer1's lean confirmation.
    confirm(store, KEY, "peer1", ver=store.storage[KEY]["sent_ver"])
    # A second write, which peer2 confirms by stamp.
    other = _hset_key("players", "other")
    # Written 0.2 s later.
    local_write(store, other, "o")
    # peer2's lean confirmation proves it.
    confirm(store, other, "peer2", ver=store.storage[other]["sent_ver"])
    # At 300 s KEY goes to peer2 (other is due only at 300.2 s).
    first = [(peers, body["key"]) for peers, body in sweep(store, 300)]
    # A third write, which peer2 confirms in the previous release's full form, without a stamp.
    third = _hset_key("players", "third")
    # Written.
    local_write(store, third, "t")
    # The confirmation without a stamp withdraws peer2's proof.
    confirm(store, third, "peer2", value="t", owner="node")
    # At 600 s KEY is due to peer2 again but not sent; other goes to peer1 (third waits: the worker's window is 1).
    second = [(peers, body["key"]) for peers, body in sweep(store, 600)]
    # KEY reached peer2 once, while it was proven.
    self.assertEqual((first, second), ([(["peer2"], KEY)], [(["peer1"], other)]))

  def test_the_window_per_peer_and_the_budget_per_sweep(self):
    """In 'on' at most 8 re-sends wait per peer (window_full counted for the others), 1 for a writer in neither
    registry, and at most 32 re-sends go per sweep; a sweep 1 s after another does not run, and a re-send whose next
    due time came leaves the window and goes again."""
    # An oracle writer with one peer and a worker writer with one peer, ten unconfirmed records each.
    for oracle, sent in ((True, 8), (False, 1)):
      # One sub-test each.
      with self.subTest(oracle=oracle):
        # The writer.
        store = writer("on", peers=("peer1",), oracle=oracle)
        # Ten writes.
        for n in range(10):
          # Each unconfirmed.
          local_write(store, _hset_key("players", str(n)), n)
        # The first sweep at 310 s, when all ten are due (each write moved the fake clock 0.2 s): as many as the
        # window, the others counted as held back.
        self.assertEqual((len(sweep(store, 310)), handoff_counts(store)),
                         (sent, {"handoff/send/peer1": sent, "handoff/window_full/peer1": 10 - sent}))
        if oracle:
          # 1 s later the sweep does not run: nothing sent, no count changes.
          self.assertEqual((len(sweep(store, 311)), handoff_counts(store)),
                           (0, {"handoff/send/peer1": 8, "handoff/window_full/peer1": 2}))
          # At 610 s the 8 re-sends reached their next due time, so they leave the window and go again; the 2 held
          # pairs still wait (the walk reaches records 0-7 first).
          self.assertEqual((len(sweep(store, 610)), handoff_counts(store)),
                           (8, {"handoff/send/peer1": 8, "handoff/resend/peer1": 8, "handoff/window_full/peer1": 4}))
    # An oracle writer with five peers and ten unconfirmed records: 50 pairs, 40 within the windows.
    store = writer("on", peers=tuple("peer" + str(n) for n in range(5)))
    # Ten writes.
    for n in range(10):
      # Each unconfirmed.
      local_write(store, _hset_key("players", str(n)), n)
    # The first sweep, when all are due, sends 32 pairs (in fewer stores: one store per record to its due peers).
    sweep(store, 310)
    # 32 first re-sends.
    self.assertEqual(sum(n for label, n in handoff_counts(store).items() if "/send/" in label), 32)

  def test_report_counts_each_due_pair_without_a_window(self):
    """In 'report', where nothing is answered, the window is not applied: ten due pairs to one peer are all counted
    as first re-sends at their due time, none as window_full, and nothing is sent."""
    # An oracle writer in 'report' with one peer.
    store = writer("report", peers=("peer1",))
    # Ten writes.
    for n in range(10):
      # Each unconfirmed.
      local_write(store, _hset_key("players", str(n)), n)
    # The sweep at 310 s, when all ten are due.
    self.assertEqual((sweep(store, 310), handoff_counts(store)), ([], {"handoff/send/peer1": 10}))

  def test_the_registry_is_read_only_for_a_due_pair_in_on(self):
    """The window's registry read happens only in 'on', once per sweep, when a due pair reaches the window: a sweep
    with nothing due reads no registry, and neither does any sweep in 'report'."""
    # Per mode, the registry reads after each sweep.
    reads = {}
    # Each mode.
    for mode in ("on", "report"):
      # The writer.
      store = writer(mode)
      # Its oracle registry reads.
      calls = []
      # The oracle registry, counting each read.
      store.bc.get_eth_oracles = lambda: calls.append(1) or ["node", "peer1", "peer2"]
      # Its write, unconfirmed.
      local_write(store, KEY, "v")
      # A sweep with nothing due (15 s), then one with both pairs due (300 s).
      for at in (15, 300):
        # The sweep.
        sweep(store, at)
        # The reads so far.
        reads.setdefault(mode, []).append(len(calls))
    # 'on' read once, at the sweep with due pairs; 'report' never.
    self.assertEqual(reads, {"on": [0, 1], "report": [0, 0]})

  def test_report_counts_answered_only_for_a_confirmation_after_the_pair_fell_due(self):
    """In 'report' a confirmation that arrives after a sweep saw the pair but before it fell due is not counted
    answered; one that arrives after the pair fell due (a re-send 'on' would have wasted) is counted once."""
    # The writer in 'report'.
    store = writer("report")
    # Its write, unconfirmed.
    local_write(store, KEY, "v")
    # A sweep at 15 s tracks both pairs, not yet due.
    sweep(store, 15)
    # peer1 confirms then, by stamp.
    confirm(store, KEY, "peer1", ver=[NOW_MS, "node"])
    # Nothing counted yet.
    early = getattr(store, STATS).get(BUCKET + "/handoff/answered")
    # At 300 s peer2's pair falls due ('report' sends nothing).
    sweep(store, 300)
    # peer2 confirms then, by stamp.
    confirm(store, KEY, "peer2", ver=[NOW_MS, "node"])
    # Only peer2's late confirmation is counted.
    self.assertEqual((early, getattr(store, STATS).get(BUCKET + "/handoff/answered")), (None, 1))

  def test_a_sweep_that_raises_keeps_the_last_table(self):
    """A sweep that raises on a record (an own record without default_peers, a shape no release writes, while a peer
    joined) leaves the last sweep's table in place, with each pair's re-send count, so no back-off starts again."""
    # The writer in 'report'.
    store = writer("report")
    # Two writes, unconfirmed.
    local_write(store, KEY, "v")
    # The second.
    other = _hset_key("players", "other")
    # Written 0.2 s later.
    local_write(store, other, "o")
    # At 315 s both pairs of both records are counted as first re-sends.
    sweep(store, 315)
    # The second record loses default_peers.
    del store.storage[other]["default_peers"]
    # peer3 joins, as a refresh notes it.
    store._ChainStoreBasePlugin__peer_back["peer3"] = None
    # The next sweep raises on the second record.
    with self.assertRaises(KeyError):
      # The sweep.
      sweep(store, 330)
    # Both records' entries are kept with their re-send counts (the first one also tracks the joined peer3).
    self.assertEqual({key: {peer: pair[0] for peer, pair in pairs.items()} for key, (_, pairs) in
                      getattr(store, TABLE).items()},
                     {KEY: {"peer1": 1, "peer2": 1, "peer3": 0}, other: {"peer1": 1, "peer2": 1}})

  def test_a_confirmation_and_a_refusal_answer_each_settle_a_pair(self):
    """After the first re-send, peer1's confirmation (counted answered) and peer2's refusal answer each end their
    pair: nothing is re-sent later and nothing is pending."""
    # The writer in 'on'.
    store = writer("on")
    # Its write, unconfirmed.
    local_write(store, KEY, "v")
    # Re-sent to both at 300 s.
    self.assertEqual([peers for peers, _ in sweep(store, 300)], [["peer1", "peer2"]])
    # peer1 confirms by stamp; peer2 refuses with an answer.
    confirm(store, KEY, "peer1", ver=[NOW_MS, "node"])
    # The answer of peer2.
    refusal(store, KEY, "peer2", [NOW_MS, "node"])
    # Nothing later, nothing pending, and the confirmation counted as answered.
    self.assertEqual((sweep(store, 600), store._ChainStoreBasePlugin__handoff_pending,
                      getattr(store, STATS).get(BUCKET + "/handoff/answered")), ([], 0, 1))

  def test_a_secret_record_is_resent_only_between_registry_members(self):
    """A dAuth secret record is re-sent only to a target still in the dAuth registry, and a peer that joins later is
    not added to its targets."""
    # The signed store "node" (dAuth registry node and peer1) in 'on', with peers peer1 and peer2.
    store = make("on", signed=True)
    # peer2 in the dAuth registry at the write.
    store.bc.get_eth_dauth_oracles = lambda: ["node", "peer1", "peer2"]
    # Both peers proven, and online.
    store._ChainStoreBasePlugin__stamped_peers.update(("peer1", "peer2"))
    # The secret's key.
    key = _hset_key("DAUTH_JOB_SECRETS", "7")
    # The coordinator's bundle, written here unconfirmed.
    store._set_value(key, {"job_id": "7", "secret": "s"}, owner="node", timeout=.1, max_retries=0)
    # Its store sent, as the plugin loop does.
    store._ChainStoreBasePlugin__maybe_broadcast()
    # peer2 leaves the dAuth registry; peer3 joins the peers.
    store.bc.get_eth_dauth_oracles = lambda: ["node", "peer1", "peer3"]
    # The join, as a refresh notes it.
    store._ChainStoreBasePlugin__peer_back["peer3"] = None
    # The re-send goes to peer1 alone; peer3 is not a target.
    self.assertEqual(([peers for peers, _ in sweep(store, 300)], store.storage[key]["target_peers"]),
                     ([["peer1"]], ["peer1", "peer2"]))

  def test_a_secret_record_is_not_resent_once_this_node_left_the_registries(self):
    """A dAuth secret record is not re-sent, even to a dAuth registry member, once this node itself is in neither
    registry."""
    # The signed store "node" in 'on', with peer1 and peer2 in the dAuth registry and proven.
    store = make("on", signed=True)
    # The dAuth registry at the write.
    store.bc.get_eth_dauth_oracles = lambda: ["node", "peer1", "peer2"]
    # Both peers proven.
    store._ChainStoreBasePlugin__stamped_peers.update(("peer1", "peer2"))
    # The secret's key.
    key = _hset_key("DAUTH_JOB_SECRETS", "7")
    # The coordinator's bundle, written here unconfirmed.
    local_write(store, key, {"job_id": "7", "secret": "s"})
    # The node leaves the dAuth registry; the oracle registry holds only "oracle".
    store.bc.get_eth_dauth_oracles = lambda: ["peer1", "peer2"]
    # At 300 s nothing goes.
    self.assertEqual(sweep(store, 300), [])

  def test_a_revoked_secret_payload_is_not_resent_but_a_marker_and_an_erasure_are(self):
    """Once this node holds a secret revision's revocation marker it does not re-send its own plaintext payload of
    that revision, while its own marker and redacted erasure of another revision are still re-sent."""
    # The signed store "node" in 'on', with peer1 and peer2 in the dAuth registry and proven.
    store = make("on", signed=True)
    # The dAuth registry.
    store.bc.get_eth_dauth_oracles = lambda: ["node", "peer1", "peer2"]
    # Both peers proven.
    store._ChainStoreBasePlugin__stamped_peers.update(("peer1", "peer2"))
    # Revision 1 of job 7 and its marker; revision 2, its marker and its redacted erasure's key.
    payload, marker, payload2, marker2 = (_hset_key("DAUTH_JOB_SECRET_REVISIONS", field)
                                          for field in ("7|1", "7|1|revoked", "7|2", "7|2|revoked"))
    # The node's plaintext payload of revision 1, unconfirmed.
    local_write(store, payload, {"job_id": "7", "secret": "s"})
    # peer1 revokes revision 1: the node gets the marker (and misses the erasure).
    deliver(store, marker, {"job_id": "7", "revision": 1}, "peer1", [NOW_MS + 1000, "peer1"])
    # The node revokes revision 2 itself: its marker, then its redacted erasure, both unconfirmed.
    local_write(store, marker2, {"job_id": "7", "revision": 2})
    # The erasure.
    local_write(store, payload2, {"job_id": "7", "revision": 2, "revoked": True})
    # At 315 s every own record is due: the marker and the erasure go, the revoked plaintext does not.
    self.assertEqual([body["key"] for _, body in sweep(store, 315)], [marker2, payload2])

  def test_off_keeps_no_state_and_counts_nothing(self):
    """With the switch 'off' the sweep sends nothing, keeps no table and counts nothing."""
    # The writer in 'off'.
    store = writer("off")
    # Its write, unconfirmed.
    local_write(store, KEY, "v")
    # A sweep long after.
    self.assertEqual((sweep(store, 4000), getattr(store, TABLE), handoff_counts(store)), ([], {}, {}))

  def test_a_local_delete_is_resent_as_a_delete_and_lands_as_a_tombstone(self):
    """The tombstone of a local delete is re-sent as a store of value None under the deleted key; a receiver that
    missed the delete removes the key and keeps the tombstone, and its confirmation settles the pair."""
    # A network that delivers at once.
    network = _FakeNetwork()
    # The writer and a peer holding KEY, both in 'on'; the peer is not on the network until the re-send.
    store, peer = writer("on", peers=("peer1",)), make("on", "peer1", peers=("node",),
                                                       storage={KEY: record(KEY, "v", "node", [NOW_MS - 1000, "node"])})
    # The writer's delete, which the peer misses.
    local_write(store, KEY, None)
    # The writer and the peer on the network from here.
    for node in (store, peer):
      # Reachable.
      network.register(node)
    # The peer's clock.
    peer._now = NOW + 300
    # The re-send at 300 s: the delete under KEY.
    sent = sweep(store, 300)
    # One store of value None under KEY, with the delete's stamp.
    self.assertEqual([(body["key"], body["value"], body["ver"]) for _, body in sent], [(KEY, None, [NOW_MS, "node"])])
    # The peer dropped the key and holds the tombstone; the writer's tombstone is settled by peer1.
    self.assertEqual((KEY in peer.storage, peer.storage[TOMB]["ver"], store.storage[TOMB]["confirmed_peers"]),
                     (False, [NOW_MS, "node"], ["peer1"]))


class TestPeersThatJoinOrComeBack(unittest.TestCase):
  """Widening and re-confirm: in 'on' they change the record's lists, in 'report' only the table."""

  def test_a_peer_that_joins_later_gets_the_records_meant_for_every_default_peer(self):
    """A record written to peer1 alone (peer2 not yet a peer) goes to peer2 once a refresh adds it and peer2 proved
    after joining that it stamps its stores: in 'on' peer2 becomes a target, in 'report' the record is left as
    written and the send is only counted."""
    # Each mode.
    for mode in ("on", "report"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The writer with peer1 alone, and peer1 confirming.
        store = writer(mode, peers=("peer1",))
        # Its write.
        local_write(store, KEY, "v")
        # peer1 confirms.
        confirm(store, KEY, "peer1", value="v", owner="node")
        # peer2 whitelisted and online; the refresh is due.
        store.whitelist.append("peer2")
        # Online.
        store.online.add("peer2")
        # A refresh more than 60 s after the last.
        store._now = NOW + 120
        # The refresh notes the join.
        getattr(store, REFRESH)()
        # peer2 then proves it stamps its stores, with a stamped store of a key the writer does not own.
        deliver(store, _hset_key("players", "p2"), "x", "peer2", [NOW_MS + 150_000, "peer2"])
        # The sweep at 300 s.
        sent = sweep(store, 300)
        # Sent to peer2 in 'on' (now a target); counted only in 'report' (targets as written).
        self.assertEqual(([peers for peers, _ in sent], store.storage[KEY]["target_peers"], handoff_counts(store)),
                         ([["peer2"]] if mode == "on" else [], ["peer1", "peer2"] if mode == "on" else ["peer1"],
                          {"handoff/send/peer2": 1}))

  def test_a_tunnel_record_is_not_widened_to_a_joining_peer(self):
    """A Tunnels Manager record, written to the oracle registry members among the peers only, is not widened to a
    peer that joins later outside the registry: it is not re-sent to it and does not become a target."""
    # The writer in 'on' with peer1, an oracle registry member.
    store = writer("on", peers=("peer1",))
    # A tunnel credential's key.
    key = _hset_key("tunnels_manager_secrets", "0xabc")
    # Written unconfirmed.
    local_write(store, key, {"cs_rev": 1})
    # peer2 whitelisted and online, not in the oracle registry.
    store.whitelist.append("peer2")
    # Online.
    store.online.add("peer2")
    # A refresh more than 60 s after the last.
    store._now = NOW + 120
    # The refresh notes the join.
    getattr(store, REFRESH)()
    # peer2 then proves it stamps its stores.
    deliver(store, _hset_key("players", "p2"), "x", "peer2", [NOW_MS + 150_000, "peer2"])
    # At 300 s the record goes to peer1 alone, and its targets stay peer1.
    self.assertEqual(([peers for peers, _ in sweep(store, 300)], store.storage[key]["target_peers"]),
                     ([["peer1"]], ["peer1"]))

  def test_a_peer_back_after_being_lost_is_unsettled_on_its_recent_records_only(self):
    """A peer dropped as LOST and back, once it proved again that it stamps its stores, has its settlement re-opened
    on the records written at most 300 s before it was dropped, not on older ones; a peer that stopped in order
    (SHUTDOWN) has none re-opened."""
    # Each way of leaving, and whether the recent record is re-sent.
    for status, reopened in (("LOST STATUS", True), ("SHUTDOWN", False)):
      # One sub-test each.
      with self.subTest(status=status):
        # The writer in 'on', peer1 alone.
        store = writer("on", peers=("peer1",))
        # NetMon reports the status of a peer that left.
        store.netmon.network_node_simple_status = lambda peer, **kwargs: "ONLINE" if peer in store.online else status
        # An old write at 0 s and a recent one at 500 s, both confirmed by peer1.
        local_write(store, _hset_key("players", "old"), "o")
        # Confirmed.
        confirm(store, _hset_key("players", "old"), "peer1", value="o", owner="node")
        # The recent write.
        store._now = NOW + 500
        # Written.
        local_write(store, KEY, "v")
        # Confirmed.
        confirm(store, KEY, "peer1", value="v", owner="node")
        # peer1 leaves at 700 s.
        store.online = set()
        # The refresh drops it.
        store._now = NOW + 700
        # Dropped.
        getattr(store, REFRESH)()
        # Back at 800 s.
        store.online = {"peer1"}
        # The refresh adds it.
        store._now = NOW + 800
        # Back.
        getattr(store, REFRESH)()
        # It left, so it proves again with a stamped store (of a key the writer does not own).
        deliver(store, _hset_key("players", "p"), "x", "peer1", [NOW_MS + 850_000, "peer1"])
        # The sweep at 900 s: only the recent record re-opened, re-sent to peer1, and unsettled in the record.
        self.assertEqual(([body["key"] for _, body in sweep(store, 900)], store.storage[KEY]["confirmed_peers"]),
                         ([KEY], []) if reopened else ([], ["peer1"]))

  def test_a_peer_that_left_proves_again_before_a_resend(self):
    """A proven peer that stops in order and comes back gets no re-send of a record written while it was away until
    it proves again after it joined: a stamped confirmation it sent before it stopped, processed after the refresh
    that dropped it, does not prove it past its return; a confirmation without a stamp (as the previous release
    sends) keeps it unproven, a stamped one proves it and the record goes to it at the next sweep."""
    # The writer in 'on' with peer1 and peer2, both proven.
    store = writer("on")
    # NetMon reports a peer that left as stopped in order.
    store.netmon.network_node_simple_status = lambda peer, **kwargs: "ONLINE" if peer in store.online else "SHUTDOWN"
    # peer1 stops.
    store.online = {"peer2"}
    # The refresh at 61 s.
    store._now = NOW + 61
    # It drops peer1.
    getattr(store, REFRESH)()
    # A stamped confirmation peer1 sent before it stopped, processed only now (an input queue that lags).
    confirm(store, _hset_key("players", "earlier"), "peer1", ver=[NOW_MS, "node"])
    # A write at 100 s.
    store._now = NOW + 100
    # Sent to peer2 alone.
    local_write(store, KEY, "v")
    # peer2 confirms it by stamp.
    confirm(store, KEY, "peer2", ver=store.storage[KEY]["sent_ver"])
    # peer1 back at 400 s, having sent nothing.
    store.online = {"peer1", "peer2"}
    # The refresh at 400 s.
    store._now = NOW + 400
    # It adds peer1 back.
    getattr(store, REFRESH)()
    # The key of another write, which peer1's confirmations name.
    other = _hset_key("players", "other")
    # KEY is due for peer1 (widened to it), which is not proven: nothing goes.
    first = sweep(store, 415)
    # peer1 confirms in the full form without a stamp.
    confirm(store, other, "peer1", value="o", owner="node")
    # Still nothing.
    second = sweep(store, 430)
    # peer1 confirms with a stamp.
    confirm(store, other, "peer1", ver=[NOW_MS, "node"])
    # KEY goes to peer1 now.
    third = [(peers, body["key"]) for peers, body in sweep(store, 445)]
    # Nothing until the stamped confirmation, then the record.
    self.assertEqual((first, second, third), ([], [], [(["peer1"], KEY)]))


class TestReceiverOfAResend(unittest.TestCase):
  """A receiver admits a re-send by the live-write steps of 'on' in 'report' too, and answers a refused one."""

  def test_report_refuses_and_answers_an_older_resend_and_applies_the_same_live_store(self):
    """In 'report' an older re-send is refused and answered while the same store without the mark is applied as in
    the previous release; a re-send over an unstamped copy is applied; a re-send without cs_rev over a value with
    cs_rev is refused as older."""
    # A receiver in 'report' holding KEY stamped at +2 s, an unstamped copy and a value with cs_rev.
    store = make("report", storage={KEY: record(KEY, "b", "peer1", [NOW_MS + 2000, "peer1"]),
                                    "legacy": record("legacy", "old", "peer1"),
                                    "rev": record("rev", {"t": "v", "cs_rev": 5}, "peer1", [NOW_MS, "peer1"])})
    # The store a re-send carries: its body as the network delivers it.
    resend = {"op": "SSTORE", "key": KEY, "value": "a", "owner": "peer2", "ver": [NOW_MS + 1000, "peer2"],
              "resend": True}
    # Delivered from peer2.
    store.default_handler({"EE_SENDER": "peer2", "EE_ID": "peer2", "EE_DESTINATION": ["node"],
                           "EE_IS_ENCRYPTED": True, "decoded": {"CHAIN_STORE_DATA": resend}})
    # Refused with an answer, the held value kept.
    self.assertEqual((store.sent_payloads[-1]["data"]["CHAIN_STORE_DATA"].get("refused"), store.storage[KEY]["value"]),
                     ("older", "b"))
    # The same store without the mark: applied, as the previous release.
    self.assertEqual((answers(deliver(store, KEY, "a", "peer2", [NOW_MS + 1000, "peer2"])),
                      store.storage[KEY]["value"]), (["confirm"], "a"))
    # Re-sends over the unstamped copy and without cs_rev over cs_rev.
    for key, value in (("legacy", "new"), ("rev", {"t": "w"})):
      # Delivered as re-sends from peer2, stamped later than the held ones.
      store.default_handler({"EE_SENDER": "peer2", "EE_ID": "peer2", "EE_DESTINATION": ["node"],
                             "EE_IS_ENCRYPTED": True, "decoded": {"CHAIN_STORE_DATA": {
                               "op": "SSTORE", "key": key, "value": value, "owner": "peer2",
                               "ver": [NOW_MS + 5000, "peer2"], "resend": True}}})
    # The unstamped copy replaced; the value with cs_rev kept, that re-send refused as older (counted under the plain
    # bucket, the first refusal under KEY's).
    self.assertEqual((store.storage["legacy"]["value"], store.storage["rev"]["value"],
                      getattr(store, STATS).get("plain/remote/refused_older")), ("new", {"t": "v", "cs_rev": 5}, 1))


class TestRecordsAndStart(unittest.TestCase):
  """The record fields the hand-off needs, the sync answer's fields, and the start check."""

  def test_remote_records_have_no_peer_lists_and_a_sync_answer_carries_five_fields(self):
    """A remote write is stored with confirms -1 and no peer lists, and a late confirmation of it adds none; an own
    record carries default_peers; a sync answer carries only key, value, owner, readonly and the stamp."""
    # A store in 'report'.
    store = writer("report")
    # A remote write and an own one.
    deliver(store, KEY, "r", "peer1", [NOW_MS, "peer1"])
    # The own one.
    local_write(store, _hset_key("players", "own"), "o")
    # A confirmation of the remote record by value.
    confirm(store, KEY, "peer2", value="r", owner="peer1")
    # The remote record's fields, and the own record's default_peers.
    self.assertEqual((sorted(store.storage[KEY]), store.storage[_hset_key("players", "own")]["default_peers"]),
                     (["confirms", "key", "min_confirms", "owner", "readonly", "token", "value", "ver"], True))
    # The sync answer's records: five fields each.
    self.assertEqual({key: sorted(rec) for key, rec in getattr(store, EXPORT)("players").items()},
                     dict.fromkeys((KEY, _hset_key("players", "own")), ["key", "owner", "readonly", "value", "ver"]))
    # Merged by another store of this release, as before.
    other = make("report", "other")
    # One record changed per answered record.
    self.assertEqual(getattr(other, MERGE)("players", getattr(store, EXPORT)("players")), 2)

  def test_handoff_on_needs_order_on_tombstones_and_stamped_confirmations(self):
    """on_init stops with hand-off 'on' while the order switch is 'report', tombstone days or the tombstone cap are 0,
    or the lean switch is 'off'; it starts with every switch 'on', and with the lean switch 'report'."""
    # A data folder with a cache folder, removed after the test.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # The cache folder of the plugin.
    os.makedirs(os.path.join(folder.name, "chain_store"))
    # The manager in use, put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", SerializationManager.instance)
    # A manager that takes the registration.
    SerializationManager.instance = SimpleNamespace(register=lambda save, every: None)
    # Each start: the order switch, the tombstone days and cap, the lean switch, and whether it starts.
    for order, days, cap, lean, starts in (("report", 7, 570, "on", False), ("on", 0, 570, "on", False),
                                           ("on", 7, 0, "on", False), ("on", 7, 570, "off", False),
                                           ("on", 7, 570, "report", True), ("on", 7, 570, "on", True)):
      # One sub-test each.
      with self.subTest(order=order, days=days, cap=cap, lean=lean):
        # A store whose hand-off switch is 'on', its other switches and tombstone numbers as given.
        store = _StartHarness(folder.name)
        # The switches.
        store.cfg_cstore_order, store.cfg_cstore_lean_confirm, store.cfg_cstore_handoff = order, lean, "on"
        # The save back-off and the tombstone numbers.
        store.cfg_cstore_save_backoff, store.cfg_cstore_tombstone_days, store.cfg_cstore_tombstone_cap = "on", days, cap
        # An empty whitelist for the refresh of on_init.
        store.bc = SimpleNamespace(get_whitelist=lambda with_prefix=False: [])
        if starts:
          # The real on_init starts and publishes the setter.
          store.on_init()
          # Published.
          self.assertIn(store.CS_SETTER, store.plugins_shmem)
        else:
          # The start stops, naming the hand-off switch.
          with self.assertRaisesRegex(ValueError, "CSTORE_HANDOFF 'on' needs"):
            # The real on_init.
            store.on_init()


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
