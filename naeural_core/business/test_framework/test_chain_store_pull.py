"""
Offline tests of the chain store's catch-up after a start: what the replica keeps for it under the reserved key
(the tombstone horizon and the marks of a start).
"""
# The data folder of a started store.
import os
# The replica file of a started store.
import pickle
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

# The counter attribute's private name and the harness that runs the real on_init.
from test_chain_store_accounting import STATS, _StartHarness
# The harness, and the composers of a namespace's index and of a hash key.
from test_chain_store_hsync import _ChainStoreRuntimeHarness, _hset_index, _hset_key
# The order tests' store builder, deliverer of a live store, record and tombstone builders, constants and names.
from test_chain_store_order import DAY_MS, DIRTY, EXPORT, KEY, NOW, NOW_MS, deliver, make, record, tombstone

# The reserved key the store keeps its own record under.
RESERVED = "hs:cstore_pull"
# The marks a catch-up still owes, the tombstone horizon and the tombstone cap, by their private names.
OWED, HORIZON, CAP = ("_ChainStoreBasePlugin__pull_owed", "_ChainStoreBasePlugin__tomb_horizon",
                      "_ChainStoreBasePlugin__tomb_cap")
# The tombstone sweep, the save and the writer of the reserved record, by their private names.
SWEEP, SAVE, KEEP = ("_ChainStoreBasePlugin__sweep_tombstones", "_ChainStoreBasePlugin__save_chain_storage",
                     "_ChainStoreBasePlugin__pull_keep")
# The bound of a stamp that is not a future one, at the tests' wall clock: 60 s ahead of it.
LIMIT = NOW_MS + 60_000


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
    as both; a well-formed record owned by another address is kept as it is, with no line."""
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
      # A well-formed record written under another address (the node's key was replaced since): kept, no line.
      (record(RESERVED, {"floor": 7, "owed": {"w1": NOW_MS - 5000}}, "old-address"), 7, {"w1": NOW_MS - 5000}, 0),
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
    as the file gave it: no record is created, and a saved one stays as it is, its horizon and marks read; the
    number is read once, as an int."""
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
    # The number as a numeric str is taken as an int: marks are kept.
    self.assertEqual(getattr(started(self, "report", held, "3"), "_ChainStoreBasePlugin__pull_peers"), 3)
    # A word stops the start on the conversion.
    with self.assertRaisesRegex(ValueError, "invalid literal for int"):
      # The real on_init.
      started(self, "report", held, "two")


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
