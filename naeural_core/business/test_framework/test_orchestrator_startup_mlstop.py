"""A main-loop stop before the first lap is the node's normal start: no alert and no withheld heartbeat, within a bound."""

# The test framework.
import unittest
# Stand-ins for the orchestrator's collaborators.
from unittest import mock

# The heartbeat event name and the heartbeat field names.
from naeural_core import constants as ct
# The module of the start-up grace, and the class under test.
from naeural_core.main import orchestrator as orchestrator_module


class _Engine(orchestrator_module.Orchestrator):
  """
  An orchestrator whose configuration values and timings are fixed class members.

  The heartbeat method reads these through properties of the real class; here they are plain values, so an
  instance made without the constructor can run the method.

  Parameters
  ----------
  None

  Returns
  -------
  None

  Examples
  --------
  >>> eng = _Engine.__new__(_Engine)
  >>> eng.cfg_app_seconds_heartbeat
  10
  """
  # Seconds between two heartbeats, and the node's name.
  cfg_app_seconds_heartbeat, cfg_eeid = 10, "node"
  # Loop timing figures of the heartbeat's log lines.
  avg_loop_timings, real_main_loop_resolution, cfg_main_loop_resolution = 0.0, 0, 5
  # Seconds since the process started, printed in the stop lines; a test sets its own value on the instance's
  # class copy.
  running_time = 30.0


def _engine(running_time=30.0):
  """
  Build an orchestrator, without its constructor, in the state it has when the first heartbeat is due.

  Parameters
  ----------
  running_time : float, optional
    Seconds since the process started, as the heartbeat's lines print them. Default 30. The start-up grace
    does not read it: it is counted from the first heartbeat, on the clock the test case moves.

  Returns
  -------
  tuple
    ``(eng, sent, lines, alerts)``: the orchestrator; the list that gets one entry per heartbeat handed to the
    communication manager; the logged ``(text, color)`` lines; the ``is_alert`` value of each notification
    that carries one.

  Examples
  --------
  >>> eng, sent, lines, alerts = _engine()
  >>> eng._maybe_send_heartbeat(force=True)
  >>> len(sent)
  1
  """
  # A class of its own for this engine, so the running time of one test does not leak into another.
  engine_class = type("_EngineOfOneTest", (_Engine,), {"running_time": running_time})
  # An instance without the constructor: the method needs only the members set below.
  eng = engine_class.__new__(engine_class)
  # What the method records: heartbeats sent, lines logged, alert flags of the stop notifications.
  sent, lines, alerts = [], [], []
  # The communication manager: every heartbeat handed to it is counted.
  eng._comm_manager = mock.Mock(should_register_local_self_heartbeat=False)
  # Count the heartbeats.
  eng._comm_manager.send.side_effect = lambda data, event_type: sent.append(event_type)
  # The start-up log was already sent, and no heartbeat was sent yet.
  eng._should_send_initial_log, eng._initial_log_sent, eng._last_heartbeat = False, True, 0
  # The checks made before each heartbeat are not part of these tests.
  eng._maybe_send_periodic_notifications = eng._check_for_other_issues = lambda: None
  # The application monitor: an empty status, no alert.
  eng._app_monitor = mock.Mock(alert=False)
  # The heartbeat's content: only the counters the method reads.
  eng._app_monitor.get_status.return_value = {ct.HB.NR_INFERENCES: 0, ct.HB.NR_PAYLOADS: 0, ct.HB.NR_STREAMS_DATA: 0}
  # The main loop has not begun its first lap.
  eng._main_loop_counts = {"ITER": 0, "VOID_ITER": 0}
  # No heartbeat has compared the lap counter yet.
  eng._last_checked_main_loop_iter, eng._heartbeat_counter = None, 0
  # The payload counters of the heartbeat's log line.
  eng._payloads_count_queue = [0, 0]
  # The stop state as the constructor leaves it: not stopped, no stop counted.
  eng._Orchestrator__main_loop_stopped, eng._Orchestrator__main_loop_stopped_count = False, 0
  # No stop time or stage yet; the stage the loop is in, and when it last changed.
  eng._Orchestrator__main_loop_stopped_time, eng._Orchestrator__main_loop_stop_at_stage = 0, None
  # The admin plugins are being loaded, a stage that marks a stop as dangerous, as on every start.
  eng._Orchestrator__loop_stage, eng._Orchestrator__main_loop_last_stage_change = "2.bm.refresh.get_class", 0
  # What the plugin loading sets through set_loop_stage() before the first lap.
  eng._Orchestrator__is_mlstop_dangerous = True
  # The record of a stop written to the node's status is not part of these tests.
  eng._Orchestrator__log_main_loop_stop = lambda end=None: None
  # The version text of the heartbeat's log line.
  eng._Orchestrator__get_system_version = lambda: "v"
  # The last loop timing of the stop lines.
  eng._get_last_timing = lambda: -1.0
  # The logger: no open timer, and a simple text for elapsed times.
  eng.log = mock.Mock()
  # No timer is open while the plugins load.
  eng.log.get_opened_timer.return_value = ("app_alive_timer", 0.0)
  # Elapsed times as plain text.
  eng.log.elapsed_to_str.side_effect = lambda seconds, **kwargs: "{:.0f}s".format(seconds)
  # The lines the orchestrator logs, with their color.
  eng.P = lambda text, color=None, **kwargs: lines.append((text, color))
  # The notifications: keep the alert flag of those that set one.
  eng._create_notification = lambda **kwargs: alerts.append(kwargs["is_alert"]) if "is_alert" in kwargs else None
  # The engine and what it records.
  return eng, sent, lines, alerts


