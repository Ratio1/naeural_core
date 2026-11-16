"""
Offline tests of the chain store's order rule (switch CSTORE_ORDER): the version stamp every record and store carries,
the steps that decide which of two writes is newer, and convergence of several replicas over random histories.
"""
# Shapes of the replicas compared after a history.
import collections
# The records a replica holds at the start of a test.
import copy
# The CSTORE_STATS line.
import json
# The replica files a start reads.
import os
# The replica file of a start, and the size of a stamped replica.
import pickle
# The seeded histories of the convergence runs.
import random
# A data folder for a start.
import tempfile
# The test framework.
import unittest
# A stand-in for the SerializationManager a start registers its save with.
from types import SimpleNamespace
# Builds every harness of a test in the mode it asks for.
from unittest import mock

# The manager on_init registers its save with and on_close removes it from.
from naeural_core.main.serialization_manager import SerializationManager

# The signed store of the accounting tests (a block engine that accepts records signed "valid"), the harness that
# runs the real on_init, the counter attribute and the signed pointer fixture.
from test_chain_store_accounting import STATS, SIGNED_POINTER, _StartHarness, make_signed_store
# The harness, its fake network, the composer of a hash key and the payload field names.
from test_chain_store_hsync import _ChainStoreRuntimeHarness, _FakeNetwork, _PayloadData, _hset_key
# The harness of the plugin API (chainstore_get, hget, hlist, hgetall) over a store's memory.
from test_chain_store_hsync import _PeerSelectionHarness

# The wall clock of the tests, in seconds, and the same in ms (the unit of a stamp).
NOW, NOW_MS = 1_700_000_000.0, 1_700_000_000_000
# The private names of the store's state read here.
DIRTY, UNMARKED = "_ChainStoreBasePlugin__chain_storage_dirty", "_ChainStoreBasePlugin__chain_storage_unmarked"
# The stamp clock, the stamp of a local write, the merge and the export.
CLOCK, TICK = "_ChainStoreBasePlugin__ver_clock", "_ChainStoreBasePlugin__ver_tick"
# The merge of a sync answer and the export that answers a sync request.
MERGE, EXPORT = "_ChainStoreBasePlugin__merge_hset_snapshot", "_ChainStoreBasePlugin__export_hset_snapshot"
# The hand-off's sweep.
SWEEP = "_ChainStoreBasePlugin__maybe_handoff"
# The namespace and the field most tests write.
KEY = _hset_key("players", "alpha")
# Its bucket, the 14-character "hs:" prefix the counters use.
BUCKET = KEY[:14]
# The key a tombstone of KEY is held under.
TOMB = "dl:" + KEY
# One day in ms, the unit of a tombstone's life.
DAY_MS = 86_400_000


def make(mode, address="node", peers=("peer1", "peer2"), storage=None, signed=False):
  """
  Build a chain store with every switch of the store in ``mode``, at the tests' wall clock.

  Parameters
  ----------
  mode : str
    ``'off'``, ``'report'`` or ``'on'``, for every switch (only the order switch acts in this module).
  address : str, optional
    The node's address; ``"node"`` by default.
  peers : sequence of str, optional
    Its replication peers; ``("peer1", "peer2")`` by default.
  storage : dict, optional
    Records it holds (copied); none by default.
  signed : bool, optional
    True for the store of ``make_signed_store`` (a block engine that accepts records signed "valid", dAuth
    registry ``node`` and ``peer1``, oracle registry ``oracle``); ``address``, ``peers`` and ``storage`` are then
    applied to it. False by default.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store, with an empty send queue and an instance path for its confirmations.

  Examples
  --------
  >>> make("on")._ChainStoreBasePlugin__cs_modes["order"]
  'on'
  """
  # The harness reads its mode when it is built.
  with mock.patch.object(_ChainStoreRuntimeHarness, "CSTORE_MODE", mode):
    # The signed store, or a plain one.
    store = make_signed_store() if signed else _ChainStoreRuntimeHarness(address)
  # Its address, peers and records.
  store.ee_addr, store._ChainStoreBasePlugin__chain_peers = address, list(peers)
  # The records, copied so a test's fixture is not changed.
  store.storage.update(copy.deepcopy(storage or {}))
  # The send queue of local writes, and the instance path a confirmation names.
  store._ChainStoreBasePlugin__ops = collections.deque()
  # A list's copy method returns the path when called.
  store.get_instance_path = [address, "stream", "CHAIN_STORE", "instance"].copy
  # The tests' wall clock.
  store._now = NOW
  # Ready.
  return store


def deliver(store, key, value, sender, ver=None, owner=None):
  """
  Deliver one live store from ``sender`` to ``store`` through its payload handler, as the network does.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The receiving replica.
  key : str
    Composed key of the write.
  value : any
    The value; None for a delete.
  sender : str
    The verified sender (``EE_SENDER``).
  ver : list, optional
    The stamp the store carries; None (the default) sends an unstamped store, as the previous release does.
  owner : str, optional
    The owner the store names; the sender by default.

  Returns
  -------
  list of dict
    The confirmation bodies the replica sent back for this store: one when it applied it, none when it refused.

  Examples
  --------
  >>> deliver(make("on"), KEY, "v", "peer1", [NOW_MS, "peer1"])
  [{'op': 'SCONFIRM', 'key': ..., 'value': 'v', ...}]
  """
  # Only what this store makes the replica send.
  before = len(store.sent_payloads)
  # The store's body: the previous release's fields, and the stamp when there is one.
  data = {store.CS_OP: store.CS_STORE, store.CS_KEY: key, store.CS_VALUE: value, store.CS_OWNER: owner or sender,
          **({store.CS_VER: ver} if ver is not None else {})}
  # The payload as the network delivers it; the handler takes a copy of the body.
  store.default_handler({_PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
                         _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                         "decoded": {store.CS_DATA: data}})
  # The bodies sent back.
  return [sent["data"][store.CS_DATA] for sent in store.sent_payloads[before:]]


def answers(bodies):
  """
  Name what a replica sent back for one store: ``"confirm"`` for a confirmation, the word of a refusal answer.

  Parameters
  ----------
  bodies : list of dict
    The bodies ``deliver`` returns.

  Returns
  -------
  list of str
    One name per body: ``"confirm"``, or the ``refused`` word (``"older"``, ``"origin"``, ...).

  Examples
  --------
  >>> answers([{"op": "SCONFIRM", "key": KEY, "ver": [NOW_MS, "peer1"], "refused": "older", "held_ver": None}])
  ['older']
  """
  # A refusal answer carries its word; a confirmation carries none.
  return [body.get("refused", "confirm") for body in bodies]


def record(key, value, owner, ver=None):
  """
  Build one stored record of a remote write, as a replica holds it.

  Parameters
  ----------
  key : str
    Composed key.
  value : any
    The value.
  owner : str
    The owner.
  ver : list, optional
    Its stamp; none by default (a record of the previous release).

  Returns
  -------
  dict
    The record.

  Examples
  --------
  >>> record(KEY, "v", "peer1", [NOW_MS, "peer1"])["ver"]
  [1700000000000, 'peer1']
  """
  # The fields every record has, a remote record's confirmation fields, and the stamp when given.
  return {"key": key, "value": value, "owner": owner, "readonly": False, "token": None, "confirms": -1,
          "min_confirms": 0, **({"ver": ver} if ver is not None else {})}


def local_write(store, key, value):
  """
  Make a local write on ``store`` that waits one short round for confirmations, then queue its sends.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The writing replica.
  key : str
    Composed key.
  value : any
    The value; None for a delete.

  Returns
  -------
  bool
    What ``_set_value`` returned (False when no confirmation came back in time).

  Examples
  --------
  >>> local_write(make("on"), KEY, "v")
  False
  """
  # One round of 0.1 s and no retry: the fake clock moves 0.2 s.
  result = store._set_value(key, value, owner=store.ee_addr, timeout=.1, max_retries=0)
  # Send what the write queued, as the plugin loop does.
  store._ChainStoreBasePlugin__maybe_broadcast()
  # What the write returned.
  return result


class TestStamps(unittest.TestCase):
  """Every write takes a stamp from a clock held near the wall clock; 'off' makes and reads none."""

  def test_a_local_write_is_stamped_and_sends_its_stamp_and_off_makes_none(self):
    """In 'report' a local write's record and store carry a fresh stamp, two in one ms differ by one ms and an
    identical rewrite takes a newer stamp without a dirty mark; in 'off' neither record nor store carries one."""
    # Report: the stamps are made and sent.
    store = make("report")
    # A local write.
    local_write(store, KEY, "a")
    # Its record and its queued store carry [wall clock in ms, own address].
    self.assertEqual((store.storage[KEY]["ver"], store.sent_payloads[-1]["data"][store.CS_DATA]["ver"]),
                     ([NOW_MS, "node"], [NOW_MS, "node"]))
    # The saved state of the replica, so the identical rewrite below can be seen not to mark it.
    setattr(store, DIRTY, False)
    # The same value written again 1 s later.
    store._now = NOW + 1
    # The identical rewrite.
    local_write(store, KEY, "a")
    # A newer stamp, held without a dirty mark (an orderly stop saves it).
    self.assertEqual((store.storage[KEY]["ver"], getattr(store, DIRTY), getattr(store, UNMARKED)),
                     ([NOW_MS + 1000, "node"], False, True))
    # Two stamps in one ms: the second is one ms later.
    store._now = NOW + 2
    # Taken one after the other at the same wall clock.
    self.assertEqual([getattr(store, TICK)(), getattr(store, TICK)()], [[NOW_MS + 2000, "node"],
                                                                        [NOW_MS + 2001, "node"]])
    # Off: no stamp is made, stored or sent.
    store = make("off")
    # A local write.
    local_write(store, KEY, "a")
    # Neither its record nor its store has a ver field.
    self.assertEqual(("ver" in store.storage[KEY], "ver" in store.sent_payloads[-1]["data"][store.CS_DATA]),
                     (False, False))

  def test_off_reads_no_stamp_from_a_store_or_a_sync_answer(self):
    """With the order switch 'off' a stamped store and a stamped sync copy are applied as the previous release and
    their stamps are not stored."""
    # Off.
    store = make("off")
    # A stamped live store from a peer.
    deliver(store, KEY, "v", "peer1", [NOW_MS, "peer1"])
    # A stamped sync copy of another field.
    other = _hset_key("players", "beta")
    # Merged as a sync answer.
    getattr(store, MERGE)("players", {other: record(other, "w", "peer2", [NOW_MS, "peer2"])})
    # Both applied, neither stamp stored, nothing counted but the writes.
    self.assertEqual((store.storage[KEY]["value"], "ver" in store.storage[KEY], store.storage[other]["value"],
                      "ver" in store.storage[other], getattr(store, STATS)),
                     ("v", False, "w", False, {BUCKET + "/remote/changed": 1}))

  def test_a_taken_stamp_moves_the_clock_and_a_future_one_does_not(self):
    """A peer's stamp 30 s ahead raises the next local stamp above it; one 61 s ahead is refused in 'on' and
    applied in 'report', counted ver_future in both, and moves the clock in neither."""
    # Each mode.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The receiving replica.
        store = make(mode)
        # A peer's write stamped 30 s ahead: taken.
        self.assertEqual(len(deliver(store, KEY, "a", "peer1", [NOW_MS + 30_000, "peer1"])), 1)
        # The next local write is stamped one ms above it.
        local_write(store, _hset_key("players", "beta"), "b")
        # Its stamp.
        self.assertEqual(store.storage[_hset_key("players", "beta")]["ver"], [NOW_MS + 30_001, "node"])
        # The clock now.
        clock = getattr(store, CLOCK)
        # Back to the tests' wall clock.
        store._now = NOW
        # A write stamped 61 s ahead.
        confirms = deliver(store, KEY, "c", "peer1", [NOW_MS + 61_000, "peer1"])
        # Refused and not confirmed in 'on'; applied and confirmed in 'report'; the clock has not moved.
        self.assertEqual((len(confirms), store.storage[KEY]["value"], getattr(store, CLOCK)),
                         (0, "a", clock) if mode == "on" else (1, "c", clock))
        # Counted in both modes; its refusal word only in 'on'.
        self.assertEqual({label: n for label, n in getattr(store, STATS).items() if "future" in label}, {
          BUCKET + "/remote/ver_future": 1, **({BUCKET + "/remote/refused_future": 1} if mode == "on" else {})})

  def test_the_clock_starts_again_from_the_wall_clock_at_its_bound(self):
    """A clock at or past CSTORE_VER_MAX_AHEAD ahead of the wall clock gives the wall clock to the next stamp."""
    # A replica whose clock was left 60 s ahead (its wall clock set back).
    store = make("on")
    # At the bound.
    setattr(store, CLOCK, NOW_MS + 60_000)
    # The next stamp is the wall clock again.
    self.assertEqual(getattr(store, TICK)(), [NOW_MS, "node"])

  def test_a_malformed_stamp_is_counted_and_treated_as_none(self):
    """A stamp that is not [int ms, str address] counts ver_invalid and is treated as no stamp: the write is applied
    and stored without one."""
    # Malformed stamps: a float time, a third item, a tuple, a bool time, a str.
    for bad in ([NOW_MS + 0.5, "peer1"], [NOW_MS, "peer1", 1], (NOW_MS, "peer1"), [True, "peer1"], "x"):
      # One sub-test each.
      with self.subTest(ver=bad):
        # The receiving replica.
        store = make("on")
        # The store carrying it, applied as an unstamped write to an absent key.
        self.assertEqual(len(deliver(store, KEY, "v", "peer1", bad)), 1)
        # Stored without a stamp, and counted.
        self.assertEqual(("ver" in store.storage[KEY], getattr(store, STATS)), (False, {
          BUCKET + "/remote/ver_invalid": 1, BUCKET + "/remote/changed": 1}))

  def test_start_sets_the_clock_from_the_newest_stamp_that_is_not_a_future_one(self):
    """on_init starts the stamp clock at the newest stored stamp at most CSTORE_VER_MAX_AHEAD ahead (a farther one
    is left out); with the order switch 'off' the clock is not read."""
    # A data folder with a cache folder, removed after the test.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # The cache folder the plugin's persistence folder names.
    os.makedirs(os.path.join(folder.name, "chain_store"))
    # The manager in use, put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", SerializationManager.instance)
    # A manager that takes the registration.
    SerializationManager.instance = SimpleNamespace(register=lambda save, every: None)
    # Records stamped 30 s and 10 min ahead of the wall clock, and one unstamped.
    stored = {"a": record("a", 1, "p", [NOW_MS + 30_000, "p"]), "b": record("b", 2, "p", [NOW_MS + 600_000, "p"]),
              "c": record("c", 3, "p")}
    # Written as the replica file.
    with open(os.path.join(folder.name, "chain_store", "object.pickle"), "wb") as stream:
      # The pickle the start loads.
      pickle.dump(stored, stream)
    # Each mode, and the clock it starts with.
    for mode, clock in (("report", NOW_MS + 30_000), ("off", 0)):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A store to start in this mode.
        with mock.patch.object(_ChainStoreRuntimeHarness, "CSTORE_MODE", mode):
          # Built with the switches in this mode.
          store = _StartHarness(folder.name)
        # At the tests' wall clock, with an empty whitelist for the peer refresh on_init makes.
        store._now, store.bc = NOW, SimpleNamespace(get_whitelist=lambda with_prefix=False: [])
        # The real on_init.
        store.on_init()
        # The clock it started.
        self.assertEqual(getattr(store, CLOCK), clock)


