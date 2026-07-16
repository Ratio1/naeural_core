import sys
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
    plugin = _make_base_plugin(module)
    plugin.cfg_forced_pause = False
    plugin.cfg_disabled = False
    plugin.cfg_ignore_working_hours = False
    plugin._was_stopped_last_iter = False
    plugin._pause_transition_in_progress = False
    plugin.pause_events = []
    plugin.notifications = []
    plugin.payloads = []
    plugin.P = lambda *_args, **_kwargs: None
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

  def test_callback_failure_does_not_replay_transition(self):
    plugin = self._make_plugin()
    callback_calls = []

    def on_pause():
      callback_calls.append("pause")
      raise RuntimeError("pause callback failed")

    plugin.on_pause = on_pause
    plugin.should_pause = lambda: True
    plugin.should_resume = lambda: False

    with self.assertRaisesRegex(RuntimeError, "pause callback failed"):
      plugin.is_plugin_temporary_stopped

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertFalse(plugin._pause_transition_in_progress)
    self.assertEqual(callback_calls, ["pause"])

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

  def test_initial_disabled_state_calls_pause_before_initialization(self):
    plugin = self._make_plugin()
    plugin._init_process_finalized = False
    plugin.cfg_disabled = True

    self.assertTrue(plugin.is_plugin_temporary_stopped)
    self.assertEqual(plugin.pause_events, ["pause"])


if __name__ == "__main__":
  unittest.main()
