"""
Offline tests of the chain store's catch-up after a start: what the replica keeps for it under the reserved key
(the tombstone horizon and the marks of a start), and the pages an oracle answers a catch-up request with.
"""
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
# The harness, the payload field names of the test network, and the composers of a namespace's index and of a hash
# key.
from test_chain_store_hsync import _ChainStoreRuntimeHarness, _PayloadData, _hset_index, _hset_key
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
# The broker's packet limit, which no message may reach: 1 MiB.
PACKET = 1_048_576
# The six namespaces no page carries.
PROTECTED = _ChainStoreRuntimeHarness.CS_PROTECTED_NAMESPACES


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
    by stamp time and then stored key; a writer the request does not name is sent from the floor; the last page
    says no more follows and each page counts pull_answered."""
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
        # Each record travels with the five fields a merge reads, as a namespace-sync answer carries them.
        (_, body), = ask(store, {"since": {"w1": base + 300, "w2": NOW_MS, "w3": NOW_MS}, "floor": 0, "after": None})
        # The one record of that page.
        self.assertEqual(body["snapshot"], {"w1@300": {"key": "w1@300", "value": "w1@300", "owner": "w1",
                                                        "readonly": False, "ver": [base + 300, "w1"]}})
        # Five pages were sent.
        self.assertEqual(getattr(store, STATS), {"pull_answered": 5})

  def test_no_page_carries_a_record_of_a_protected_namespace_a_token_or_an_unstamped_record(self):
    """A node that holds, in each of the six protected namespaces, a stamped record and a tombstone (in the secret
    revisions a payload, its revocation marker and its redacted copy), a token record, an unstamped record and
    its own reserved record sends none of them: a page holds only the ordinary stamped records."""
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
    # The answering oracle.
    store, after, sizes = oracle("report", held), None, []
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
    # 11, 11, 11 and 7 records, each page within the bound.
    self.assertEqual(([count for count, _ in sizes], all(size <= store.CS_PULL_PAGE_BYTES for _, size in sizes)),
                     ([11, 11, 11, 7], True))
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


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