class TestOrderOfLiveWrites(unittest.TestCase):
  """A live write is checked against the held record: origin, signed field, cs_rev, then the stamp."""

  def test_a_forged_origin_is_refused_in_on_and_counted_in_report(self):
    """A store whose stamp names another node than its verified sender counts ver_origin; 'on' refuses it unconfirmed,
    'report' applies it."""
    # Each mode and what it does.
    for mode, applied in (("report", True), ("on", False)):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The receiving replica.
        store = make(mode)
        # Sent by peer1 with peer2's address in the stamp.
        confirms = deliver(store, KEY, "v", "peer1", [NOW_MS, "peer2"])
        # Applied and confirmed only in 'report'; refused with an answer in 'on'.
        self.assertEqual((answers(confirms), KEY in store.storage), (["confirm" if applied else "origin"], applied))
        # Counted in both modes.
        self.assertEqual(getattr(store, STATS).get(BUCKET + "/remote/ver_origin"), 1)

  def test_an_older_write_is_refused_in_on_a_newer_one_applied_and_an_identical_one_confirmed(self):
    """Over a held stamp: an older write counts ver_older (refused in 'on', applied in 'report'), a newer one is
    applied with its stamp, and the held value written again is confirmed in both modes, its newer stamp raised in
    place without a dirty mark."""
    # Each mode.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding "b" stamped by peer1 at +2 s.
        store = make(mode, storage={KEY: record(KEY, "b", "peer1", [NOW_MS + 2000, "peer1"])})
        # An older write from peer2.
        older = deliver(store, KEY, "a", "peer2", [NOW_MS + 1000, "peer2"])
        # Refused with an answer in 'on', applied and confirmed in 'report'.
        self.assertEqual((answers(older), store.storage[KEY]["value"]),
                         (["older"], "b") if mode == "on" else (["confirm"], "a"))
        # Counted in both modes.
        self.assertEqual(getattr(store, STATS).get(BUCKET + "/remote/ver_older"), 1)
        # A newer write from peer2: applied with its stamp.
        self.assertEqual(len(deliver(store, KEY, "c", "peer2", [NOW_MS + 3000, "peer2"])), 1)
        # Its value and stamp.
        self.assertEqual((store.storage[KEY]["value"], store.storage[KEY]["ver"]), ("c", [NOW_MS + 3000, "peer2"]))
        # Saved state, to see that the identical writes below do not mark it.
        setattr(store, DIRTY, False)
        # The same value with an older stamp: confirmed, the held stamp kept.
        self.assertEqual(len(deliver(store, KEY, "c", "peer1", [NOW_MS + 2500, "peer1"])), 1)
        # Kept.
        self.assertEqual(store.storage[KEY]["ver"], [NOW_MS + 3000, "peer2"])
        # The same value with a newer stamp: confirmed, the stamp raised in place.
        self.assertEqual(len(deliver(store, KEY, "c", "peer1", [NOW_MS + 4000, "peer1"])), 1)
        # Raised, without a dirty mark but held for the save at an orderly stop; the owner is the previous release's.
        self.assertEqual((store.storage[KEY]["ver"], store.storage[KEY]["owner"], getattr(store, DIRTY),
                          getattr(store, UNMARKED)), ([NOW_MS + 4000, "peer1"], "peer2", False, True))

  def test_an_unstamped_write_over_a_stamp_is_refused_in_on_and_a_stamp_far_ahead_is_not_trusted(self):
    """An unstamped write over a trusted stamp counts ver_unstamped (refused in 'on', applied in 'report'); over an
    unstamped record, or a stamp more than 60 s ahead, the last arrival wins as before, also for a stamped write."""
    # Each mode.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding a stamped record, an unstamped one and one stamped 10 min ahead.
        far = _hset_key("players", "far")
        # The three records.
        store = make(mode, storage={
          KEY: record(KEY, "b", "peer1", [NOW_MS, "peer1"]), "legacy": record("legacy", "old", "peer1"),
          far: record(far, "ahead", "peer1", [NOW_MS + 600_000, "peer1"])})
        # An unstamped write over the stamped record: counted, refused only in 'on'.
        self.assertEqual(len(deliver(store, KEY, "a", "peer2")), 0 if mode == "on" else 1)
        # Counted in both modes.
        self.assertEqual(getattr(store, STATS).get(BUCKET + "/remote/ver_unstamped"), 1)
        # An unstamped write over the unstamped record, and an older stamped one over the stamp far ahead: applied.
        self.assertEqual([len(deliver(store, "legacy", "new", "peer2")),
                          len(deliver(store, far, "now", "peer2", [NOW_MS, "peer2"]))], [1, 1])
        # Both stored.
        self.assertEqual((store.storage["legacy"]["value"], store.storage[far]["value"]), ("new", "now"))

  def test_the_signed_field_then_cs_rev_decide_before_the_stamp(self):
    """In each signed namespace a higher signed sequence or revision with an older stamp is applied live and from a
    sync answer; an equal sequence is ordered by the stamp; a higher cs_rev with an older stamp wins and a lower one
    with a newer stamp loses ('on')."""
    # The signed namespaces, each with its field, a value at version 1 and one at version 2 (signed "valid").
    pointer = {**SIGNED_POINTER}
    # Its version 2.
    pointer2 = {**pointer, "revision": 2, "pipeline_cid": "cid-8"}
    # A dAuth bundle of job 7, at deployment revision 1 and 2.
    bundle = {"job_id": "7", "deployment_revision": 1, "operation_id": "op", "coordinator": "oracle",
              "EE_SENDER": "oracle", "EE_SIGN": "valid", "secret": "s1"}
    # A coordinator's operation at sequence 9, and a node operation at sequence 9.
    operation = {"operation_id": "op", "sequence": 9, "coordinator": "oracle", "EE_SENDER": "oracle",
                 "EE_SIGN": "valid"}
    # The node operation.
    node_op = {"operation_id": "op", "attempt": "deploy", "sequence": 9, "node_addr": "worker", "EE_SENDER": "worker",
               "EE_SIGN": "valid"}
    # Namespace, field, held value, newer value.
    cases = (
      ("DEEPLOY_DEPLOYED_JOBS", "7", pointer, pointer2),
      ("DAUTH_JOB_SECRETS", "7", bundle, {**bundle, "deployment_revision": 2, "secret": "s2"}),
      ("DEEPLOY_OPERATIONS", "op", operation, {**operation, "sequence": 10}),
      ("DEEPLOY_NODE_OPERATIONS", "op|deploy|worker", node_op, {**node_op, "sequence": 10}),
    )
    # Each signed namespace.
    for namespace, field, held, newer in cases:
      # One sub-test each.
      with self.subTest(namespace=namespace):
        # Its key.
        key = _hset_key(namespace, field)
        # A replica holding the version-1 value stamped at +5 s.
        store = make("on", signed=True, storage={key: record(key, held, "oracle", [NOW_MS + 5000, "peer1"])})
        # The higher version stamped earlier, live from peer1 (a dAuth and chain peer of the signed store).
        self.assertEqual(len(deliver(store, key, newer, "peer1", [NOW_MS + 1000, "peer1"])), 1)
        # Applied.
        self.assertEqual(store.storage[key]["value"], newer)
        # The same from a sync answer, into a fresh replica holding version 1.
        store = make("on", signed=True, storage={key: record(key, held, "oracle", [NOW_MS + 5000, "peer1"])})
        # Merged.
        self.assertEqual(getattr(store, MERGE)(namespace, {key: record(key, newer, "oracle",
                                                                        [NOW_MS + 1000, "peer1"])}), 1)
        # Applied.
        self.assertEqual(store.storage[key]["value"], newer)
    # An equal sequence: the stamp decides. A replica holding sequence 9 stamped at +5 s.
    key = _hset_key("DEEPLOY_OPERATIONS", "op")
    # Held.
    store = make("on", signed=True, storage={key: record(key, operation, "oracle", [NOW_MS + 5000, "peer1"])})
    # The same sequence with another body: older stamp refused, newer applied.
    other = {**operation, "note": "x"}
    # Older (refused with an answer), then newer (confirmed).
    self.assertEqual([answers(deliver(store, key, other, "peer1", [NOW_MS + 1000, "peer1"])),
                      answers(deliver(store, key, other, "peer1", [NOW_MS + 9000, "peer1"]))], [["older"], ["confirm"]])
    # cs_rev: a replica holding cs_rev 200 stamped at +5 s.
    store = make("on", storage={KEY: record(KEY, {"t": "b", "cs_rev": 200}, "peer1", [NOW_MS + 5000, "peer1"])})
    # A higher cs_rev with an older stamp: applied.
    self.assertEqual(len(deliver(store, KEY, {"t": "c", "cs_rev": 300}, "peer2", [NOW_MS + 1000, "peer2"])), 1)
    # A lower cs_rev with a newer stamp: refused (by the cs_rev rule of every mode, before the order rule), with an
    # answer since the store is stamped and the order switch 'on'.
    self.assertEqual(answers(deliver(store, KEY, {"t": "a", "cs_rev": 250}, "peer2", [NOW_MS + 9000, "peer2"])),
                     ["cs_rev"])
    # The cs_rev 300 value kept, and the refusal under the cs_rev word.
    self.assertEqual((store.storage[KEY]["value"]["t"], getattr(store, STATS).get(BUCKET + "/remote/refused_cs_rev")),
                     ("c", 1))

  def test_a_versioned_value_beats_an_unversioned_one_whatever_their_stamps(self):
    """A split history found in review: an oracle that missed a signed pointer and then wrote an unversioned one with a
    newer stamp takes the signed pointer live, as a re-sent store and from a sync answer ('on'); a value with cs_rev
    beats one without it, and in 'on' a live write without cs_rev over one with it is refused (applied in 'report',
    by the cs_rev rule of every mode)."""
    # The pointer key of job 7.
    key = _hset_key("DEEPLOY_DEPLOYED_JOBS", "7")
    # Each way the signed pointer reaches the oracle: live, re-sent later with its original stamp, and by sync.
    for path in ("live", "resent", "sync"):
      # One sub-test each.
      with self.subTest(path=path):
        # The oracle that missed the pointer [+1 s] and then wrote an unversioned CID at +60 s.
        store = make("on", signed=True, storage={key: record(key, "cid-legacy", "node", [NOW_MS + 60_000, "node"])})
        # Later wall clock for the re-send.
        store._now = NOW + (400 if path == "resent" else 61)
        if path == "sync":
          # A sync answer from a peer holding the pointer.
          getattr(store, MERGE)("DEEPLOY_DEPLOYED_JOBS", {key: record(key, SIGNED_POINTER, "oracle",
                                                                      [NOW_MS + 1000, "oracle"])})
        else:
          # The live or re-sent store of its coordinator "oracle".
          deliver(store, key, SIGNED_POINTER, "oracle", [NOW_MS + 1000, "oracle"])
        # The signed pointer is held, whatever the stamps.
        self.assertEqual(store.storage[key]["value"], SIGNED_POINTER)
    # cs_rev: an unversioned value with a newer stamp, then a versioned one with an older stamp.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding an unversioned credential stamped at +5 s.
        store = make(mode, storage={KEY: record(KEY, {"t": "legacy"}, "peer1", [NOW_MS + 5000, "peer1"])})
        # The versioned one with an older stamp: applied in both modes (no counter: the order rule admits it).
        self.assertEqual(len(deliver(store, KEY, {"t": "v", "cs_rev": 100}, "peer2", [NOW_MS + 1000, "peer2"])), 1)
        # An unversioned one with a newer stamp: refused in 'on' (it lacks cs_rev), applied in 'report'.
        self.assertEqual(answers(deliver(store, KEY, {"t": "x"}, "peer2", [NOW_MS + 9000, "peer2"])),
                         ["older" if mode == "on" else "confirm"])
        # Counted as older in both modes.
        self.assertEqual(getattr(store, STATS).get(BUCKET + "/remote/ver_older"), 1)

  def test_a_stamped_write_replaces_a_legacy_record_and_a_legacy_copy_does_not_come_back(self):
    """A record written before stamps existed is replaced by a stamped write ('on'), and a sync answer from a peer
    that still holds the unstamped legacy copy does not bring it back (counted ver_unstamped)."""
    # A replica holding the legacy unstamped value.
    store = make("on", storage={KEY: record(KEY, "v0", "peer1")})
    # A stamped write from peer2: applied over the unstamped record.
    self.assertEqual(len(deliver(store, KEY, "v1", "peer2", [NOW_MS, "peer2"])), 1)
    # A sync answer from a peer that missed it and holds the legacy copy.
    self.assertEqual(getattr(store, MERGE)("players", {KEY: record(KEY, "v0", "peer1")}), 0)
    # The stamped value kept; the legacy copy counted.
    self.assertEqual((store.storage[KEY]["value"], getattr(store, STATS).get(BUCKET + "/hsync/ver_unstamped")),
                     ("v1", 1))

  def test_equal_stamps_with_different_values_end_the_same_on_both_replicas(self):
    """Two replicas holding different values under one stamp (a writer that reused a ms after a restart) exchange
    them: the one fixed comparison keeps the same value on both, the loser counted ver_collision."""
    # One stamp, two values.
    ver = [NOW_MS, "peer1"]
    # Replica A holds "a", replica B holds "b", both stamped with it.
    first = make("on", "node-a", storage={KEY: record(KEY, "a", "peer1", list(ver))})
    # Replica B.
    second = make("on", "node-b", storage={KEY: record(KEY, "b", "peer1", list(ver))})
    # Each gets the other's value as a live store from peer1.
    deliver(first, KEY, "b", "peer1", list(ver))
    # And the other way.
    deliver(second, KEY, "a", "peer1", list(ver))
    # Both hold the value with the larger canonical JSON; only the side whose arrival lost counts.
    self.assertEqual((first.storage[KEY]["value"], second.storage[KEY]["value"],
                      getattr(first, STATS).get(BUCKET + "/remote/ver_collision"),
                      getattr(second, STATS).get(BUCKET + "/remote/ver_collision")), ("b", "b", None, 1))


