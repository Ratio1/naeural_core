"""Deterministic real-loop regressions without runtime, network or GPU imports."""

import ast
import pathlib
import sys
import types
import unittest
from collections import deque
from threading import RLock
from unittest import mock


_BASE_PATH = pathlib.Path(__file__).resolve().parents[1] / "base"


def _load_wait_code():
  """Load the loop and exact scheduling methods with isolated dependencies.

  Returns
  -------
  tuple
      Loop module, scheduling namespace and original scheduling class.

  Notes
  -----
  Only the unrelated imports/inheritance of BasePluginExecutor are omitted.
  Keeping its class name preserves private-attribute mangling. Missing methods
  fail loudly rather than silently falling back to copied scheduling logic.
  """
  constants = types.SimpleNamespace(
    PLUGIN_EXCEPTION_DELAY=2,
    PLUGIN_END_PREFIX="END: ",
    NOTIFICATION_CODES=types.SimpleNamespace(
      PLUGIN_CONFIG_OK="config_ok", PLUGIN_DELAYED="delayed",
      PLUGIN_INSTANCE_COMMAND_OK="command_ok",
      PLUGIN_INSTANCE_COMMAND_FAILED="command_failed",
      PLUGIN_PAUSE_OK="pause_ok", PLUGIN_RESUME_OK="resume_ok",
    ),
    STATUS_TYPE=types.SimpleNamespace(
      STATUS_NORMAL="normal", STATUS_EXCEPTION="exception",
    ),
  )
  package = types.ModuleType("naeural_core")
  package.constants = constants
  numpy = types.ModuleType("numpy")
  loop = types.ModuleType("plugin_loop_waits_under_test")
  loop_path = _BASE_PATH / "base_plugin_biz_loop.py"
  # Restore every pre-existing module immediately after import: discovery of
  # other business tests must never inherit these lightweight dependency stubs.
  with mock.patch.dict(sys.modules, {"naeural_core": package, "numpy": numpy}):
    exec(compile(loop_path.read_text(), str(loop_path), "exec"), loop.__dict__)

  biz_path = _BASE_PATH / "base_plugin_biz.py"
  tree = ast.parse(biz_path.read_text(), filename=str(biz_path))
  plugin_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "BasePluginExecutor")
  names = {
    "__maybe_reconfigure_process_delay", "is_process_postponed",
    "last_process_time", "time_from_last_process", "_reset_last_process_time",
    "is_plugin_temporary_stopped", "__is_plugin_temporary_stopped",
    "should_pause", "should_resume", "on_pause", "on_resume",
  }
  methods = [node for node in plugin_class.body
             if isinstance(node, ast.FunctionDef) and node.name in names]
  assert {node.name for node in methods} == names, "Scheduling methods changed"
  plugin_class.bases = []
  plugin_class.keywords = []
  plugin_class.decorator_list = []
  plugin_class.body = methods
  namespace = {"time": loop.time, "ct": constants}
  exec(compile(ast.Module(body=[plugin_class], type_ignores=[]), str(biz_path), "exec"), namespace)
  return loop, namespace, namespace["BasePluginExecutor"]


_LOOP, _SCHEDULING_GLOBALS, _Scheduling = _load_wait_code()


class _Plugin(_LOOP._BasePluginLoopMixin, _Scheduling):
  """Supply lifecycle collaborators while retaining real loop and delay logic."""

  def __init__(self):
    super().__init__()
    self.events = []
    self.now = 10.0
    self._stop_requested = False
    self._loop_checks = 0
    self.cfg_disabled = False
    self.cfg_dataset_builder = False
    self.cfg_process_delay = 0
    self.cfg_max_process_delay = None
    self.cfg_forced_loop_sleep = None
    self.cfg_instance_command = "PING"
    self.cfg_instance_command_log = False
    self.cfg_simulate_crash_loop = False
    self.cfg_forced_pause = False
    self.cfg_ignore_working_hours = False
    self._was_stopped_last_iter = False
    self._pause_transition_in_progress = False
    self._pause_transition_incomplete = False
    self._plugin_lifecycle_lock = RLock()
    self.outside_working_hours = False
    self.resolution = 100
    self.work_duration = 0
    self.fail_init = False
    self.fail_execute = False
    self.fail_close = False
    self.instance_hash = "wait-test"
    self.commands_deque = deque()
    self.close_commands = ["close-command"]
    self._BasePluginExecutor__last_process_time = None
    self.log = types.SimpleNamespace(
      config_data={"PLUGINS_DEBUG_LOAD_TIMINGS": False},
      get_error_info=lambda: "test failure",
    )

  @property
  def done_loop(self):
    # One iteration is enough for each assertion. This also bounds baseline
    # zero/negative forced sleeps, which can skip sleep entirely.
    self._loop_checks += 1
    return self._stop_requested or self._loop_checks > 1

  def P(self, *_args, **_kwargs):
    pass

  def _create_notification(self, **_kwargs):
    pass

  def _create_error_notification(self, **_kwargs):
    pass

  def add_payload_by_fields(self, **_kwargs):
    pass

  def start_timer(self, _name):
    pass

  def end_timer(self, _name):
    pass

  def _on_init(self):
    self.events.append("init")
    if self.fail_init:
      raise RuntimeError("init failure")

  def archive_config_keys(self, **_kwargs):
    self.events.append("archive")
    self.cfg_instance_command = None

  def _on_command(self, _command, **_kwargs):
    self.events.append("command")

  def execute(self):
    self.events.append("execute")
    self.now += self.work_duration
    if self.fail_execute:
      raise RuntimeError("loop failure")

  def get_plugin_loop_resolution(self):
    return self.resolution

  def _on_close(self):
    self.events.append("close")
    if self.fail_close:
      raise RuntimeError("close failure")

  def get_commands_after_exec(self):
    self.events.append("drain")
    return self.close_commands


