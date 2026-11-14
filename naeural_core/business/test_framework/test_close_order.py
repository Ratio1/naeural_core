"""The node's close: plugins outside the admin pipeline stop first, and no fixed wait follows the SHUTDOWN heartbeat."""

# The plugins' input queues, the heartbeat queue and the graceful-stop counters.
from collections import defaultdict, deque
# The collector, whose full collection is watched.
import gc
# The helper thread that plays the heartbeat communicator.
import threading
# Timing of the wait.
import time
# The test framework.
import unittest
# The stand-in for the heartbeat communicator.
from types import SimpleNamespace
# Stubs for the orchestrator's collaborators and for the module's sleep.
from unittest import mock

# The admin pipeline's name and the communicator names.
from naeural_core import constants as ct
# The module of the manager under test.
from naeural_core.business import business_manager as business_manager_module
# The manager that owns the heartbeat communicator.
from naeural_core.comm.communication_manager import CommunicationManager
# The module whose sleep is watched, and the class under test.
from naeural_core.main import orchestrator as orchestrator_module


class _Plugin:
  """
  Stand-in for a running plugin instance that records when it is stopped.

  Parameters
  ----------
  name : str
    Name written to the shared stop log.
  stopped : list
    The shared stop log, in stop order.

  Returns
  -------
  None

  Examples
  --------
  >>> log = []
  >>> _Plugin('app', log).stop_thread()
  >>> log
  ['app']
  """

  def __init__(self, name, stopped):
    """
    Keep the name and the shared stop log; the instance runs and has no pending input.

    Parameters
    ----------
    name : str
      Name written to the stop log.
    stopped : list
      The shared stop log.

    Returns
    -------
    None

    Examples
    --------
    >>> _Plugin('app', []).done_loop
    False
    """
    # What the stop log shows for this instance.
    self.name = name
    # The log shared by all instances of one test.
    self.stopped = stopped
    # Still running: the manager stops it instead of reporting it as already stopped.
    self.done_loop = False
    # No pending inputs.
    self.upstream_inputs_deque = deque()
    return

  def stop_thread(self):
    """
    Record the stop, as the plugin's thread join would end.

    Returns
    -------
    None

    Examples
    --------
    >>> _Plugin('app', []).stop_thread()
    """
    # One more instance stopped, in order.
    self.stopped.append(self.name)
    return


def _manager(streams):
  """
  Build a business manager that holds one running stand-in plugin per given stream, in start order.

  Parameters
  ----------
  streams : list of tuple
    ``(instance hash, stream name)`` pairs in the order the instances were started.

  Returns
  -------
  tuple
    ``(manager, stopped)``: the manager and the list that receives the instance hashes in stop order.

  Examples
  --------
  >>> manager, stopped = _manager([('h1', 'admin_pipeline'), ('h2', 'app')])
  >>> list(manager._dct_current_instances)
  ['h1', 'h2']
  """
  # No constructor: close() needs only the tables below.
  manager = business_manager_module.BusinessManager.__new__(business_manager_module.BusinessManager)
  # The shared stop log.
  stopped = []
  # Running instances by hash, in start order (the admin pipeline's are started first on a node).
  manager._dct_current_instances = {key: _Plugin(key, stopped) for key, _ in streams}
  # Hash -> (stream, signature, instance id), as the manager fills it when it starts an instance.
  manager._dct_hash_mappings = {key: (stream, 'SIG', 'inst') for key, stream in streams}
  # No stop is being timed and no instance is marked for a graceful stop.
  manager._dct_stop_timings, manager._graceful_stop_instances = {}, defaultdict(int)
  # Instances run on threads, so a stop joins the thread.
  manager._run_on_threads = True
  # No deployment lifecycle service to close.
  manager.shmem = {}
  # The manager's log lines are not read here.
  manager.P = lambda *args, **kwargs: None
  # No dispatch thread to stop.
  manager._stop_admin_dispatch_thread = lambda: None
  # Both, so a test can call close() and read the order.
  return manager, stopped