class TestOrderOfSyncCopies(unittest.TestCase):
  """A sync copy goes through the same order rule; an equal copy only raises the held stamp."""

  def test_copies_are_ordered_by_stamp_in_on_and_applied_as_before_in_report(self):
    """An older copy counts ver_older and is skipped in 'on' (applied in 'report'); a copy equal in value, owner,
    readonly and token raises the held stamp in place without a dirty mark; a newer copy is applied with its stamp."""
    # Each mode.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding "b" stamped at +2 s.
        store = make(mode, storage={KEY: record(KEY, "b", "peer1", [NOW_MS + 2000, "peer1"])})
        # An older copy.
        merged = getattr(store, MERGE)("players", {KEY: record(KEY, "a", "peer2", [NOW_MS + 1000, "peer2"])})
        # Skipped in 'on', applied in 'report'.
        self.assertEqual((merged, store.storage[KEY]["value"]), (0, "b") if mode == "on" else (1, "a"))
        # Counted in both.
        self.assertEqual(getattr(store, STATS), {BUCKET + "/hsync/ver_older": 1})
        # A replica holding "b" again, saved.
        store = make(mode, storage={KEY: record(KEY, "b", "peer1", [NOW_MS + 2000, "peer1"])})
        # The same record with a newer stamp.
        self.assertEqual(getattr(store, MERGE)("players", {KEY: record(KEY, "b", "peer1", [NOW_MS + 3000, "peer1"])}),
                         0)
        # The stamp raised in place, no dirty mark, held for the save at an orderly stop.
        self.assertEqual((store.storage[KEY]["ver"], getattr(store, DIRTY), getattr(store, UNMARKED)),
                         ([NOW_MS + 3000, "peer1"], False, True))
        # A newer copy with another value: applied with its stamp.
        self.assertEqual(getattr(store, MERGE)("players", {KEY: record(KEY, "c", "peer2", [NOW_MS + 4000, "peer2"])}),
                         1)
        # Its value and stamp.
        self.assertEqual((store.storage[KEY]["value"], store.storage[KEY]["ver"]), ("c", [NOW_MS + 4000, "peer2"]))

  def test_an_equal_copy_raises_the_held_stamp_only_to_a_newer_stamp_the_rule_admits(self):
    """A sync copy equal to the held record in value, owner, readonly and token leaves the held stamp as it is when
    its stamp is older or more than 60 s ahead (the rule's words older and future, each counted), and raises it in
    place to a newer one with no dirty mark but the unmarked flag set, in 'report' and 'on'; in 'on', after the
    older copy, a live store stamped between that copy and the held stamp is still refused as older."""
    # The stamp the replica holds, at +2 s.
    held = [NOW_MS + 2000, "peer1"]
    # Each copy: the rule's word for its stamp, the stamp, and the stamp the replica holds after it.
    cases = (("older", [NOW_MS + 1000, "peer1"], held), ("future", [NOW_MS + 61_000, "peer1"], held),
             (None, [NOW_MS + 3000, "peer1"], [NOW_MS + 3000, "peer1"]))
    # Each mode.
    for mode in ("report", "on"):
      # Each copy.
      for word, ver, after in cases:
        # One sub-test each.
        with self.subTest(mode=mode, word=word):
          # A replica holding "b" of peer1 at the held stamp, saved.
          store = make(mode, storage={KEY: record(KEY, "b", "peer1", list(held))})
          # The same record (value, owner, readonly, token) with the copy's stamp, as a sync answer.
          merged = getattr(store, MERGE)("players", {KEY: record(KEY, "b", "peer1", list(ver))})
          # Nothing applied; the stamp held after it; no dirty mark; held for the stop only when raised; the word
          # counted under the sync origin.
          self.assertEqual(
            (merged, store.storage[KEY]["ver"], getattr(store, DIRTY), getattr(store, UNMARKED), getattr(store, STATS)),
            (0, after, False, word is None, {BUCKET + "/hsync/ver_" + word: 1} if word else {}))
          if mode == "on" and word == "older":
            # A live store of another value from peer2, stamped between the older copy and the held stamp.
            bodies = deliver(store, KEY, "x", "peer2", [NOW_MS + 1500, "peer2"])
            # Still older than the held stamp: refused with an answer, the held value and stamp kept.
            self.assertEqual((answers(bodies), store.storage[KEY]["value"], store.storage[KEY]["ver"]),
                             (["older"], "b", held))


class TestCostAndPersistence(unittest.TestCase):
  """What stamps add to the replica file, the stats line and the save at an orderly stop."""

  def test_stamped_records_pickle_at_most_two_percent_larger(self):
    """5,700 records of about 0.9 KB from 11 writers, stamped through the order rule by live stores and sync copies,
    pickle at most 2 % larger than the same records unstamped (the writer addresses are kept once each)."""
    # Eleven writers, with addresses as long as a node's.
    writers = ["0xai_" + str(n) * 44 for n in range(11)]
    # One replica per mode: 'off' stores no stamp, 'report' stores every one.
    sizes = {}
    # Each mode.
    for mode in ("off", "report"):
      # The replica, with the writers as peers.
      store = make(mode, peers=writers)
      # Half of the records by live stores, each from its writer; the address in the stamp is built anew each time,
      # as decoding a message builds it.
      for n in range(2850):
        # About 0.9 KB per value, as the production replica's average record.
        deliver(store, _hset_key("ns", str(n)), "v" * 800 + str(n), writers[n % 11],
                [NOW_MS + n, "0xai_" + str(n % 11) * 44])
      # The other half by one sync answer, each stamp's address built anew too.
      snapshot = {_hset_key("ns", "s" + str(n)): record(_hset_key("ns", "s" + str(n)), "w" * 800 + str(n), "x",
                                                       [NOW_MS + n, "0xai_" + str(n % 11) * 44])
                  for n in range(2850)}
      # Merged.
      getattr(store, MERGE)("ns", copy.deepcopy(snapshot))
      # The size of the replica file of this mode.
      sizes[mode] = len(pickle.dumps(store.storage))
    # Stamped records pickle at most 2 % larger.
    self.assertLessEqual(sizes["report"], sizes["off"] * 1.02, sizes)

  def test_the_stats_line_carries_the_largest_stamp_lead(self):
    """The CSTORE_STATS line gives the largest lead of a stamp taken in the period over the wall clock, in seconds,
    and starts again from 0."""
    # A replica in 'report'.
    store = make("report")
    # Stamps 12.5 s and 3 s ahead, taken.
    deliver(store, KEY, "a", "peer1", [NOW_MS + 12_500, "peer1"])
    # The second.
    deliver(store, _hset_key("players", "beta"), "b", "peer1", [NOW_MS + 3000, "peer1"])
    # Its whitelist and netmon for the peer refresh of process().
    store.bc = SimpleNamespace(get_whitelist=lambda with_prefix=False: ["peer1", "peer2"])
    # Both peers running.
    store.netmon = SimpleNamespace(network_node_simple_status=lambda peer, **kwargs: "ONLINE")
    # The interval ends.
    store._now = NOW + store.CS_STATS_INTERVAL
    # The lap that writes the line, and the next interval's.
    store.process()
    # The next one, with nothing taken.
    store._now += store.CS_STATS_INTERVAL
    # Its lap.
    store.process()
    # The two lines' leads.
    leads = [json.loads(msg[len("CSTORE_STATS "):])["stamp_lead_max_s"]
             for msg in store.messages if msg.startswith("CSTORE_STATS ")]
    # 12.5 s, then 0.
    self.assertEqual(leads, [12.5, 0.0])

  def test_an_orderly_stop_saves_a_newer_stamp_held_without_a_dirty_mark(self):
    """A newer stamp on an unchanged value is not saved by the periodic flush, but is saved at an orderly stop, so a
    restarted replica does not take an older write over it."""
    # A replica holding "v" stamped at +1 s, saved.
    store = make("on", storage={KEY: record(KEY, "v", "peer1", [NOW_MS + 1000, "peer1"])})
    # The same value with a newer stamp.
    deliver(store, KEY, "v", "peer2", [NOW_MS + 5000, "peer2"])
    # The periodic flush finds nothing changed.
    store._ChainStoreBasePlugin__maybe_flush_chain_storage()
    # Nothing written yet.
    self.assertEqual(store.saved_storage, [])
    # The manager in use, put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", SerializationManager.instance)
    # A manager that takes the removal of the registration.
    SerializationManager.instance = SimpleNamespace(unregister=lambda save: None)
    # The orderly stop.
    store.on_close()
    # Saved with the newer stamp.
    self.assertEqual(store.saved_storage[-1][KEY]["ver"], [NOW_MS + 5000, "peer2"])


