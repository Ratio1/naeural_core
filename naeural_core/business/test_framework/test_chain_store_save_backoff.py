"""
Offline tests of the chain store's save back-off (switch CSTORE_SAVE_BACKOFF): after a save of d seconds the next
save waits until min(CSTORE_SAVE_MAX_GAP, 10 x d) after that save started.

The store's clocks (time.monotonic and time.thread_time, as the chain-store module imports them) are replaced by a
fake clock, and each save advances it by the save's duration, so the schedule is exact.
"""
# A folder for the store started through on_init.
import os
# Its temporary place.
import tempfile
# The test framework.
import unittest
# The stand-in for the SerializationManager.
from types import SimpleNamespace
# Replaces the module's clocks for one test.
from unittest import mock

# The manager the store registers with and unregisters from.
from naeural_core.main.serialization_manager import SerializationManager

# The store loaded from its source, and the harness that gives it the state on_init would.
from test_chain_store_hsync import ChainStoreBasePlugin, _ChainStoreRuntimeHarness
# The private names of the dirty flag and of the counters, and the harness that runs the real on_init.
from test_chain_store_accounting import DIRTY, STATS, _StartHarness

# The globals of the chain-store module as the tests load it: monotonic and thread_time are looked up here.
MODULE = ChainStoreBasePlugin.on_init.__globals__


class _Clock:
  """
  A fake monotonic clock and a fake thread-CPU clock, each counting how often it was read.

  Examples
  --------
  >>> clock = _Clock()
  >>> clock.now = 20.0
  >>> clock.monotonic(), clock.reads
  (20.0, 1)
  """

  def __init__(self):
    """
    Start both clocks at 0 with no read counted.

    Returns
    -------
    None
    """
    # Wall time and the save thread's CPU time, in seconds, and how many reads the store made.
    self.now, self.cpu, self.reads = 0.0, 0.0, 0

  def monotonic(self):
    """
    Read the fake monotonic clock.

    Returns
    -------
    float
      The current fake time in seconds.
    """
    # One more read.
    self.reads += 1
    # The time the test set, advanced by the saves.
    return self.now

  def thread_time(self):
    """
    Read the fake CPU clock of the save thread.

    Returns
    -------
    float
      The fake CPU seconds spent so far.
    """
    # One more read.
    self.reads += 1
    # The CPU time the saves added.
    return self.cpu


def timed_saves(store, clock, save_s, cpu_s=0.0, failures=0):
  """
  Make every save of ``store`` take ``save_s`` seconds on ``clock`` and record when it starts.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store whose replica write is replaced.
  clock : _Clock
    The fake clock the module reads; each save advances it.
  save_s : float
    The wall time of each save, d.
  cpu_s : float, optional
    The CPU time of each save on the save thread; 0 by default.
  failures : int, optional
    How many of the first saves raise OSError after taking their time; none by default.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The same store, with ``save_starts``: the fake time at which each save began writing.

  Examples
  --------
  >>> clock = _Clock()
  >>> store = timed_saves(_ChainStoreRuntimeHarness("node"), clock, 1.93)
  >>> flush_at(store, clock, 0.0)
  >>> store.save_starts, clock.now
  ([0.0], 1.93)
  """
  # When each save began.
  store.save_starts = []

  def save(snapshot, verbose=True, strict=False):  # pylint: disable=unused-argument
    """
    Stand for the strict replica write: take ``save_s`` of wall time and ``cpu_s`` of CPU time, maybe fail.

    Parameters
    ----------
    snapshot : dict
      The copy of the records being written.
    verbose : bool
      Unused, as in the cache API.
    strict : bool
      Unused, as in the cache API.

    Returns
    -------
    None

    Raises
    ------
    OSError
      For each of the first ``failures`` saves.
    """
    # The start of this save.
    store.save_starts.append(clock.now)
    # Its duration on both clocks.
    clock.now, clock.cpu = clock.now + save_s, clock.cpu + cpu_s
    if len(store.save_starts) <= failures:
      # A full disk, as the strict save would report it.
      raise OSError("no space left on device")

  # Every save of this store goes through the stand-in.
  store.cacheapi_save_pickle = save
  # The store, ready to flush.
  return store