class TestPluginCloseOrder(unittest.TestCase):

  def test_instances_outside_the_admin_pipeline_stop_before_the_admin_plugins(self):
    """close() stops dAuth, then every instance outside the admin pipeline, then the admin plugins, each in start order."""
    # Start order on a node: the admin pipeline first, then the other pipelines; here mixed, to show the sort is stable.
    manager, stopped = _manager([
      ('adm1', ct.CONST_ADMIN_PIPELINE_NAME), ('dauth', ct.CONST_ADMIN_PIPELINE_NAME), ('app1', 'app_one'),
      ('adm2', ct.CONST_ADMIN_PIPELINE_NAME), ('app2', 'app_two'),
    ])
    # The special instance the manager already stops before every other one.
    manager._BusinessManager__dauth_hash = 'dauth'
    # The orderly close of all plugins.
    manager.close()
    # dAuth first as before, then the two instances outside the admin pipeline, then the admin plugins.
    self.assertEqual(stopped, ['dauth', 'app1', 'app2', 'adm1', 'adm2'])
    # Every instance was removed.
    self.assertEqual(manager._dct_current_instances, {})

  def test_a_plugin_stop_runs_no_full_collection_and_no_pause(self):
    """Stopping plugins, at the close or one at a time while the node runs, calls no gc.collect() and does not sleep."""
    # Ten admin plugins, about the number a node closes.
    manager, stopped = _manager([('adm%d' % index, ct.CONST_ADMIN_PIPELINE_NAME) for index in range(10)])
    # No special instance in this test.
    manager._BusinessManager__dauth_hash = None
    # Watch the collector for the whole test.
    with mock.patch.object(gc, "collect") as collect:
      # Time the stops: the pause that followed each one was 0.1 s, so ten took at least a second.
      started = time.time()
      # One plugin stopped while the node runs (its pipeline was removed).
      manager._send_stop_signal_to_plugin('adm0', forced=True)
      # The other nine at the node's close.
      manager.close()
      # Seconds all ten stops needed.
      elapsed = time.time() - started
    # All ten were stopped.
    self.assertEqual(len(stopped), 10)
    # No full collection after a stop: it froze every thread of the node for seconds and freed nothing of the
    # plugin just stopped, which its caller still holds.
    collect.assert_not_called()
    # No fixed pause after a stop either.
    self.assertLess(elapsed, 0.5)