def tombstone(key, owner, ver):
  """
  Build the tombstone a remote stamped delete leaves, as a replica holds it under ``"dl:" + key``.

  Parameters
  ----------
  key : str
    The deleted key.
  owner : str
    The deleter's owner.
  ver : list
    The delete's stamp.

  Returns
  -------
  dict
    The record: value None, the stamp, a remote record's confirmation fields.

  Examples
  --------
  >>> tombstone(KEY, "peer1", [NOW_MS, "peer1"])["value"] is None
  True
  """
  # A remote record of the key with value None and the delete's stamp.
  return record(key, None, owner, ver)


class TestTombstones(unittest.TestCase):
  """A stamped delete leaves a tombstone under "dl:" + key: hidden from reads, ordered, synced, swept."""

  def test_a_delete_leaves_a_tombstone_that_no_read_shows(self):
    """In 'report' a local delete of a held key removes it, stores a stamped tombstone and marks the replica; get,
    hget, hlist and hgetall do not show it; an older or equal delete changes nothing; a delete of a key not held
    leaves a tombstone held without a dirty mark; 'off' leaves none."""
    # A replica holding KEY and another field.
    store = make("report", storage={KEY: record(KEY, "v", "peer1"),
                                    _hset_key("players", "beta"): record(_hset_key("players", "beta"), "b", "p")})
    # The local delete: True at once, as before.
    self.assertTrue(local_write(store, KEY, None))
    # The key is gone, its tombstone holds the stamp and the deleter, and the replica is marked.
    self.assertEqual((KEY in store.storage, store.storage[TOMB]["value"], store.storage[TOMB]["ver"],
                      store.storage[TOMB]["owner"], getattr(store, DIRTY)),
                     (False, None, [NOW_MS, "node"], "node", True))
    # The plugin API over the replica's memory.
    api = _PeerSelectionHarness()
    # Its key hashing and field encoding, as the plugin's.
    api.get_hash, api.str_to_base64 = _ChainStoreRuntimeHarness.get_hash, _ChainStoreRuntimeHarness.str_to_base64
    # Its field decoding.
    api.base64_to_str = _ChainStoreRuntimeHarness.base64_to_str
    # The replica and its getter in the shared memory.
    api.plugins_shmem.update({"__chain_storage": store.storage, "__chain_storage_get": store._get_value})
    # Neither a read nor a listing shows the deleted field.
    self.assertEqual((api.chainstore_hget("players", "alpha"), api.chainstore_get(KEY),
                      api.chainstore_hlist("players"), api.chainstore_hgetall("players")),
                     (None, None, ["beta"], {"beta": "b"}))
    # An older and an equal delete from peers: the tombstone kept as it is.
    deliver(store, KEY, None, "peer1", [NOW_MS - 5, "peer1"])
    # The equal one.
    deliver(store, KEY, None, "node", [NOW_MS, "node"])
    # Kept.
    self.assertEqual(store.storage[TOMB]["ver"], [NOW_MS, "node"])
    # A delete of a key not held, on a saved replica.
    setattr(store, DIRTY, False)
    # A key never held.
    other = _hset_key("players", "gamma")
    # Deleted locally.
    local_write(store, other, None)
    # A tombstone, held without a dirty mark.
    self.assertEqual(("dl:" + other in store.storage, getattr(store, DIRTY), getattr(store, UNMARKED)),
                     (True, False, True))
    # Off: the same delete leaves no tombstone.
    store = make("off", storage={KEY: record(KEY, "v", "peer1")})
    # Deleted.
    local_write(store, KEY, None)
    # Nothing left.
    self.assertEqual(store.storage, {})

  def test_a_value_older_than_the_delete_is_refused_in_on(self):
    """A value stamped before the tombstone counts ver_older_than_delete: refused in 'on', applied in 'report' (the
    tombstone removed); a newer value with its own owner, readonly and token is applied as to a key not held."""
    # Each mode.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding the tombstone of a delete at +5 s.
        store = make(mode, storage={TOMB: tombstone(KEY, "peer1", [NOW_MS + 5000, "peer1"])})
        # A value written before the delete arrives late.
        late = deliver(store, KEY, "old", "peer2", [NOW_MS + 1000, "peer2"])
        # Refused with an answer in 'on'; applied in 'report', its tombstone removed.
        self.assertEqual((answers(late), KEY in store.storage, TOMB in store.storage),
                         (["older_than_delete"], False, True) if mode == "on" else (["confirm"], True, False))
        # Counted in both.
        self.assertEqual(getattr(store, STATS).get(BUCKET + "/remote/ver_older_than_delete"), 1)
    # A newer write with a token and readonly over the tombstone: applied as to a key not held.
    store = make("on", storage={TOMB: tombstone(KEY, "peer1", [NOW_MS + 5000, "peer1"])})
    # Its body, with a token and readonly.
    store.default_handler({_PayloadData.EE_SENDER: "peer2", _PayloadData.EE_ID: "peer2",
                           _PayloadData.EE_DESTINATION: ["node"], _PayloadData.EE_IS_ENCRYPTED: True,
                           "decoded": {store.CS_DATA: {store.CS_OP: store.CS_STORE, store.CS_KEY: KEY,
                                                       store.CS_VALUE: "new", store.CS_OWNER: "peer2",
                                                       store.CS_TOKEN: "t", store.CS_READONLY: True,
                                                       store.CS_VER: [NOW_MS + 9000, "peer2"]}}})
    # Stored with them, the tombstone gone.
    self.assertEqual((store.storage[KEY]["value"], store.storage[KEY]["token"], store.storage[KEY]["readonly"],
                      TOMB in store.storage), ("new", "t", True, False))

  def test_a_value_write_removes_only_a_tombstone_and_none_in_off(self):
    """A value written to a key, by a peer's store, locally or by a sync copy, removes the tombstone held for it with
    the order switch 'report' or 'on' but not 'off', and never removes a "dl:" record that holds a value (one a caller
    of the previous release could write there) nor one with value None and no stamp, which no delete leaves."""
    # The "dl:" + KEY records: a tombstone of KEY, a value a caller of the previous release wrote there, and a record
    # with value None but no stamp.
    held = {"tombstone": tombstone(KEY, "peer1", [NOW_MS - 5000, "peer1"]), "app": record(TOMB, {"progress": 5}, "app"),
            "unstamped": record(KEY, None, "peer1")}
    # Per mode, held record and way of writing: the value of KEY then, and whether the "dl:" record is still held.
    results = {}
    # Each mode.
    for mode in ("off", "report", "on"):
      # Each held record.
      for name, held_record in held.items():
        # Each way of writing.
        for way in ("remote", "local", "sync"):
          # A replica holding the "dl:" record.
          store = make(mode, storage={TOMB: held_record})
          if way == "remote":
            # A peer's store of KEY, stamped after the tombstone.
            deliver(store, KEY, "v", "peer2", [NOW_MS, "peer2"])
          elif way == "local":
            # A local write of KEY.
            local_write(store, KEY, "v")
          else:
            # A sync copy of KEY, stamped after the tombstone.
            getattr(store, MERGE)("players", {KEY: record(KEY, "v", "peer2", [NOW_MS, "peer2"])})
          # The value written, and whether the "dl:" record stayed.
          results[(mode, name, way)] = (store.storage[KEY]["value"], TOMB in store.storage)
    # Written every time; the tombstone removed except in 'off', the other records never.
    self.assertEqual(results, {
      (mode, name, way): ("v", name != "tombstone" or mode == "off")
      for mode in ("off", "report", "on") for name in held for way in ("remote", "local", "sync")})

  def test_a_delete_leaves_a_dl_record_that_holds_a_value_in_place(self):
    """A stamped delete of a key, by a peer's store, locally or by a sync copy, leaves a "dl:" record that holds a
    value (one a caller of the previous release could write there) as it is in every mode: a live delete only removes
    the key, as the previous release does, and a sync copy of the delete is skipped, as the previous release skips
    it."""
    # Per mode and way of deleting: the "dl:" + KEY record's value after the delete, and whether KEY is still held.
    results = {}
    # Each mode.
    for mode in ("off", "report", "on"):
      # Each way of deleting.
      for way in ("remote", "local", "sync"):
        # A replica holding KEY (unstamped) and a value a caller of the previous release wrote under "dl:" + KEY.
        store = make(mode, storage={TOMB: record(TOMB, {"progress": 5}, "app"), KEY: record(KEY, "v", "node")})
        if way == "remote":
          # A peer's stamped delete of KEY.
          deliver(store, KEY, None, "peer2", [NOW_MS + 1000, "peer2"])
        elif way == "local":
          # A local delete of KEY.
          local_write(store, KEY, None)
        else:
          # A sync copy of KEY's delete, stamped.
          getattr(store, MERGE)("players", {KEY: record(KEY, None, "peer2", [NOW_MS + 1000, "peer2"])})
        # The "dl:" record's value, and whether KEY is still held.
        results[(mode, way)] = (store.storage[TOMB]["value"], KEY in store.storage)
    # The "dl:" record kept every time; KEY removed by a live delete, kept under a sync copy of one.
    self.assertEqual(results, {(mode, way): ({"progress": 5}, way == "sync")
                               for mode in ("off", "report", "on") for way in ("remote", "local", "sync")})

  def test_no_write_names_a_tombstone_key(self):
    """A local write to a "dl:" key raises; a store for one from a peer is dropped unconfirmed and counted
    refused_invalid_key, without raising."""
    # A replica in 'on'.
    store = make("on")
    # The local write raises.
    with self.assertRaisesRegex(ValueError, "reserved"):
      # Through the store's setter.
      store._set_value("dl:x", "v", owner="node")
    # The remote one: nothing stored or sent, and counted under the bucket of the key it names.
    self.assertEqual((deliver(store, TOMB, "v", "peer1", [NOW_MS, "peer1"]), store.storage, getattr(store, STATS)),
                     ([], {}, {BUCKET + "/remote/refused_invalid_key": 1}))

  def test_the_export_sends_a_tombstone_under_its_key_except_in_off(self):
    """A sync answer carries a tombstone under the key it deleted (value None, its stamp) in 'report' and 'on', none
    in 'off'; a "dl:" record holding a value is never sent."""
    # Each mode and whether the tombstone is sent.
    for mode, sent in (("off", False), ("report", True), ("on", True)):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding a tombstone and a "dl:" record a caller of the previous release wrote with a value.
        other = _hset_key("players", "beta")
        # Both records.
        store = make(mode, storage={TOMB: tombstone(KEY, "peer1", [NOW_MS, "peer1"]),
                                    "dl:" + other: record("dl:" + other, "x", "api")})
        # Its answer.
        snapshot = getattr(store, EXPORT)("players")
        # The tombstone under KEY with value None and its stamp (the five fields every exported record carries), or
        # nothing.
        self.assertEqual(snapshot, {KEY: {field: item for field, item in tombstone(KEY, "peer1", [NOW_MS, "peer1"])
                                          # Its exported fields only.
                                          .items() if field in store.CS_EXPORT_FIELDS}} if sent else {})

  def test_a_tombstone_copy_is_applied_in_on_and_only_counted_in_report(self):
    """A sync copy of a delete removes the held value and stores the tombstone in 'on' (counted ver_copy_delete and
    tombstone_new); 'report' keeps the value and counts ver_copy_delete; 'off' skips it and counts nothing."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding KEY stamped at +1 s.
        store = make(mode, storage={KEY: record(KEY, "v", "peer1", [NOW_MS + 1000, "peer1"])})
        # A peer's tombstone of a later delete, as its answer carries it.
        merged = getattr(store, MERGE)("players", {KEY: tombstone(KEY, "peer2", [NOW_MS + 2000, "peer2"])})
        # Applied only in 'on'.
        self.assertEqual((merged, KEY in store.storage, TOMB in store.storage),
                         (1, False, True) if mode == "on" else (0, True, False))
        # In 'on', where it is applied: a new tombstone, and a delete of peer1's record by peer2.
        applied = {BUCKET + "/hsync/tombstone_new": 1, BUCKET + "/hsync/delete_foreign": 1} if mode == "on" else {}
        # Counted in 'report' and 'on'.
        self.assertEqual(getattr(store, STATS), {} if mode == "off" else {BUCKET + "/hsync/ver_copy_delete": 1,
                                                                          **applied})

  def test_a_deleted_key_does_not_come_back_on_a_replica_that_held_a_legacy_copy(self):
    """A split history found in review ('on'): a key written before stamps existed is deleted while one replica is away;
    the delete, sent again with its own stamp, removes the legacy copy there; afterwards neither a sync from a peer
    that still holds the legacy copy nor one from a peer holding an older stamped copy brings the key back."""
    # The replica that was away, still holding the legacy unstamped value.
    store = make("on", storage={KEY: record(KEY, "v0", "peer1")})
    # The delete of peer1 reaches it (a live store, or the same store sent again later).
    self.assertEqual(len(deliver(store, KEY, None, "peer1", [NOW_MS, "peer1"])), 1)
    # Gone, a tombstone held.
    self.assertEqual((KEY in store.storage, TOMB in store.storage), (False, True))
    # The same delete again: still deleted, confirmed.
    self.assertEqual(len(deliver(store, KEY, None, "peer1", [NOW_MS, "peer1"])), 1)
    # A sync from a peer holding the legacy copy, then from one holding a copy stamped before the delete.
    merged = [getattr(store, MERGE)("players", {KEY: record(KEY, "v0", "peer2")}),
              getattr(store, MERGE)("players", {KEY: record(KEY, "v1", "peer2", [NOW_MS - 1000, "peer2"])})]
    # Neither brings the key back.
    self.assertEqual((merged, KEY in store.storage), ([0, 0], False))

  def test_a_delete_confirmation_lands_on_the_tombstone(self):
    """A peer's confirmation of a local delete (value None, the previous release's full form) counts on the
    tombstone the delete left, once per target."""
    # A replica in 'report' holding KEY.
    store = make("report", storage={KEY: record(KEY, "v", "peer1")})
    # The local delete, targeting peer1 and peer2.
    local_write(store, KEY, None)
    # peer1's confirmation, twice, as the previous release sends it.
    for _ in range(2):
      # Through the payload handler.
      store.default_handler({_PayloadData.EE_SENDER: "peer1", _PayloadData.EE_ID: "peer1",
                             _PayloadData.EE_DESTINATION: ["node"], _PayloadData.EE_IS_ENCRYPTED: True,
                             "decoded": {store.CS_DATA: {store.CS_OP: store.CS_CONFIRM, store.CS_KEY: KEY,
                                                         store.CS_VALUE: None, store.CS_OWNER: "node"}}})
    # Counted once on the tombstone.
    self.assertEqual((store.storage[TOMB]["confirms"], store.storage[TOMB]["confirmed_peers"]), (1, ["peer1"]))

  def test_the_sweep_removes_expired_and_surplus_tombstones_and_raises_the_horizon(self):
    """At each CSTORE_STATS line the tombstones older than CSTORE_TOMBSTONE_DAYS and the oldest past
    CSTORE_TOMBSTONE_CAP go, counted, the replica marked; the horizon is the newest removed stamp that is not a
    future one, and the sweep writes it into the reserved record; a removed future one is counted; 'off' and a life
    of 0 days remove every tombstone; a "dl:" record that is not a tombstone stays."""
    # Tombstones 8 days, 1 day and 1 s old, and one 2 min ahead; and a "dl:" record holding a value.
    stamps = {"a": NOW_MS - 8 * DAY_MS, "b": NOW_MS - DAY_MS, "c": NOW_MS - 1000, "d": NOW_MS + 120_000}
    # As held.
    held = {"dl:" + name: tombstone(name, "p", [ms, "p"]) for name, ms in stamps.items()}
    # The record that is not a tombstone.
    held["dl:x"] = record("dl:x", "kept", "api")
    # Each case: mode, cap, life in days, the tombstones left, the horizon.
    cases = (("on", 2, 7, ["c", "d"], stamps["b"]), ("on", 570, 7, ["b", "c", "d"], stamps["a"]),
             ("on", 0, 7, [], stamps["c"]), ("report", 570, 0, [], stamps["c"]), ("off", 570, 7, [], stamps["c"]))
    # Each case.
    for mode, cap, days, left, horizon in cases:
      # One sub-test each.
      with self.subTest(mode=mode, cap=cap, days=days):
        # The replica, with this cap and life.
        store = make(mode, storage=held)
        # As on_init reads them.
        store._ChainStoreBasePlugin__tomb_cap, store._ChainStoreBasePlugin__tomb_ms = cap, days * DAY_MS
        # The sweep.
        store._ChainStoreBasePlugin__sweep_tombstones()
        # The keys left: the record that is no tombstone, the tombstones kept, and the store's own record, which the
        # sweep wrote.
        keys = sorted(["dl:x", "hs:cstore_pull"] + ["dl:" + name for name in left])
        # The counts: those removed and those held, and the removed tombstone stamped too far ahead ("d"), which does
        # not raise the horizon.
        counts = {"tombstones_dropped": 4 - len(left), **({"tombstones_held": len(left)} if left else {}),
                  **({} if "d" in left else {"pull_h_future": 1})}
        # What the sweep left: the keys, the horizon, the mark for a save and the counts.
        found = (sorted(store.storage), store._ChainStoreBasePlugin__tomb_horizon, getattr(store, DIRTY), getattr(store, STATS))
        # As expected.
        self.assertEqual(found, (keys, horizon, True, counts))
        # The record holds the horizon as the sweep left it, and no marks (this store owes none).
        self.assertEqual(store.storage["hs:cstore_pull"]["value"], {"floor": horizon, "owed": None})

  def test_an_outsiders_delete_of_a_key_not_held_leaves_no_tombstone_and_cannot_raise_the_horizon(self):
    """In 'report' and 'on', a stamped delete of a key not held from a sender that is neither a replication peer
    nor in an oracle registry leaves no tombstone; the same delete from a replication peer or from a registry
    member, and the outside sender's delete of a key held, leave one. 600 such deletes of new keys stamped 59 s
    ahead then leave the real tombstones and the horizon as they were through the sweep (before, they evicted
    every real tombstone and set the horizon 59 s ahead)."""
    # Five real tombstones an hour old.
    real = {"dl:real%d" % index: tombstone("real%d" % index, "peer1", [NOW_MS - 3_600_000 + index, "peer1"])
            for index in range(5)}
    # Each mode that stamps.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The signed store (oracle registry "oracle", peers peer1 and peer2), holding a key of the outside sender.
        store = make(mode, storage={"held": record("held", "v", "out"), **real}, signed=True)
        # Deletes of keys not held from the outside sender, a replication peer and a registry member, and the
        # outside sender's delete of the key it holds.
        for key, sender in (("gone-out", "out"), ("gone-peer", "peer2"), ("gone-oracle", "oracle"), ("held", "out")):
          # Each delete, stamped by its sender.
          deliver(store, key, None, sender, [NOW_MS, sender])
        # Only the outside sender's delete of a key not held left no tombstone; three new ones were counted.
        self.assertEqual((sorted(key for key in store.storage if key.startswith("dl:") and key not in real),
                          getattr(store, STATS)["plain/remote/tombstone_new"]),
                         (["dl:gone-oracle", "dl:gone-peer", "dl:held"], 3))
        # The flood: 600 deletes of new keys from the outside sender, stamped 59 s ahead.
        for index in range(600):
          # Each one.
          deliver(store, "flood%d" % index, None, "out", [NOW_MS + 59_000, "out"])
        # The sweep of the next CSTORE_STATS line.
        store._ChainStoreBasePlugin__sweep_tombstones()
        # Every real tombstone is held and the horizon is where it was (0: none was ever removed).
        self.assertEqual((sorted(key for key in real if key in store.storage), store._ChainStoreBasePlugin__tomb_horizon),
                         (sorted(real), 0))

  def test_an_outsiders_delete_of_a_key_stored_before_the_lock_leaves_a_tombstone(self):
    """The sender's membership is asked before the persistence lock, for a key not held then; when another thread
    stores the key in between, the outside sender's stamped delete removes a key held, so it leaves a tombstone."""
    # The signed store (oracle registry "oracle", peers peer1 and peer2), in 'on'.
    store = make("on", signed=True)

    def stored_meanwhile():
      """
      Stand for the oracle registry's read, during which another thread stores the key.

      Returns
      -------
      list of str
        The oracle registry.

      Examples
      --------
      >>> stored_meanwhile()
      ['oracle']
      """
      # Another writer's record of the key, stored while the question runs.
      store.storage["raced"] = record("raced", "v", "peer1", [NOW_MS - 1000, "peer1"])
      # The registry.
      return ["oracle"]
    # The oracle registry's read.
    store.bc.get_eth_oracles = stored_meanwhile
    # The outside sender's stamped delete of the key, not held when it arrives.
    store._set_value("raced", None, owner="out", local_sync_storage_op=True, sender="out", ver=[NOW_MS, "out"])
    # The key is removed and its tombstone holds the delete's stamp.
    self.assertEqual(("raced" in store.storage, store.storage["dl:raced"]["ver"]), (False, [NOW_MS, "out"]))

  def test_an_outsider_that_writes_keys_and_deletes_them_holds_at_most_32_tombstones_and_cannot_raise_the_horizon(self):
    """In 'report' and 'on', a sender outside the replication peers and the registries that writes 600 keys and
    deletes them, stamped 58 and 59 s ahead, removes every key but leaves only 32 tombstones; the 5 real ones and
    the horizon are as they were after the sweep (before, the 600 tombstones evicted every real one at the cap and
    set the horizon 59 s ahead). A replication peer's and a registry member's deletes are not limited. Each delete
    names a new owner, which the sender chooses itself, so the limit holds by the verified sender and the writer in
    the stamp, not by the owner field."""
    # Five real tombstones an hour old.
    real = {"dl:real%d" % index: tombstone("real%d" % index, "peer1", [NOW_MS - 3_600_000 + index, "peer1"])
            for index in range(5)}
    # Each mode that stamps.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The signed store (oracle registry "oracle", peers peer1 and peer2) with the real tombstones.
        store = make(mode, storage=dict(real), signed=True)
        # The outside sender, a replication peer and a registry member each write keys, then delete them.
        for sender, count in (("out", 600), ("peer2", 40), ("oracle", 40)):
          # Each key of that sender.
          for index in range(count):
            # Its write, stamped 58 s ahead.
            deliver(store, "%s%d" % (sender, index), "x", sender, [NOW_MS + 58_000, sender])
            # Its delete, stamped 59 s ahead, naming a new owner each time: a limit that compared the stamp with the
            # owner, or counted tombstones by their owner, would let all 600 leave one.
            deliver(store, "%s%d" % (sender, index), None, sender, [NOW_MS + 59_000, sender], owner="w%d" % index)
        # The tombstones each sender's deletes left, by the writer of their stamp.
        held = {sender: sum(key.startswith("dl:" + sender) for key in store.storage) for sender in ("out", "peer2", "oracle")}
        # Every key removed; 32 tombstones of the outside sender, every one of the others.
        self.assertEqual((held, sum(not key.startswith("dl:") for key in store.storage)),
                         ({"out": 32, "peer2": 40, "oracle": 40}, 0))
        # The sweep of the next CSTORE_STATS line: 117 tombstones, under the cap.
        store._ChainStoreBasePlugin__sweep_tombstones()
        # Every real tombstone is held and the horizon is where it was (0: none was ever removed).
        self.assertEqual((sorted(key for key in real if key in store.storage), store._ChainStoreBasePlugin__tomb_horizon),
                         (sorted(real), 0))

  def test_an_outsider_under_32_tombstones_keeps_each_delete_and_one_stamped_as_another_writer_leaves_none(self):
    """An outside sender's delete of a key held leaves a tombstone while the replica holds fewer than 32 stamped
    with its address: its 32nd delete leaves one, its 33rd removes the key and leaves none, and after the sweep
    removes its expired tombstones its next delete leaves one again; in 'report', which takes a delete stamped as
    another writer's, such a delete removes the key and leaves no tombstone, also while the sender holds none and
    when it names that writer as owner too. The rule is for deletes only: the sender's value store of a key not
    held, while it holds 32 tombstones, keeps its stamp."""
    # The outside sender's 34 keys, written an hour ago, and a key of peer1.
    held = {"k%d" % index: record("k%d" % index, "v", "out", [NOW_MS - 3_600_000, "out"]) for index in range(34)}
    # The signed store in 'report', holding them.
    store = make("report", storage={**held, "p": record("p", "v", "peer1", [NOW_MS - 3_600_000, "peer1"])}, signed=True)
    # First, while it holds no tombstone, the sender's delete of peer1's key, stamped as peer1's own ('report' takes
    # it, counting ver_origin) and naming peer1 as owner: the stamp is checked against the verified sender, not
    # against the owner the sender wrote.
    deliver(store, "p", None, "out", [NOW_MS - 2000, "peer1"], owner="peer1")
    # Removed, and no tombstone.
    self.assertEqual(("p" in store.storage, "dl:p" in store.storage), (False, False))
    # Its first 32 deletes, each stamped by the sender.
    for index in range(32):
      # One delete of a key it holds.
      deliver(store, "k%d" % index, None, "out", [NOW_MS - 1000 + index, "out"])
    # The 32nd left a tombstone: 32 held.
    self.assertEqual(sum(key.startswith("dl:k") for key in store.storage), 32)
    # The 33rd delete.
    deliver(store, "k32", None, "out", [NOW_MS, "out"])
    # The key is removed, and no tombstone was left for it.
    self.assertEqual(("k32" in store.storage, "dl:k32" in store.storage), (False, False))
    # The sender's value store of a key not held, while it holds 32 tombstones.
    deliver(store, "fresh", "v", "out", [NOW_MS, "out"])
    # Stored with its stamp: the limit takes the stamp of a delete only.
    self.assertEqual(store.storage["fresh"]["ver"], [NOW_MS, "out"])
    # Eight days later the sweep removes the 32 expired tombstones.
    store._now = NOW + 8 * 86_400
    # The sweep.
    store._ChainStoreBasePlugin__sweep_tombstones()
    # The sender's next delete, stamped then, leaves a tombstone again.
    deliver(store, "k33", None, "out", [NOW_MS + 8 * DAY_MS, "out"])
    # The only tombstone held.
    self.assertEqual(sorted(key for key in store.storage if key.startswith("dl:")), ["dl:k33"])

  def test_start_drops_a_tombstone_whose_key_is_held_again(self):
    """on_init ('report') drops the tombstone of a key that is held again (rewritten under the previous release) and
    keeps a tombstone whose key is not held and a "dl:" record holding a value."""
    # A data folder with a cache folder, removed after the test.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # The cache folder.
    os.makedirs(os.path.join(folder.name, "chain_store"))
    # The manager in use, put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", SerializationManager.instance)
    # A manager that takes the registration.
    SerializationManager.instance = SimpleNamespace(register=lambda save, every: None)
    # KEY held again with its old tombstone; "b" deleted; "x" held next to a "dl:x" holding a value.
    stored = {KEY: record(KEY, "again", "p"), TOMB: tombstone(KEY, "p", [NOW_MS, "p"]),
              "dl:b": tombstone("b", "p", [NOW_MS, "p"]), "x": record("x", 1, "p"), "dl:x": record("dl:x", 2, "api")}
    # Written as the replica file.
    with open(os.path.join(folder.name, "chain_store", "object.pickle"), "wb") as stream:
      # The pickle the start loads.
      pickle.dump(stored, stream)
    # A store to start in 'report'.
    with mock.patch.object(_ChainStoreRuntimeHarness, "CSTORE_MODE", "report"):
      # Built with the switches in 'report'.
      store = _StartHarness(folder.name)
    # At the tests' wall clock, with an empty whitelist for the peer refresh.
    store._now, store.bc = NOW, SimpleNamespace(get_whitelist=lambda with_prefix=False: [])
    # The real on_init.
    store.on_init()
    # Only the tombstone of the held key is gone; the start added the store's own record (the horizon and the marks).
    self.assertEqual(sorted(store.storage), sorted([KEY, "dl:b", "x", "dl:x", "hs:cstore_pull"]))

  def test_an_orderly_stop_saves_a_tombstone_held_without_a_dirty_mark(self):
    """The tombstone of a delete of a key not held is not saved by the periodic flush but is saved at an orderly
    stop, so after a restart an older write of that key is still refused."""
    # A replica in 'on' holding nothing.
    store = make("on")
    # A peer's delete of a key this replica does not hold.
    deliver(store, KEY, None, "peer1", [NOW_MS, "peer1"])
    # The periodic flush finds nothing changed.
    store._ChainStoreBasePlugin__maybe_flush_chain_storage()
    # Nothing written.
    self.assertEqual(store.saved_storage, [])
    # The manager in use, put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", SerializationManager.instance)
    # A manager that takes the removal of the registration.
    SerializationManager.instance = SimpleNamespace(unregister=lambda save: None)
    # The orderly stop.
    store.on_close()
    # The tombstone is in the file.
    self.assertEqual(store.saved_storage[-1][TOMB]["ver"], [NOW_MS, "peer1"])


