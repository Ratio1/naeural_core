import sys
import threading
from types import ModuleType
import unittest

from test_modified_by_propagation import (
  _load_base_plugin_biz_module,
  _make_base_plugin,
)


class PluginPauseHookTests(unittest.TestCase):
  def _make_plugin(self):
    previous_numpy = sys.modules.get("numpy")
    sys.modules["numpy"] = ModuleType("numpy")
    try:
      module = _load_base_plugin_biz_module()
    finally:
      if previous_numpy is None:
        sys.modules.pop("numpy", None)
      else:
        sys.modules["numpy"] = previous_numpy
    module.ct.NOTIFICATION_CODES.PLUGIN_PAUSE_OK = "PLUGIN_PAUSE_OK"
    module.ct.NOTIFICATION_CODES.PLUGIN_RESUME_OK = "PLUGIN_RESUME_OK"
    module.ct.NON_ACTIONABLE_INSTANCE_CONFIG_KEYS = []
    plugin = _make_base_plugin(module)
    plugin.ct = module.ct
    plugin.cfg_forced_pause = False
    plugin.cfg_disabled = False
    plugin.cfg_ignore_working_hours = False
    plugin._was_stopped_last_iter = False
    plugin._pause_transition_in_progress = False
    plugin.pause_events = []
    plugin.messages = []
    plugin.notifications = []
    plugin.payloads = []
    plugin.P = lambda msg, *_args, **_kwargs: plugin.messages.append(msg)
    plugin._create_notification = lambda **kwargs: plugin.notifications.append(kwargs)
    plugin.add_payload_by_fields = lambda **kwargs: plugin.payloads.append(kwargs)
    plugin.on_pause = lambda: plugin.pause_events.append("pause")
    plugin.on_resume = lambda: plugin.pause_events.append("resume")
    return plugin

  def test_default_hooks_preserve_config_pause_and_emit_each_edge_once(self):
    plugin = self._make_plugin()

    self.assertFalse(plugin.is_plugin_temporary_stopped)

    plugin.cfg_forced_pause = True
    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertTrue(plugin.is_plugin_temporary_stopped)

    plugin.cfg_forced_pause = False
    self.assertFalse(plugin.is_plugin_temporary_stopped)
    self.assertFalse(plugin.is_plugin_temporary_stopped)

    self.assertEqual(plugin.pause_events, ["pause", "resume"])
    self.assertEqual(
      [item["notif_code"] for item in plugin.notifications],
      ["PLUGIN_PAUSE_OK", "PLUGIN_RESUME_OK"],
    )
    self.assertEqual(len(plugin.payloads), 2)

  def test_custom_hooks_control_pause_and_resume_independently(self):
    plugin = self._make_plugin()
    should_pause = False
    should_resume = False
    checks = []

    def pause_check():
      checks.append("pause")
      return should_pause

    def resume_check():
      checks.append("resume")
      return should_resume

    plugin.should_pause = pause_check
    plugin.should_resume = resume_check

    self.assertFalse(plugin.is_plugin_temporary_stopped)
    should_pause = True
    self.assertTrue(plugin.is_plugin_temporary_stopped)

    should_pause = False
    self.assertTrue(plugin.is_plugin_temporary_stopped)
    should_resume = True
    self.assertFalse(plugin.is_plugin_temporary_stopped)

    self.assertEqual(plugin.pause_events, ["pause", "resume"])
    self.assertEqual(checks, ["pause", "pause", "resume", "resume"])

  def test_operator_pause_cannot_be_bypassed_by_resume_hook(self):
    plugin = self._make_plugin()
    plugin.should_resume = lambda: True
    plugin.cfg_disabled = True

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertTrue(plugin.is_plugin_temporary_stopped)

    plugin.cfg_disabled = False
    self.assertFalse(plugin.is_plugin_temporary_stopped)
    self.assertEqual(plugin.pause_events, ["pause", "resume"])

  def test_pause_callback_failure_keeps_execution_stopped_until_retry_succeeds(self):
    plugin = self._make_plugin()
    callback_calls = []
    should_pause = True
    should_resume = False
    resources = {"server": True, "tunnel": True}

    def on_pause():
      nonlocal should_pause
      callback_calls.append("pause")
      resources["server"] = False
      should_pause = False
      if len(callback_calls) == 1:
        raise RuntimeError("pause callback failed")
      resources["tunnel"] = False

    def on_resume():
      plugin.pause_events.append("resume")
      resources.update(server=True, tunnel=True)

    plugin.on_pause = on_pause
    plugin.on_resume = on_resume
    plugin.should_pause = lambda: should_pause
    plugin.should_resume = lambda: should_resume

    with self.assertRaisesRegex(RuntimeError, "pause callback failed"):
      plugin.is_plugin_temporary_stopped

    self.assertFalse(plugin._pause_transition_in_progress)
    self.assertTrue(plugin._pause_transition_incomplete)
    self.assertEqual(resources, {"server": False, "tunnel": True})
    self.assertEqual(plugin.notifications, [])
    self.assertEqual(plugin.payloads, [])

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertFalse(plugin._pause_transition_incomplete)
    self.assertEqual(resources, {"server": False, "tunnel": False})
    self.assertEqual(callback_calls, ["pause", "pause"])
    self.assertEqual(len(plugin.notifications), 1)
    self.assertEqual(len(plugin.payloads), 1)

    should_resume = True
    self.assertFalse(plugin.is_plugin_temporary_stopped)
    self.assertEqual(plugin.pause_events, ["resume"])
    self.assertEqual(resources, {"server": True, "tunnel": True})

  def test_config_update_defers_without_waiting_for_pause_callback(self):
    plugin = self._make_plugin()
    plugin.cfg_instance_id = "instance"
    plugin.loop_paused = False
    plugin.time = lambda: 0
    plugin._BasePluginExecutor__set_loop_stage = lambda **_kwargs: None
    plugin._update_instance_config = lambda: None
    plugin.reset_exec_counter_after_config = lambda: None
    callback_entered = threading.Event()
    callback_release = threading.Event()
    config_entered = threading.Event()
    config_returned = threading.Event()
    errors = []

    plugin.should_pause = lambda: True

    def on_pause():
      callback_entered.set()
      callback_release.wait(timeout=2)

    def update_config():
      try:
        plugin.maybe_update_instance_config({"VALUE": 1})
      except Exception as exc:
        errors.append(exc)
      finally:
        config_returned.set()

    plugin.on_pause = on_pause
    plugin._BasePluginExecutor__on_config = config_entered.set
    pause_thread = threading.Thread(
      target=lambda: plugin.is_plugin_temporary_stopped
    )
    config_thread = threading.Thread(target=update_config)

    pause_thread.start()
    self.assertTrue(callback_entered.wait(timeout=1))
    config_thread.start()
    try:
      self.assertTrue(config_returned.wait(timeout=1))
      self.assertFalse(config_entered.is_set())
      self.assertEqual(plugin._upstream_config, {})
    finally:
      callback_release.set()
      pause_thread.join(timeout=1)
      config_thread.join(timeout=1)

    self.assertFalse(pause_thread.is_alive())
    self.assertFalse(config_thread.is_alive())
    self.assertEqual(errors, [])
    self.assertFalse(plugin.loop_paused)
    plugin.maybe_update_instance_config({"VALUE": 1})
    self.assertTrue(config_entered.is_set())
    self.assertEqual(plugin._upstream_config, {"VALUE": 1})

  def test_working_hours_conflict_only_reported_for_forced_pause(self):
    plugin = self._make_plugin()
    plugin.cfg_ignore_working_hours = True
    plugin.should_pause = lambda: True

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertFalse(any("IGNORE_WORKING_HOURS" in msg for msg in plugin.messages))

    plugin = self._make_plugin()
    plugin.cfg_ignore_working_hours = True
    plugin.cfg_forced_pause = True

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertTrue(any("IGNORE_WORKING_HOURS" in msg for msg in plugin.messages))

  def test_resume_callback_failure_keeps_plugin_paused_until_retry_succeeds(self):
    plugin = self._make_plugin()
    plugin.should_pause = lambda: True
    plugin.should_resume = lambda: False
    self.assertTrue(plugin.is_plugin_temporary_stopped)

    callback_calls = []

    def on_resume():
      callback_calls.append("resume")
      raise RuntimeError("resume callback failed")

    plugin.on_resume = on_resume
    plugin.should_resume = lambda: True
    with self.assertRaisesRegex(RuntimeError, "resume callback failed"):
      plugin.is_plugin_temporary_stopped

    self.assertTrue(plugin._was_stopped_last_iter)
    self.assertFalse(plugin._pause_transition_in_progress)
    self.assertEqual(len(plugin.notifications), 1)
    plugin.on_resume = lambda: callback_calls.append("resume")
    self.assertFalse(plugin.is_plugin_temporary_stopped)
    self.assertEqual(callback_calls, ["resume", "resume"])
    self.assertEqual(
      [item["notif_code"] for item in plugin.notifications],
      ["PLUGIN_PAUSE_OK", "PLUGIN_RESUME_OK"],
    )

  def test_reentrant_resume_check_stays_paused_without_repeating_transition(self):
    plugin = self._make_plugin()
    plugin.should_pause = lambda: True
    plugin.should_resume = lambda: False
    self.assertTrue(plugin.is_plugin_temporary_stopped)

    nested_states = []
    callback_calls = []

    def on_resume():
      callback_calls.append("resume")
      nested_states.append(plugin.is_plugin_temporary_stopped)

    plugin.on_resume = on_resume
    plugin.should_resume = lambda: True

    self.assertFalse(plugin.is_plugin_temporary_stopped)
    self.assertEqual(nested_states, [True])
    self.assertEqual(callback_calls, ["resume"])
    self.assertEqual(
      [item["notif_code"] for item in plugin.notifications],
      ["PLUGIN_PAUSE_OK", "PLUGIN_RESUME_OK"],
    )

  def test_reentrant_pause_check_cannot_trigger_resume(self):
    plugin = self._make_plugin()
    plugin.should_pause = lambda: True
    plugin.should_resume = lambda: True
    nested_states = []
    callback_calls = []

    def on_pause():
      callback_calls.append("pause")
      nested_states.append(plugin.is_plugin_temporary_stopped)

    plugin.on_pause = on_pause
    plugin.on_resume = lambda: callback_calls.append("resume")

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertEqual(nested_states, [True])
    self.assertEqual(callback_calls, ["pause"])
    self.assertEqual(
      [item["notif_code"] for item in plugin.notifications],
      ["PLUGIN_PAUSE_OK"],
    )

  def test_initially_disabled_plugin_initializes_before_resume_hooks(self):
    plugin = self._make_plugin()
    plugin._init_process_finalized = False
    plugin.cfg_disabled = True
    self.assertTrue(plugin.is_plugin_temporary_stopped)

    plugin.cfg_instance_id = "instance"
    plugin.loop_paused = False
    plugin.time = lambda: 0
    plugin._BasePluginExecutor__set_loop_stage = lambda **_kwargs: None
    plugin._on_config = lambda: None
    plugin.reset_exec_counter_after_config = lambda: None
    disabled_cleared = threading.Event()
    finish_config = threading.Event()
    state_started = threading.Event()
    states = []

    def update_instance_config():
      plugin.cfg_disabled = False
      disabled_cleared.set()
      finish_config.wait(timeout=2)

    def initialize():
      plugin.resume_ready = True

    def check_state():
      state_started.set()
      states.append(plugin.is_plugin_temporary_stopped)

    plugin._update_instance_config = update_instance_config
    plugin._on_init = initialize
    plugin.should_resume = lambda: plugin.resume_ready
    config_thread = threading.Thread(
      target=lambda: plugin.maybe_update_instance_config({"DISABLED": False})
    )
    state_thread = threading.Thread(target=check_state)

    config_thread.start()
    self.assertTrue(disabled_cleared.wait(timeout=1))
    state_thread.start()
    self.assertTrue(state_started.wait(timeout=1))
    try:
      state_thread.join(timeout=0.1)
      self.assertTrue(state_thread.is_alive())
    finally:
      finish_config.set()
      config_thread.join(timeout=1)
      state_thread.join(timeout=1)

    self.assertFalse(config_thread.is_alive())
    self.assertFalse(state_thread.is_alive())
    self.assertTrue(plugin._init_process_finalized)
    self.assertEqual(states, [False])
    self.assertEqual(plugin.pause_events, ["pause", "resume"])


if __name__ == "__main__":
  unittest.main()