class TestShutdownHeartbeatWait(unittest.TestCase):

  def _comm_manager(self, queued):
    """
    Build a communication manager whose heartbeat communicator holds the given queued messages.

    Parameters
    ----------
    queued : list
      Messages waiting in the heartbeat communicator's send queue.

    Returns
    -------
    tuple
      ``(manager, buffer, lines)``: the manager, the heartbeat send queue and the list of ``(text, color)`` lines
      the manager logged.

    Examples
    --------
    >>> manager, buffer, lines = self._comm_manager(['hb'])
    >>> len(buffer)
    1
    """
    # No constructor: the wait needs only the communicators' table and the log method.
    manager = CommunicationManager.__new__(CommunicationManager)
    # The heartbeat communicator's send queue.
    buffer = deque(queued)
    # The table of communicators, with the one the wait reads.
    manager._dct_comm_plugins = {ct.COMMS.COMMUNICATION_HEARTBEATS: SimpleNamespace(_send_buff=buffer)}
    # The lines the manager logs, with their color.
    lines = []
    # Record each line instead of printing it.
    manager.P = lambda text, color=None, **kwargs: lines.append((text, color))
    # All three, so a test can drain the queue and read the lines.
    return manager, buffer, lines

  def test_the_wait_ends_as_soon_as_the_heartbeat_left_the_queue(self):
    """The wait returns when the communicator thread took the queued heartbeat, long before its 5 s limit, silently."""
    # One heartbeat queued, as right after the SHUTDOWN heartbeat is sent.
    manager, buffer, lines = self._comm_manager(['shutdown-heartbeat'])
    # The communicator thread takes the heartbeat 0.2 s later.
    taker = threading.Timer(0.2, buffer.popleft)
    # Time the wait.
    started = time.time()
    # Start the stand-in for the communicator thread.
    taker.start()
    # The wait the orchestrator makes after the SHUTDOWN heartbeat.
    manager.wait_heartbeats_sent(timeout=5)
    # Seconds the wait needed.
    elapsed = time.time() - started
    # The stand-in thread has ended.
    taker.join()
    # Not before the heartbeat was taken.
    self.assertGreaterEqual(elapsed, 0.2)
    # And right after it: nowhere near the 5 s the fixed sleep took.
    self.assertLess(elapsed, 1.0)
    # Nothing to report.
    self.assertEqual(lines, [])

  def test_an_empty_queue_is_not_waited_for(self):
    """With nothing queued (no heartbeat was sent) the wait returns at once."""
    # Nothing queued.
    manager, buffer, lines = self._comm_manager([])
    # Time the wait.
    started = time.time()
    # Nothing to wait for.
    manager.wait_heartbeats_sent(timeout=5)
    # At once and silently.
    self.assertLess(time.time() - started, 0.05)
    # No line.
    self.assertEqual(lines, [])

  def test_a_heartbeat_that_is_not_taken_ends_the_wait_at_its_limit_with_one_red_line(self):
    """When the communicator cannot send (its broker is down), the wait ends at the limit and says so once, in red."""
    # A heartbeat that nobody takes.
    manager, buffer, lines = self._comm_manager(['shutdown-heartbeat'])
    # Time the wait.
    started = time.time()
    # A short limit for the test; the node uses 5 s.
    manager.wait_heartbeats_sent(timeout=0.4)
    # Seconds the wait needed.
    elapsed = time.time() - started
    # The whole limit, and not much more.
    self.assertTrue(0.4 <= elapsed < 1.0, elapsed)
    # One red line that names the heartbeat and the limit.
    self.assertEqual(len(lines), 1)
    # Its color.
    self.assertEqual(lines[0][1], 'r')
    # Its text.
    self.assertIn("not sent after 0.4s", lines[0][0])

  def test_the_orderly_stop_waits_for_the_heartbeat_and_does_not_sleep(self):
    """_maybe_gracefull_stop sends the SHUTDOWN heartbeat, waits for it to leave (5 s at most), then stops the threads."""
    # No constructor: the method needs only the members set below.
    eng = orchestrator_module.Orchestrator.__new__(orchestrator_module.Orchestrator)
    # One recorder for the three steps, so their order can be read.
    steps = mock.Mock()
    # Not yet in shutdown, and no return code set.
    eng._in_shutdown, eng._return_code = False, None
    # The log line of the stop is not read here.
    eng.P = mock.Mock()
    # Step 1: the SHUTDOWN heartbeat is queued.
    eng._maybe_send_heartbeat = steps.heartbeat
    # Step 2: the communication manager, whose wait is under test.
    eng._comm_manager = steps.comm
    # Step 3: the join of the orchestrator's threads.
    eng._stop = steps.stop
    # Any fixed sleep of the module is recorded.
    with mock.patch.object(orchestrator_module, "sleep") as fixed_sleep:
      # The orderly stop, as close_main_loop() calls it.
      eng._maybe_gracefull_stop()
    # The names of the recorded steps, in order.
    order = [call[0] for call in steps.mock_calls]
    # Heartbeat first, then the bounded wait, then the thread joins.
    self.assertEqual(order, ['heartbeat', 'comm.wait_heartbeats_sent', 'stop'])
    # The wait is bounded by the 5 s the fixed sleep used to take.
    steps.comm.wait_heartbeats_sent.assert_called_once_with(timeout=5)
    # No fixed sleep any more.
    fixed_sleep.assert_not_called()
    # The return code of an orderly stop.
    self.assertEqual(eng._return_code, ct.CODE_SHUTDOWN)


class TestCloseStartsTheLastSavesFirst(unittest.TestCase):

  def test_the_close_asks_for_the_last_saves_first_and_stops_the_saver_last(self):
    """close_main_loop() starts the last saves before the heartbeat and every close, and stops the saver last."""
    # No constructor: the method needs only the members set below.
    eng = orchestrator_module.Orchestrator.__new__(orchestrator_module.Orchestrator)
    # One recorder for every step, so their order can be read.
    steps = mock.Mock()
    # The log line of the close is not read here.
    eng.P = mock.Mock()
    # The SHUTDOWN heartbeat and the thread joins.
    eng._maybe_gracefull_stop = steps.graceful_stop
    # The four managers the close closes.
    eng._comm_manager, eng._business_manager = steps.comm, steps.business
    # Serving and capture.
    eng._serving_manager, eng._capture_manager = steps.serving, steps.capture
    # The node's state saver, replaced by the recorder.
    with mock.patch.object(orchestrator_module, "SerializationManager", steps.saver):
      # The node's close.
      eng.close_main_loop()
    # The names of the recorded steps, in order.
    order = [call[0] for call in steps.mock_calls]
    # The last saves are asked for before anything that can take time, and the saver is stopped at the very end.
    self.assertEqual(order, [
      'saver.instance.start_last_saves', 'graceful_stop', 'comm.close', 'business.close',
      'serving.stop_all_servers', 'capture.close', 'saver.instance.stop',
    ])
    # The stop still waits up to 60 s for the saves being written.
    steps.saver.instance.stop.assert_called_once_with(timeout=60)


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()