class TestPermissionCount(unittest.TestCase):
  """Writes that change a readonly record for another owner or node, or delete another node's record, are counted
  and never refused."""

  def test_readonly_takeovers_and_deletes_are_counted_and_applied_in_every_mode_but_off(self):
    """Over a readonly record of peer1, stamped by peer1: a delete by another owner, a same-value write by another
    owner (a takeover) and another value under the same owner from another node count perm_readonly and are applied
    as before ('report' and 'on'); another value under another owner counts only refused_readonly; the same owner
    and node deleting counts nothing; 'off' counts nothing."""
    # Each case: the writer's address, the owner it names, the value, and the counts it adds.
    cases = (
      ("peer2", "other", None, {"perm_readonly": 1}), ("peer2", "other", "v", {"perm_readonly": 1}),
      ("peer2", "peer1", "w", {"perm_readonly": 1}), ("peer2", "other", "w", {"refused_readonly": 1}),
      ("peer1", "peer1", None, {}),
    )
    # Each mode.
    for mode in ("off", "report", "on"):
      # Each case.
      for sender, owner, value, counts in cases:
        # One sub-test each.
        with self.subTest(mode=mode, sender=sender, owner=owner, value=value):
          # A replica holding peer1's readonly record, stamped by peer1.
          store = make(mode, storage={KEY: {**record(KEY, "v", "peer1", [NOW_MS, "peer1"]), "readonly": True}})
          # The write, with a newer stamp.
          deliver(store, KEY, value, sender, [NOW_MS + 1000, sender], owner=owner)
          # The counts it adds besides the write's own outcome, prefixed by the bucket and origin.
          added = {label.rsplit("/", 1)[1]: n for label, n in getattr(store, STATS).items()
                   if label.rsplit("/", 1)[1] in ("perm_readonly", "delete_foreign", "refused_readonly")}
          # None in 'off' but the readonly refusal, which every mode counts.
          self.assertEqual(added, counts if mode != "off" else {k: n for k, n in counts.items() if k.startswith("ref")})
          # Applied unless the readonly check refused it, in every mode.
          self.assertEqual(store.storage.get(KEY, {}).get("value"), "v" if "refused_readonly" in counts else value)

  def test_a_delete_of_another_nodes_record_is_counted_as_foreign(self):
    """A delete of a record that is not readonly, stamped by another node, counts delete_foreign and is applied; a
    record written before stamps existed is judged by its owner only, so its delete counts nothing; a local write
    over a tombstone counts nothing."""
    # A replica holding a record stamped by peer1 and a legacy one.
    store = make("report", storage={KEY: record(KEY, "v", "peer1", [NOW_MS, "peer1"]),
                                    "legacy": record("legacy", "v", "peer1")})
    # peer2 deletes both.
    deliver(store, KEY, None, "peer2", [NOW_MS + 1000, "peer2"])
    # The legacy one.
    deliver(store, "legacy", None, "peer2", [NOW_MS + 1000, "peer2"])
    # This node writes the deleted key again (over its tombstone).
    local_write(store, KEY, "again")
    # The permission events counted.
    events = {label: n for label, n in getattr(store, STATS).items() if "foreign" in label or "perm" in label}
    # Both deletes applied; only the stamped record's counted.
    self.assertEqual((KEY in store.storage, "legacy" in store.storage, events),
                     (True, False, {BUCKET + "/remote/delete_foreign": 1}))

  def test_protected_namespaces_and_another_token_are_not_counted(self):
    """A delete of a readonly record of a protected namespace by another owner, and a write carrying another token,
    count no permission event (the token is refused under its own word)."""
    # A secret revision record, readonly, of a dAuth namespace (protected).
    key = _hset_key("DAUTH_JOB_SECRET_REVISIONS", "7|1")
    # A replica of the signed store (a dAuth member) holding it, and a plain record with a token.
    store = make("on", signed=True, storage={key: {**record(key, {"s": 1}, "oracle", [NOW_MS, "oracle"]),
                                                    "readonly": True},
                                              "tok": {**record("tok", "v", "peer1"), "readonly": True, "token": "t"}})
    # Deleted by peer1, a dAuth member, under another owner.
    deliver(store, key, None, "peer1", [NOW_MS + 1000, "peer1"])
    # A write without the token.
    deliver(store, "tok", "w", "peer2", [NOW_MS + 1000, "peer2"], owner="other")
    # The delete applied, nothing counted as a permission event, the token refusal under its word.
    self.assertEqual((key in store.storage, [label for label in getattr(store, STATS) if "perm" in label],
                      getattr(store, STATS).get("plain/remote/refused_token")), (False, [], 1))

  def test_a_sync_copy_over_a_readonly_record_of_another_owner_is_counted(self):
    """A sync copy that replaces a readonly record with another value and owner (which the merge applies whatever
    the readonly flag) counts perm_readonly under the hsync origin."""
    # A replica holding peer1's readonly record.
    store = make("report", storage={KEY: {**record(KEY, "v", "peer1", [NOW_MS, "peer1"]), "readonly": True}})
    # A newer copy of peer2 with another value and owner.
    getattr(store, MERGE)("players", {KEY: record(KEY, "w", "peer2", [NOW_MS + 1000, "peer2"])})
    # Applied as before, and counted.
    self.assertEqual((store.storage[KEY]["value"], getattr(store, STATS)),
                     ("w", {BUCKET + "/hsync/perm_readonly": 1}))