def make_store(mode, clock, save_s, cpu_s=0.0, failures=0):
  """
  Build the chain store of "node" with the save back-off in ``mode`` and saves timed by ``timed_saves``.

  Parameters
  ----------
  mode : str
    The save back-off's mode: ``'off'``, ``'report'`` or ``'on'``.
  clock : _Clock
    The fake clock the module reads; each save advances it.
  save_s : float
    The wall time of each save, d.
  cpu_s : float, optional
    The CPU time of each save on the save thread; 0 by default.
  failures : int, optional
    How many of the first saves raise OSError; none by default.

  Returns
  -------
  _ChainStoreRuntimeHarness
    The store, with the 30 s ceiling, no pause yet and ``save_starts``.

  Examples
  --------
  >>> store = make_store("on", _Clock(), 1.93)
  """
  # A store of "node" with the 30 s ceiling and no pause yet.
  store = _ChainStoreRuntimeHarness("node")
  # The back-off's mode, as on_init would read it.
  store._ChainStoreBasePlugin__cs_modes["save_backoff"] = mode
  # Its saves take save_s each.
  return timed_saves(store, clock, save_s, cpu_s=cpu_s, failures=failures)


def flush_at(store, clock, at, changed=True):
  """
  Run one call of the store's periodic flush at fake time ``at``, as the SerializationManager thread does.

  Parameters
  ----------
  store : _ChainStoreRuntimeHarness
    The store whose flush runs.
  clock : _Clock
    The fake clock, set to ``at`` first.
  at : float
    The fake time of the call.
  changed : bool, optional
    True (the default) to mark the store changed first, as an accepted write does.

  Returns
  -------
  None

  Examples
  --------
  >>> flush_at(store, clock, 5.0)   # a write was accepted, the manager calls the flush at t = 5 s
  """
  # The time of the call.
  clock.now = at
  if changed:
    # A write accepted since the last save.
    setattr(store, DIRTY, True)
  # The flush the manager calls every CS_SAVE_INTERVAL seconds.
  store._ChainStoreBasePlugin__maybe_flush_chain_storage()


