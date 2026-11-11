"""The SerializationManager runs the registered saves on its own thread, each at its cadence."""
import importlib.util
import inspect
import threading
import time
import unittest
from pathlib import Path

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

  def P(self, msg, **kwargs):  # pylint: disable=unused-argument
    self.lines.append(msg)


class TestCadence(unittest.TestCase):
  """The schedule, driven by hand through _run_due(now): no thread, no waiting."""

  def setUp(self):
    self.log = _Log()
    self.manager = SerializationManager(self.log)
    self.runs = []

  def save(self, name):
    return lambda: self.runs.append(name)

  def test_a_save_runs_when_its_time_has_come_and_then_one_cadence_later(self):
    registered = time.time()
    self.manager.register(self.save("store"), every=100)
    self.manager._run_due(registered + 99)  # one second early
    self.assertEqual(self.runs, [])
    self.manager._run_due(registered + 101)
    self.assertEqual(self.runs, ["store"])
    self.manager._run_due(time.time() + 99)  # the next run is 100 s after the first one started
    self.assertEqual(self.runs, ["store"])
    self.manager._run_due(time.time() + 101)
    self.assertEqual(self.runs, ["store", "store"])

  def test_each_save_keeps_its_own_cadence(self):
    registered = time.time()
    self.manager.register(self.save("store"), every=5)
    self.manager.register(self.save("network"), every=120)
    self.manager._run_due(registered + 6)
    self.assertEqual(self.runs, ["store"])
    self.manager._run_due(time.time() + 121)
    self.assertEqual(self.runs, ["store", "store", "network"])

  def test_a_failing_save_is_logged_and_neither_stops_the_others_nor_itself(self):
    attempts = []

    def flaky():
      attempts.append(len(attempts))
      if len(attempts) == 1:
        raise OSError("disk full")

    self.manager.register(flaky, every=5)
    self.manager.register(self.save("network"), every=5)
    self.manager._run_due(time.time() + 6)
    self.assertEqual((len(attempts), self.runs), (1, ["network"]))
    self.assertEqual(len(self.log.lines), 1)
    self.assertIn("failed", self.log.lines[0])
    self.assertIn("OSError: disk full", self.log.lines[0])  # the traceback, not only the fact
    self.manager._run_due(time.time() + 6)  # tried again at its next cadence
    self.assertEqual((len(attempts), len(self.log.lines)), (2, 1))

  def test_an_unregistered_save_is_not_run_again(self):
    save = self.save("store")
    self.manager.register(save, every=5)
    self.manager.unregister(save)
    self.manager._run_due(time.time() + 6)
    self.assertEqual(self.runs, [])

  def test_unregistering_an_unknown_save_fails(self):
    with self.assertRaises(KeyError):
      self.manager.unregister(self.save("never registered"))

  def test_registering_the_same_save_again_replaces_its_cadence(self):
    save = self.save("store")
    self.manager.register(save, every=5)
    self.manager.register(save, every=500)
    self.manager._run_due(time.time() + 6)
    self.assertEqual(self.runs, [])
    self.manager._run_due(time.time() + 501)
    self.assertEqual(self.runs, ["store"])  # once, not once per registration

  def test_a_save_unregistered_during_an_earlier_save_of_the_same_pass_is_not_run(self):
    # The network save takes seconds; the chain store may close (and make its own final save) meanwhile.
    closed = self.save("closed store")
    self.manager.register(lambda: self.manager.unregister(closed), every=5)
    self.manager.register(closed, every=5)
    self.manager._run_due(time.time() + 6)
    self.assertEqual(self.runs, [])

  def test_no_save_starts_once_stop_was_asked(self):
    self.manager.register(self.manager._stopping.set, every=5)  # stop() arrives during the first save
    self.manager.register(self.save("network"), every=5)
    self.manager._run_due(time.time() + 6)
    self.assertEqual(self.runs, [])

  def test_a_bound_method_registers_as_one_save(self):
    # The network-monitor plugin registers netmon.network_save_status again when it restarts.
    class Owner:
      def __init__(self, runs):
        self.runs = runs

      def save(self):
        self.runs.append("owner")

    owner = Owner(self.runs)
    self.manager.register(owner.save, every=5)
    self.manager.register(owner.save, every=5)
    self.manager._run_due(time.time() + 6)
    self.assertEqual(self.runs, ["owner"])


class TestThread(unittest.TestCase):
  """The real thread: who runs the save, and what stop() waits for."""

  def tearDown(self):
    SerializationManager.instance.stop(timeout=5)
    SerializationManager.instance = None

  def test_the_save_runs_on_the_manager_thread_not_on_the_owner(self):
    ran_on, done = [], threading.Event()

    def save():
      ran_on.append(threading.current_thread().name)
      done.set()

    manager = SerializationManager.start(_Log())
    self.assertIs(SerializationManager.instance, manager)
    started = time.time()
    manager.register(save, every=0.1)
    self.assertLess(time.time() - started, 0.5)  # registering does not wait for a save
    self.assertTrue(done.wait(5))
    self.assertEqual(ran_on[0], "serialization_manager")
    self.assertNotEqual(ran_on[0], threading.current_thread().name)

  def test_stop_waits_for_the_save_in_progress_and_ends_the_thread(self):
    entered, finished = threading.Event(), threading.Event()

    def slow_save():
      entered.set()
      time.sleep(0.4)
      finished.set()

    manager = SerializationManager.start(_Log())
    manager.register(slow_save, every=0.1)
    self.assertTrue(entered.wait(5))
    manager.stop(timeout=5)
    self.assertTrue(finished.is_set())  # the write was not cut short
    self.assertFalse(manager._thread.is_alive())

  def test_stop_can_be_called_twice(self):
    manager = SerializationManager.start(_Log())
    manager.stop(timeout=5)
    manager.stop(timeout=5)
    self.assertFalse(manager._thread.is_alive())


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