class TestStartupMainLoopStop(unittest.TestCase):
  """A stop on lap counter 0 is the normal start for the grace after the first heartbeat, and dangerous past it."""

  def setUp(self):
    """
    Replace the orchestrator module's clock with one the test moves.

    Returns
    -------
    None
    """
    # The time the heartbeat reads: one cell, so a test moves it by assigning to it.
    self.clock = [1_000_000.0]
    # The heartbeat calls the module's name `time`.
    patcher = mock.patch.object(orchestrator_module, "time", lambda: self.clock[0])
    # In force during the whole test.
    patcher.start()
    # Removed after it, also when it fails.
    self.addCleanup(patcher.stop)

  def _ticks(self, eng, count):
    """
    Run heartbeat ticks 10 s apart on the test's clock.

    Parameters
    ----------
    eng : _Engine
      The orchestrator built by ``_engine``.
    count : int
      Number of ticks.

    Returns
    -------
    None
    """
    # One tick after another, as the communication thread makes them.
    for _ in range(count):
      # Ten seconds later.
      self.clock[0] += 10
      # One tick of the heartbeat.
      eng._maybe_send_heartbeat(force=True)

  def test_a_stop_before_the_first_lap_is_normal_and_withholds_no_heartbeat(self):
    """Three heartbeats while the first lap has not begun, then one after it: all four are sent, nothing is an alert."""
    # A node 30 s into its start, loading its admin plugins.
    eng, sent, lines, alerts = _engine(running_time=30.0)
    # Three heartbeat ticks with the lap counter still at 0: the second and third see a stopped loop.
    self._ticks(eng, 3)
    # The main loop has started and is at its fiftieth lap when the next heartbeat is due.
    eng._main_loop_counts["ITER"] = 50
    # The tick that sees the loop running again.
    self._ticks(eng, 1)
    # All four heartbeats left; before, the third and the fourth were withheld.
    self.assertEqual(len(sent), 4)
    # The two stop notifications are not alerts.
    self.assertEqual(alerts, [False, False])
    # The stop lines and the resume line.
    stop_lines = [text for text, _ in lines if "MLSTOP" in text or "main loop resumed" in text]
    # Two stopped ticks and the resume, each labelled as the normal start.
    self.assertEqual([text.split(" ")[0] for text in stop_lines], ["Normal", "Normal", "Normal"])
    # No line says a heartbeat was not sent.
    self.assertEqual([text for text, _ in lines if "NOT sent" in text], [])

  def test_a_start_that_lasts_beyond_the_grace_is_dangerous(self):
    """One second past the grace after the first heartbeat, a stop before the first lap is dangerous and withholds heartbeats."""
    # A node at its first heartbeat, loading its admin plugins.
    eng, sent, lines, alerts = _engine(running_time=30.0)
    # The first heartbeat: the grace is counted from here.
    eng._maybe_send_heartbeat(force=True)
    # The plugins are still loading one second after the grace has passed: a hung start.
    self.clock[0] += orchestrator_module.MLSTOP_STARTUP_GRACE + 1
    # Two more heartbeat ticks at that time, with the lap counter still at 0.
    for _ in range(2):
      # One tick of the heartbeat.
      eng._maybe_send_heartbeat(force=True)
    # The first two were sent (the stop is only known after the second); the third is withheld.
    self.assertEqual(len(sent), 2)
    # Both stop notifications are alerts.
    self.assertEqual(alerts, [True, True])
    # The stop lines carry the warning label.
    self.assertTrue(all(text.startswith("WARNING:") for text, _ in lines if "MLSTOP" in text))

  def test_the_last_second_of_the_grace_is_still_the_normal_start(self):
    """Exactly the grace after the first heartbeat, a stop before the first lap is still normal: the bound is 600 s, not less."""
    # A node at its first heartbeat, loading its admin plugins.
    eng, sent, lines, alerts = _engine(running_time=30.0)
    # The first heartbeat: the grace is counted from here.
    eng._maybe_send_heartbeat(force=True)
    # The whole grace later, to the second.
    self.clock[0] += orchestrator_module.MLSTOP_STARTUP_GRACE
    # Two more heartbeat ticks at that time, with the lap counter still at 0.
    for _ in range(2):
      # One tick of the heartbeat.
      eng._maybe_send_heartbeat(force=True)
    # All three heartbeats left, and the two stop notifications are not alerts.
    self.assertEqual((len(sent), alerts), (3, [False, False]))
    # The stop lines read as the normal start.
    self.assertTrue(all(text.startswith("Normal") for text, _ in lines if "MLSTOP" in text))

  def test_the_grace_is_counted_from_the_first_heartbeat_not_from_the_process_start(self):
    """A node whose dAuth wait took longer than the grace still gets it: its start-up stop is normal, with no gap."""
    # The process is twice the grace old at its first heartbeat: the dAuth wait before it took that long.
    eng, sent, lines, alerts = _engine(running_time=2 * orchestrator_module.MLSTOP_STARTUP_GRACE)
    # Three heartbeat ticks with the lap counter still at 0.
    self._ticks(eng, 3)
    # All three heartbeats left and nothing is an alert; counted from the process start, the third was withheld.
    self.assertEqual((len(sent), alerts), (3, [False, False]))
    # The stop lines read as the normal start.
    self.assertTrue(all(text.startswith("Normal") for text, _ in lines if "MLSTOP" in text))

  def test_a_stop_after_the_first_lap_is_dangerous_as_before(self):
    """Once the loop has made laps, a stop in a dangerous stage is an alert and withholds heartbeats, as before."""
    # A node 30 s after its start whose loop already runs.
    eng, sent, lines, alerts = _engine(running_time=30.0)
    # The loop is at its seventh lap and stays there.
    eng._main_loop_counts["ITER"] = 7
    # Three heartbeat ticks on the same lap.
    self._ticks(eng, 3)
    # The third heartbeat is withheld.
    self.assertEqual(len(sent), 2)
    # Both stop notifications are alerts.
    self.assertEqual(alerts, [True, True])

  def test_the_grace_is_ten_minutes(self):
    """The bound after which a start that has not reached its first lap counts as hung is 600 s."""
    # Long enough for a slow host to load its plugins, short enough to report a hung start.
    self.assertEqual(orchestrator_module.MLSTOP_STARTUP_GRACE, 600)


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()