def refusal(store, key, sender, ver, held_ver=None, word="older"):
  """
  Deliver one refusal answer from ``sender`` to the writer ``store``, as a receiver with the order switch 'on' sends it.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The writer.
  key : str
    The key of the refused store.
  sender : str
    The refusing receiver.
  ver : list
    The refused store's stamp, echoed.
  held_ver : list, optional
    The receiver's stamp for the key; None by default.
  word : str, optional
    The refusal word; ``"older"`` by default.

  Returns
  -------
  None

  Examples
  --------
  >>> refusal(store, KEY, "peer1", [NOW_MS, "node"])
  """
  # Through the payload handler, as the network delivers it.
  store.default_handler({_PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
                         _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                         "decoded": {store.CS_DATA: {store.CS_OP: store.CS_CONFIRM, store.CS_KEY: key,
                                                     store.CS_VER: ver, store.CS_REFUSED: word,
                                                     store.CS_HELD_VER: held_ver}}})


class TestRefusalAnswers(unittest.TestCase):
  """With the order switch 'on' a receiver answers a refused stamped store; the writer settles on the answer."""

  def test_a_refused_stamped_store_is_answered_to_its_sender_in_on_only(self):
    """'on' answers a refused stamped store with one message to its sender: the key, the echoed stamp, the word and
    the held stamp, no value. 'report' answers nothing; an unstamped store, a future stamp and a store dropped for
    its sender's membership are never answered (the future one is counted)."""
    # In 'report' an older store is applied and confirmed in the full form, with the store's stamp (the lean switch
    # in 'report').
    full = {"op": "SCONFIRM", "key": KEY, "value": "a", "owner": "peer2", "confirm_by_addr": "node",
            "confirm_by": ["node", "stream", "CHAIN_STORE", "instance"], "ver": [NOW_MS + 1000, "peer2"]}
    # In 'on' it is refused with an answer.
    answer = {"op": "SCONFIRM", "key": KEY, "ver": [NOW_MS + 1000, "peer2"], "refused": "older",
              "held_ver": [NOW_MS + 2000, "peer1"]}
    # Each mode and what an older store gets back.
    for mode, sent in (("report", [full]), ("on", [answer])):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding "b" stamped by peer1 at +2 s, and a plain record with a token.
        store = make(mode, storage={KEY: record(KEY, "b", "peer1", [NOW_MS + 2000, "peer1"]),
                                    "tok": {**record("tok", "v", "peer1", [NOW_MS, "peer1"]), "token": "t"}})
        # An older store from peer2: its answer, sent to peer2 alone.
        self.assertEqual((deliver(store, KEY, "a", "peer2", [NOW_MS + 1000, "peer2"]),
                          store.sent_payloads[-1]["node_addr"]), (sent, ["peer2"]))
        # What 'report' sends for the store stamped 61 s ahead, which it applies.
        future = [full | {"value": "c", "ver": [NOW_MS + 61_000, "peer2"]}] if mode == "report" else []
        # What an unstamped store refused by its token and the future one get back.
        replies = [deliver(store, "tok", "w", "peer2"), deliver(store, KEY, "c", "peer2", [NOW_MS + 61_000, "peer2"])]
        # Nothing for the first; nothing for the future one in 'on'.
        self.assertEqual(replies, [[], future])
    # A dAuth secret from a sender outside the registries: dropped before any rule, never answered.
    store = make("on", signed=True)
    # The store and what came back.
    self.assertEqual(deliver(store, _hset_key("DAUTH_JOB_SECRETS", "7"), {"x": 1}, "outsider", [NOW_MS, "outsider"]),
                     [])

  def test_the_writer_settles_a_matching_answer_once_and_takes_the_held_stamp(self):
    """A refusal answer settles its sender on the writer's own record whose stamp it echoes, once, without counting
    a confirmation, and moves the writer's clock up to the held stamp (not to one more than 60 s ahead); an answer
    from a non-target, a settled sender, for another stamp or none, for a remote record or for a key not held
    changes nothing."""
    # A writer in 'report' (the writer side acts in every mode) with peers peer1 and peer2, and a remote record.
    store = make("report", storage={"remote": record("remote", "v", "peer1", [NOW_MS, "peer1"])})
    # Its own write of KEY, unconfirmed.
    local_write(store, KEY, "v")
    # Its stamp.
    ver = store.storage[KEY]["ver"]
    # Answers that change nothing: a non-target, another stamp, no stamp, a remote record, a key not held.
    for sender, key, echoed in (("peer3", KEY, ver), ("peer1", KEY, [NOW_MS - 1, "node"]), ("peer1", KEY, None),
                                ("peer1", "remote", [NOW_MS, "peer1"]), ("peer1", "absent", ver)):
      # Delivered.
      refusal(store, key, sender, echoed)
    # Nothing settled yet.
    self.assertEqual(store.storage[KEY]["confirmed_peers"], [])
    # peer1's answer, held stamp 30 s ahead, twice.
    for _ in range(2):
      # Delivered.
      refusal(store, KEY, "peer1", ver, [NOW_MS + 30_000, "peer1"])
    # Settled once, no confirmation counted, the clock at the held stamp.
    self.assertEqual((store.storage[KEY]["confirmed_peers"], store.storage[KEY]["confirms"], getattr(store, CLOCK)),
                     (["peer1"], 0, NOW_MS + 30_000))
    # peer2's answer with a held stamp 2 min ahead: settled, the clock not moved.
    refusal(store, KEY, "peer2", ver, [NOW_MS + 120_000, "peer2"])
    # Both settled.
    self.assertEqual((store.storage[KEY]["confirmed_peers"], getattr(store, CLOCK)),
                     (["peer1", "peer2"], NOW_MS + 30_000))

  def test_the_wait_ends_at_once_when_every_target_refused(self):
    """A local write that both peers refuse (they hold a newer stamp) returns False before its timeout with the wait
    end refused_by_peers; with one refusal and one silent peer it waits out its retries and ends unconfirmed."""
    # With both peers answering, and with peer2 silent.
    for silent, end in ((False, "refused_by_peers"), (True, "unconfirmed")):
      # One sub-test each.
      with self.subTest(silent=silent):
        # A network that delivers at once.
        network = _FakeNetwork()
        # The writer and its two peers, in 'on'; the peers hold KEY stamped 50 s ahead by a third node.
        writer = make("on")
        # The peers.
        peers = [make("on", name, peers=("node",), storage={KEY: record(KEY, "b", "x", [NOW_MS + 50_000, "x"])})
                 for name in ("peer1", "peer2")]
        # Registered; a silent peer2 is not on the network.
        for store in [writer] + peers[:1 if silent else 2]:
          # Reachable.
          network.register(store)
        # The writer's clock, to see how long it waited.
        sleep = writer.sleep

        def sleep_and_send(seconds):
          """
          Stand for the wait loop's sleep: the plugin loop sends the queued store meanwhile.

          Parameters
          ----------
          seconds : float
            The sleep asked for.

          Returns
          -------
          None
          """
          # The queued store goes out, and the answers come back at once.
          writer._ChainStoreBasePlugin__maybe_broadcast()
          # Then the sleep itself.
          sleep(seconds)
        # Used by the wait loop.
        writer.sleep = sleep_and_send
        # The caller's dict.
        info = {}
        # The write, with a 10 s timeout and no retry.
        self.assertFalse(writer._set_value(KEY, "a", owner="node", timeout=10, max_retries=0, result_info=info))
        # How it ended and how long it waited (fake clock): at once when both refused, the whole timeout otherwise.
        self.assertEqual((info["outcome"], writer._now - NOW < 1), (end, not silent))

  def test_a_sync_copy_that_replaces_the_record_during_the_wait_leaves_it_to_the_timeout(self):
    """A sync copy of the same owner and value but readonly, merged while a local write waits, puts a remote record
    (no target list, a count of -1) in its place: the wait runs to its timeout and ends unconfirmed, it does not
    raise."""
    # A writer in 'on' with two peers that never answer.
    writer = make("on")
    # The wait loop's own sleep, which moves the fake clock.
    sleep = writer.sleep

    def sleep_and_merge(seconds):
      """
      Stand for the wait loop's sleep: a sync answer is merged meanwhile.

      Parameters
      ----------
      seconds : float
        The sleep asked for.

      Returns
      -------
      None
      """
      # A peer's copy of the writer's own record, stamped 1 s later and readonly: applied over the record.
      getattr(writer, MERGE)("players", {KEY: {**record(KEY, "a", "node", [NOW_MS + 1000, "peer1"]), "readonly": True}})
      # Then the sleep itself.
      sleep(seconds)
    # Used by the wait loop.
    writer.sleep = sleep_and_merge
    # The caller's dict.
    info = {}
    # The write, with a 10 s timeout and no retry: False after the whole timeout.
    self.assertFalse(writer._set_value(KEY, "a", owner="node", timeout=10, max_retries=0, result_info=info))
    # The record is the copy's (remote, no target list), and the wait ended by its timeout.
    self.assertEqual((writer.storage[KEY]["confirms"], "target_peers" in writer.storage[KEY], info["outcome"],
                      writer._now - NOW >= 10), (-1, False, "unconfirmed", True))


