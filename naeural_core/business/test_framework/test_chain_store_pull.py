"""
Offline tests of the chain store's catch-up after a start: what the replica keeps for it under the reserved key
(the tombstone horizon and the marks of a start), the pages an oracle answers a catch-up request with, and the
requests a started oracle sends and what it does with the answers.
"""
# A copy of a replica, to see that a page judged only changed nothing.
import copy
# The ids of a store's requests.
import itertools
# The wire form of a page, measured apart from the store.
import json
# The data folder of a started store.
import os
# The replica file of a started store.
import pickle
# Content that compresses badly, made the same at every run.
import random
# Its temporary place.
import tempfile
# The test framework.
import unittest
# Stand-ins for the block engine of a start and for the SerializationManager on_init registers its save with.
from types import SimpleNamespace
# Sets the harness's mode for one start, and replaces that manager.
from unittest import mock

# The manager on_init registers its save with, replaced during a start.
from naeural_core.main.serialization_manager import SerializationManager
# The SDK's encryption of a message for its receivers, as the comm thread calls it.
from ratio1.bc.ec import BaseBCEllipticCurveEngine
# The encoder a node uses on the wire (the plugin's json_dumps).
from ratio1.logging.logger_mixins.json_serialization_mixin import _JSONSerializationMixin

# The counter attribute's private name and the harness that runs the real on_init.
from test_chain_store_accounting import STATS, _StartHarness
# The harness, its fake network, the payload field names of the test network, and the composers of a namespace's
# index and of a hash key.
from test_chain_store_hsync import _ChainStoreRuntimeHarness, _FakeNetwork, _PayloadData, _hset_index, _hset_key
# The order tests' store builder, deliverer of a live store, record and tombstone builders, constants and names.
from test_chain_store_order import (BUCKET, DAY_MS, DIRTY, EXPORT, KEY, NOW, NOW_MS, UNMARKED, answers, deliver, make,
                                    record, tombstone)
# The save back-off tests' fake clock, their store whose saves take a set time, and one call of the periodic flush.
from test_chain_store_save_backoff import _Clock, flush_at, timed_saves
# The builder of a value nested a given number of levels deep.
from test_chain_store_size_cap import nested

# The reserved key the store keeps its own record under.
RESERVED = "hs:cstore_pull"
# The key prefixes no page carries, as on_init computes them, by their private name.
SKIP = "_ChainStoreBasePlugin__pull_skip"
# The marks a catch-up still owes, the tombstone horizon and the tombstone cap, by their private names.
OWED, HORIZON, CAP = ("_ChainStoreBasePlugin__pull_owed", "_ChainStoreBasePlugin__tomb_horizon",
                      "_ChainStoreBasePlugin__tomb_cap")
# The tombstone sweep, the save and the writer of the reserved record, by their private names.
SWEEP, SAVE, KEEP = ("_ChainStoreBasePlugin__sweep_tombstones", "_ChainStoreBasePlugin__save_chain_storage",
                     "_ChainStoreBasePlugin__pull_keep")
# The bound of a stamp that is not a future one, at the tests' wall clock: 60 s ahead of it.
LIMIT = NOW_MS + 60_000
# The broker's packet limit, which no message may reach: 1 MiB.
PACKET = 1_048_576
# The six namespaces no page carries.
PROTECTED = _ChainStoreRuntimeHarness.CS_PROTECTED_NAMESPACES
# The catch-up's state, its step, the resync's step (which forgets the requests whose wait is over), the time of the
# resync's next request, the last save's duration and the catch-up's number, by their private names.
PULL, STEP, FORGET, RESYNC_NEXT, TOOK, NUMBER = (
  "_ChainStoreBasePlugin__pull", "_ChainStoreBasePlugin__maybe_pull", "_ChainStoreBasePlugin__maybe_resync",
  "_ChainStoreBasePlugin__resync_next", "_ChainStoreBasePlugin__save_took", "_ChainStoreBasePlugin__pull_peers")
# The send queue of a store's local writes, and its replication peers, by their private names.
OPS, PEERS = "_ChainStoreBasePlugin__ops", "_ChainStoreBasePlugin__chain_peers"
# The globals of the chain store's module, where its pick of an oracle and its clocks are looked up.
GLOBALS = _ChainStoreRuntimeHarness._hsync.__globals__
# The margin a request takes off each mark (twice the 60 s a stamp may run ahead), and the tombstone life, in ms.
MARGIN, LIFE = 120_000, 7 * DAY_MS
# The floor of a request sent at the tests' wall clock by a node that removed no tombstone.
FLOOR = NOW_MS - LIFE + MARGIN


def reserved(floor, owed):
  """
  Build the reserved record as a store writes it on the node "node".

  Parameters
  ----------
  floor : any
    The tombstone horizon it holds (a stamp time in ms; a test gives other values to see them repaired).
  owed : any
    The marks it holds (``{writer: ms}``), or None for none.

  Returns
  -------
  dict
    The record: the fields of a record synced from a peer, owned by "node", with the value
    ``{"floor": floor, "owed": owed}``.

  Examples
  --------
  >>> reserved(0, None)["value"]
  {'floor': 0, 'owed': None}
  """
  # As __pull_keep writes it.
  return record(RESERVED, {"floor": floor, "owed": owed}, "node")


def started(case, mode, storage=None, number=2, memory=None):
  """
  Start a chain store through the real ``on_init`` from a replica file, at the tests' wall clock.

  Parameters
  ----------
  case : unittest.TestCase
    The running test: the start's folder is removed when it ends.
  mode : str
    ``'off'``, ``'report'`` or ``'on'``, for every switch.
  storage : dict, optional
    The records of the replica file; None (the default) starts with no file.
  number : any, optional
    The config value ``CSTORE_PULL_PEERS``; 2 by default.
  memory : dict, optional
    The shared plugin memory the start publishes its functions in; a plain dict by default.

  Returns
  -------
  _StartHarness
    The started store of the node "node"; its ``lines`` hold what the start logged, as ``(text, color)``.

  Examples
  --------
  >>> started(self, "report").storage["hs:cstore_pull"]["value"]
  {'floor': 0, 'owed': {}}
  """
  # A data folder for this start.
  folder = tempfile.TemporaryDirectory()
  # Removed when the test ends.
  case.addCleanup(folder.cleanup)
  # Its cache folder, as the plugin's persistence folder names it.
  os.makedirs(os.path.join(folder.name, "chain_store"))
  if storage is not None:
    # The replica file.
    with open(os.path.join(folder.name, "chain_store", "object.pickle"), "wb") as stream:
      # Holding the records.
      pickle.dump(storage, stream)
  # The switches of the store, read when the harness is built.
  with mock.patch.object(_ChainStoreRuntimeHarness, "CSTORE_MODE", mode):
    # The store, not started yet.
    store = _StartHarness(folder.name)
  # The tests' wall clock, the catch-up's number, and an empty whitelist for the peer refresh of the start.
  store._now, store.cfg_cstore_pull_peers, store.bc = NOW, number, SimpleNamespace(
    get_whitelist=lambda with_prefix=False: [])
  # The instance path a confirmation of this store names (a list's copy method returns the path when called).
  store.get_instance_path = ["node", "stream", "CHAIN_STORE", "instance"].copy
  if memory is not None:
    # The memory the start publishes in, when the test watches it.
    store.plugins_shmem = memory
  # The real on_init.
  store.on_init()
  # Started.
  return store


def red(store):
  """
  Give the red lines a started store logged.

  Parameters
  ----------
  store : _StartHarness
    A store started by ``started``.

  Returns
  -------
  list of str
    The texts of the lines logged in red, in order.

  Examples
  --------
  >>> red(started(self, "report"))
  []
  """
  # The texts logged with the color red.
  return [text for text, color in store.lines if color == "r"]


class _StartCase(unittest.TestCase):
  """A test case whose starts register their save with a manager that does nothing."""

  def setUp(self):
    """
    Replace the SerializationManager for the test.

    A start registers its periodic save with the manager; the stand-in takes the registration and starts no save
    thread. The real manager is put back when the test ends.

    Returns
    -------
    None
    """
    # A manager that takes a registration and does nothing with it.
    manager = mock.patch.object(SerializationManager, "instance", SimpleNamespace(register=lambda save, every: None))
    # In force for this test.
    manager.start()
    # Put back after it.
    self.addCleanup(manager.stop)