class TestSaveBackoff(unittest.TestCase):
  """The save back-off defers saves in 'on', only counts them in 'report' and saves as the previous release in
  'off'."""

  def setUp(self):
    """Replace the module's clocks by a fake clock for the test."""
    # The fake clock of this test.
    self.clock = _Clock()
    # The module reads it as monotonic and thread_time.
    patcher = mock.patch.dict(MODULE, {"monotonic": self.clock.monotonic, "thread_time": self.clock.thread_time})
    # In force from here.
    patcher.start()
    # The real clocks again after the test.
    self.addCleanup(patcher.stop)

  def test_on_defers_the_saves_inside_the_pause_and_saves_when_it_ends(self):
    """'on', a 1.93 s save: the calls at 5, 10 and 15 s are deferred and the call at 20 s saves."""
    # Saves of 1.93 s, as on a 0.35-core oracle under load.
    store = make_store("on", self.clock, 1.93)
    # One call every 5 s, each after a write.
    for at in (0.0, 5.0, 10.0, 15.0, 20.0):
      # One call.
      flush_at(store, self.clock, at)
    # Saved at 0 s and at 20 s, the first call past 19.3 s; the three calls between were deferred and counted.
    self.assertEqual((store.save_starts, getattr(store, STATS)["saves_deferred"], getattr(store, STATS)["saves"]),
                     ([0.0, 20.0], 3, 2))
    # Nothing left unsaved after the last call.
    self.assertFalse(getattr(store, DIRTY))

  def test_on_keeps_the_5_s_cadence_after_a_fast_save(self):
    """'on', a 0.26 s save (an idle healthy oracle): no pause, the next call 5 s later saves."""
    # Saves of 0.26 s.
    store = make_store("on", self.clock, 0.26)
    # Two calls 5 s apart.
    for at in (0.0, 5.0):
      # One call.
      flush_at(store, self.clock, at)
    # Both saved, nothing deferred.
    self.assertEqual((store.save_starts, "saves_deferred" in getattr(store, STATS)), ([0.0, 5.0], False))

  def test_on_caps_the_pause_at_the_max_gap(self):
    """'on', a 10 s save: the pause is the 30 s ceiling, not 100 s; the calls at 11 to 26 s wait, 31 s saves."""
    # Saves of 10 s.
    store = make_store("on", self.clock, 10.0)
    # The manager calls again 1 s after the long save returned, then every 5 s.
    for at in (0.0, 11.0, 16.0, 21.0, 26.0, 31.0):
      # One call.
      flush_at(store, self.clock, at)
    # Saved at 0 s and at 31 s, the first call past 30 s.
    self.assertEqual((store.save_starts, getattr(store, STATS)["saves_deferred"]), ([0.0, 31.0], 4))

  def test_report_saves_as_the_previous_release_and_counts_what_on_would_defer(self):
    """'report', a 1.93 s save: every call saves; the count follows the 'on' schedule, which no save restarts."""
    # Saves of 1.93 s.
    store = make_store("report", self.clock, 1.93)
    # One call every 5 s, each after a write.
    for at in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0):
      # One call.
      flush_at(store, self.clock, at)
    # Every call saved. The calls at 5, 10 and 15 s are inside the pause of the 0 s save; the one at 20 s is past
    # it (the saves at 5-15 s did not restart it) and starts the next pause, so the call at 25 s is inside it again.
    self.assertEqual((store.save_starts, getattr(store, STATS)["saves_deferred"]),
                     ([0.0, 5.0, 10.0, 15.0, 20.0, 25.0], 4))

  def test_off_saves_at_every_call_and_reads_no_clock(self):
    """'off' saves as the previous release: a 10 s save sets no pause, nothing new is counted and no clock is
    read."""
    # Saves of 10 s.
    store = make_store("off", self.clock, 10.0)
    # Three calls.
    for at in (0.0, 11.0, 16.0):
      # One call.
      flush_at(store, self.clock, at)
    # Every call saved; only the count the previous release made; the store never read the clock.
    self.assertEqual((store.save_starts, getattr(store, STATS), self.clock.reads), ([0.0, 11.0, 16.0], {"saves": 3}, 0))

  def test_a_call_on_an_unchanged_store_counts_nothing_and_the_pause_is_measured_in_time(self):
    """'on': calls that find nothing changed count nothing; a write after them is still held to the pause."""
    # Saves of 1.93 s.
    store = make_store("on", self.clock, 1.93)
    # A save at 0 s.
    flush_at(store, self.clock, 0.0)
    # Two calls with nothing changed.
    for at in (5.0, 10.0):
      # One call, no write before it.
      flush_at(store, self.clock, at, changed=False)
    # A write before the call at 15 s, still inside the pause of the 0 s save, then the call at 20 s.
    for at in (15.0, 20.0):
      # One call.
      flush_at(store, self.clock, at)
    # One deferral only (15 s), and the save at 20 s.
    self.assertEqual((store.save_starts, getattr(store, STATS)["saves_deferred"]), ([0.0, 20.0], 1))

  def test_a_failed_save_stays_changed_and_is_retried_after_the_pause(self):
    """'on': a 1.93 s save that fails sets the pause too; the store stays changed and the call at 20 s retries."""
    # The first save fails after 1.93 s.
    store = make_store("on", self.clock, 1.93, failures=1)
    # The failed save at 0 s.
    flush_at(store, self.clock, 0.0)
    # Still changed, so the next flush retries it.
    self.assertTrue(getattr(store, DIRTY))
    # Calls without a new write: the retry waits for the pause.
    for at in (5.0, 10.0, 15.0, 20.0):
      # One call.
      flush_at(store, self.clock, at, changed=False)
    # Retried at 20 s and saved.
    self.assertEqual((store.save_starts, getattr(store, STATS)["saves_deferred"], getattr(store, DIRTY)),
                     ([0.0, 20.0], 3, False))

  def test_an_orderly_stop_saves_during_the_pause(self):
    """'on': on_close saves a changed store at once, also inside the pause."""
    # Saves of 1.93 s.
    store = make_store("on", self.clock, 1.93)
    # A save at 0 s starts a pause until 19.3 s.
    flush_at(store, self.clock, 0.0)
    # A write at 3 s.
    setattr(store, DIRTY, True)
    # The stop at 3 s.
    self.clock.now = 3.0
    # A manager that only accepts the unregistration on_close makes.
    with mock.patch.object(SerializationManager, "instance", SimpleNamespace(unregister=lambda save: None)):
      # The orderly stop.
      store.on_close()
    # Saved at the stop, inside the pause.
    self.assertEqual((store.save_starts, getattr(store, DIRTY)), ([0.0, 3.0], False))

  def test_save_cpu_ms_adds_the_cpu_time_of_each_save(self):
    """'report': each save adds its CPU time on the save thread, in ms, to save_cpu_ms."""
    # Saves of 0.26 s that take 0.18 s of CPU.
    store = make_store("report", self.clock, 0.26, cpu_s=0.18)
    # Two saves.
    for at in (0.0, 5.0):
      # One call.
      flush_at(store, self.clock, at)
    # 180 ms each.
    self.assertEqual(getattr(store, STATS)["save_cpu_ms"], 360)

  def test_a_started_store_saves_at_once_and_takes_its_ceiling_from_the_config(self):
    """After on_init the first save is immediate; a CSTORE_SAVE_MAX_GAP of 20 caps the pause of a 10 s save."""
    # A data folder for the start.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # Its cache folder.
    os.makedirs(os.path.join(folder.name, "chain_store"))
    # A store to start, with the back-off 'on' and a 20 s ceiling.
    store = _StartHarness(folder.name)
    # The config on_init reads.
    store.cfg_cstore_save_backoff, store.cfg_cstore_save_max_gap = "on", 20
    # A manager that accepts the registration.
    with mock.patch.object(SerializationManager, "instance", SimpleNamespace(register=lambda save, every: None)):
      # The real start.
      store.on_init()
    # Saves of 10 s from here.
    timed_saves(store, self.clock, 10.0)
    # Calls at 1000 s (the first after the start), then 1 s after the save and every 5 s.
    for at in (1000.0, 1011.0, 1016.0, 1021.0):
      # One call.
      flush_at(store, self.clock, at)
    # The first save at once; the next at 1021 s, the first call past the 20 s ceiling (not 30 s, not 100 s).
    self.assertEqual((store.save_starts, getattr(store, STATS)["saves_deferred"]), ([1000.0, 1021.0], 2))

  def test_a_ceiling_that_is_not_a_number_stops_the_start_after_the_registration(self):
    """A CSTORE_SAVE_MAX_GAP that is not a number makes on_init raise, after the save was registered."""
    # A data folder for the start.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # Its cache folder.
    os.makedirs(os.path.join(folder.name, "chain_store"))
    # A store whose ceiling is a word.
    store = _StartHarness(folder.name)
    # The config on_init reads.
    store.cfg_cstore_save_max_gap = "thirty"
    # The saves on_init registers.
    registered = []
    # A manager that records the registration.
    with mock.patch.object(SerializationManager, "instance",
                           SimpleNamespace(register=lambda save, every: registered.append(every))):
      # The start stops.
      with self.assertRaises(ValueError):
        # The real start.
        store.on_init()
    # The save was registered first, so the on_close after a failed start finds it; the setter is not published.
    self.assertEqual((registered, store.CS_SETTER in store.plugins_shmem), ([store.CS_SAVE_INTERVAL], False))


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