def confirm(store, key, sender, **fields):
  """
  Deliver one confirmation from ``sender`` to the writer ``store``, with the given fields.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The writer.
  key : str
    The confirmed key.
  sender : str
    The confirming receiver.
  **fields
    The other fields of the body: ``value``, ``owner`` and ``ver`` as the form being tested carries them.

  Returns
  -------
  None

  Examples
  --------
  >>> confirm(store, KEY, "peer1", ver=[NOW_MS, "node"])                 # lean form
  >>> confirm(store, KEY, "peer1", value="v", owner="node")             # the previous release's full form
  """
  # Through the payload handler, as the network delivers it.
  store.default_handler({_PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
                         _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                         "decoded": {store.CS_DATA: {store.CS_OP: store.CS_CONFIRM, store.CS_KEY: key, **fields}}})


class TestLeanConfirmations(unittest.TestCase):
  """With CSTORE_LEAN_CONFIRM 'on' a stamped store is confirmed by key and stamp; the writer counts either form."""

  def test_the_form_of_a_confirmation_follows_the_lean_switch_and_the_stamp(self):
    """A stamped store is confirmed with {op, key, ver} in 'on', with the full form plus the stamp in 'report' and
    with the previous release's full form in 'off'; an unstamped store gets the full form in every mode."""
    # The full form of the previous release for value "v" from peer1.
    full = {"op": "SCONFIRM", "key": KEY, "value": "v", "owner": "peer1",
            "confirm_by": ["node", "stream", "CHAIN_STORE", "instance"], "confirm_by_addr": "node"}
    # Each mode and the forms a stamped and an unstamped store get back.
    for mode, stamped in (("off", full), ("report", {**full, "ver": [NOW_MS, "peer1"]}),
                          ("on", {"op": "SCONFIRM", "key": KEY, "ver": [NOW_MS, "peer1"]})):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica in this mode.
        store = make(mode)
        # A stamped store, then an unstamped one of another key.
        self.assertEqual((deliver(store, KEY, "v", "peer1", [NOW_MS, "peer1"]),
                          deliver(store, KEY[:-1] + "x", "v", "peer1")), ([stamped], [{**full, "key": KEY[:-1] + "x"}]))

  def test_the_writer_counts_a_lean_confirmation_by_the_stamp_it_sent(self):
    """A lean confirmation counts when it echoes the stamp of the writer's last own store (sent_ver), once per
    target, also after a peer's identical write raised the record's stamp; another stamp, a non-target or a
    duplicate does not count; a full confirmation of the previous release counts by value as before."""
    # The writer in 'report': the writer side acts in every mode, and its stamps need 'report' or 'on'.
    store = make("report")
    # Its own write of KEY.
    local_write(store, KEY, "v")
    # The stamp it sent.
    sent = store.storage[KEY]["sent_ver"]
    # A peer's identical write with a newer stamp raises the record's stamp, not sent_ver.
    deliver(store, KEY, "v", "peer2", [NOW_MS + 5000, "peer2"])
    # Lean confirmations that do not count: another stamp (the raised one), and a non-target.
    confirm(store, KEY, "peer1", ver=store.storage[KEY]["ver"])
    # The non-target.
    confirm(store, KEY, "peer3", ver=sent)
    # Nothing counted.
    self.assertEqual(store.storage[KEY]["confirms"], 0)
    # peer1's lean confirmation of the stamp sent, twice.
    for _ in range(2):
      # Delivered.
      confirm(store, KEY, "peer1", ver=sent)
    # peer2 confirms in the previous release's full form.
    confirm(store, KEY, "peer2", value="v", owner="node")
    # Both counted once.
    self.assertEqual((store.storage[KEY]["confirms"], store.storage[KEY]["confirmed_peers"]), (2, ["peer1", "peer2"]))

  def test_report_counts_where_the_two_rules_disagree(self):
    """In 'report' a full confirmation with a stamp counts by value; one that echoes an earlier own stamp of the same
    value (after an identical rewrite) counts disagree_own, one whose value differs but whose stamp is the one sent
    counts disagree_other and is not counted as a confirmation."""
    # The writer in 'report'.
    store = make("report")
    # Its write of KEY, and the stamp it sent.
    local_write(store, KEY, "v")
    # The first stamp.
    first = store.storage[KEY]["sent_ver"]
    # The same value written again a second later: a fresh stamp.
    store._now += 1
    # The identical rewrite.
    local_write(store, KEY, "v")
    # A late full confirmation of the first write: counted by value, and counted as disagree_own.
    confirm(store, KEY, "peer1", value="v", owner="node", ver=first)
    # A full confirmation of the second stamp with another value: not counted, disagree_other.
    confirm(store, KEY, "peer2", value="w", owner="node", ver=store.storage[KEY]["sent_ver"])
    # One confirmation, and one count of each disagreement.
    self.assertEqual((store.storage[KEY]["confirms"],
                      {label: n for label, n in getattr(store, STATS).items() if "disagree" in label}),
                     (1, {BUCKET + "/confirm/disagree_own": 1, BUCKET + "/confirm/disagree_other": 1}))

  def test_a_lean_confirmation_of_a_delete_counts_on_its_tombstone(self):
    """A lean confirmation of a local delete counts on the tombstone the delete left."""
    # The writer in 'on', holding KEY.
    store = make("on", storage={KEY: record(KEY, "v", "peer1")})
    # Its local delete.
    local_write(store, KEY, None)
    # peer1's lean confirmation of the delete's stamp.
    confirm(store, KEY, "peer1", ver=store.storage[TOMB]["sent_ver"])
    # Counted on the tombstone.
    self.assertEqual(store.storage[TOMB]["confirms"], 1)

  def test_a_lean_and_a_full_confirmation_both_end_the_wait(self):
    """A write that needs two confirmations ends True when one receiver confirms in the lean form ('on') and the other
    in the previous release's full form ('off')."""
    # A network that delivers at once.
    network = _FakeNetwork()
    # The writer in 'on', needing both peers.
    writer = make("on")
    # Two confirmations required.
    writer.cfg_min_confirmations = 2
    # peer1 confirms lean, peer2 as the previous release.
    for store in (writer, make("on", "peer1", peers=("node",)), make("off", "peer2", peers=("node",))):
      # Reachable.
      network.register(store)
    # The writer's sleep.
    sleep = writer.sleep

    def sleep_and_send(seconds):
      """
      Stand for the wait loop's sleep: the plugin loop sends the queued store meanwhile.

      Parameters
      ----------
      seconds : float
        The sleep asked for.

      Returns
      -------
      None
      """
      # The store goes out, the confirmations come back at once.
      writer._ChainStoreBasePlugin__maybe_broadcast()
      # Then the sleep itself.
      sleep(seconds)
    # Used by the wait loop.
    writer.sleep = sleep_and_send
    # Confirmed by both.
    self.assertTrue(writer._set_value(KEY, "v", owner="node", timeout=10, max_retries=0))
    # Both counted.
    self.assertEqual(sorted(writer.storage[KEY]["confirmed_peers"]), ["peer1", "peer2"])