class TestReservedRecord(_StartCase):
  """The replica keeps the tombstone horizon and the marks of a start under a reserved key no write may name."""

  def test_the_marks_of_a_start_are_the_newest_stamp_per_writer_taken_before_the_setter_is_published(self):
    """With the hand-off switch not 'off', on_init keeps per writer the newest stamp time held that is at most
    60 s ahead of the wall clock, over every record (a protected one and a tombstone included), before it
    publishes the setter; a write made after the start does not move them."""
    # Two records of writer w1 (the newer one in a protected namespace), a tombstone of w2, a stamp 59 s ahead by w3,
    # stamps 61 s ahead by w2 and by w4 (future ones), and a record without a stamp.
    held = {
      KEY: record(KEY, "a", "w1", [NOW_MS - 1000, "w1"]),
      _hset_key("DEEPLOY_OPERATIONS", "op"): record(_hset_key("DEEPLOY_OPERATIONS", "op"), "b", "w1", [NOW_MS - 500, "w1"]),
      "dl:plain": tombstone("plain", "w2", [NOW_MS - 2000, "w2"]),
      "ahead": record("ahead", "c", "w3", [NOW_MS + 59_000, "w3"]),
      "future2": record("future2", "d", "w2", [NOW_MS + 61_000, "w2"]),
      "future4": record("future4", "e", "w4", [NOW_MS + 61_000, "w4"]),
      "legacy": record("legacy", "f", "w5"),
    }
    # The marks such a replica gives.
    marks = {"w1": NOW_MS - 500, "w2": NOW_MS - 2000, "w3": NOW_MS + 59_000}
    # Each mode in which marks are kept.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # What the store owed at the moment the setter was published.
        seen = []

        class Memory(dict):
          """The shared plugin memory of the start, which notes the marks when the setter is put in it."""

          def __setitem__(self, name, value):
            """
            Put one function of the store in the memory, noting the store's marks when it is the setter.

            Parameters
            ----------
            name : str
              The memory key.
            value : any
              What the start publishes under it.

            Returns
            -------
            None
            """
            if name == _ChainStoreRuntimeHarness.CS_SETTER:
              # The marks as they are when a writer can first reach the store (the bound method's own store).
              seen.append(dict(getattr(value.__self__, OWED)))
            # Published.
            super().__setitem__(name, value)

        # The start.
        store = started(self, mode, held, memory=Memory())
        # The marks were taken before the setter was published, and are what the store owes and keeps.
        self.assertEqual((seen, getattr(store, OWED), store.storage[RESERVED]["value"]),
                         ([marks], marks, {"floor": 0, "owed": marks}))
        # A write of w1 after the start, stamped now.
        deliver(store, KEY, "newer", "w1", [NOW_MS, "w1"])
        # The marks stay those of the start.
        self.assertEqual((getattr(store, OWED), store.storage[RESERVED]["value"]["owed"]), (marks, marks))

  def test_what_a_start_owes_is_the_lower_of_the_saved_and_the_new_mark_and_no_writer_either_side_lacks(self):
    """While marks are still owed from an earlier start, a start owes per writer the lower of the saved mark and
    its own; a writer only the saved marks name, or only the replica holds, is left out, so all its records are
    owed; with nothing saved the start owes its own marks."""
    # The replica: w1 at 500 s, w2 at 700 s, w3 at 300 s before now.
    held = {name: record(name, "v", name, [NOW_MS - 1000 * age, name]) for name, age in (("w1", 500), ("w2", 700), ("w3", 300))}
    # Saved from an earlier start: w1 lower than held, w2 higher than held, and w5, which the replica no longer holds.
    saved = {"w1": NOW_MS - 900_000, "w2": NOW_MS - 100_000, "w5": NOW_MS - 50_000}
    # The start over the saved marks.
    store = started(self, "report", {**held, RESERVED: reserved(NOW_MS - DAY_MS, saved)})
    # The lower one per writer both sides name; w3 (not saved) and w5 (not held) are left out. The horizon is kept.
    self.assertEqual(store.storage[RESERVED]["value"],
                     {"floor": NOW_MS - DAY_MS, "owed": {"w1": NOW_MS - 900_000, "w2": NOW_MS - 700_000}})
    # No red line: the record was well formed.
    self.assertEqual(red(store), [])
    # The same replica with nothing owed from before: the start's own marks.
    store = started(self, "report", {**held, RESERVED: reserved(5, None)})
    # One per writer held.
    self.assertEqual(store.storage[RESERVED]["value"],
                     {"floor": 5, "owed": {"w1": NOW_MS - 500_000, "w2": NOW_MS - 700_000, "w3": NOW_MS - 300_000}})

  def test_the_horizon_is_written_with_the_removals_it_covers_and_read_back_at_the_next_start(self):
    """The sweep raises the horizon and writes it into the reserved record inside the lock hold of its removals,
    in every mode, so a save never holds a removal without it; a start reads it back from the saved replica; a
    removed tombstone stamped more than 60 s ahead does not raise it."""
    # Tombstones 3 h, 2 h and 1 h old and one 2 min ahead, by writer p.
    stamps = {"a": NOW_MS - 3 * 3_600_000, "b": NOW_MS - 2 * 3_600_000, "c": NOW_MS - 3_600_000, "d": NOW_MS + 120_000}
    # As held.
    held = {"dl:" + name: tombstone(name, "p", [ms, "p"]) for name, ms in stamps.items()}
    # Each mode: the horizon is raised and kept whatever the switches say.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding the four tombstones, with room for none: the sweep removes them all.
        store = make(mode, storage=held)
        # The cap as on_init would read it.
        setattr(store, CAP, 0)
        # The lock the store works under, to look at the replica each time it is released.
        lock, depth, torn = getattr(store, "_ChainStoreBasePlugin__persistence_lock"), [0], []

        class Watched:
          """The persistence lock, which checks the reserved record each time the last hold of it ends."""

          def __enter__(self):
            """
            Take the lock.

            Returns
            -------
            Watched
              The lock stand-in.
            """
            # One more hold.
            depth[0] += 1
            # The real lock.
            lock.acquire()
            # As a lock gives itself.
            return self

          def __exit__(self, *error):
            """
            Release the lock, noting a replica that holds a raised horizon without its record.

            Parameters
            ----------
            *error : tuple
              The exception of the block, if any.

            Returns
            -------
            None
            """
            # One hold less.
            depth[0] -= 1
            if depth[0] == 0 and getattr(store, HORIZON) != store.storage.get(RESERVED, reserved(0, None))["value"]["floor"]:
              # Released for a save to copy the replica while the record lags behind the horizon.
              torn.append(getattr(store, HORIZON))
            # The real lock.
            lock.release()

        # In place of the lock.
        setattr(store, "_ChainStoreBasePlugin__persistence_lock", Watched())
        # The sweep.
        getattr(store, SWEEP)()
        # The horizon is the newest removed stamp that is not a future one, the record holds it, and at no release
        # of the lock did the replica hold the horizon without the record.
        self.assertEqual((getattr(store, HORIZON), store.storage[RESERVED]["value"], torn, getattr(store, DIRTY)),
                         (stamps["c"], {"floor": stamps["c"], "owed": None}, [], True))
        # The save the mark asks for.
        getattr(store, SAVE)()
        # A start from the saved replica, in the same mode: the horizon is read back, with no red line.
        again = started(self, mode, store.saved_storage[-1])
        # As saved.
        self.assertEqual((getattr(again, HORIZON), red(again)), (stamps["c"], []))

  def test_no_write_names_the_reserved_key(self):
    """A local write of the reserved key, or of a key that starts with it, raises; a peer's store or delete of
    one is dropped unconfirmed and counted, and the record stays as it was, in every mode."""
    # Each mode.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A replica holding the record.
        store = make(mode, storage={RESERVED: reserved(7, {"w1": 5})})
        # The key itself and one that starts with it.
        for key in (RESERVED, RESERVED + "2"):
          # A local write raises, naming the reserved keys.
          with self.assertRaisesRegex(ValueError, "reserved for the chain store"):
            # Through the setter a plugin's write reaches.
            store._set_value(key, {"floor": 0, "owed": None}, owner="node", wait=False)
          # A peer's store and a peer's delete: no confirmation, no answer.
          self.assertEqual([deliver(store, key, value, "peer1", None if mode == "off" else [NOW_MS, "peer1"])
                            for value in ({"floor": 0, "owed": None}, None)], [[], []])
        # Only the record is held, as it was.
        self.assertEqual(store.storage, {RESERVED: reserved(7, {"w1": 5})})
        # Each of the four remote messages counted under the key's first 14 characters, which are the reserved key.
        self.assertEqual(getattr(store, STATS).get(RESERVED + "/remote/refused_invalid_key"), 4)

  def test_a_malformed_reserved_record_is_repaired_at_start_and_one_of_another_owner_is_kept(self):
    """A saved horizon that is not an int or lies more than 60 s ahead becomes that bound, and saved marks that
    are not a map of writer to int are dropped, each with one red line; a value that is not a dict is repaired
    as both; a well-formed record owned by another address is kept as it is, with one red line."""
    # One stamped record, so a start has a mark of its own.
    held = {KEY: record(KEY, "v", "w1", [NOW_MS - 1000, "w1"])}
    # The marks of such a start.
    marks = {"w1": NOW_MS - 1000}
    # Each saved record: what it holds, the horizon and the marks the start leaves, and its red lines.
    cases = (
      # A horizon of another type.
      (reserved("x", None), LIMIT, marks, 1), (reserved(None, None), LIMIT, marks, 1),
      # A float and a bool are not stamp times either.
      (reserved(1.5, None), LIMIT, marks, 1), (reserved(True, None), LIMIT, marks, 1),
      # A horizon one ms past the bound, and one at the bound, which is kept.
      (reserved(LIMIT + 1, None), LIMIT, marks, 1), (reserved(LIMIT, None), LIMIT, marks, 0),
      # Marks of another form: dropped, so the start owes its own.
      (reserved(7, ["w1"]), 7, marks, 1), (reserved(7, {"w1": "x"}), 7, marks, 1), (reserved(7, "x"), 7, marks, 1),
      # A float is no stamp time; both fields wrong: two lines.
      (reserved(7, {"w1": 1.5}), 7, marks, 1), (reserved("x", 5), LIMIT, marks, 2),
      # A value that is not a dict has no horizon (repaired) and no marks.
      (record(RESERVED, "x", "node"), LIMIT, marks, 1), (record(RESERVED, [1], "node"), LIMIT, marks, 1),
      # A well-formed record written under another address (the node's key was replaced since): kept, with the one red
      # line of a record this node did not write.
      (record(RESERVED, {"floor": 7, "owed": {"w1": NOW_MS - 5000}}, "old-address"), 7, {"w1": NOW_MS - 5000}, 1),
    )
    # Each case.
    for saved, horizon, owed, lines in cases:
      # One sub-test each.
      with self.subTest(saved=saved["value"]):
        # The start over that record.
        store = started(self, "report", {**held, RESERVED: saved})
        # The horizon and the marks as repaired, written back, and one red line per repair.
        self.assertEqual((getattr(store, HORIZON), store.storage[RESERVED]["value"], len(red(store))),
                         (horizon, {"floor": horizon, "owed": owed}, lines))

  def test_a_reserved_record_another_address_wrote_gives_one_red_line_and_is_read_as_before(self):
    """In every mode, a start over a well-formed reserved record that another address wrote logs one red line
    naming that address and reads the record as it reads its own (the same horizon and marks); the node's own
    record gives no line, and neither does a replica without the record."""
    # One stamped record, so a start in 'report' or 'on' has a mark of its own (w1, 1 s ago).
    held = {KEY: record(KEY, "v", "w1", [NOW_MS - 1000, "w1"])}
    # The same well-formed value, written by this node ("node") and by another address.
    value = {"floor": 7, "owed": {"w1": NOW_MS - 5000}}
    # Each mode: the record is read in all three.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The starts over the other address's record, over the node's own, and over a replica without one.
        other, own, none = (started(self, mode, {**held, **saved}) for saved in (
          {RESERVED: record(RESERVED, value, "old-address")}, {RESERVED: reserved(**value)}, {}))
        # The other address's record: one red line that names it; the same horizon and marks as the node's own.
        self.assertEqual(([("old-address" in text) for text in red(other)], getattr(other, HORIZON), getattr(other, OWED)),
                         ([True], getattr(own, HORIZON), getattr(own, OWED)))
        # The node's own record and no record: no red line.
        self.assertEqual((red(own), red(none)), ([], []))

  def test_the_red_line_quotes_and_cuts_an_owner_a_peer_chose(self):
    """An owner with newlines and 5,000 more characters, as a peer's store may name it, gives one red line that is
    one line: the owner quoted, its newlines escaped, and cut after 80 characters of that quoted form."""
    # An owner that would add a start-log line of its own, then flood the log.
    owner = "peer\n === Chain storage: a line the peer wrote\n" + "A" * 5000
    # A start over a well-formed record that names it, beside one stamped record.
    store = started(self, "report", {KEY: record(KEY, "v", "w1", [NOW_MS - 1000, "w1"]),
                                     RESERVED: record(RESERVED, {"floor": 7, "owed": {}}, owner)})
    # One red line: no newline in it, the first 80 characters of the quoted owner in it, the 81st not.
    self.assertEqual([(text.count("\n"), repr(owner)[:80] in text, repr(owner)[:81] in text) for text in red(store)],
                     [(0, True, False)])

  def test_the_namespace_answers_the_census_and_the_hand_off_do_not_see_the_reserved_record(self):
    """The reserved record, as the store writes it, is in no namespace's sync answer and in no row of the census,
    the hand-off's sweep passes over it, and the save writes it with the other records."""
    # A replica holding one record of the namespace "players".
    store = make("report", storage={KEY: record(KEY, "v", "w1", [NOW_MS, "w1"])})
    # A horizon and marks to keep.
    setattr(store, HORIZON, 7)
    # The marks.
    setattr(store, OWED, {"w1": 5})
    # The store writes its record: owned by this node, with the fields of a record synced from a peer.
    getattr(store, KEEP)()
    # As written.
    self.assertEqual(store.storage[RESERVED], reserved(7, {"w1": 5}))
    # The answer of the namespace, and of a namespace named as the key and as its parts: none carries the record.
    self.assertEqual([sorted(getattr(store, EXPORT)(name)) for name in ("players", RESERVED, "cstore_pull", "hs")],
                     [[KEY], [], [], []])
    # The census line.
    getattr(store, "_ChainStoreBasePlugin__log_census")()
    # Its JSON object.
    census = [text for text in store.messages if text.startswith("CSTORE_CENSUS ")][-1]
    # One row, the namespace's: the record is counted nowhere.
    self.assertEqual(sorted(store.json_loads(census[len("CSTORE_CENSUS "):])), [_hset_index("players")])
    # A sweep of the hand-off, due now.
    setattr(store, "_ChainStoreBasePlugin__handoff_next", 0)
    # It walks the replica without an error and tracks nothing for the record.
    getattr(store, "_ChainStoreBasePlugin__maybe_handoff")()
    # No pair.
    self.assertEqual(getattr(store, "_ChainStoreBasePlugin__handoff"), {})
    # The save.
    getattr(store, SAVE)()
    # The snapshot holds the record, and pickles as the replica file does.
    self.assertEqual(pickle.loads(pickle.dumps(store.saved_storage[-1]))[RESERVED], reserved(7, {"w1": 5}))

  def test_a_start_with_a_replica_older_than_the_tombstone_life_says_so_in_red(self):
    """When the newest stamp a start holds is older than CSTORE_TOMBSTONE_DAYS, one red line says that keys
    deleted meanwhile are still held; a newer replica, and an empty one, log none."""
    # The newest stamp held, as its age in ms, and the red lines expected.
    for age, lines in ((7 * DAY_MS + 1, 1), (7 * DAY_MS, 0), (DAY_MS, 0)):
      # One sub-test each.
      with self.subTest(age=age):
        # A replica whose two records are that old and one day older.
        store = started(self, "report", {"a": record("a", "v", "w1", [NOW_MS - age, "w1"]),
                                         "b": record("b", "v", "w2", [NOW_MS - age - DAY_MS, "w2"])})
        # The line, when the newest mark is older than the life.
        self.assertEqual(len([text for text in red(store) if "older than CSTORE_TOMBSTONE_DAYS" in text]), lines)
    # A start with no replica file: nothing is held, nothing is said.
    self.assertEqual(red(started(self, "report")), [])

  def test_with_the_switch_off_or_the_number_zero_a_start_keeps_no_marks_and_writes_nothing(self):
    """With CSTORE_HANDOFF 'off', or CSTORE_PULL_PEERS 0 or below, a start takes no marks and leaves the replica
    as the file gave it: no record is created, and a saved one stays as it is, its horizon and marks read; it
    starts no catch-up, so its laps ask nobody, also with saved marks; the number is read once, as an int."""
    # One stamped record.
    held = {KEY: record(KEY, "v", "w1", [NOW_MS - 1000, "w1"])}
    # A record saved by an earlier run.
    saved = reserved(7, {"w1": 5})
    # The switch 'off' with the default number, and the switch not 'off' with the number 0 and below.
    for mode, number in (("off", 2), ("report", 0), ("on", -1), ("report", "0")):
      # One sub-test each.
      with self.subTest(mode=mode, number=number):
        # A start with no record saved: none is created, nothing is owed, the horizon is 0.
        store = started(self, mode, held, number)
        # As the file gave it.
        self.assertEqual((store.storage, getattr(store, OWED), getattr(store, HORIZON)), (held, None, 0))
        # A start over a saved record: it stays as saved, and its two fields are read.
        store = started(self, mode, {**held, RESERVED: saved}, number)
        # Unchanged.
        self.assertEqual((store.storage, getattr(store, OWED), getattr(store, HORIZON)),
                         ({**held, RESERVED: saved}, {"w1": 5}, 7))
        # Two replication peers, both registry members.
        setattr(store, PEERS, ["peer1", "oracle"])
        # The registry of the tests, in which this node is a member too.
        store.bc = oracle(mode).bc
        # One run of the catch-up's step.
        getattr(store, STEP)()
        # No catch-up was started, and nothing was sent.
        self.assertEqual((getattr(store, PULL), store.sent_payloads), (None, []))
    # The number as a numeric str is taken as an int: marks are kept.
    self.assertEqual(getattr(started(self, "report", held, "3"), "_ChainStoreBasePlugin__pull_peers"), 3)
    # A word stops the start on the conversion.
    with self.assertRaisesRegex(ValueError, "invalid literal for int"):
      # The real on_init.
      started(self, "report", held, "two")


def oracle(mode, storage=None, address="node", peers=("peer1", "oracle")):
  """
  Build the chain store of one oracle of the tests' registry, encoding as a node does.

  The registry is the signed store's: dAuth registry ``node`` and ``peer1``, oracle registry ``oracle``; every
  other address (``worker``) is in neither.

  Parameters
  ----------
  mode : str
    ``'off'``, ``'report'`` or ``'on'``, for every switch.
  storage : dict, optional
    Records it holds; none by default.
  address : str, optional
    The node's address; ``"node"`` by default.
  peers : sequence of str, optional
    Its replication peers; ``("peer1", "oracle")`` by default.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store, whose ``json_dumps`` is the wire's encoder (compact separators), so a page is measured as on a node.

  Examples
  --------
  >>> oracle("report").json_dumps({"a": 1}, sort_keys=False)
  '{"a":1}'
  """
  # The signed store of the order tests, in this mode.
  store = make(mode, address=address, peers=peers, storage=storage, signed=True)
  # The encoder every message leaves a node with.
  store.json_dumps = _JSONSerializationMixin.safe_json_dumps
  # Ready.
  return store


def ask(store, request, sender="oracle", wait=8, **fields):
  """
  Deliver one catch-up request to ``store`` through its payload handler and give what it sent back.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store asked.
  request : any
    The request's ``pull`` field: ``{"since", "floor", "after"}``, or anything a peer could put there.
  sender : str, optional
    The verified sender; the oracle registry member ``"oracle"`` by default.
  wait : float, optional
    Seconds the store's clock moves on before the request, so a request is not answered as busy for the page
    before it; 8, the gap between two pages, by default.
  **fields : dict
    More fields of the request message (``hkey``, for a request that also names a namespace).

  Returns
  -------
  list of tuple
    One ``(recipients, body)`` per message the store sent for this request; ``body`` is the answer's
    ``CHAIN_STORE_DATA``.

  Examples
  --------
  >>> ask(oracle("report"), {"since": {}, "floor": 0, "after": None})[0][1]["pull"]
  {'after': None, 'more': False, 'partial': False, 'wait': 8}
  """
  # The store's clock, past the gap after the page before.
  store._now += wait
  # Only what this request makes the store send.
  before = len(store.sent_payloads)
  # Through the handler, as the network delivers a peer's message.
  store.default_handler({_PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
                         _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                         "decoded": {store.CS_DATA: {"op": "SHSYNC_REQ", "request_id": "q1", "pull": request, **fields}}})
  # Recipients and body of each message sent.
  return [(sent["node_addr"], sent["data"][store.CS_DATA]) for sent in store.sent_payloads[before:]]


def page(store, since=None, floor=0, after=None, **kwargs):
  """
  Ask ``store`` for one page and give its records' keys, in order, and its ``pull`` field.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store asked.
  since : dict, optional
    The marks of the request, writer to stamp time; none by default (every writer from 0).
  floor : int, optional
    The stamp time below which nothing may be sent; 0 by default.
  after : list, optional
    The cursor of the page before; None (the default) for the first page.
  **kwargs : dict
    The other arguments of ``ask`` (``sender``, ``wait``).

  Returns
  -------
  tuple
    ``(keys, pull)``: the keys of the page's records in the order sent, and the answer's ``pull`` field.

  Examples
  --------
  >>> page(oracle("report"))
  ([], {'after': None, 'more': False, 'partial': False, 'wait': 8})
  """
  # The one answer, to the sender alone.
  (_, body), = ask(store, {"since": since or {}, "floor": floor, "after": after}, **kwargs)
  # Its records' keys in the order sent, and its own field.
  return list(body["snapshot"]), body["pull"]