class TestPluginLoopWaits(unittest.TestCase):
  def _run(self, plugin):
    """Run one bounded iteration with a shared fake wall clock and sleep.

    Parameters
    ----------
    plugin : _Plugin
        Harness with the scheduling state for this scenario.

    Returns
    -------
    list of float
        Requested waits; no real sleeping or background thread is used.
    """
    sleeps = []

    def fake_sleep(seconds):
      sleeps.append(seconds)
      plugin.events.append("sleep")
      plugin.now += seconds
      plugin._stop_requested = True

    with mock.patch.object(_LOOP, "time", lambda: plugin.now), \
         mock.patch.object(_LOOP, "sleep", fake_sleep), \
         mock.patch.dict(_SCHEDULING_GLOBALS, {"time": lambda: plugin.now}):
      plugin.plugin_loop()
    self.assertEqual(plugin.events[-2:], ["close", "drain"])
    self.assertEqual(list(plugin.commands_deque), [plugin.close_commands])
    self.assertFalse(getattr(plugin, "_plugin_loop_in_exec", False))
    return sleeps

  def test_normal_wait_floor_covers_zero_negative_and_short_forced_sleeps(self):
    """Active and over-budget iterations must still yield at least 20 ms."""
    for delay in (0, -1):
      for forced_sleep in (None, -1, 0, 0.001, 0.019):
        for work_duration in (0, 0.1):
          with self.subTest(delay=delay, forced=forced_sleep, work=work_duration):
            plugin = _Plugin()
            plugin.cfg_process_delay = delay
            plugin._BasePluginLoopMixin__current_iteration = 1
            plugin._BasePluginExecutor__last_process_time = plugin.now
            plugin.cfg_forced_loop_sleep = forced_sleep
            plugin.work_duration = work_duration
            self.assertEqual(self._run(plugin), [0.020])
            self.assertIn("execute", plugin.events)

  def test_longer_normal_forced_and_error_waits_are_preserved(self):
    for mode, expected in (("normal", 0.5), ("cadence", 0.04), ("forced", 0.75), ("error", 4)):
      with self.subTest(mode=mode):
        plugin = _Plugin()
        if mode == "normal":
          plugin.resolution = 2
        elif mode == "cadence":
          plugin.resolution = 20
          plugin.work_duration = 0.01
        elif mode == "forced":
          plugin.cfg_forced_loop_sleep = expected
        else:
          plugin.has_exec_error = True
          plugin._BasePluginLoopMixin__plugin_exec_errors = 2
          plugin._BasePluginLoopMixin__post_error_ok_count = 5
        sleeps = self._run(plugin)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], expected)

  def test_configuration_pause_suppresses_commands_and_processing(self):
    plugin = _Plugin()
    plugin.pause_loop()
    self.assertEqual(self._run(plugin), [0.020])
    self.assertEqual(plugin.events, ["init", "sleep", "close", "drain"])

  def test_postponed_wait_clamps_actual_remaining_delay(self):
    """The real postponement property, not positive config alone, gates work."""
    for remaining, expected in ((0.001, 0.020), (0.125, 0.125), (0.250, 0.250), (2, 0.250)):
      with self.subTest(remaining=remaining):
        plugin = _Plugin()
        plugin.cfg_process_delay = 4
        plugin._BasePluginLoopMixin__current_iteration = 1
        plugin._BasePluginExecutor__last_process_time = plugin.now - (4 - remaining)
        self.assertAlmostEqual(self._run(plugin)[0], expected)
        self.assertEqual(plugin.events, ["init", "archive", "command", "sleep", "close", "drain"])

  def test_positive_delay_executes_when_due_or_never_processed(self):
    for elapsed, iteration in ((4, 1), (5, 1), (None, 1), (0, 0)):
      with self.subTest(elapsed=elapsed, iteration=iteration):
        plugin = _Plugin()
        plugin.cfg_process_delay = 4
        plugin._BasePluginLoopMixin__current_iteration = iteration
        if elapsed is not None:
          plugin._BasePluginExecutor__last_process_time = plugin.now - elapsed
        self.assertEqual(self._run(plugin), [0.020])
        self.assertLess(plugin.events.index("command"), plugin.events.index("execute"))

  def test_temporary_pause_and_working_hours_keep_command_order(self):
    for gate in ("temporary", "forced", "outside_working_hours"):
      with self.subTest(gate=gate):
        plugin = _Plugin()
        if gate == "temporary":
          plugin.should_pause = lambda: True
        elif gate == "forced":
          plugin.cfg_forced_pause = True
        else:
          plugin.outside_working_hours = True
        self.assertEqual(self._run(plugin), [0.250])
        self.assertEqual(plugin.events, ["init", "archive", "command", "sleep", "close", "drain"])

  def test_loop_error_backoff_and_cleanup_remain_safe(self):
    plugin = _Plugin()
    plugin.fail_execute = True
    self.assertEqual(self._run(plugin), [10])
    self.assertTrue(plugin.has_loop_error)

    for failure in ("fail_init", "fail_close", "stopped"):
      with self.subTest(failure=failure):
        plugin = _Plugin()
        if failure == "stopped":
          plugin._stop_requested = True
        else:
          setattr(plugin, failure, True)
        self.assertEqual(self._run(plugin), [0.020] if failure == "fail_close" else [])


if __name__ == "__main__":
  unittest.main()
