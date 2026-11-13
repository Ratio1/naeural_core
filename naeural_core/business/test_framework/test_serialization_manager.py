"""The SerializationManager runs each registered save on a thread of its own, at the save's cadence."""
import functools
import importlib.util
import inspect
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]


def _load_manager():
  """The module alone (it imports only the standard library): importing its package would import the node."""
  spec = importlib.util.spec_from_file_location("_serialization_manager", _ROOT / "main" / "serialization_manager.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module.SerializationManager


SerializationManager = _load_manager()


def _load_network_monitor_plugin(manager):
  """
  NetMon01Plugin from its source, with stand-ins for what it imports from the node.

  Parameters
  ----------
  manager : object
    What ``SerializationManager.instance`` is inside the loaded module.

  Returns
  -------
  type
    The plugin class; its base is an empty class with an empty CONFIG.
  """
  source = (_ROOT / "business" / "default" / "admin" / "net_mon_01.py").read_text(encoding="utf-8")
  # Its imports would load the node (as in test_chain_store_hsync, the source is run without them).
  source = "\n".join(line for line in source.splitlines() if not line.startswith("from naeural_core."))
  namespace = {
    "BasePluginExecutor": type("BasePluginExecutor", (), {"CONFIG": {"VALIDATION_RULES": {}}}),
    "_NetworkMonitorMixin": type("_NetworkMonitorMixin", (), {}), "NMonConst": None,
    "NET_MON_01_SUPERVISOR_LOG_TIME": None, "SerializationManager": SimpleNamespace(instance=manager),
  }
  exec(compile(source, "net_mon_01.py", "exec"), namespace)  # noqa: S102
  return namespace["NetMon01Plugin"]


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

  def test_a_cadence_that_is_not_a_positive_number_is_refused_and_nothing_changes(self):
    # With 0 or less a 20 MB save would run back to back, with NaN or infinity never; a bool, None or a string
    # is a mistake.
    save = self.save("store")
    self.manager.register(save, every=100)
    entry, threads = self.manager._saves[save], self.manager._threads
    for every in (0, 0.0, -5, float("nan"), float("inf"), True, None, "120"):
      with self.subTest(every=every), self.assertRaises(ValueError):
        self.manager.register(save, every=every)
    self.assertIs(self.manager._saves[save], entry)  # the earlier registration keeps running
    self.assertIs(self.manager._threads, threads)  # and no thread was started

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

  def test_a_save_registered_for_the_stop_runs_once_more_when_the_manager_stops(self):
    """A save registered with at_stop runs one last time on its own thread, and stop() waits for it."""
    # Where each run happened, and whether the last run was written to its end.
    ran_on, finished = [], threading.Event()

    def save():
      """Note the thread, take 0.2 s as a write does, then say the write is done."""
      # The name of the thread this run is on.
      ran_on.append(threading.current_thread().name)
      # A write that takes a moment, so a stop() that did not wait would return before it ends.
      time.sleep(0.2)
      # The write reached its end.
      finished.set()

    # A log that keeps its lines, to find the line about the last run.
    log = _Log()
    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(log)
    # A cadence far away: no periodic run happens in this test, so the one run seen is the run at stop.
    manager.register(save, every=100, at_stop=True)
    # The stop: it is what starts the last run, and it must wait for it.
    manager.stop(timeout=5)
    # stop() returned only after the last run was written to its end...
    self.assertTrue(finished.is_set())
    # ...which ran exactly once, on the save's own thread...
    self.assertEqual(ran_on, ["serialization_manager:" + save.__qualname__])
    # ...and its duration is in the log, since it adds to the time the node needs to stop.
    self.assertEqual(len([line for line in log.lines if "last save" in line and "at stop took" in line]), 1)

  def test_a_save_not_registered_for_the_stop_gets_no_run_at_stop(self):
    """Without at_stop nothing changes: stop() ends the thread and the save is not run."""
    # One entry per run of the save.
    runs = []

    def save():
      """Count a run."""
      # One more run.
      runs.append(1)

    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(_Log())
    # The default registration, as the chain store makes it: its owner saves in its own closing code.
    manager.register(save, every=100)
    # The stop, which must only end the thread.
    manager.stop(timeout=5)
    # No run was made, and the thread ended.
    self.assertEqual(runs, [])
    self.assertFalse(manager._threads[0].is_alive())

  def test_a_registration_that_ended_before_the_stop_gets_no_run_at_stop(self):
    """The last run belongs to the registration current at stop: an unregistered or replaced one gets none."""
    # The name of the save for each run, in order.
    runs = []

    def gone():
      """A save its owner unregisters before the node stops."""
      # A run of the unregistered save, which must never happen.
      runs.append("gone")

    def again():
      """A save registered twice: only its second registration is current at stop."""
      # A run of the save that was registered again.
      runs.append("again")

    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(_Log())
    # Two saves that ask for the last run, far from their first periodic run.
    manager.register(gone, every=100, at_stop=True)
    manager.register(again, every=100, at_stop=True)
    # The threads of these two registrations, to wait for their end below.
    first_threads = list(manager._threads)
    # The owner of `gone` closes, and `again` is registered once more.
    manager.unregister(gone)
    manager.register(again, every=100, at_stop=True)
    for thread in first_threads:
      # Each ends at its next check, at most one second away.
      thread.join(5)
      # It did end: the absence of a run below is not just a thread that is still waiting.
      self.assertFalse(thread.is_alive())
    # Ending because the registration is gone or replaced is not a stop: no run.
    self.assertEqual(runs, [])
    # The stop of the node.
    manager.stop(timeout=5)
    # At stop only the current registration of `again` ran, once.
    self.assertEqual(runs, ["again"])

  def test_a_stop_that_comes_before_an_ended_registration_was_noticed_gives_it_no_run(self):
    """An owner closes and the node stops within the same second: the unregistered save is not run at stop."""
    # The name of the save for each run, in order.
    runs = []

    def gone():
      """A save its owner unregisters just before the node stops."""
      # A run of the unregistered save, which must never happen.
      runs.append("gone")

    def again():
      """A save registered twice just before the node stops."""
      # A run of the save that was registered again.
      runs.append("again")

    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(_Log())
    # Two saves that ask for the last run, far from their first periodic run.
    manager.register(gone, every=100, at_stop=True)
    manager.register(again, every=100, at_stop=True)
    # The owner of `gone` closes, and `again` is registered once more.
    manager.unregister(gone)
    manager.register(again, every=100, at_stop=True)
    # Stop at once: the threads of the two ended registrations are still in their one-second wait, so it
    # is the stop that wakes them, and they must see that their registration is over.
    manager.stop(timeout=5)
    # Only the registration of `again` that is current at stop ran, once.
    self.assertEqual(runs, ["again"])

  def test_a_registration_made_after_the_stop_was_asked_gets_no_run(self):
    """An owner that registers while the node is already stopping gets a thread that ends at once, with no run."""
    # One entry per run of the save.
    runs = []

    def save():
      """Count a run."""
      # One more run.
      runs.append(1)

    # The node's manager, already stopped when the owner arrives.
    manager = SerializationManager.start(_Log())
    manager.stop(timeout=5)
    # A late owner asks for the periodic save and for the last run.
    manager.register(save, every=100, at_stop=True)
    # Its thread, the only one: it finds the stop at its first wait.
    manager._threads[-1].join(5)
    # The thread ended and made no run: stop() is over and would not have waited for one.
    self.assertFalse(manager._threads[-1].is_alive())
    self.assertEqual(runs, [])

  def test_a_stop_asked_during_a_periodic_run_waits_for_it_and_for_the_last_run_after_it(self):
    """When stop() comes during a periodic run, that run ends, the last run follows, and stop() waits for both."""
    # Set when the first run started; set to let it end; what happened, in order.
    entered, release, events = threading.Event(), threading.Event(), []

    def save():
      """The first call blocks as a slow write does; every call notes its start and its end."""
      # This run started.
      events.append("start")
      if not entered.is_set():
        # The first run: say it is being written, then wait to be released.
        entered.set()
        release.wait(10)
      # This run ended.
      events.append("end")

    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(_Log())
    # A short cadence, so the periodic run starts at the thread's first check.
    manager.register(save, every=0.1, at_stop=True)
    # Wait until the periodic run is being written.
    self.assertTrue(entered.wait(5))
    # Let that run end half a second after stop() was asked.
    threading.Timer(0.5, release.set).start()
    # The stop, asked while the periodic run is blocked.
    manager.stop(timeout=5)
    # Two whole runs, one after the other: the periodic one, then the last one.
    self.assertEqual(events, ["start", "end", "start", "end"])
    # stop() returned only after both: the thread has ended.
    self.assertFalse(manager._threads[0].is_alive())

  def test_a_stop_whose_timeout_ends_during_the_last_run_returns_and_the_run_goes_on(self):
    """stop() waits no longer than its timeout for a slow last run; a later stop() waits for the rest, no new run."""
    # Set when the last run started; set to let it end; one entry per run.
    started, release, runs = threading.Event(), threading.Event(), []

    def save():
      """A last run that lasts until it is released."""
      # One more run.
      runs.append(1)
      # The run is being written.
      started.set()
      # A write slower than the stop's timeout.
      release.wait(10)

    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(_Log())
    # A cadence far away: the only run is the one at stop.
    manager.register(save, every=100, at_stop=True)
    # The moment the stop is asked, to measure how long it waits.
    begun = time.monotonic()
    # A stop that gives the saves 0.3 s.
    manager.stop(timeout=0.3)
    # How long stop() took.
    waited = time.monotonic() - begun
    # It came back at its timeout...
    self.assertLess(waited, 1.0)
    # ...while the last run was still being written.
    self.assertTrue(started.wait(5) and manager._threads[0].is_alive())
    # Let the write end.
    release.set()
    # A second stop waits for it...
    manager.stop(timeout=5)
    self.assertFalse(manager._threads[0].is_alive())
    # ...and starts no second run.
    self.assertEqual(runs, [1])

  def test_a_last_run_that_fails_is_logged_in_red_and_stop_still_returns(self):
    """A save that raises at stop is reported with its traceback and does not keep the node from closing."""
    def save():
      """Fail as a write to a full disk does."""
      raise OSError("no space left")

    # A log that keeps its lines and their colours.
    log = _Log()
    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(log)
    # A cadence far away: the only run is the one at stop.
    manager.register(save, every=100, at_stop=True)
    # The stop, whose last run raises.
    manager.stop(timeout=5)
    # The log lines that report the failure with its traceback, whose last line is "OSError: ...", by position.
    failed = [index for index, line in enumerate(log.lines)
              if "at stop failed" in line and "OSError: no space left" in line]
    # Reported once...
    self.assertEqual(len(failed), 1)
    # ...in red, like every failed save.
    self.assertEqual(log.colors[failed[0]], 'r')
    # The thread ended by itself, so stop() did not have to wait for its timeout.
    self.assertFalse(manager._threads[0].is_alive())

  def test_a_stop_asked_between_a_thread_s_wait_and_its_check_still_gets_the_last_run(self):
    """stop() can come right after a thread's one-second wait returned: its check ends it, and the last run happens."""
    # One entry per run of the save, and the waits the thread made on the stand-in event.
    runs, waits = [], []

    def save():
      """Count a run."""
      # One more run.
      runs.append(1)

    # The node's manager, as Orchestrator.startup() makes it.
    manager = SerializationManager.start(_Log())
    # The table entry register() would store, put there by hand so that no real thread runs beside this test.
    entry = [100, time.monotonic() + 100]
    manager._saves[save] = entry
    # The moment in question: the first wait reports "not stopping", and by the time of the check stop() was
    # asked. Any later wait reports the stop, so a thread that wrongly kept looping ends instead of spinning.
    manager._stopping = SimpleNamespace(
      wait=lambda seconds: bool(waits.append(seconds)) or len(waits) > 1, is_set=lambda: True, set=lambda: None
    )
    # The thread body, run here on the test's thread.
    manager._run(save, entry, True)
    # One wait, then the check ended the loop without a periodic run, and the last run was made.
    self.assertEqual((runs, len(waits)), ([1], 1))


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
    """The plugin hands its save to the manager, for an interval of its own and for the stop, and saves nowhere else."""
    # The plugin's source, read as text: importing it would import the node.
    source = (_ROOT / "business" / "default" / "admin" / "net_mon_01.py").read_text(encoding="utf-8")
    # The text of on_init, where the save is registered...
    on_init = source.split("  def on_init(self):", 1)[1].split("\n  def ", 1)[0]
    # ...and of _process, the plugin's loop, where the save used to be made.
    process = source.split("  def _process(self):", 1)[1].split("\n  def ", 1)[0]
    # The registration asks for the last run at a clean stop.
    self.assertIn("SerializationManager.instance.register(save, every=every, at_stop=True)", on_init)
    # on_init without its comments...
    init_code = "\n".join(line for line in on_init.splitlines() if not line.strip().startswith("#"))
    # ...and without its docstring: only what runs.
    init_code = init_code.split('"""', 2)[2]
    # The interval is the plugin's own setting in seconds...
    self.assertIn("self.cfg_save_nmon_seconds", init_code)
    # ...and no longer a number of iterations of the loop delay.
    self.assertNotIn("cfg_process_delay", init_code)
    self.assertNotIn("cfg_save_nmon_each", init_code)
    # _process without its comments...
    code = "\n".join(line for line in process.splitlines() if not line.strip().startswith("#"))
    # ...and without its docstring.
    code = code.split('"""', 2)[2]
    # The loop makes neither of the two saves itself.
    self.assertNotIn("network_save_status(", code)
    self.assertNotIn("epoch_manager.save_status(", code)

  def netmon_cadences(self, supervisor, process_delay, save_nmon_seconds=600, manager=None):
    """
    Run NetMon01Plugin.on_init on a stand-in node and return what its save was registered with.

    The plugin class is loaded from its source with stand-ins for the node, given the settings an instance
    would have, and its on_init is run; the stand-in manager records the registration.

    Parameters
    ----------
    supervisor : bool
      True for an oracle.
    process_delay : int or float
      The plugin's PROCESS_DELAY, which must not change the interval.
    save_nmon_seconds : int or float
      The plugin's SAVE_NMON_SECONDS.
    manager : SerializationManager or None
      The manager on_init registers with; None records the registration without one.

    Returns
    -------
    list
      One ``(seconds, at_stop)`` pair per registration. The plugin's log lines are left in
      ``self.netmon_lines``.

    Examples
    --------
    >>> self.netmon_cadences(True, 10)
    [(600, True)]
    """
    # What the stand-in manager was asked to register, and what the plugin logged.
    registered, self.netmon_lines = [], []
    # A stand-in manager that keeps the interval and the stop flag of each registration.
    manager = manager or SimpleNamespace(register=lambda save, every, at_stop: registered.append((every, at_stop)))
    # The plugin class with that manager in place of the node's.
    plugin_class = _load_network_monitor_plugin(manager)
    # An instance without the node's constructor: only what on_init reads is set below.
    plugin = plugin_class.__new__(plugin_class)
    # A network monitor whose two saves do nothing.
    netmon = SimpleNamespace(network_save_status=lambda: None, epoch_manager=SimpleNamespace(save_status=lambda: None))
    # Without np the sending period, not set in os_environ, ends as 0 in on_init's own except, as on a node.
    vars(plugin).update(
      cfg_supervisor=supervisor, is_supervisor_node=supervisor, cfg_supervisor_log_time=None, os_environ={},
      cfg_save_nmon_seconds=save_nmon_seconds, cfg_process_delay=process_delay, netmon=netmon, const=mock.Mock(),
      P=lambda msg, **kwargs: self.netmon_lines.append(msg),
    )
    # The start of the plugin: it registers the save.
    plugin.on_init()
    return registered

  def test_the_network_monitor_save_has_an_interval_of_its_own_in_seconds_and_runs_at_stop(self):
    """An oracle saves every SAVE_NMON_SECONDS, any other node every 300 s, whatever PROCESS_DELAY is; both at stop."""
    # The setting as the plugin ships it: 600 s, under the name on_init reads as cfg_save_nmon_seconds.
    self.assertEqual(_load_network_monitor_plugin(None).CONFIG["SAVE_NMON_SECONDS"], 600)
    # Loop delays that ship or are valid (0 is, for the plugin loop): none of them moves the interval any more.
    for delay in (0, 0.5, 5, 10, 20):
      with self.subTest(delay=delay):
        # An oracle: 600 s, where it was 12 iterations of the loop delay.
        self.assertEqual(self.netmon_cadences(True, delay), [(600, True)])
        # Any other node: 300 s for its small epochs status.
        self.assertEqual(self.netmon_cadences(False, delay), [(300, True)])
    # The setting is the oracle's interval, and the log says what was registered.
    self.assertEqual(self.netmon_cadences(True, 10, save_nmon_seconds=900), [(900, True)])
    self.assertIn("Netmon status save registered every 900s and at a clean stop", self.netmon_lines)
    # Another node does not read the setting.
    self.assertEqual(self.netmon_cadences(False, 10, save_nmon_seconds=900), [(300, True)])
    # The real manager, to see an interval that is not a positive number refused: the plugin does not start.
    manager = SerializationManager(_Log())
    # Stopped at the end, so a thread started if the refusal regressed does not outlive this test.
    self.addCleanup(manager.stop, timeout=5)
    for bad in (0, -5, None, "600"):
      # Each of these must be refused when the plugin registers its save.
      with self.subTest(bad=bad), self.assertRaises(ValueError):
        self.netmon_cadences(True, 10, save_nmon_seconds=bad, manager=manager)

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