def page_bytes(store, snapshot):
  """
  Measure the records of a page as they travel: the UTF-8 bytes of their compact JSON.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    A store (for its field names only).
  snapshot : dict
    The page's records, key to record.

  Returns
  -------
  int
    The bytes of the ``snapshot`` field of the answer on the wire, with non-ASCII characters as themselves.

  Examples
  --------
  >>> page_bytes(store, {})
  2
  """
  # Compact JSON, as the wire's encoder writes it, then the bytes that travel.
  return len(json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def at_the_cap(key, owner, ver, content):
  """
  Build a str value that makes a record take exactly ``CS_MAX_RECORD_BYTES`` on the wire.

  Parameters
  ----------
  key : str
    The record's composed key.
  owner : str
    Its owner.
  ver : list
    Its stamp.
  content : callable
    Given a number of bytes, returns a str of about that many UTF-8 bytes (at most that many).

  Returns
  -------
  str
    The value: ``content`` of the bytes the cap leaves, filled up with the letter ``x`` to the exact size, as the
    size cap measures the record (its entry in a sync answer plus its token).

  Examples
  --------
  >>> len(at_the_cap(KEY, "w1", [NOW_MS, "w1"], lambda size: "x" * size)) < 524_288
  True
  """
  def size(value):
    """
    Measure the record with this value as the size cap does.

    Parameters
    ----------
    value : str
      The value.

    Returns
    -------
    int
      The UTF-8 bytes of ``{key: {key, value, owner, readonly, token, ver}}`` as compact JSON.
    """
    # The entry of a sync answer plus the token a store carries, as __wire_refusal encodes it.
    return len(json.dumps({key: {"key": key, "value": value, "owner": owner, "readonly": False, "token": None,
                                 "ver": ver}}, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

  # The content that fits beside the record's other fields.
  value = content(_ChainStoreRuntimeHarness.CS_MAX_RECORD_BYTES - size(""))
  # Filled up to the exact cap, one byte per letter.
  return value + "x" * (_ChainStoreRuntimeHarness.CS_MAX_RECORD_BYTES - size(value))


def worst_content(size):
  """
  Give text of about ``size`` UTF-8 bytes that the wire's compression shrinks least.

  Parameters
  ----------
  size : int
    The bytes wanted.

  Returns
  -------
  str
    Characters of one to four UTF-8 bytes, drawn (with a fixed seed) in the proportions that make the bytes'
    frequencies flattest; at most ``size`` bytes.

  Examples
  --------
  >>> len(worst_content(100).encode("utf-8")) <= 100
  True
  """
  # The same text at every run.
  rng = random.Random(1)
  # The printable ASCII characters JSON writes as themselves.
  ascii_chars = "".join(chr(code) for code in range(32, 128) if chr(code) not in '"\\')
  # Makers of one character of one, two, three and four bytes, with the weight each is drawn with.
  pools = ((94, 1, lambda: rng.choice(ascii_chars)), (88, 2, lambda: chr(rng.randrange(0x80, 0x800))),
           (60, 3, lambda: chr(rng.choice((rng.randrange(0x800, 0xD800), rng.randrange(0xE000, 0x10000))))),
           (40, 4, lambda: chr(rng.randrange(0x10000, 0x110000))))
  # The characters drawn and their bytes so far.
  chars, used = [], 0
  # Until no character of any width is sure to fit.
  while used < size - 4:
    # One more character, of a width drawn by the weights.
    _, width, make_char = rng.choices(pools, [weight for weight, _, _ in pools])[0]
    # Taken.
    chars.append(make_char())
    # Its bytes.
    used += width
  # The text.
  return "".join(chars)


class TestResponder(_StartCase):
  """An oracle answers a catch-up request with pages of the records the asking oracle may have missed."""

  def test_a_page_holds_the_records_from_the_marks_and_the_floor_after_the_cursor_in_stamp_order(self):
    """A page holds the stamped records at or above the floor and their writer's mark, after the cursor, ordered
    by stamp time and then stored key; a writer the request does not name is sent from the floor, and so is a named
    writer whose mark lies below it; the last page says no more follows and each page counts pull_answered."""
    # Records by their offset in ms from a base time: w1 at 100, 200, 300; w2 at 150, 250; w3 at 50, 260, 260.
    base = NOW_MS - 600_000
    # Their keys: plain keys named after the writer and the offset, two of w3 at the same stamp time.
    stamps = {"w1@100": 100, "w1@200": 200, "w1@300": 300, "w2@150": 150, "w2@250": 250, "w3@050": 50,
              "w3@260a": 260, "w3@260b": 260}
    # As held.
    held = {key: record(key, key, key[:2], [base + offset, key[:2]]) for key, offset in stamps.items()}
    # Each mode in which a node answers.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The answering oracle.
        store = oracle(mode, held)
        # Marks for w1 and w2 (w2's one ms above its newest record), none for w3, and a floor above w3's oldest.
        since, floor = {"w1": base + 200, "w2": base + 251}, base + 60
        # The first page: w1 from its mark, w3 from the floor, by stamp time and then key; nothing more follows.
        self.assertEqual(page(store, since, floor), (["w1@200", "w3@260a", "w3@260b", "w1@300"], {
          "after": [base + 300, "w1@300"], "more": False, "partial": False, "wait": 8}))
        # After a cursor between the two records of one stamp time: the second of them and what follows.
        self.assertEqual(page(store, since, floor, [base + 260, "w3@260a"])[0], ["w3@260b", "w1@300"])
        # After the last record: an empty page that keeps the request's cursor and says no more follows.
        self.assertEqual(page(store, since, floor, [base + 300, "w1@300"]),
                         ([], {"after": [base + 300, "w1@300"], "more": False, "partial": False, "wait": 8}))
        # A mark exactly at a record's stamp time sends that record; a floor exactly at one sends it too.
        self.assertEqual(page(store, {"w1": base + 300, "w2": base + 250, "w3": base + 261}, base + 250)[0],
                         ["w2@250", "w1@300"])
        # Marks below the floor for every writer named: the floor still binds them, nothing below it is sent.
        self.assertEqual(page(store, {"w1": base, "w2": base, "w3": base}, base + 250)[0],
                         ["w2@250", "w3@260a", "w3@260b", "w1@300"])
        # Each record travels with the five fields a merge reads, as a namespace-sync answer carries them.
        (_, body), = ask(store, {"since": {"w1": base + 300, "w2": NOW_MS, "w3": NOW_MS}, "floor": 0, "after": None})
        # The one record of that page.
        self.assertEqual(body["snapshot"], {"w1@300": {"key": "w1@300", "value": "w1@300", "owner": "w1",
                                                        "readonly": False, "ver": [base + 300, "w1"]}})
        # Six pages were sent.
        self.assertEqual(getattr(store, STATS), {"pull_answered": 6})

  def test_no_page_carries_a_record_of_a_protected_namespace_a_token_or_an_unstamped_record(self):
    """A node that holds, in each of the six protected namespaces, a stamped record and a tombstone (in the secret
    revisions a payload, its revocation marker and its redacted copy), a token record, an unstamped record and
    its own reserved record sends none of them: a page holds only the ordinary stamped records; a store started
    through on_init computes the same prefixes as the harness the asker's tests use."""
    # One stamped record and one tombstone per protected namespace.
    held = {}
    # Each of the six.
    for index, namespace in enumerate(PROTECTED):
      # A record of the namespace.
      key = _hset_key(namespace, "field")
      # Held with a stamp.
      held[key] = record(key, {"payload": namespace}, "w1", [NOW_MS - 5000 + index, "w1"])
      # And the tombstone of a deleted key of it.
      held["dl:" + _hset_key(namespace, "gone")] = tombstone(_hset_key(namespace, "gone"), "w1", [NOW_MS - 4000 + index, "w1"])
    # A secret revision's payload, its revocation marker and the redacted copy of another revision.
    for field, value in (("7|1", {"job_secrets": "s"}), ("7|1|revoked", {"job_id": "7", "revision": 1}),
                         ("7|2", {"job_id": "7", "revision": 2, "revoked": True})):
      # Under the secret revisions' namespace.
      key = _hset_key("DAUTH_JOB_SECRET_REVISIONS", field)
      # Held with a stamp.
      held[key] = record(key, value, "w1", [NOW_MS - 3000, "w1"])
    # A record with a token, one without a stamp, and the store's own record.
    held.update({"token": {**record("token", "t", "w1", [NOW_MS - 2000, "w1"]), "token": "tok"},
                 "legacy": record("legacy", "u", "w1"), RESERVED: reserved(0, None)})
    # Two ordinary stamped records: one of a namespace, one plain key; and the tombstone of an ordinary key.
    held.update({KEY: record(KEY, "a", "w1", [NOW_MS - 1000, "w1"]), "plain": record("plain", "b", "w2", [NOW_MS - 900, "w2"]),
                 "dl:gone": tombstone("gone", "w2", [NOW_MS - 800, "w2"])})
    # Each mode in which a node answers.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # Asked for everything it holds: no mark, floor 0.
        keys, pull = page(oracle(mode, held))
        # Only the three ordinary records, in stamp order, and nothing more to follow.
        self.assertEqual((keys, pull["more"]), ([KEY, "plain", "gone"], False))
        # The same replica on a store started through on_init, which computes the prefixes no page carries.
        store = started(self, mode, held)
        # The registry of the tests, in which the asking node "oracle" is a member.
        store.bc = oracle(mode).bc
        # The same three records.
        self.assertEqual(page(store)[0], [KEY, "plain", "gone"])
        # The prefixes on_init computed are those of the harness, which the asker's tests drop a page's records by
        # (the "dl:" key of a forged page among them).
        self.assertEqual(getattr(store, SKIP), getattr(oracle(mode), SKIP))

  def test_a_tombstone_travels_under_the_key_it_deleted_and_the_cursor_names_the_stored_key(self):
    """A tombstone is sent under the key it deleted, with value None and its stamp; pages are ordered and cut by
    the stored key ("dl:" and the key), which is what the cursor carries, so a request after a tombstone's cursor
    gets what follows it."""
    # A value, a tombstone and another value, all at one stamp time.
    held = {"b": record("b", "v", "w1", [NOW_MS, "w1"]), "dl:c": tombstone("c", "w1", [NOW_MS, "w1"]),
            "e": record("e", "v", "w1", [NOW_MS, "w1"])}
    # The answering oracle.
    store = oracle("on", held)
    # The whole page.
    (_, body), = ask(store, {"since": {}, "floor": 0, "after": None})
    # By stored key: "b", then "dl:c" (sent as "c"), then "e"; the tombstone carries no value and its stamp.
    self.assertEqual((list(body["snapshot"]), body["snapshot"]["c"], body["pull"]["after"]),
                     (["b", "c", "e"], {"key": "c", "value": None, "owner": "w1", "readonly": False,
                                        "ver": [NOW_MS, "w1"]}, [NOW_MS, "e"]))
    # After the tombstone's cursor, which names its stored key: only what follows it.
    self.assertEqual(page(store, after=[NOW_MS, "dl:c"])[0], ["e"])
    # After the key it deleted, which sorts before "dl:c": the tombstone again, then "e".
    self.assertEqual(page(store, after=[NOW_MS, "c"])[0], ["c", "e"])

  def test_a_page_is_cut_at_its_byte_bound_and_a_larger_record_travels_alone(self):
    """Records are added while the page stays within CS_PULL_PAGE_BYTES: 40 records of 23 KB go as pages of 11, 11,
    11 and 7; a record larger than a page, and one at exactly the size cap, are sent alone, as the first record
    of a page is always taken."""
    # 40 records of 23 KB, one ms apart.
    held = {"k%02d" % index: record("k%02d" % index, "x" * 23_000, "w1", [NOW_MS + index, "w1"]) for index in range(40)}
    # The answering oracle, and each record its page builds encode, counted through its encoder.
    store, after, sizes, encoded = oracle("report", held), None, [], []
    # The encoder, counting each call.
    store.json_dumps = lambda value, **kwargs: encoded.append(value) or json.dumps(value, **kwargs)
    # Page after page, until one says no more follows (at most ten: a paging that never ends fails here).
    for _ in range(10):
      # The next page.
      (_, body), = ask(store, {"since": {}, "floor": 0, "after": after})
      # Its records and its bytes on the wire.
      sizes.append((len(body["snapshot"]), page_bytes(store, body["snapshot"])))
      if not body["pull"]["more"]:
        break
      # The next request asks after this page.
      after = body["pull"]["after"]
    # 11, 11, 11 and 7 records, each page within the bound; each build stops at the first record that does not fit
    # (12 + 12 + 12 + 7 encoded), so a page never skips a record for a later one, nor walks past its bound.
    self.assertEqual(([count for count, _ in sizes], all(size <= store.CS_PULL_PAGE_BYTES for _, size in sizes),
                      len(encoded)), ([11, 11, 11, 7], True, 43))
    # Two records that fill a page to the byte, and a third: the two go together, the third alone.
    held = {"p%d" % index: record("p%d" % index, None, "w1", [NOW_MS + index, "w1"]) for index in range(3)}
    # Each value sized so that its record counts half the page's bound.
    for key, stored in held.items():
      # The record's bytes with an empty value, as the page counts an entry: its key and its five fields.
      empty = page_bytes(None, {key: {"key": key, "value": "", "owner": "w1", "readonly": False, "ver": stored["ver"]}})
      # Filled up to half the bound, one byte per letter.
      stored["value"] = "x" * (_ChainStoreRuntimeHarness.CS_PULL_PAGE_BYTES // 2 - empty)
    # The answering oracle.
    store = oracle("report", held)
    # The first page: exactly the bound is within it, so both records go; the third is left for the next page.
    (_, body), = ask(store, {"since": {}, "floor": 0, "after": None})
    # Two records, counted as two halves of the bound.
    self.assertEqual((list(body["snapshot"]), body["pull"]["more"], sum(
      page_bytes(store, {key: entry}) for key, entry in body["snapshot"].items())), (["p0", "p1"], True, store.CS_PULL_PAGE_BYTES))
    # A small record, one of 300 KB (larger than a page, under the size cap), one exactly at the cap, a small one.
    held = {"a": record("a", "v", "w1", [NOW_MS, "w1"]), "b": record("b", "x" * 300_000, "w1", [NOW_MS + 1, "w1"]),
            "c": record("c", at_the_cap("c", "w1", [NOW_MS + 2, "w1"], lambda size: "x" * size), "w1", [NOW_MS + 2, "w1"]),
            "d": record("d", "v", "w1", [NOW_MS + 3, "w1"])}
    # The answering oracle, which holds the record at the cap as one the cap lets through.
    store, after, pages = oracle("report", held), None, []
    # The record at the cap is not refused by the size check, and one byte more would be.
    self.assertEqual([getattr(store, "_ChainStoreBasePlugin__wire_refusal")("c", held["c"]["value"] + extra, "w1", False, None,
                                                                            [NOW_MS + 2, "w1"]) for extra in ("", "x")],
                     [None, "too_large"])
    # Page after page, at most ten.
    for _ in range(10):
      # The next page.
      (_, body), = ask(store, {"since": {}, "floor": 0, "after": after})
      # Its keys.
      pages.append(list(body["snapshot"]))
      if not body["pull"]["more"]:
        break
      # The next request asks after this page.
      after = body["pull"]["after"]
    # The small record alone (the large one would pass the bound), each large record alone, then the last small one.
    self.assertEqual(pages, [["a"], ["b"], ["c"], ["d"]])

  def test_the_worst_page_of_one_record_at_the_size_cap_stays_under_the_brokers_packet(self):
    """One record at exactly CS_MAX_RECORD_BYTES, of content the compression shrinks least, is sent alone, and that
    page, encrypted by the SDK as the comm thread sends it, takes less than 90 % of the broker's 1 MiB packet."""
    # The record's stamp and its value: characters of one to four bytes, filled up to the exact cap.
    ver = [NOW_MS, "w1"]
    # At the cap.
    value = at_the_cap(KEY, "w1", ver, worst_content)
    # The answering oracle holds it and a second record, so the page is cut after the first.
    store = oracle("report", {KEY: record(KEY, value, "w1", ver), "next": record("next", "v", "w1", [NOW_MS + 1, "w1"])})
    # The page.
    (receivers, body), = ask(store, {"since": {}, "floor": 0, "after": None})
    # The record at the cap alone; more follows.
    self.assertEqual((list(body["snapshot"]), body["pull"]["more"]), ([KEY], True))
    # The fields a payload carries beside the store's own, with values of the sizes a node sends.
    envelope = {"EE_IS_ENCRYPTED": True, "EE_PAYLOAD_PATH": ["0xai_" + "S" * 44, "admin_pipeline", "CHAIN_STORE_BASE", "CHAIN_STORE_BASE_INST"],
                "EE_ID": "node-alias-0123456789", "EE_SENDER": "0xai_" + "S" * 44, "EE_MESSAGE_SEQ": 123456,
                "EE_TIMESTAMP": "2026-11-14 20:00:00.000000", "EE_TIMEZONE": "UTC+2", "EE_TZ": "Europe/Bucharest",
                "EE_VERSION": "2.10.721", "EE_FORMATTER": None, "STREAM_NAME": "admin_pipeline",
                "SIGNATURE": "CHAIN_STORE_BASE", "INSTANCE_ID": "CHAIN_STORE_BASE_INST", "SESSION_ID": None,
                "INITIATOR_ID": None, "INITIATOR_ADDR": None, "MODIFIED_BY_ID": None, "MODIFIED_BY_ADDR": None,
                "EE_HASH": "h" * 64, "EE_SIGN": "s" * 96, "EE_ETH_SENDER": "0x" + "a" * 40, "EE_ETH_SIGN": "0x" + "b" * 130}
    # The plaintext the comm thread encrypts: the payload as the wire's encoder writes it, non-ASCII as itself.
    plaintext = _JSONSerializationMixin.safe_json_dumps({store.CS_DATA: body, **envelope}, ensure_ascii=False)
    # A stand-in for the block engine's own state: its address and one key per receiver (random, as a derived one).
    engine = SimpleNamespace(address="0xai_" + "S" * 44, _shared_key=lambda own, peer, info, debug: os.urandom(32))
    # The SDK's encryption for the one receiver: compressed, encrypted, written as hex in JSON, compressed, base64.
    package = BaseBCEllipticCurveEngine.encrypt_for_multi(engine, plaintext, ["0xai_" + "R" * 44])
    # The message the broker carries: the package and the payload's fields outside it.
    message = len(json.dumps({"EE_ENCRYPTED_DATA": package, "EE_DESTINATION": ["0xai_" + "R" * 44], **envelope},
                             separators=(",", ":")))
    # Larger than the record itself (the content hardly compresses, and the package doubles as hex before it is
    # compressed again), and under nine tenths of the broker's packet.
    self.assertTrue(store.CS_MAX_RECORD_BYTES < message < PACKET * 9 // 10, message)

  def test_a_request_of_another_form_or_from_a_sender_outside_the_registries_is_refused_and_counted(self):
    """A request whose marks are not a map of writer to int, whose floor is not an int, whose cursor is not None
    or a list of an int and a str, or whose sender is in neither oracle registry, gets no answer and counts
    pull_refused; a request whose pull field is not a dict is no catch-up request at all."""
    # A well-formed request.
    good = {"since": {"w1": NOW_MS}, "floor": 0, "after": None}
    # Each field with values of another form.
    bad = [{**good, "since": value} for value in (["w1"], "w1", 5, None, {"w1": "x"}, {"w1": 1.5}, {"w1": True}, {"w1": None})]
    # The floor.
    bad += [{**good, "floor": value} for value in ("0", 1.5, True, None, [0])]
    # The cursor.
    bad += [{**good, "after": value} for value in ([1], [1, 2], ["a", "b"], [1, "k", 2], "x", {"a": 1}, 5, [1.5, "k"])]
    # A request without its fields.
    bad.append({})
    # The answering oracle, holding one record.
    store = oracle("report", {KEY: record(KEY, "v", "w1", [NOW_MS, "w1"])})
    # None of them is answered.
    self.assertEqual([ask(store, request) for request in bad], [[]] * len(bad))
    # The well-formed one from a sender in neither registry: not answered either.
    self.assertEqual(ask(store, good, sender="worker"), [])
    # Each counted.
    self.assertEqual(getattr(store, STATS), {"pull_refused": len(bad) + 1})
    # From a member of the dAuth registry and from one of the oracle registry: answered.
    self.assertEqual([[body["op"] for _, body in ask(store, good, sender=sender)] for sender in ("peer1", "oracle")],
                     [["SHSYNC_RESP"], ["SHSYNC_RESP"]])
    # A pull field that is not a dict names no catch-up: with no namespace the message is dropped uncounted, and
    # with one it is answered as the namespace sync it is, with no page field.
    self.assertEqual([ask(store, value) for value in (5, "x", ["a"], None)], [[]] * 4)
    # The namespace's answer.
    (_, body), = ask(store, 5, hkey="players")
    # A plain sync answer.
    self.assertEqual((sorted(body), list(body["snapshot"])), (["hkey", "op", "request_id", "snapshot"], [KEY]))
    # Nothing more was refused.
    self.assertEqual(getattr(store, STATS)["pull_refused"], len(bad) + 1)

  def test_a_node_sends_one_page_per_gap_and_none_with_a_long_queue_or_with_the_switch_off(self):
    """A second request within CS_PULL_GAP of a page, from anyone, gets a busy reply with no record; so does a
    request while more inputs wait than one lap takes; with CSTORE_HANDOFF 'off' nothing is answered or counted."""
    # One record to send.
    held = {KEY: record(KEY, "v", "w1", [NOW_MS, "w1"])}
    # A request for everything.
    request = {"since": {}, "floor": 0, "after": None}
    # The answering oracle.
    store = oracle("report", held)
    # A first page.
    self.assertEqual(page(store)[0], [KEY])
    # 7.9 s later, from another oracle: a busy reply, to that sender alone, with no record and the wait.
    self.assertEqual(ask(store, request, sender="peer1", wait=7.9),
                     [(["peer1"], {"op": "SHSYNC_RESP", "request_id": "q1", "snapshot": {},
                                   "pull": {"busy": True, "wait": 8}})])
    # At 8 s after the page: a page again.
    self.assertEqual(page(store, wait=0.1)[0], [KEY])
    # More inputs waiting than one lap takes: busy, also after the gap.
    store.input_queue_size = store.cfg_max_inputs_per_iteration + 1
    # A busy reply.
    self.assertEqual(ask(store, request)[0][1]["pull"], {"busy": True, "wait": 8})
    # Exactly as many as one lap takes: a page.
    store.input_queue_size = store.cfg_max_inputs_per_iteration
    # Sent.
    self.assertEqual(page(store)[0], [KEY])
    # Three pages were sent; a busy reply is not counted.
    self.assertEqual(getattr(store, STATS), {"pull_answered": 3})
    # The same node with every switch 'off': no answer to a request of either form, and nothing counted.
    store = oracle("off", held)
    # A good request and a malformed one.
    self.assertEqual(([ask(store, request), ask(store, {"since": 5})], getattr(store, STATS)), ([[], []], {}))

  def test_a_page_says_partial_while_its_sender_still_owes_a_catch_up_itself(self):
    """A page carries partial true while the answering node's replica holds marks a catch-up still owes (also
    empty ones: a start with an empty replica owes everything), and false when it holds none."""
    # What the answering node owes, and what its pages say.
    for owed, partial in ((None, False), ({}, True), ({"w1": NOW_MS}, True)):
      # One sub-test each.
      with self.subTest(owed=owed):
        # The answering oracle.
        store = oracle("report", {KEY: record(KEY, "v", "w1", [NOW_MS, "w1"])})
        # Its marks.
        setattr(store, OWED, owed)
        # The page says so.
        self.assertEqual(page(store)[1]["partial"], partial)

  def test_a_page_is_measured_in_the_bytes_that_travel_as_the_size_cap_measures_a_record(self):
    """The page's bound counts UTF-8 bytes of compact JSON with non-ASCII characters as themselves: 30 records of
    10,000 three-byte characters each (30 KB on the wire, 10,000 characters, 60 KB as ASCII escapes) go 8 to a
    page, within the bound."""
    # 30 records of 10,000 characters of three UTF-8 bytes.
    held = {"k%02d" % index: record("k%02d" % index, "\u20ac" * 10_000, "w1", [NOW_MS + index, "w1"]) for index in range(30)}
    # The answering oracle.
    store = oracle("report", held)
    # The first page.
    (_, body), = ask(store, {"since": {}, "floor": 0, "after": None})
    # Its bytes on the wire.
    size = page_bytes(store, body["snapshot"])
    # Eight records (26 if characters were counted, 4 if escapes were), within the bound and with no room for a ninth.
    self.assertEqual((len(body["snapshot"]), size <= store.CS_PULL_PAGE_BYTES < size + size // 8), (8, True))


def asking(mode, storage=None, owed=None, peers=("peer1", "oracle"), network=None, address="node"):
  """
  Build the chain store of an oracle whose start left a catch-up to run, at the tests' wall clock.

  Parameters
  ----------
  mode : str
    ``'off'``, ``'report'`` or ``'on'``, for every switch.
  storage : dict, optional
    Records it holds; none by default.
  owed : dict, optional
    The marks its start took, writer to stamp time; none by default (an empty replica: everything is owed).
  peers : sequence of str, optional
    Its replication peers; ``("peer1", "oracle")`` by default, both registry members.
  network : _FakeNetwork, optional
    A network to register the store on, which then delivers what it sends; None (the default) only records it.
  address : str, optional
    The node's address; ``"node"`` by default, a registry member.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store, with the state ``on_init`` leaves when it takes marks: the marks, the reserved record and a
    catch-up that asked nobody yet. Its ``uuid`` gives ``"r1"``, ``"r2"``, ..., and its resync is not due.

  Examples
  --------
  >>> lap(asking("report"), 0)[0][0]
  ['peer1']
  """
  # The oracle's store.
  store = oracle(mode, storage, address, peers)
  # A counter for the request ids.
  ids = itertools.count(1)
  # Distinct ids, as a node's uuid gives (the harness's own gives one fixed id).
  store.uuid = lambda *args, **kwargs: "r%d" % next(ids)
  # The marks of the start.
  setattr(store, OWED, {} if owed is None else owed)
  # The catch-up as on_init starts it: nobody asked, the first request due at once.
  setattr(store, PULL, dict(peer=None, tried=set(), tries=0, done=0, full=0, request=None, next=0, start=NOW))
  # The reserved record with the marks.
  getattr(store, KEEP)()
  # No request of the resync in these tests: only its forgetting of the requests whose wait is over runs.
  setattr(store, RESYNC_NEXT, NOW + 10 ** 9)
  if network is not None:
    # Reachable for requests and answers.
    network.register(store)
  # Ready.
  return store


def lap(store, at, *others):
  """
  Run the catch-up's step of ``store`` at ``at`` seconds after the tests' wall clock, as one lap of ``process``.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The asking store.
  at : float
    Seconds after ``NOW``.
  *others : _ChainStoreRuntimeHarness
    The other stores of the network, whose clocks are set to the same time.

  Returns
  -------
  list of tuple
    One ``(recipients, pull)`` per catch-up request the lap made the store send: who was asked, and the
    request's ``pull`` field.

  Examples
  --------
  >>> lap(asking("report"), 0)
  [(['peer1'], {'since': {}, 'floor': 1699395320000, 'after': None})]
  """
  # One wall clock for every node.
  for each in (store, *others):
    # This lap's time.
    each._now = NOW + at
  # Only what this lap sends.
  before = len(store.sent_payloads)
  # The catch-up's step.
  getattr(store, STEP)()
  # Then, as in a lap, the resync's step, which forgets the store's own requests whose wait is over.
  getattr(store, FORGET)()
  # The catch-up requests among what was sent.
  return [(sent["node_addr"], sent["data"][store.CS_DATA]["pull"]) for sent in store.sent_payloads[before:]
          if sent["data"][store.CS_DATA].get("op") == "SHSYNC_REQ" and "pull" in sent["data"][store.CS_DATA]]


def reply(store, sender, snapshot=None, at=None, request_id=None, pull=None, **fields):
  """
  Deliver one answer to a catch-up request of ``store`` through its payload handler.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The asking store.
  sender : str
    The answering node.
  snapshot : any, optional
    The answer's records, key to record; none by default.
  at : float, optional
    Seconds after ``NOW`` at which the answer arrives; None (the default) leaves the store's clock as it is.
  request_id : str, optional
    The id the answer echoes; the store's request in flight by default.
  pull : any, optional
    The answer's whole ``pull`` field; None (the default) builds a page's field from ``fields``.
  **fields : dict
    Fields of a page's ``pull`` that differ from ``{"after": None, "more": False, "partial": False, "wait": 8}``.

  Returns
  -------
  None

  Examples
  --------
  >>> reply(store, "peer1", {KEY: record(KEY, "v", "w1", [NOW_MS, "w1"])}, after=[NOW_MS, KEY], more=True)
  """
  if at is not None:
    # The time the answer arrives.
    store._now = NOW + at
  # The page's own field, as a node sends it, unless the test gives the whole field.
  page = {"after": None, "more": False, "partial": False, "wait": 8, **fields} if pull is None else pull
  # Through the handler, as the network delivers a peer's message.
  store.default_handler({_PayloadData.EE_SENDER: sender, _PayloadData.EE_ID: sender,
                         _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                         "decoded": {store.CS_DATA: {
                           "op": "SHSYNC_RESP", "request_id": request_id or getattr(store, PULL)["request"],
                           "snapshot": {} if snapshot is None else snapshot, "pull": page}}})


def pull_counts(store):
  """
  Give the counters of ``store`` that the catch-up fills.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    A store.

  Returns
  -------
  dict
    The labels of the ``CSTORE_STATS`` counters that hold ``pull``, with their counts.

  Examples
  --------
  >>> pull_counts(asking("report"))
  {}
  """
  # The catch-up's own labels and the merge's counts under its origins.
  return {label: count for label, count in getattr(store, STATS).items() if "pull" in label}


class TestRequester(_StartCase):
  """A started oracle asks other oracles, one after the other, for the records stamped after its marks."""

  def setUp(self):
    """
    Make the pick of the oracle a series asks the first one of the candidates.

    The store picks at random; the stand-in takes the candidates in the order of the replication peers, so each
    test knows who is asked. The module's own pick is put back when the test ends. The starts of these tests
    register their save with the stand-in manager of ``_StartCase``.

    Returns
    -------
    None
    """
    # The manager that takes a start's registration.
    super().setUp()
    # The first of the candidates, in place of a random one.
    pick = mock.patch.dict(GLOBALS, {"sample": lambda population, k: population[:k]})
    # In force for this test.
    pick.start()
    # Put back after it.
    self.addCleanup(pick.stop)

  def test_the_first_request_asks_one_oracle_for_what_is_stamped_after_the_marks_less_the_margin(self):
    """The first lap sends one SHSYNC_REQ with no namespace to one registry member among the peers: each mark
    less 120 s, the floor (now less the tombstone life plus 120 s) and no cursor; it is registered as the
    store's own request for 10 s, and nothing more is sent while it is in flight. A node in no registry asks
    nobody and stops after the hour without a red line."""
    # Each mode in which a catch-up runs.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # An oracle whose peers are a node in no registry and two registry members.
        store = asking(mode, owed={"w1": NOW_MS - 1000, "w2": NOW_MS - 500_000}, peers=("worker", "peer1", "oracle"))
        # What the first lap asks.
        request = {"since": {"w1": NOW_MS - 1000 - MARGIN, "w2": NOW_MS - 500_000 - MARGIN}, "floor": FLOOR, "after": None}
        # One request, to the first registry member; the node in no registry is not asked.
        self.assertEqual(lap(store, 0), [(["peer1"], request)])
        # The message: the namespace sync's request, with the catch-up's field and no namespace.
        self.assertEqual(store.sent_payloads[-1]["data"][store.CS_DATA], {"op": "SHSYNC_REQ", "request_id": "r1", "pull": request})
        # Registered as the store's own request: the oracle asked, until when, and what was asked.
        self.assertEqual(store.pending_hsync, {"r1": {"PEERS": ["peer1"], "response": None, "until": NOW + 10, "pull": request}})
        # While it is in flight, a lap sends nothing.
        self.assertEqual(lap(store, 5), [])
    # A node in no registry, with marks of its own and two registry members as peers.
    store = asking("report", owed={"w1": NOW_MS - 1000}, address="worker")
    # Laps for more than an hour: it never asks.
    self.assertEqual([lap(store, at) for at in range(0, 3840, 60)], [[]] * 64)
    # Its catch-up stopped at the first round past the hour, with no red line, and its marks are still kept.
    self.assertEqual((getattr(store, PULL), [text for text in store.messages if "did not finish" in text],
                      getattr(store, OWED)), (None, [], {"w1": NOW_MS - 1000}))

  def test_an_unanswered_request_ends_the_series_and_the_next_oracle_is_asked_from_the_beginning(self):
    """A request not answered within 10 s counts pull_no_answer and ends the series: the next oracle of the round
    is asked without a cursor, an answer that comes late is dropped, and when every oracle was tried a new round
    starts 120 s later."""
    # An oracle with two registry members as peers and one mark.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # The first request, to peer1.
    self.assertEqual([peers for peers, _ in lap(store, 0)], [["peer1"]])
    # Its first page, one second later: one record, and more to follow after it.
    reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, at=1, after=[NOW_MS - 5000, "a"], more=True)
    # The answered request is no longer pending, so the same answer delivered again is dropped: counted once.
    self.assertEqual(list(store.pending_hsync), [])
    # The duplicate.
    reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, request_id="r1", after=[NOW_MS - 5000, "a"], more=True)
    # One record judged, once.
    self.assertEqual(pull_counts(store)["plain/pull1/missing"], 1)
    # The next request goes 8 s after the page, to the same oracle, after the page's cursor.
    self.assertEqual((lap(store, 8.9), [(peers, pull["after"]) for peers, pull in lap(store, 9)]),
                     ([], [(["peer1"], [NOW_MS - 5000, "a"])]))
    # No answer. At 19 s its wait is not over; just after, the request is forgotten.
    self.assertEqual((lap(store, 19), lap(store, 19.5), list(store.pending_hsync)), ([], [], []))
    # The next lap counts the missing answer and starts a series with the other oracle, from the beginning.
    self.assertEqual(([(peers, pull["after"]) for peers, pull in lap(store, 19.6)], pull_counts(store)["pull_no_answer"]),
                     ([(["oracle"], None)], 1))
    # The first oracle's answer to the forgotten request comes late: dropped, the request in flight stays.
    reply(store, "peer1", {"b": record("b", "v", "w1", [NOW_MS - 4000, "w1"])}, request_id="r2")
    # Still waiting for the second oracle.
    self.assertEqual((list(store.pending_hsync), getattr(store, PULL)["peer"]), (["r3"], "oracle"))
    # It does not answer either: forgotten after its 10 s, then counted; no oracle is left in this round.
    self.assertEqual((lap(store, 29.7), lap(store, 29.8), pull_counts(store)["pull_no_answer"]), ([], [], 2))
    # The new round starts 120 s later, with the first oracle again, from the beginning.
    self.assertEqual((lap(store, 149.7), [(peers, pull["after"]) for peers, pull in lap(store, 149.8)]),
                     ([], [(["peer1"], None)]))
    # Three oracles were tried so far.
    self.assertEqual(getattr(store, PULL)["tries"], 3)
    # This time the first oracle answers, with nothing more to follow: one series ended.
    reply(store, "peer1", {"b": record("b", "v", "w1", [NOW_MS - 4000, "w1"])})
    # More oracles were tried than the number of series, but one series is not two: the catch-up goes on.
    self.assertEqual(((getattr(store, PULL) or {}).get("done"), getattr(store, OWED) is not None), (1, True))

  def test_a_namespace_sync_answer_while_a_catch_up_request_waits_is_taken_as_before(self):
    """While a catch-up request is in flight to an oracle, that oracle's answer to a caller's namespace sync is
    recorded for the caller as before, and the catch-up still waits for its own answer."""
    # An oracle with one mark.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # Its first catch-up request, to peer1.
    lap(store, 0)
    # A caller's blocking sync of a namespace, waiting for peer1's answer.
    store.pending_hsync["s1"] = {"hkey": "players", "PEERS": ["peer1"], "response": None}
    # The records of that answer.
    snapshot = {_hset_key("players", "p1"): record(_hset_key("players", "p1"), "v", "w1", [NOW_MS - 5000, "w1"])}
    # The answer, through the handler, as the network delivers it.
    store.default_handler({_PayloadData.EE_SENDER: "peer1", _PayloadData.EE_ID: "peer1",
                           _PayloadData.EE_DESTINATION: [store.ee_addr], _PayloadData.EE_IS_ENCRYPTED: True,
                           "decoded": {store.CS_DATA: {"op": "SHSYNC_RESP", "request_id": "s1", "hkey": "players",
                                                       "snapshot": snapshot}}})
    # Recorded for the caller; the catch-up's request is still in flight, and nothing of the catch-up was counted.
    self.assertEqual((store.pending_hsync["s1"]["response"], getattr(store, PULL)["request"], list(store.pending_hsync),
                      pull_counts(store)), ({"sender": "peer1", "snapshot": snapshot}, "r1", ["r1", "s1"], {"pull1_cut_s": 0}))

  def test_a_long_input_queue_holds_the_requests_back_and_starts_the_wait_of_one_in_flight_again(self):
    """While more inputs wait than one lap takes, no request is sent, and the 10 s wait of a request in flight
    starts again at each lap, so an answer still in the queue is taken when it is handled; without the long
    queue the same answer comes too late."""
    # An oracle with one mark.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # More inputs waiting than one lap takes: nothing is asked.
    store.input_queue_size = store.cfg_max_inputs_per_iteration + 1
    # Held back.
    self.assertEqual((lap(store, 0), getattr(store, PULL)["request"]), ([], None))
    # As many as one lap takes: the request goes.
    store.input_queue_size = store.cfg_max_inputs_per_iteration
    # Sent, to be forgotten 10 s later.
    self.assertEqual((len(lap(store, 1)), store.pending_hsync["r1"]["until"]), (1, NOW + 11))
    # The queue is long again: each lap starts the wait again.
    store.input_queue_size = store.cfg_max_inputs_per_iteration + 1
    # Two laps, the second after the first wait would have ended.
    self.assertEqual((lap(store, 10.9), lap(store, 15), store.pending_hsync["r1"]["until"]), ([], [], NOW + 25))
    # The queue is short again; the answer is handled at 24.5 s and taken: the series ends with it.
    store.input_queue_size = 0
    # A lap before the answer sends nothing.
    self.assertEqual(lap(store, 24), [])
    # The page, with nothing more to follow.
    reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, at=24.5)
    # One series ended; no answer was missed.
    self.assertEqual((getattr(store, PULL)["done"], pull_counts(store).get("pull_no_answer")), (1, None))
    # The same without a long queue: the request is forgotten after 10 s and the late answer is dropped.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # Asked at 0; forgotten just after 10 s.
    self.assertEqual((len(lap(store, 0)), lap(store, 10.1), list(store.pending_hsync)), (1, [], []))
    # The answer at 24.5 s finds no request.
    reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, at=24.5, request_id="r1")
    # No series ended.
    self.assertEqual(getattr(store, PULL)["done"], 0)

  def test_the_lap_runs_the_catch_up_before_it_forgets_the_requests_whose_wait_is_over(self):
    """process() runs the catch-up's step each lap, and before the resync's step: a request is sent by a lap, and
    with a long queue a lap past the request's 10 s starts its wait again before anything forgets it."""
    # An oracle with one mark.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # No peer refresh and no census line in these laps: the refresh just ran, and the hour is the current one.
    store._ChainStoreBasePlugin__last_chain_peers_refresh, store._ChainStoreBasePlugin__census_hour = NOW + 20, int((NOW + 20) // 3600)
    # A whole lap: the request goes.
    store.process()
    # Sent and pending.
    self.assertEqual((store.sent_payloads[-1]["data"][store.CS_DATA]["pull"]["after"], list(store.pending_hsync)), (None, ["r1"]))
    # 11 s later, past the request's wait, with more inputs waiting than one lap takes.
    store._now, store.input_queue_size = NOW + 11, store.cfg_max_inputs_per_iteration + 1
    # A whole lap: the wait starts again first, so the request is not forgotten on this lap.
    store.process()
    # Still pending, for 10 s more.
    self.assertEqual(store.pending_hsync["r1"]["until"], NOW + 21)

  def test_the_next_page_is_asked_after_the_gap_the_answerers_wait_and_ten_times_the_last_save(self):
    """After a page that says more follows, the next request waits max(8 s, the answer's wait, 10 x the last
    save's duration), at most 120 s; the save keeps its duration only with the save back-off not 'off'."""
    # Each case: the answer's wait, the last save's duration, and the seconds until the next request.
    cases = ((8, 0.0, 8), (3, 0.0, 8), (20.5, 0.0, 20.5), (8, 1.5, 15), (500, 0.0, 120), (8, 30.0, 120),
             (float("nan"), 0.0, 8), (-5, 0.0, 8))
    # Each case.
    for wait, took, pause in cases:
      # One sub-test each.
      with self.subTest(wait=wait, took=took):
        # An oracle with one mark, and the duration of its last save as the flush keeps it.
        store = asking("on", owed={"w1": NOW_MS - 600_000})
        # The duration.
        setattr(store, TOOK, took)
        # The first request.
        lap(store, 0)
        # Its page one second later: more follows after its record.
        reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, at=1, after=[NOW_MS - 5000, "a"],
              more=True, wait=wait)
        # Nothing just before the pause ends; the next page is asked when it does.
        self.assertEqual((lap(store, 1 + pause - 0.1), [pull["after"] for _, pull in lap(store, 1 + pause)]),
                         ([], [[NOW_MS - 5000, "a"]]))
    # The duration the flush keeps: a save of 2.5 s on a fake clock, with the save back-off in 'report' and in 'off'.
    for backoff, kept in (("report", 2.5), ("on", 2.5), ("off", 0.0)):
      # One sub-test each.
      with self.subTest(backoff=backoff):
        # The fake clock the module reads.
        clock = _Clock()
        # In place of the module's clocks for this case.
        with mock.patch.dict(GLOBALS, {"monotonic": clock.monotonic, "thread_time": clock.thread_time}):
          # A store whose every save takes 2.5 s.
          store = timed_saves(asking("report"), clock, 2.5)
          # The save back-off's mode, as on_init would read it.
          store._ChainStoreBasePlugin__cs_modes["save_backoff"] = backoff
          # One flush of a changed replica.
          flush_at(store, clock, 100.0)
        # The duration is kept, unless the back-off reads no clock.
        self.assertEqual(getattr(store, TOOK), kept)

  def test_a_catch_up_runs_as_many_series_as_the_number_says_with_a_pause_between_them(self):
    """With CSTORE_PULL_PEERS 2 the catch-up asks one oracle, 120 s later another, and then nothing is owed: the
    marks leave the reserved record without a dirty mark of their own; with 1 it is done after one series. A
    series with a partial page (its oracle still owes a catch-up itself) counts only once one series more than
    the number ended: two series with the number 1, three with the number 2."""
    # One record of w1 the asking oracle lacks, held by both others.
    held = {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}
    # Each case: the number, which of the two answering oracles still owes a catch-up, and when each series runs.
    cases = ((2, (), [(0, "peer1"), (120, "oracle")]), (1, (), [(0, "peer1")]),
             # Both partial with the number 1: the second series ends it, so the limit follows the number.
             (1, ("peer1", "oracle"), [(0, "peer1"), (120, "oracle")]),
             # Both partial: neither series counts until a third series ended, in the next round.
             (2, ("peer1", "oracle"), [(0, "peer1"), (120, "oracle"), (360, "peer1")]),
             # One partial, one not: one series without a partial page is not enough for the number 2.
             (2, ("peer1",), [(0, "peer1"), (120, "oracle"), (360, "peer1")]))
    # Each case.
    for number, partial, series in cases:
      # One sub-test each.
      with self.subTest(number=number, partial=partial):
        # The network of the three oracles.
        network = _FakeNetwork()
        # The two that answer; one that still owes a catch-up holds marks.
        others = [oracle("report", held, address, ("node",)) for address in ("peer1", "oracle")]
        # On the network, with their marks.
        for other in others:
          # Reachable.
          network.register(other)
          # Marks held, or none.
          setattr(other, OWED, {} if other.ee_addr in partial else None)
        # The asking oracle, with this number.
        store = asking("report", owed={"w1": NOW_MS - 600_000}, network=network)
        # As on_init reads it.
        setattr(store, NUMBER, number)
        # Laps every 10 s for 400 s: when a request went, and to whom, and the series that ended by then: all of them
        # and those without a partial page (none once the catch-up is complete).
        asked, ended = [], {}
        # Each lap.
        for at in range(0, 400, 10):
          # The requests of this lap.
          asked += [(at, peers[0]) for peers, _ in lap(store, at, *others)]
          # The count of series so far, while the catch-up runs.
          ended[at] = (getattr(store, PULL) or {}).get("done"), (getattr(store, PULL) or {}).get("full")
        # The series ran when and with whom expected.
        self.assertEqual(asked, series)
        if len(series) > 1:
          # After the second series: two ended, and each oracle's series counts as partial or not by its own pages.
          self.assertEqual(ended[130], (2, sum(peer not in partial for _, peer in series[:2])) if len(series) > 2 else (None, None))
        # Nothing is owed: no catch-up, no marks, the record without them, held without a dirty mark.
        self.assertEqual((getattr(store, PULL), getattr(store, OWED), store.storage[RESERVED]["value"],
                          getattr(store, UNMARKED), getattr(store, DIRTY)), (None, None, {"floor": 0, "owed": None}, True, False))
        # Each series judged the record it was sent ('report': the replica lacks it at every series) and counted its
        # cut: the first under pull1, every later one under pull2 (a third series adds no pull3 label).
        self.assertEqual(pull_counts(store), {"pull1_cut_s": 0, "plain/pull1/missing": 1, **(
          {"pull2_cut_s": 0, "plain/pull2/missing": len(series) - 1} if len(series) > 1 else {})})

  def test_oracles_that_drop_the_request_do_not_let_two_partial_series_end_the_catch_up(self):
    """During a rolling update two oracles of the previous release drop the request (each counts pull_no_answer
    after 10 s) and two new ones answer with partial pages: after those two series the catch-up is not complete
    (before, the four oracles tried let the two partial series end it at 144 s); it asks again in the next round,
    240 s after the last series (the pause after a series, then the pause before a new round), the old oracles
    first again, and is complete when a third series ends (here peer1's, still partial, at 407 s); the marks
    stay owed until then."""
    # An oracle with one mark, whose replication peers are two oracles of the previous release and two new ones.
    store = asking("report", owed={"w1": NOW_MS - 600_000}, peers=("old1", "old2", "peer1", "oracle"))
    # The two old ones are in the oracle registry with "oracle" (peer1 is a dAuth registry member).
    store.bc.get_eth_oracles = lambda: ["oracle", "old1", "old2"]
    # Who was asked when, and when the catch-up ended.
    events = []
    # Laps every half second for up to an hour.
    for at in [half / 2 for half in range(2 * 3600)]:
      # The requests of this lap.
      for peers, _ in lap(store, at):
        # Who.
        events.append((at, peers[0]))
        if peers[0] in ("peer1", "oracle"):
          # A new oracle answers one second later with its only page: partial, as it still owes a catch-up itself.
          reply(store, peers[0], {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, at=at + 1, partial=True)
      if getattr(store, PULL) is None:
        # The catch-up ended: complete when nothing is owed any more.
        events.append((at, "complete" if getattr(store, OWED) is None else "stopped"))
        # Done.
        break
      if at == 200:
        # Between the rounds: two partial series ended, and the catch-up still runs with the marks owed.
        self.assertEqual((getattr(store, PULL)["done"], getattr(store, PULL)["full"], getattr(store, OWED)),
                         (2, 0, {"w1": NOW_MS - 600_000}))
    # The first round: the old oracles unanswered, the new ones partial; the next round 240 s after the last series,
    # the old ones again, then peer1's third series ends the catch-up.
    self.assertEqual(events, [(0, "old1"), (11, "old2"), (22, "peer1"), (143, "oracle"), (384, "old1"), (395, "old2"),
                              (406, "peer1"), (406, "complete")])
    # Each old oracle counted twice as unanswered; the three series judged the record, the first under pull1 and the
    # two later ones under pull2.
    self.assertEqual(pull_counts(store), {"pull_no_answer": 4, "pull1_cut_s": 0, "plain/pull1/missing": 1,
                                          "pull2_cut_s": 0, "plain/pull2/missing": 2})

  def test_the_cleared_marks_stay_marked_when_a_save_copies_the_replica_as_the_catch_up_ends(self):
    """A save that copies the replica as the catch-up completes, before the record without the marks is written,
    leaves the change marked: the next save, or the save of an orderly stop, still writes that record."""
    # An oracle with one mark and one series to run.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # As on_init reads it.
    setattr(store, NUMBER, 1)
    # The store's writer of the reserved record.
    keep = getattr(store, KEEP)
    # A save of the save thread that copies the replica just before that record is written.
    setattr(store, KEEP, lambda: (getattr(store, SAVE)(), keep()))
    # The request, and one page with nothing more to follow: the catch-up completes.
    lap(store, 0)
    # The page.
    reply(store, "peer1", at=1)
    # The save wrote the marks; the record held has none, and is still marked for the next save.
    self.assertEqual((store.saved_storage[-1][RESERVED]["value"]["owed"], store.storage[RESERVED]["value"]["owed"],
                      getattr(store, PULL), getattr(store, UNMARKED)), ({"w1": NOW_MS - 600_000}, None, None, True))

  def test_a_busy_reply_makes_the_asker_wait_and_the_third_in_a_row_ends_the_series(self):
    """A busy reply (no record, no cursor) is not checked as a page: the same oracle is asked again after the
    wait, with the same cursor; the third busy reply in a row ends the series, uncounted, and a page in between
    starts the count again."""
    # A busy reply as a node sends it.
    busy = {"busy": True, "wait": 8}
    # An oracle with one mark.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # The first request; a busy reply one second later, which also says more follows, with no cursor: as a page that
    # would be refused, as a busy reply it is not checked.
    lap(store, 0)
    # Busy once.
    reply(store, "peer1", at=1, pull={**busy, "more": True})
    # Asked again 8 s later, the same oracle, still without a cursor.
    self.assertEqual((lap(store, 8.9), [(peers, pull["after"]) for peers, pull in lap(store, 9)]), ([], [(["peer1"], None)]))
    # Busy a second time; asked a third time.
    reply(store, "peer1", at=10, pull=busy)
    # The third request.
    self.assertEqual(len(lap(store, 18)), 1)
    # Busy a third time: the series with this oracle ends.
    reply(store, "peer1", at=19, pull=busy)
    # The next lap starts a series with the other oracle; nothing was counted as refused.
    self.assertEqual(([peers for peers, _ in lap(store, 19.1)], pull_counts(store).get("pull_refused")), ([["oracle"]], None))
    # That oracle is busy once: its count starts at one, so it is asked again after the wait, not given up.
    reply(store, "oracle", at=20, pull=busy)
    # The same oracle, 8 s later.
    self.assertEqual([peers for peers, _ in lap(store, 28)], [["oracle"]])
    # A new asker: two busy replies, a page with more to follow, two busy replies.
    store = asking("report", owed={"w1": NOW_MS - 600_000})
    # The answers, one per request, 10 s apart.
    for index, answer in enumerate((busy, busy, None, busy, busy)):
      # The request.
      lap(store, 10 * index)
      if answer is None:
        # The page: one record, more after it.
        reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, after=[NOW_MS - 5000, "a"], more=True)
      else:
        # A busy reply.
        reply(store, "peer1", pull=answer)
    # Still the same oracle: no three busy replies in a row.
    self.assertEqual((getattr(store, PULL)["peer"], getattr(store, PULL)["after"]), ("peer1", [NOW_MS - 5000, "a"]))

  def test_an_answer_of_another_form_or_a_cursor_that_does_not_advance_ends_the_series(self):
    """An answer whose pull field is not a dict with a number wait, or whose snapshot is not a dict, and a page
    that says more follows without a cursor past the one asked after, count pull_refused and end the series
    with that oracle; nothing of them is stored."""
    # A record a page could carry.
    held = {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}
    # Answers of another form: the whole pull field, or the snapshot.
    malformed = [dict(pull=value) for value in (5, "x", ["a"], {"after": None, "more": False}, {"more": False, "wait": "8"},
                                                {"more": False, "wait": True}, {"more": False, "wait": None})]
    # A snapshot that is not a dict.
    malformed += [dict(snapshot=value) for value in ([1], "x", 5)]
    # Pages that say more follows with no cursor, a malformed one, or one not past the cursor asked after (none yet).
    stuck = [dict(more=True, after=value) for value in (None, [1], "x", [NOW_MS, 5], [0, ""])]
    # Each of them, as the first answer.
    for fields in malformed + stuck:
      # One sub-test each.
      with self.subTest(fields=fields):
        # An oracle with one mark, in 'on'.
        store = asking("on", owed={"w1": NOW_MS - 600_000})
        # The request.
        lap(store, 0)
        # The answer.
        reply(store, "peer1", **{"snapshot": held, **fields})
        # Counted once, the series ended, nothing stored, and the next lap asks the other oracle.
        self.assertEqual((pull_counts(store).get("pull_refused"), sorted(store.storage), [peers for peers, _ in lap(store, 1)]),
                         (1, [RESERVED], [["oracle"]]))
    # A second page whose cursor is the first page's, or before it.
    for cursor in ([NOW_MS - 5000, "a"], [NOW_MS - 5000, "Z"], [NOW_MS - 5001, "z"]):
      # One sub-test each.
      with self.subTest(cursor=cursor):
        # The asking oracle.
        store = asking("on", owed={"w1": NOW_MS - 600_000})
        # The first request and its page, with more to follow.
        lap(store, 0)
        # Taken.
        reply(store, "peer1", held, after=[NOW_MS - 5000, "a"], more=True)
        # The second request, after the first page's cursor.
        lap(store, 8)
        # A page that does not advance.
        reply(store, "peer1", after=cursor, more=True)
        # Refused; the series ended.
        self.assertEqual((pull_counts(store).get("pull_refused"), getattr(store, PULL)["peer"]), (1, None))

  def test_a_record_a_page_was_not_asked_for_is_dropped_and_the_others_are_merged(self):
    """Of a page, a record without a well-formed stamp, stamped below the floor, below its writer's mark less the
    margin, or below the cursor asked after, is dropped and counted pull_refused; the records at those bounds
    and above are merged."""
    # The asking oracle's mark for w1; w9 is a writer it never saw.
    mark = NOW_MS - 600_000
    # Records by key: stamp time and writer. Exactly at each bound is taken, one ms below is not.
    stamps = {"at-since": (mark - MARGIN, "w1"), "below-since": (mark - MARGIN - 1, "w1"), "at-floor": (FLOOR, "w9"),
              "below-floor": (FLOOR - 1, "w9"), "newer": (NOW_MS - 1000, "w1")}
    # The page's records, and four that are no stamped record at all.
    snapshot = {key: record(key, "v", writer, [ms, writer]) for key, (ms, writer) in stamps.items()}
    # No stamp, a stamp of another form, a record that is not a dict, and a stamp whose time is a float.
    snapshot.update({"unstamped": record("unstamped", "v", "w1"), "bad-stamp": {**record("bad-stamp", "v", "w1"), "ver": "x"},
                     "no-record": 5, "float-stamp": {**record("float-stamp", "v", "w1"), "ver": [1.5, "w1"]}})
    # An oracle in 'on' with that mark.
    store = asking("on", owed={"w1": mark})
    # The request and its page.
    lap(store, 0)
    # The page.
    reply(store, "peer1", snapshot)
    # The three records inside the bounds are stored; the six others were dropped, each counted.
    self.assertEqual((sorted(store.storage), pull_counts(store)["pull_refused"]),
                     (sorted(["at-since", "at-floor", "newer", RESERVED]), 6))
    # A second page asked after a cursor: a record stamped before the cursor's time is dropped, one at it is taken.
    store = asking("on", owed={"w1": mark})
    # The first request and a page with more to follow after a cursor at 5 s before now.
    lap(store, 0)
    # Taken.
    reply(store, "peer1", {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}, after=[NOW_MS - 5000, "a"], more=True)
    # The second request.
    lap(store, 8)
    # Its page: one record one ms before the cursor's time, one at it.
    reply(store, "peer1", {"early": record("early", "v", "w1", [NOW_MS - 5001, "w1"]),
                           "b": record("b", "v", "w1", [NOW_MS - 5000, "w1"])})
    # Only the one at the cursor's time is stored.
    self.assertEqual((sorted(store.storage), pull_counts(store)["pull_refused"]), (sorted(["a", "b", RESERVED]), 1))

  def test_a_page_stores_no_protected_record_no_token_and_nothing_over_what_a_live_write_could_not_replace(self):
    """A forged page from the oracle asked that carries a secret payload without its revocation marker, a record
    of each protected namespace, a "dl:" key, the reserved key and a record with a token stores none of them
    (each counted pull_refused); a copy of a record held with a token, or readonly for another owner, is skipped
    and counted held; the ordinary records of the same page are merged."""
    # The stamp of every copy.
    stamp = [NOW_MS - 1000, "w1"]
    # A record of each protected namespace; the secret revisions' one is a payload with no marker.
    forged = {_hset_key(namespace, "7|1"): record(_hset_key(namespace, "7|1"), {"job_secrets": "s"}, "w1", stamp)
              for namespace in PROTECTED}
    # A key of the tombstones' prefix, the reserved key, and a record that carries a token.
    forged.update({"dl:gone": record("dl:gone", "v", "w1", stamp), RESERVED: record(RESERVED, {"floor": 0, "owed": None}, "w1", stamp),
                   "with-token": {**record("with-token", "v", "w1", stamp), "token": "t"}})
    # Copies of records held with a token, readonly for another owner, and readonly for the same owner.
    forged.update({"held-token": record("held-token", "new", "w1", stamp), "held-readonly": record("held-readonly", "new", "w1", stamp),
                   "own-readonly": record("own-readonly", "new", "w1", stamp)})
    # Two ordinary records: one of a namespace, one plain key.
    forged.update({KEY: record(KEY, "v", "w1", stamp), "plain": record("plain", "v", "w1", stamp)})
    # What the asking oracle holds: the three records a live write treats specially, stamped earlier.
    held = {"held-token": {**record("held-token", "old", "w1", [NOW_MS - 9000, "w1"]), "token": "t"},
            "held-readonly": {**record("held-readonly", "old", "other", [NOW_MS - 9000, "w1"]), "readonly": True},
            "own-readonly": {**record("own-readonly", "old", "w1", [NOW_MS - 9000, "w1"]), "readonly": True}}
    # Each mode: nothing protected is stored in either; 'report' stores nothing at all.
    for mode in ("report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # The asking oracle.
        store = asking(mode, held, owed={"w1": NOW_MS - 600_000})
        # The request, and the forged page from the oracle asked.
        lap(store, 0)
        # The page.
        reply(store, "peer1", forged)
        # In 'on' the two ordinary records and the copy of the own readonly record are stored; nothing else changed.
        changed = {KEY: "v", "plain": "v", "own-readonly": "new"} if mode == "on" else {}
        # The values held now.
        self.assertEqual({key: stored["value"] for key, stored in store.storage.items() if key != RESERVED},
                         {"held-token": "old", "held-readonly": "old", "own-readonly": "old", **changed})
        # The store's own record is as its start left it.
        self.assertEqual(store.storage[RESERVED], reserved(0, {"w1": NOW_MS - 600_000}))
        # Nine records refused (six protected, the "dl:" key, the reserved key, the token), two skipped as held.
        self.assertEqual((pull_counts(store)["pull_refused"], pull_counts(store)["plain/pull1/held"]), (9, 2))

  def test_a_page_judged_in_report_counts_what_on_applies_and_changes_nothing(self):
    """The same page of value copies gives the same counts with CSTORE_HANDOFF 'report' as with 'on' (nothing
    held, an older stamp, no stamp, the same record, a newer stamp held, a future stamp, an equal stamp with
    another value, a lower cs_rev, a value nested too deep, a record over the size cap); 'report' leaves the
    replica, its dirty mark and its stamps as they were, also beside CSTORE_ORDER 'on', and 'on' stores what the
    rules admit; the merge of a page reads no registry."""
    # The stamp of most copies, and an older and a newer one.
    stamp, older, newer = [NOW_MS - 5000, "w1"], [NOW_MS - 9000, "w1"], [NOW_MS - 1000, "w1"]
    # What the asking oracle holds, by case.
    held = {
      # Held with an older stamp, with no stamp, and as the copy itself.
      "older-held": record("older-held", "old", "w1", older), "legacy-held": record("legacy-held", "old", "w1"),
      "same": record("same", "v", "w1", stamp),
      # The same four fields under an older stamp, and a record held with a newer stamp.
      "same-older-stamp": record("same-older-stamp", "v", "w1", older), "newer-held": record("newer-held", "kept", "w1", newer),
      # An equal stamp with another value, and a value that carries a higher cs_rev.
      "equal-stamp": record("equal-stamp", "a", "w1", stamp), "revision": record("revision", {"cs_rev": 5}, "w1", older),
      # The copy's own value under a newer stamp: the copy is older, although it changes nothing.
      "same-newer-stamp": record("same-newer-stamp", "v", "w1", newer),
    }
    # The page: one copy per case, and three for keys not held.
    page_records = {key: record(key, "v", "w1", stamp) for key in ("missing", "older-held", "legacy-held", "same",
                                                                    "same-older-stamp", "newer-held", "same-newer-stamp")}
    # A copy stamped 61 s ahead, the equal stamp's other value, a lower cs_rev under a newer stamp, a value too deep.
    page_records.update({"future": record("future", "v", "w1", [NOW_MS + 61_000, "w1"]), "equal-stamp": record("equal-stamp", "b", "w1", stamp),
                         "revision": record("revision", {"cs_rev": 3}, "w1", newer), "deep": record("deep", nested(65), "w1", stamp)})
    # And a record larger than the size cap of one record.
    page_records["large"] = record("large", "x" * (_ChainStoreRuntimeHarness.CS_MAX_RECORD_BYTES + 1), "w1", stamp)
    # The counts and the replica of each pair of switches.
    counts, replicas = {}, {}
    # CSTORE_HANDOFF and CSTORE_ORDER: both 'report', the first 'report' beside the order rule 'on', and both 'on'.
    for mode, order in (("report", "report"), ("report", "on"), ("on", "on")):
      # The asking oracle.
      store = asking(mode, held, owed={"w1": NOW_MS - 600_000})
      # The order switch, as on_init would read it.
      store._ChainStoreBasePlugin__cs_modes["order"] = order
      # The replica before the page.
      before = copy.deepcopy(store.storage)
      # The request (the registries are read for it: who may be asked).
      lap(store, 0)
      # How often a registry is read from here on.
      reads = []
      # Both registries, counted.
      store.bc.get_eth_oracles, store.bc.get_eth_dauth_oracles = (lambda: reads.append(1) or ["oracle"]), (
        lambda: reads.append(1) or ["node", "peer1"])
      # The page.
      reply(store, "peer1", page_records)
      # Taking and merging it read no registry.
      self.assertEqual(reads, [])
      # What was counted, and what is held now.
      counts[mode, order], replicas[mode, order] = pull_counts(store), {
        key: stored.get("value") for key, stored in store.storage.items()}
      if mode == "report":
        # Judged only, whatever the order switch: the replica is as it was, with no mark for a save of either kind.
        self.assertEqual((store.storage, getattr(store, DIRTY), getattr(store, UNMARKED)), (before, False, False))
    # 'report' counted what 'on' counted, beside either mode of the order switch.
    self.assertEqual((counts["report", "report"], counts["report", "on"]), (counts["on", "on"], counts["on", "on"]))
    # One count per case, under the first series: three repairs, two copies sent for nothing, and the rules' words
    # (the two copies older than what is held count as older only, also the one of the same value).
    self.assertEqual(counts["on", "on"], {
      "pull1_cut_s": 0, "plain/pull1/missing": 1, "plain/pull1/newer": 2, "plain/pull1/over_legacy": 1,
      "plain/pull1/same": 2, "plain/pull1/ver_older": 2, "plain/pull1/ver_future": 1, "plain/pull1/refused_too_deep": 1,
      "plain/pull1/refused_too_large": 1})
    # 'on' stored the repairs and kept the rest: the newer record held, the higher cs_rev, no future or deep copy.
    self.assertEqual({key: value for key, value in replicas["on", "on"].items() if key != RESERVED}, {
      "missing": "v", "older-held": "v", "legacy-held": "v", "same": "v", "same-older-stamp": "v", "newer-held": "kept",
      "equal-stamp": "b", "revision": {"cs_rev": 5}, "same-newer-stamp": "v"})

  def test_on_merges_a_page_of_any_namespace_applies_its_tombstones_and_marks_the_replica(self):
    """A page names no namespace: in 'on' its records of two namespaces and a plain key are stored, a tombstone
    removes the value held and is kept, and the replica is marked for a save; in 'report' the same page changes
    nothing and counts the delete that would be applied."""
    # The stamp of the page's copies.
    stamp = [NOW_MS - 1000, "w1"]
    # A second namespace's key.
    other = _hset_key("routes", "r1")
    # The page: a record of each namespace, a plain key, the tombstone of a held key and of a key not held, and a
    # key written again after its delete.
    snapshot = {KEY: record(KEY, "v", "w1", stamp), other: record(other, {"port": 1}, "w1", stamp),
                "plain": record("plain", "v", "w1", stamp), "deleted": tombstone("deleted", "w1", stamp),
                "never-held": tombstone("never-held", "w1", stamp), "written-again": record("written-again", "v", "w1", stamp)}
    # The asking oracle holds the key the first tombstone deletes, and the tombstone of the key written again, both
    # stamped earlier.
    held = {"deleted": record("deleted", "v", "w1", [NOW_MS - 9000, "w1"]),
            "dl:written-again": tombstone("written-again", "w1", [NOW_MS - 9000, "w1"])}
    # 'on': applied.
    store = asking("on", held, owed={"w1": NOW_MS - 600_000})
    # The request and the page.
    lap(store, 0)
    # The page.
    reply(store, "peer1", snapshot)
    # The four records and the two tombstones are held, the deleted value and the older tombstone are gone, and the
    # replica is marked.
    self.assertEqual((sorted(store.storage), getattr(store, DIRTY)),
                     (sorted([KEY, other, "plain", "written-again", "dl:deleted", "dl:never-held", RESERVED]), True))
    # Counted by what each changed: three records and a tombstone for a key not held, a delete over a held value and
    # a value over a held tombstone.
    self.assertEqual(pull_counts(store), {
      "pull1_cut_s": 0, BUCKET + "/pull1/missing": 1, _hset_index("routes") + "/pull1/missing": 1, "plain/pull1/missing": 2,
      "plain/pull1/newer": 2, "plain/pull1/ver_copy_delete": 1, "plain/pull1/tombstone_new": 2})
    # 'report': judged only.
    store = asking("report", held, owed={"w1": NOW_MS - 600_000})
    # The request and the page.
    lap(store, 0)
    # The page.
    reply(store, "peer1", snapshot)
    # Nothing changed; the four records are counted, and of the deletes the one that would remove a held value.
    self.assertEqual((sorted(store.storage), getattr(store, DIRTY), pull_counts(store)), (
      sorted(["deleted", "dl:written-again", RESERVED]), False, {
        "pull1_cut_s": 0, BUCKET + "/pull1/missing": 1, _hset_index("routes") + "/pull1/missing": 1, "plain/pull1/missing": 1,
        "plain/pull1/newer": 1, "plain/pull1/ver_copy_delete": 1}))

  def test_a_stamped_copy_replaces_a_record_without_a_stamp_and_is_counted_over_legacy(self):
    """A record held without a stamp (written by the previous release, also after the copy's own stamp) is
    replaced in 'on' by a stamped copy of a page and counted over_legacy, in its own count."""
    # Held with no stamp; the page's copy was stamped an hour ago.
    held, snapshot = {KEY: record(KEY, "rewritten", "w2")}, {KEY: record(KEY, "stamped", "w1", [NOW_MS - 3_600_000, "w1"])}
    # The asking oracle, which never saw a stamp of w1.
    store = asking("on", held)
    # The request and the page.
    lap(store, 0)
    # The page.
    reply(store, "peer1", snapshot)
    # The stamped copy is held now, counted in its own count.
    self.assertEqual((store.storage[KEY]["value"], pull_counts(store)[BUCKET + "/pull1/over_legacy"]), ("stamped", 1))

  def test_a_re_send_and_a_page_of_one_write_converge_in_either_order(self):
    """With every switch 'on': a page then the writer's re-send of the same stamp confirms the re-send as the
    record held; the re-send then the page counts the page's copy same; and after a page with a newer write, an
    older re-send is refused with an answer."""
    # One write of w1, its stamp, and a newer write of the same key.
    stamp, newer = [NOW_MS - 5000, "w1"], [NOW_MS - 1000, "w1"]

    def resend(store, value, ver):
      """
      Deliver the writer's hand-off re-send of ``KEY`` to ``store`` and give the words of what it sent back.

      Parameters
      ----------
      store : _ChainStoreRuntimeHarness
        The receiving oracle.
      value : any
        The value re-sent.
      ver : list
        The write's stamp.

      Returns
      -------
      list of str
        ``"confirm"`` for a confirmation, the refusal word for a refusal answer.
      """
      # Only what this store makes the oracle send.
      before = len(store.sent_payloads)
      # The store with the re-send mark, from its writer.
      store.default_handler({_PayloadData.EE_SENDER: "w1", _PayloadData.EE_ID: "w1", _PayloadData.EE_DESTINATION: ["node"],
                             _PayloadData.EE_IS_ENCRYPTED: True, "decoded": {store.CS_DATA: {
                               "op": "SSTORE", "key": KEY, "value": value, "owner": "w1", "ver": ver, "resend": True}}})
      # Confirmation or refusal word of each answer.
      return answers([sent["data"][store.CS_DATA] for sent in store.sent_payloads[before:]])

    # The page first, then the re-send: identical, confirmed.
    store = asking("on", owed={"w1": NOW_MS - 600_000})
    # The request and the page with the write.
    lap(store, 0)
    # The page.
    reply(store, "peer1", {KEY: record(KEY, "v", "w1", stamp)})
    # The re-send of the same write.
    self.assertEqual((resend(store, "v", stamp), store.storage[KEY]["ver"]), (["confirm"], stamp))
    # The re-send first, then the page: the page's copy is the record held.
    store = asking("on", owed={"w1": NOW_MS - 600_000})
    # The re-send, applied and confirmed.
    self.assertEqual(resend(store, "v", stamp), ["confirm"])
    # The request and the page with the same write.
    lap(store, 0)
    # The page.
    reply(store, "peer1", {KEY: record(KEY, "v", "w1", stamp)})
    # Counted as sent for nothing.
    self.assertEqual(pull_counts(store)[BUCKET + "/pull1/same"], 1)
    # A page with the newer write first, then the re-send of the older one: refused as older, with an answer.
    store = asking("on", owed={"w1": NOW_MS - 600_000})
    # The request and the page.
    lap(store, 0)
    # The page with the newer write.
    reply(store, "peer1", {KEY: record(KEY, "v2", "w1", newer)})
    # The older re-send is refused, and the newer value stays.
    self.assertEqual((resend(store, "v", stamp), store.storage[KEY]["value"]), (["older"], "v2"))

  def test_what_is_owed_survives_a_crash_in_the_middle_of_a_series(self):
    """A started oracle merges the first page of two, saves, and is started again from that save: its marks are
    again those of the first start, not the higher ones of the merged records, so the second start asks from
    the same marks; when a catch-up completes, nothing is owed."""
    # The record the oracle held when it went away, ten minutes ago.
    file = {"old": record("old", "v", "w1", [NOW_MS - 600_000, "w1"])}
    # What was written meanwhile: two records of 150 KB (one page takes only one of them) and a small one.
    missed = {"m1": record("m1", "x" * 150_000, "w1", [NOW_MS - 500_000, "w1"]),
              "m2": record("m2", "x" * 150_000, "w1", [NOW_MS - 400_000, "w1"]),
              "m3": record("m3", "v", "w1", [NOW_MS - 300_000, "w1"])}
    # The marks of the first start, and what a request of it asks.
    marks = {"w1": NOW_MS - 600_000}
    # The network: the starting oracle and one that holds everything.
    network = _FakeNetwork()
    # The answering oracle.
    other = oracle("on", {**file, **missed}, "peer1", ("node",))
    # Reachable.
    network.register(other)

    def start(storage):
      """
      Start the oracle "node" through the real on_init from ``storage`` and put it on the network.

      Parameters
      ----------
      storage : dict
        The records of its replica file.

      Returns
      -------
      _StartHarness
        The started store, in 'on', with one series to run, the registry of the tests and the peer "peer1".
      """
      # The start, with one series per catch-up.
      store = started(self, "on", storage, number=1)
      # The registry of the tests, as the answering oracle has it, and distinct request ids.
      store.bc, ids = other.bc, itertools.count(1)
      # The ids.
      store.uuid = lambda *args, **kwargs: "r%d" % next(ids)
      # Its one replication peer, and no resync request in this test.
      setattr(store, PEERS, ["peer1"])
      # The resync not due.
      setattr(store, RESYNC_NEXT, NOW + 10 ** 9)
      # Reachable.
      network.register(store)
      # Started.
      return store

    # The first start: its marks are the file's.
    store = start(file)
    # As taken.
    self.assertEqual(getattr(store, OWED), marks)
    # The first lap: the first page is asked for, answered and merged; the series is not over.
    self.assertEqual(([pull["since"] for _, pull in lap(store, 0, other)], sorted(key for key in store.storage if key != RESERVED),
                      getattr(store, DIRTY), getattr(store, OWED)),
                     ([{"w1": NOW_MS - 600_000 - MARGIN}], ["m1", "old"], True, marks))
    # The save the merged page asks for.
    getattr(store, SAVE)()
    # The crash: a second start from what the save wrote, which holds the merged record and the marks.
    again = start(store.saved_storage[-1])
    # The marks are those of the first start, not the merged record's stamp.
    self.assertEqual((getattr(again, OWED), again.storage[RESERVED]["value"]["owed"]), (marks, marks))
    # It asks from the same marks, from the beginning, and gets the first page again (its record is held: same).
    self.assertEqual(lap(again, 10, other), [(["peer1"], {"since": {"w1": NOW_MS - 600_000 - MARGIN}, "floor": FLOOR + 10_000, "after": None})])
    # The second page 8 s later ends the one series: every missed record is held and nothing is owed.
    lap(again, 18, other)
    # Complete.
    self.assertEqual((sorted(key for key in again.storage if key != RESERVED), getattr(again, OWED), getattr(again, PULL)),
                     (["m1", "m2", "m3", "old"], None, None))

  def test_a_catch_up_not_finished_after_an_hour_stops_with_one_red_line_and_stays_owed(self):
    """When no oracle answers for 3600 s the catch-up stops asking for this run with one red line; its marks
    stay in the reserved record for the next start, and later laps send nothing."""
    # An oracle with one mark and one registry member as peer, which never answers.
    store = asking("report", owed={"w1": NOW_MS - 600_000}, peers=("peer1",))
    # Laps every 5 s for an hour: requests go, none is answered.
    sent = sum(len(lap(store, at)) for at in range(0, 3601, 5))
    # It asked again and again, a round every 120 s, and is still running at the hour.
    self.assertEqual((sent > 20, getattr(store, PULL) is not None), (True, True))
    # Just past the hour, and later: it has stopped and sends nothing.
    self.assertEqual(([lap(store, at) for at in (3605, 3610, 3800)], getattr(store, PULL)), ([[], [], []], None))
    # One red line; the marks are still owed, in memory and in the record.
    self.assertEqual((len([text for text in store.messages if "did not finish" in text]), getattr(store, OWED),
                      store.storage[RESERVED]["value"]["owed"]), (1, {"w1": NOW_MS - 600_000}, {"w1": NOW_MS - 600_000}))

  def test_the_floor_of_each_request_is_the_horizon_or_the_tombstone_life_and_what_it_cuts_is_counted(self):
    """Each request's floor is the larger of the tombstone horizon and now less the tombstone life plus 120 s,
    computed anew for each; the seconds by which it lies above the newest mark asked for are kept as the
    period's largest pull<n>_cut_s of the series; a replica with no marks cuts nothing, and neither does a writer
    quiet for 8 days beside a mark of 10 minutes ago."""
    # The horizon after a sweep dropped a tombstone stamped 30 minutes ago, and marks of 60 and 10 minutes ago.
    horizon, owed = NOW_MS - 1_800_000, {"w1": NOW_MS - 3_600_000, "w2": NOW_MS - 600_000}
    # No tombstone removed: the floor is the life's, and it cuts nothing of marks an hour old.
    store = asking("report", owed=owed)
    # The first request.
    self.assertEqual(([pull["floor"] for _, pull in lap(store, 0)], pull_counts(store)), ([FLOOR], {"pull1_cut_s": 0}))
    # With the horizon: the floor is the horizon, below the newest mark (w2's) less the margin: no cut, although it
    # lies 1,920 s above w1's.
    store = asking("report", owed=owed)
    # The horizon, as the sweep leaves it.
    setattr(store, HORIZON, horizon)
    # The first request.
    self.assertEqual(([pull["floor"] for _, pull in lap(store, 0)], pull_counts(store)), ([horizon], {"pull1_cut_s": 0}))
    # A page with more to follow; meanwhile a sweep raises the horizon to 10 minutes ago.
    reply(store, "peer1", {"a": record("a", "v", "w2", [NOW_MS - 5000, "w2"])}, after=[NOW_MS - 5000, "a"], more=True)
    # Raised.
    setattr(store, HORIZON, NOW_MS - 600_000)
    # The next request of the series carries the new floor, 120 s above w2's mark less the margin, and the larger cut
    # replaces the smaller.
    self.assertEqual(([pull["floor"] for _, pull in lap(store, 8)], pull_counts(store)["pull1_cut_s"]), ([NOW_MS - 600_000], 120))
    # The series ends; the next one, 120 s later, counts its cut under its own number.
    reply(store, "peer1")
    # The second series' first request: the horizon still governs, and the cut of series 1 stays as it was.
    self.assertEqual(([peers for peers, _ in lap(store, 128)], pull_counts(store)["pull2_cut_s"], pull_counts(store)["pull1_cut_s"]),
                     # 120 s each: the horizon's 10 minutes ago against w2's mark less the margin.
                     ([["oracle"]], 120, 120))
    # A tombstone older than the horizon held again (a sync brought it): the horizon, and the floor, stay.
    store = asking("on", {"dl:x": tombstone("x", "w1", [NOW_MS - 3_000_000, "w1"])}, owed=owed)
    # The horizon from an earlier sweep.
    setattr(store, HORIZON, horizon)
    # A sweep that removes nothing (the tombstone is within the life and the cap).
    getattr(store, SWEEP)()
    # The floor is still the horizon, not the oldest tombstone held.
    self.assertEqual([pull["floor"] for _, pull in lap(store, 0)], [horizon])
    # A replica with no marks: everything above the floor is asked for, and nothing counts as cut.
    store = asking("report")
    # The request.
    self.assertEqual((lap(store, 0), pull_counts(store)), ([(["peer1"], {"since": {}, "floor": FLOOR, "after": None})], {"pull1_cut_s": 0}))
    # A writer whose newest record is 8 days old, beside one of 10 minutes ago: its mark lies a day and 4 minutes
    # below the floor, but the newest mark does not, so this node was not away past the floor.
    store = asking("report", owed={"w1": NOW_MS - 8 * DAY_MS, "w2": NOW_MS - 600_000})
    # The request.
    lap(store, 0)
    # No cut (86,640 s when the cut was measured from the lowest mark).
    self.assertEqual(pull_counts(store), {"pull1_cut_s": 0})

  def test_the_cut_is_measured_from_the_newest_mark_so_a_quiet_writer_gives_none_and_a_long_absence_does(self):
    """pull1_cut_s is the seconds by which the floor lies above the newest mark less the margin, about when this
    node stopped receiving: a writer quiet for 8 days beside a live one gives no cut; this node away 7 days and an
    hour (its newest mark that old) gives 3,840 s whatever quiet writers it holds, and away 8 days 86,640 s."""
    # Each case: the marks, and the cut its first request counts.
    cases = (({"quiet": NOW_MS - 8 * DAY_MS, "live": NOW_MS - 600_000}, 0),
             # Away 7 days and an hour: an hour and twice the margin past the floor.
             ({"quiet": NOW_MS - 8 * DAY_MS, "live": NOW_MS - LIFE - 3_600_000}, 3_600 + 240),
             # Away 8 days, the only writer: a day and twice the margin.
             ({"quiet": NOW_MS - 8 * DAY_MS}, 86_400 + 240))
    # Each case.
    for owed, cut in cases:
      # One sub-test each.
      with self.subTest(owed=owed):
        # An oracle that owes these marks and removed no tombstone.
        store = asking("report", owed=owed)
        # The first request, with the floor of the tombstone life.
        self.assertEqual([pull["floor"] for _, pull in lap(store, 0)], [FLOOR])
        # The cut, from the newest mark.
        self.assertEqual(pull_counts(store), {"pull1_cut_s": cut})

  def test_a_key_whose_delete_this_node_forgot_does_not_come_back_by_a_catch_up(self):
    """A node that deleted a key and removed its tombstone at a sweep asks nothing stamped below that tombstone:
    an oracle that missed the delete and still holds the key does not send it, and a page that carries it anyway
    is not merged."""
    # The key's write, 50 minutes ago, and its delete, 33 minutes ago; both by w1.
    written, deleted = NOW_MS - 3_000_000, NOW_MS - 2_000_000
    # The oracle that missed the delete still holds the key, and a record written after the delete.
    stale = {"gone": record("gone", "v", "w1", [written, "w1"]), "later": record("later", "v", "w1", [NOW_MS - 1_000_000, "w1"])}
    # The network of the two.
    network = _FakeNetwork()
    # The oracle that missed the delete.
    other = oracle("on", stale, "peer1", ("node",))
    # Reachable.
    network.register(other)
    # The asking oracle holds neither the key nor its tombstone, and its mark for w1 is older than the key.
    store = asking("on", owed={"w1": NOW_MS - 3_600_000}, peers=("peer1",), network=network)
    # Its horizon: the delete's stamp, which the sweep that removed the tombstone left.
    setattr(store, HORIZON, deleted)
    # The catch-up's first lap: asked, answered and merged.
    lap(store, 0, other)
    # The later record came; the deleted key did not.
    self.assertEqual(sorted(key for key in store.storage if key != RESERVED), ["later"])
    # An oracle that ignores the floor and sends the key anyway: its copy is dropped and counted.
    store = asking("on", owed={"w1": NOW_MS - 3_600_000})
    # The same horizon.
    setattr(store, HORIZON, deleted)
    # The request, and a page with the deleted key.
    lap(store, 0)
    # The page.
    reply(store, "peer1", {"gone": stale["gone"]})
    # Not stored.
    self.assertEqual((sorted(store.storage), pull_counts(store)["pull_refused"]), ([RESERVED], 1))

  def test_each_writers_stamps_are_compared_with_its_own_mark_whatever_the_clocks(self):
    """Writers stamp with their own clocks: one 11 minutes behind the others, one never seen before. A catch-up
    brings the records each wrote during the outage, as each is asked for from its own mark; it does not bring
    a record its writer stamped more than 120 s below its own mark, after a wall clock set back by 10 minutes."""
    # The outage: from 300 s ago until now. The writers' clocks: on time, 660 s behind, and on time until it is set back.
    behind = 660

    def write(store, key, at):
      """
      Make a writer write ``key`` at ``at`` seconds on its own clock and give the store message it sends.

      Parameters
      ----------
      store : _ChainStoreRuntimeHarness
        The writer's store, with every switch 'on'.
      key : str
        The key written.
      at : float
        The writer's clock at the write, in seconds after ``NOW``.

      Returns
      -------
      tuple
        ``(key, value, stamp)`` of the write, the stamp as the writer's clock made it.
      """
      # The writer's own clock.
      store._now = NOW + at
      # A local write that does not wait for confirmations.
      store._set_value(key, key, owner=store.ee_addr, wait=False)
      # The store message it queued carries the stamp its clock made.
      queued = getattr(store, OPS).popleft()
      # What its peers receive.
      return key, queued["value"], queued["ver"]

    # The three writers, each with the answering oracle as its peer.
    fast, slow, jumpy, fresh = (make("on", address=name, peers=("peer1",)) for name in ("fast", "slow", "jumpy", "fresh"))
    # Before the outage (wall clock 300 s ago): each writes one record, the slow one on its late clock.
    before = [write(fast, "f-old", -300), write(slow, "s-old", -300 - behind), write(jumpy, "j-old", -300)]
    # During the outage (wall clock 200 s ago): each writes again; the jumpy one's clock was set back by 600 s, and a
    # writer the starting oracle never saw writes its first record.
    during = [write(fast, "f-new", -200), write(slow, "s-new", -200 - behind), write(jumpy, "j-new", -200 - 600),
              write(fresh, "n-new", -200)]
    # The slow writer's new stamp lies 11 minutes behind the fast one's; the jumpy one's lies below its own old one.
    self.assertEqual((during[1][2][0] - during[0][2][0], during[2][2][0] < before[2][2][0] - MARGIN), (-behind * 1000, True))
    # The network: the starting oracle and one that was up all along.
    network = _FakeNetwork()
    # The oracle that was up, on the wall clock.
    other = oracle("on", None, "peer1", ("node",))
    # Reachable.
    network.register(other)
    # It received every write, each from its writer.
    for key, value, ver in before + during:
      # A live store, stamped by its sender.
      deliver(other, key, value, ver[1], ver)
    # All seven are held there.
    self.assertEqual(len(other.storage), 7)
    # The starting oracle holds the three records from before the outage; its marks are their stamps, one per writer.
    held = {key: record(key, value, ver[1], ver) for key, value, ver in before}
    # Its catch-up, in 'on'.
    store = asking("on", held, owed={ver[1]: ver[0] for _, _, ver in before}, peers=("peer1",), network=network)
    # The first lap, at the wall clock: asked, answered and merged.
    (_, request), = lap(store, 0, other)
    # One mark per writer, each its own stamp less the margin; the writer never seen has none.
    self.assertEqual(request["since"], {ver[1]: ver[0] - MARGIN for _, _, ver in before})
    # The records of the on-time writer, of the one 11 minutes behind and of the one never seen came; the record
    # stamped 10 minutes below its writer's own mark did not.
    self.assertEqual(sorted(key for key in store.storage if key != RESERVED), ["f-new", "f-old", "j-old", "n-new", "s-new", "s-old"])

  def test_an_oracle_with_the_switch_off_does_not_answer_and_the_asker_moves_on(self):
    """An oracle whose CSTORE_HANDOFF is 'off' sends nothing for a catch-up request and counts nothing; the asker
    counts the missing answer after 10 s and its series with the next oracle runs."""
    # The record the asking oracle lacks.
    held = {"a": record("a", "v", "w1", [NOW_MS - 5000, "w1"])}
    # The network of the three.
    network = _FakeNetwork()
    # One oracle with every switch 'off', one in 'report'.
    silent, other = oracle("off", held, "peer1", ("node",)), oracle("report", held, "oracle", ("node",))
    # Both reachable.
    for each in (silent, other):
      # On the network.
      network.register(each)
    # The asking oracle, with one series to run.
    store = asking("report", owed={"w1": NOW_MS - 600_000}, network=network)
    # One series.
    setattr(store, NUMBER, 1)
    # The first request goes to the silent oracle: no answer, and nothing happens there.
    self.assertEqual(([peers for peers, _ in lap(store, 0, silent, other)], silent.sent_payloads, getattr(silent, STATS)),
                     ([["peer1"]], [], {}))
    # After its 10 s the request is forgotten, then counted, and the other oracle is asked and answers.
    self.assertEqual((lap(store, 10.1, silent, other), [peers for peers, _ in lap(store, 10.2, silent, other)]), ([], [["oracle"]]))
    # The one series ran: nothing is owed.
    self.assertEqual((pull_counts(store)["pull_no_answer"], getattr(store, PULL), getattr(store, OWED)), (1, None, None))


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
