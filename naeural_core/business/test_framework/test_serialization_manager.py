"""The SerializationManager runs each registered save on a thread of its own, at the save's cadence."""
import functools
import importlib.util
import inspect
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]


def _load_manager():
  """The module alone (it imports only the standard library): importing its package would import the node."""
  spec = importlib.util.spec_from_file_location("_serialization_manager", _ROOT / "main" / "serialization_manager.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module.SerializationManager


SerializationManager = _load_manager()


class _Log:
  def __init__(self):
    self.lines = []
    self.colors = []

  def P(self, msg, color=None, **kwargs):  # pylint: disable=unused-argument
    self.lines.append(msg)
    self.colors.append(color)


class TestCadence(unittest.TestCase):
  """
  The check a save's thread makes once a second, driven by hand through _run_due(now) on the monotonic clock.

  register() also starts the save's real thread. Its first check comes one second later and finds nothing due
  (every cadence here is 100 s or more), and tearDown stops it.
  """

  def setUp(self):
    self.log = _Log()
    self.manager = SerializationManager(self.log)
    self.runs = []

  def tearDown(self):
    self.manager.stop(timeout=5)

  def save(self, name):
    return lambda: self.runs.append(name)

  def check(self, save, entry, now):
    """One check of a thread started with `entry`, at monotonic time `now` (fresh statistics: no stats line)."""
    return self.manager._run_due(save, entry, now, [now, 0, 0.0, 0.0])

  def tick(self, save, now):
    """One check of the thread of the registration `save` has now."""
    return self.check(save, self.manager._saves[save], now)

  def test_a_save_runs_when_its_time_has_come_and_then_one_cadence_after_that_run_started(self):
    registered = time.monotonic()
    save = self.save("store")
    self.manager.register(save, every=100)
    self.assertTrue(self.tick(save, registered + 99))  # one second early: nothing, and the thread goes on
    self.assertEqual(self.runs, [])
    self.tick(save, registered + 150)  # a late first run: it was due at about +100
    self.assertEqual(self.runs, ["store"])
    self.tick(save, registered + 150 + 99)  # the next run is 100 s after that run started, not after +100
    self.assertEqual(self.runs, ["store"])
    self.tick(save, registered + 150 + 100)
    self.assertEqual(self.runs, ["store", "store"])

  def test_a_thread_that_cannot_start_leaves_the_table_as_it_was(self):
    save, other = self.save("store"), self.save("other")
    self.manager.register(save, every=100)
    entry = self.manager._saves[save]
    self.assertTrue(all(thread.daemon for thread in self.manager._threads))
    with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
      with self.assertRaises(RuntimeError):
        self.manager.register(save, every=7)  # the earlier registration must keep running
      with self.assertRaises(RuntimeError):
        self.manager.register(other, every=7)  # nothing is left registered without a thread
    self.assertIs(self.manager._saves[save], entry)
    self.assertNotIn(other, self.manager._saves)
    self.assertTrue(self.tick(save, time.monotonic() + 101))
    self.assertEqual(self.runs, ["store"])

  def test_each_save_keeps_its_own_cadence(self):
    registered = time.monotonic()
    store, network = self.save("store"), self.save("network")
    self.manager.register(store, every=100)
    self.manager.register(network, every=1000)
    for save in (store, network):
      self.tick(save, registered + 101)
    self.assertEqual(self.runs, ["store"])
    for save in (store, network):
      self.tick(save, registered + 1001)
    self.assertEqual(self.runs, ["store", "store", "network"])

  def test_a_failing_save_is_logged_in_red_with_its_traceback_and_stops_neither_itself_nor_the_others(self):
    attempts = []

    def flaky():
      attempts.append(len(attempts))
      if len(attempts) == 1:
        raise OSError("disk full")

    network = self.save("network")
    self.manager.register(flaky, every=100)
    self.manager.register(network, every=100)
    now = time.monotonic() + 101
    self.assertTrue(self.tick(flaky, now))  # its thread goes on
    self.tick(network, now)
    self.assertEqual((len(attempts), self.runs), (1, ["network"]))
    self.assertEqual(len(self.log.lines), 1)
    self.assertIn("failed", self.log.lines[0])
    self.assertIn("OSError: disk full", self.log.lines[0])  # the traceback, not only the fact
    self.assertEqual(self.log.colors, ["r"])
    self.tick(flaky, now + 100)  # tried again at its next cadence
    self.assertEqual((len(attempts), len(self.log.lines)), (2, 1))

  def test_an_unregistered_save_is_not_run_again_and_its_thread_is_told_to_end(self):
    save = self.save("store")
    self.manager.register(save, every=100)
    entry = self.manager._saves[save]
    self.manager.unregister(save)
    self.assertFalse(self.check(save, entry, time.monotonic() + 101))
    self.assertEqual(self.runs, [])

  def test_unregistering_an_unknown_save_fails(self):
    with self.assertRaises(KeyError):
      self.manager.unregister(self.save("never registered"))

  def test_registering_the_same_save_again_replaces_its_cadence_and_ends_the_earlier_thread(self):
    save = self.save("store")
    self.manager.register(save, every=100)
    earlier = self.manager._saves[save]
    self.manager.register(save, every=500)
    now = time.monotonic()
    self.assertFalse(self.check(save, earlier, now + 101))  # the thread of the first registration ends
    self.assertTrue(self.tick(save, now + 101))
    self.assertEqual(self.runs, [])
    self.tick(save, now + 501)
    self.assertEqual(self.runs, ["store"])  # once, not once per registration

  def test_no_save_starts_once_stop_was_asked(self):
    save = self.save("network")
    self.manager.register(save, every=100)
    entry = self.manager._saves[save]
    self.manager.stop(timeout=5)
    self.assertFalse(self.check(save, entry, time.monotonic() + 101))
    self.assertEqual(self.runs, [])

  def test_a_bound_method_registers_as_one_save(self):
    # The network-monitor plugin registers netmon.network_save_status again when it restarts.
    class Owner:
      def __init__(self, runs):
        self.runs = runs

      def save(self):
        self.runs.append("owner")

    owner = Owner(self.runs)
    self.manager.register(owner.save, every=100)
    self.manager.register(owner.save, every=100)
    self.assertEqual(len(self.manager._saves), 1)
    self.tick(owner.save, time.monotonic() + 101)
    self.assertEqual(self.runs, ["owner"])

  def test_only_a_function_or_a_method_can_be_registered(self):
    # The thread and the statistics line are named after the save's __qualname__.
    with self.assertRaises(AttributeError):
      self.manager.register(functools.partial(self.runs.append, "store"), every=100)
    self.assertEqual((self.manager._saves, self.manager._threads), ({}, []))

  def test_the_statistics_line_reports_the_runs_of_one_interval_and_the_counting_starts_again(self):
    clock, durations = [1000.0], iter([1.5, 0.25, 2.0])

    def save():
      clock[0] += next(durations)  # this run takes that long on the stubbed monotonic clock

    # By hand, without a thread: this test plays the save's thread, started at 1000 s.
    entry = self.manager._saves[save] = [120, 1000.0]
    stats = [1000.0, 0, 0.0, 0.0]
    with mock.patch.dict(SerializationManager._run_due.__globals__, monotonic=lambda: clock[0]):
      for now in (1000.0, 1120.0, 1240.0, 1299.0):
        self.assertTrue(self.manager._run_due(save, entry, now, stats))
      self.assertEqual(self.log.lines, [])  # 299 s: not yet
      self.manager._run_due(save, entry, 1300.0, stats)  # nothing due (next run at 1360 s), but 300 s passed
    self.assertEqual(SerializationManager.STATS_INTERVAL, 300)
    self.assertEqual(self.log.lines, [
      "SERIALIZATION_STATS save={} period_s=300 calls=3 total_s=3.750 max_s=2.000".format(save.__qualname__)
    ])
    self.assertEqual(stats, [1300.0, 0, 0.0, 0.0])  # counting starts again


class TestThread(unittest.TestCase):
  """The real threads: who runs a save, how the saves' threads end, and what stop() waits for."""

  def tearDown(self):
    SerializationManager.instance.stop(timeout=5)
    SerializationManager.instance = None

  def test_the_save_runs_on_its_own_named_thread_not_on_the_owner(self):
    ran_on, done = [], threading.Event()

    def save():
      ran_on.append(threading.current_thread().name)
      done.set()

    manager = SerializationManager.start(_Log())
    self.assertIs(SerializationManager.instance, manager)
    started = time.monotonic()
    manager.register(save, every=0.1)
    self.assertLess(time.monotonic() - started, 0.5)  # registering does not wait for a save
    self.assertTrue(done.wait(5))
    self.assertEqual(ran_on[0], "serialization_manager:" + save.__qualname__)
    self.assertNotEqual(ran_on[0], threading.current_thread().name)

  def test_a_slow_save_does_not_delay_another_save_that_is_due(self):
    # The network-monitor save takes seconds on a slow node; a chain-store save due meanwhile must not wait.
    entered, release, store_ran = threading.Event(), threading.Event(), threading.Event()

    def network_save():
      entered.set()
      release.wait(10)

    manager = SerializationManager.start(_Log())
    manager.register(network_save, every=0.1)
    manager.register(store_ran.set, every=0.1)
    try:
      self.assertTrue(entered.wait(5))
      store_ran.clear()  # from here on the network save is being written
      self.assertTrue(store_ran.wait(3))  # with one thread for all saves it would wait for the network save
    finally:
      release.set()

  def test_the_thread_of_an_unregistered_save_ends(self):
    def save():
      pass

    manager = SerializationManager.start(_Log())
    manager.register(save, every=100)
    thread = manager._threads[-1]
    manager.unregister(save)
    thread.join(3)
    self.assertFalse(thread.is_alive())  # ended at its next check, without stop()

  def test_registering_again_leaves_one_live_thread_for_the_save_and_forgets_the_ended_ones(self):
    def save():
      pass

    manager = SerializationManager.start(_Log())
    manager.register(save, every=100)
    first = manager._threads[-1]
    manager.register(save, every=100)  # an owner that restarted
    second = manager._threads[-1]
    first.join(3)
    self.assertFalse(first.is_alive())  # replaced: ended at its next check
    manager.register(save, every=100)  # and restarted again
    third = manager._threads[-1]
    self.assertEqual(manager._threads, [second, third])  # the thread that ended is no longer kept
    second.join(3)
    name = "serialization_manager:" + save.__qualname__
    self.assertEqual([thread for thread in threading.enumerate() if thread.name == name], [third])

  def test_stop_waits_for_every_save_in_progress_and_ends_every_thread(self):
    entered = [threading.Event(), threading.Event()]
    finished = [threading.Event(), threading.Event()]

    def short_save():
      entered[0].set()
      time.sleep(0.1)
      finished[0].set()

    def long_save():
      entered[1].set()
      time.sleep(0.4)
      finished[1].set()

    manager = SerializationManager.start(_Log())
    manager.register(short_save, every=0.1)
    manager.register(long_save, every=0.1)
    self.assertTrue(entered[0].wait(5) and entered[1].wait(5))
    manager.stop(timeout=5)
    self.assertTrue(finished[0].is_set() and finished[1].is_set())  # no write was cut short
    self.assertEqual([thread.is_alive() for thread in manager._threads], [False, False])

  def test_stop_waits_for_all_threads_within_one_timeout_in_total(self):
    entered, release = [threading.Event(), threading.Event()], threading.Event()

    def first_save():
      entered[0].set()
      release.wait(10)

    def second_save():
      entered[1].set()
      release.wait(10)

    manager = SerializationManager.start(_Log())
    manager.register(first_save, every=0.1)
    manager.register(second_save, every=0.1)
    try:
      self.assertTrue(entered[0].wait(5) and entered[1].wait(5))
      started = time.monotonic()
      manager.stop(timeout=0.6)  # both saves are stuck: stop gives up after the timeout
      waited = time.monotonic() - started
    finally:
      release.set()
    self.assertGreater(waited, 0.5)
    self.assertLess(waited, 1.0)  # 0.6 s in total, not 0.6 s for each thread

  def test_stop_can_be_called_twice(self):
    def save():
      pass

    manager = SerializationManager.start(_Log())
    manager.register(save, every=100)
    manager.stop(timeout=5)
    manager.stop(timeout=5)
    self.assertFalse(manager._threads[0].is_alive())


class TestClock(unittest.TestCase):
  """The cadence is counted on the monotonic clock: a wall clock stepped back must not hold the saves back."""

  def test_a_wall_clock_stepped_back_by_an_hour_does_not_stop_the_saves(self):
    wall, saved = [1_000_000.0], threading.Event()
    with mock.patch("time.time", lambda: wall[0]):
      # Load the module under the stubbed wall clock, so a module that imports time.time gets this one.
      manager = _load_manager()(_Log())
      manager.register(saved.set, every=0.1)
      wall[0] -= 3600  # an NTP or manual step back of one hour, right after the registration
      try:
        self.assertTrue(saved.wait(5))  # with the wall clock the save would wait for an hour
      finally:
        manager.stop(timeout=5)


class TestOwners(unittest.TestCase):
  """The two owners hand their periodic save to the manager and no longer run it on their own thread."""

  def test_the_network_monitor_plugin_registers_its_save_and_does_not_save_in_its_loop(self):
    source = (_ROOT / "business" / "default" / "admin" / "net_mon_01.py").read_text(encoding="utf-8")
    on_init = source.split("  def on_init(self):", 1)[1].split("\n  def ", 1)[0]
    process = source.split("  def _process(self):", 1)[1].split("\n  def ", 1)[0]
    self.assertIn("SerializationManager.instance.register(save, every=every)", on_init)
    self.assertIn("* self.cfg_process_delay", on_init)  # the cadence in seconds, not in iterations
    code = "\n".join(line for line in process.splitlines() if not line.strip().startswith("#"))
    code = code.split('"""', 2)[2]  # without the docstring
    self.assertNotIn("network_save_status(", code)
    self.assertNotIn("epoch_manager.save_status(", code)

  def test_the_chain_store_registers_its_flush_right_after_loading_the_replica(self):
    # Its periodic save exists only through this registration; on_close removes it, so it must come before
    # the steps of on_init that can fail.
    source = (_ROOT / "business" / "default" / "admin" / "chain_store_base.py").read_text(encoding="utf-8")
    on_init = source.split("  def on_init(self):", 1)[1].split("\n  def ", 1)[0]
    process = source.split("  def process(self):", 1)[1].split("\n  def ", 1)[0]
    on_close = source.split("  def on_close(self):", 1)[1]
    register = "SerializationManager.instance.register(self.__maybe_flush_chain_storage, every=self.CS_SAVE_INTERVAL)"
    self.assertIn(register, on_init)
    self.assertLess(on_init.index("self.cacheapi_load_pickle("), on_init.index(register))
    self.assertLess(on_init.index(register), on_init.index("memory = self.plugins_shmem"))
    self.assertNotIn("self.__maybe_flush_chain_storage()", process)
    self.assertIn("SerializationManager.instance.unregister(self.__maybe_flush_chain_storage)", on_close)

  def test_the_orchestrator_starts_the_manager_first_and_stops_it_last(self):
    source = (_ROOT / "main" / "orchestrator.py").read_text(encoding="utf-8")
    startup = source.split("  def startup(self):", 1)[1].split("\n  def ", 1)[0]
    close = source.split("  def close_main_loop(self):", 1)[1].split("\n  def ", 1)[0]
    self.assertIn("SerializationManager.start(self.log)", startup)
    statements = [line.strip() for line in close.splitlines() if line.strip() and not line.strip().startswith("#")]
    self.assertEqual(statements[-2:], ["SerializationManager.instance.stop(timeout=60)", "return"])

  def test_every_method_is_documented(self):
    for name, member in inspect.getmembers(SerializationManager, predicate=callable):
      if name.startswith("__") and name != "__init__":
        continue
      self.assertIn("Returns", inspect.getdoc(member) or "", name)


if __name__ == "__main__":
  unittest.main()
