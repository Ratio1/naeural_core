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

# The wall clock of the tests, in seconds, and the same in ms (the unit of a stamp).
NOW, NOW_MS = 1_700_000_000.0, 1_700_000_000_000
# The private names of the store's state read here.
DIRTY, UNMARKED = "_ChainStoreBasePlugin__chain_storage_dirty", "_ChainStoreBasePlugin__chain_storage_unmarked"
# The stamp clock, the stamp of a local write, the merge and the export.
CLOCK, TICK = "_ChainStoreBasePlugin__ver_clock", "_ChainStoreBasePlugin__ver_tick"
# The merge of a sync answer and the export that answers a sync request.
MERGE, EXPORT = "_ChainStoreBasePlugin__merge_hset_snapshot", "_ChainStoreBasePlugin__export_hset_snapshot"
# The namespace and the field most tests write.
KEY = _hset_key("players", "alpha")
# Its bucket, the 14-character "hs:" prefix the counters use.
BUCKET = KEY[:14]


def make(mode, address="node", peers=("peer1", "peer2"), storage=None, signed=False):
  """
  Build a chain store with every wave 03 switch in ``mode``, at the tests' wall clock.

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
        # Applied and confirmed only in 'report'.
        self.assertEqual((len(confirms), KEY in store.storage), (int(applied), applied))
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
        # Refused unconfirmed in 'on', applied in 'report'.
        self.assertEqual((len(older), store.storage[KEY]["value"]), (0, "b") if mode == "on" else (1, "a"))
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
    # Older, then newer.
    self.assertEqual([len(deliver(store, key, other, "peer1", [NOW_MS + 1000, "peer1"])),
                      len(deliver(store, key, other, "peer1", [NOW_MS + 9000, "peer1"]))], [0, 1])
    # cs_rev: a replica holding cs_rev 200 stamped at +5 s.
    store = make("on", storage={KEY: record(KEY, {"t": "b", "cs_rev": 200}, "peer1", [NOW_MS + 5000, "peer1"])})
    # A higher cs_rev with an older stamp: applied.
    self.assertEqual(len(deliver(store, KEY, {"t": "c", "cs_rev": 300}, "peer2", [NOW_MS + 1000, "peer2"])), 1)
    # A lower cs_rev with a newer stamp: refused (by the cs_rev rule of every mode, before the order rule).
    self.assertEqual(len(deliver(store, KEY, {"t": "a", "cs_rev": 250}, "peer2", [NOW_MS + 9000, "peer2"])), 0)
    # The cs_rev 300 value kept, and the refusal under the cs_rev word.
    self.assertEqual((store.storage[KEY]["value"]["t"], getattr(store, STATS).get(BUCKET + "/remote/refused_cs_rev")),
                     ("c", 1))

  def test_a_versioned_value_beats_an_unversioned_one_whatever_their_stamps(self):
    """The council's split history: an oracle that missed a signed pointer and then wrote an unversioned one with a
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
        self.assertEqual(len(deliver(store, KEY, {"t": "x"}, "peer2", [NOW_MS + 9000, "peer2"])),
                         0 if mode == "on" else 1)
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


def run_history(seed, mode, deletes=False, steps=40):
  """
  Run one seeded random history on three replicas and bring them to rest.

  Each step is one of: a local write of a random key and value on a random replica (a delete too when ``deletes``
  is True); the delivery of a random held message; a second delivery of one (a re-delivery); the loss of one; a
  sync of one replica from another (export, then merge). The replicas' wall clocks differ by up to 20 s. At rest
  every held message is delivered, then every replica syncs from every other one.

  Parameters
  ----------
  seed : int
    The history's seed; the same seed gives the same history in every mode.
  mode : str
    The mode of every switch of the three replicas.
  deletes : bool, optional
    True to mix deletes into the writes. Default is False.
  steps : int, optional
    Number of steps before the rest. Default is 40.

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
  # Two keys of one namespace, three values (and deletes).
  keys, values = [_hset_key("ns", "a"), _hset_key("ns", "b")], ["x", "y", "z"] + ([None] if deletes else [])
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
  # At rest: every held message delivered.
  while network.pending:
    # In order.
    network.deliver(0)
  # Then every replica syncs from every other one.
  for target in fleet:
    # From each other replica.
    for source in fleet:
      if target is not source:
        # The sync.
        trace.append(("sync", target.ee_addr, source.ee_addr,
                      getattr(target, MERGE)("ns", getattr(source, EXPORT)("ns"))))
  # The value of each key each replica holds, tombstones left out.
  contents = [{key: rec["value"] for key, rec in store.storage.items() if not key.startswith("dl:")}
              for store in fleet]
  # Both.
  return contents, trace


class TestConvergence(unittest.TestCase):
  """Random histories of writes, re-deliveries, losses and syncs between three replicas."""

  def test_replicas_end_equal_in_on(self):
    """With the order switch 'on', 2,000 seeded histories of writes delivered in any order, re-delivered, lost and
    synced end with the same value of every key on the three replicas."""
    # The histories whose replicas differ at rest.
    split = [seed for seed in range(2000) if len({repr(sorted(c.items())) for c in run_history(seed, "on")[0]}) > 1]
    # None.
    self.assertEqual(split, [])

  def test_report_behaves_as_off_apart_from_stamps(self):
    """1,000 seeded histories run in 'off' and in 'report' give the same returns, the same messages apart from their
    stamps, the same sync counts and the same values at rest."""
    # The histories whose two runs differ.
    differ = [seed for seed in range(1000) if run_history(seed, "off") != run_history(seed, "report")]
    # None.
    self.assertEqual(differ, [])


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