class _QueuedNetwork(_FakeNetwork):
  """A network that holds every message until the history delivers it, so messages arrive in any order."""

  def __init__(self):
    """
    Start with no message held.

    Returns
    -------
    None
    """
    # The registered stores.
    super().__init__()
    # Messages held: (sender, target, body).
    self.pending = []

  def dispatch(self, sender, targets, decoded):
    """
    Hold one message per target instead of delivering it.

    Parameters
    ----------
    sender : str
      The sending store's address.
    targets : str or list of str
      Its recipients.
    decoded : dict
      The message body.

    Returns
    -------
    None
    """
    # One held message per recipient.
    for target in ([targets] if isinstance(targets, str) else targets):
      # Delivered later, in the order the history picks.
      self.pending.append((sender, target, decoded))

  def deliver(self, index):
    """
    Deliver one held message to its recipient.

    Parameters
    ----------
    index : int
      Its place in ``pending``.

    Returns
    -------
    None
    """
    # Taken out of the held messages.
    sender, target, decoded = self.pending.pop(index)
    # Delivered as the plain fake network delivers.
    _FakeNetwork.dispatch(self, sender, target, decoded)


def run_history(seed, mode, steps=40, rest="sync"):
  """
  Run one seeded random history on three replicas and bring them to rest.

  Each step is one of: a local write of a random key on a random replica, a value or a delete; the delivery of a
  random held message; a second delivery of one (a re-delivery); the loss of one; a sync of one replica from
  another (export, then merge). The replicas' wall clocks differ by up to 20 s. At rest every held message is
  delivered in a random order, then (``rest`` "sync") every replica syncs from every other one, the syncs in a
  random order: under arrival order the last sync of a replica would decide what it keeps, so equal contents prove
  the order rule. With ``rest`` "handoff" there is no final sync: the clocks move on by 600 s eight times, and each
  time every replica runs its hand-off sweep and every message it sends is delivered, in a random order.

  Parameters
  ----------
  seed : int
    The history's seed; the same seed gives the same history in every mode.
  mode : str
    The mode of every switch of the three replicas.
  steps : int, optional
    Number of steps before the rest. Default is 40.
  rest : str, optional
    ``"sync"`` (the default) for the final syncs, ``"handoff"`` for the hand-off's sweeps instead.

  Returns
  -------
  tuple
    ``(contents, trace)``: the value of each key each replica holds at rest (one dict per replica), and the
    history's trace: what each local write returned, each message delivered (with its stamp field removed) and
    each sync's merged count, in order.

  Examples
  --------
  >>> contents, trace = run_history(7, "on")
  """
  # The history's random choices.
  rng = random.Random(seed)
  # Three replicas, each with the others as peers, on one held network.
  names, network = ["n0", "n1", "n2"], _QueuedNetwork()
  # Built in the mode.
  fleet = [make(mode, name, peers=[other for other in names if other != name]) for name in names]
  # Each registered, so its sends are held.
  for store in fleet:
    # Registered on the network.
    network.register(store)
  # Clock offsets of the replicas, within the 60 s bound.
  skews = [rng.uniform(-10, 10) for _ in fleet]
  # Two keys of one namespace, three values and a delete.
  keys, values = [_hset_key("ns", "a"), _hset_key("ns", "b")], ["x", "y", "z", None]
  # The trace and the shared wall clock.
  trace, now = [], NOW
  # Each step.
  for _ in range(steps):
    # Time passes, and each replica reads its own clock.
    now += rng.uniform(0, 2)
    # Each replica's wall clock.
    for store, skew in zip(fleet, skews):
      # Its offset.
      store._now = now + skew
    # What happens.
    event = rng.random()
    if event < 0.35:
      # A local write.
      trace.append(("write", local_write(rng.choice(fleet), rng.choice(keys), rng.choice(values))))
    elif event < 0.75 and network.pending:
      # One held message delivered.
      index = rng.randrange(len(network.pending))
      # Recorded without its stamp, which 'off' does not send.
      trace.append(("deliver",) + network.pending[index][:2] + (
        {field: item for field, item in network.pending[index][2]["CHAIN_STORE_DATA"].items() if field != "ver"},))
      # Delivered.
      network.deliver(index)
    elif event < 0.85 and network.pending:
      # A re-delivery: a held message held a second time.
      network.pending.append(rng.choice(network.pending))
    elif event < 0.9 and network.pending:
      # A lost message.
      network.pending.pop(rng.randrange(len(network.pending)))
    else:
      # One replica syncs the namespace from another.
      target, source = rng.sample(fleet, 2)
      # The merged count of that sync.
      trace.append(("sync", target.ee_addr, source.ee_addr,
                    getattr(target, MERGE)("ns", getattr(source, EXPORT)("ns"))))
  # At rest: every held message delivered, in a random order.
  while network.pending:
    # One of them.
    network.deliver(rng.randrange(len(network.pending)))
  if rest == "handoff":
    # Each replica.
    for store in fleet:
      # Each replica takes the others as nodes that stamp their stores (each sends many stamped stores a minute in
      # production; here a peer's every store to it may have been lost), so its sweeps may re-send to both.
      store._ChainStoreBasePlugin__stamped_peers.update(names)
    # Eight rounds, 600 s apart, so every pair of a lost message falls due and is answered.
    for _ in range(8):
      # Time passes.
      now += 600
      # Each replica sweeps at its own clock and sends what it queued.
      for store, skew in zip(fleet, skews):
        # Its clock.
        store._now = now + skew
        # Its sweep.
        getattr(store, SWEEP)()
        # Its re-sends go out.
        store._ChainStoreBasePlugin__maybe_broadcast()
      # Every message held delivered, the answers included, in a random order.
      while network.pending:
        # One of them.
        index = rng.randrange(len(network.pending))
        # Recorded without its stamp, as the deliveries of the steps.
        trace.append(("deliver",) + network.pending[index][:2] + (
          {field: item for field, item in network.pending[index][2]["CHAIN_STORE_DATA"].items() if field != "ver"},))
        # Delivered.
        network.deliver(index)
  else:
    # Every replica syncs from every other one, in a random order.
    pairs = [(target, source) for target in fleet for source in fleet if target is not source]
    # Shuffled by the history's seed.
    rng.shuffle(pairs)
    # Each sync.
    for target, source in pairs:
      # Its merged count.
      trace.append(("sync", target.ee_addr, source.ee_addr,
                    getattr(target, MERGE)("ns", getattr(source, EXPORT)("ns"))))
  # The value of each key each replica holds, tombstones left out.
  contents = [{key: rec["value"] for key, rec in store.storage.items() if not key.startswith("dl:")}
              for store in fleet]
  # Both.
  return contents, trace


def split(mode, seeds, rest="sync"):
  """
  Give the seeds whose replicas end with different contents.

  Parameters
  ----------
  mode : str
    The mode of every switch.
  seeds : range
    The histories run.
  rest : str, optional
    How the histories come to rest: ``"sync"`` (the default) or ``"handoff"`` (see ``run_history``).

  Returns
  -------
  list of int
    The seeds of the split histories.

  Examples
  --------
  >>> split("on", range(10))
  []
  """
  # A history splits when its three contents are not one.
  return [seed for seed in seeds if len({repr(sorted(c.items())) for c in run_history(seed, mode, rest=rest)[0]}) > 1]


class TestConvergence(unittest.TestCase):
  """Random histories of writes, re-deliveries, losses and syncs between three replicas."""

  def test_replicas_end_equal_in_on(self):
    """With the order switch 'on', 2,000 seeded histories of writes and deletes delivered in any order, re-delivered,
    lost and synced end with the same value of every key on the three replicas, a deleted key absent on all; the
    same harness with the switch 'off' (arrival order) leaves some of the first 500 split, so it can tell."""

    # None in 'on'; some in 'off'.
    self.assertEqual((split("on", range(2000)), split("off", range(500)) != []), ([], True))

  def test_report_behaves_as_off_apart_from_stamps(self):
    """1,000 seeded histories of writes and deletes run in 'off' and in 'report' give the same returns, the same
    messages apart from their stamps, the same sync counts and the same values at rest (tombstones left out)."""
    # The histories whose two runs differ.
    differ = [seed for seed in range(1000) if run_history(seed, "off") != run_history(seed, "report")]
    # None.
    self.assertEqual(differ, [])

  def test_handoff_brings_replicas_equal_without_a_final_sync(self):
    """With every switch 'on', 2,000 seeded histories with lost messages and no final sync end with the same value of
    every key on the three replicas once the hand-off's sweeps have run; the same histories in 'off' leave some of
    the first 500 split."""
    # None in 'on'; some in 'off'.
    self.assertEqual((split("on", range(2000), "handoff"), split("off", range(500), "handoff") != []), ([], True))

  def test_report_handoff_sends_what_off_sends(self):
    """1,000 seeded histories with the hand-off's sweeps and no final sync give the same returns, the same messages
    apart from their stamps and the same values at rest in 'report' as in 'off': report sends no re-send, so it ends
    split where 'off' does."""
    # The histories whose two runs differ.
    differ = [seed for seed in range(1000)
              if run_history(seed, "off", rest="handoff") != run_history(seed, "report", rest="handoff")]
    # None.
    self.assertEqual(differ, [])


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
