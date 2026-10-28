"""Exercise production queue admission without importing the ML runtime."""

import ast
from collections import deque
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock


def _load_queue_methods(clock):
  """Load the production methods using the memory-scan regression pattern."""
  path = Path(__file__).resolve().parents[1] / "base" / "base_plugin_biz.py"
  source = ast.parse(path.read_text())
  plugin = next(node for node in source.body if isinstance(node, ast.ClassDef)
                and node.name == "BasePluginExecutor")
  methods = [node for node in plugin.body if isinstance(node, ast.FunctionDef)
             and node.name in {"add_inputs", "add_to_inputs_deque",
                               "upstream_inputs_deque", "input_queue_size",
                               "is_queue_overflown"}]
  harness = ast.ClassDef(name=plugin.name, bases=[], keywords=[],
                        body=methods, decorator_list=[])
  module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
  namespace = {"time": clock, "deepcopy": deepcopy}
  exec(compile(module, str(path), "exec"), namespace)
  return namespace[plugin.name]()


class TestPluginQueueOverflow(unittest.TestCase):
  def setUp(self):
    self.clock = Mock(return_value=1000)
    self.plugin = plugin = _load_queue_methods(self.clock)
    plugin._BasePluginExecutor__upstream_inputs_deque = deque(maxlen=2)
    plugin.is_outside_working_hours = False
    plugin.cfg_copy_in_main_thread = False
    plugin.cfg_max_inputs_queue_size = 2
    plugin.cfg_plugin_loop_resolution = 20
    plugin.actual_plugin_resolution = 1
    plugin.get_plugin_loop_resolution = Mock(return_value=1)
    plugin.queue_full_warning = False
    plugin.queue_full_delays_count = 0
    plugin._last_logged_queue_full_count = 0
    plugin._last_logged_queue_full_time = 999
    plugin.lost_inputs_count = 0
    plugin.P = Mock()
    plugin._create_abnormal_notification = Mock()
    plugin.get_plugin_queue_memory = Mock(side_effect=AssertionError("Queue scanned"))
    plugin.get_plugin_used_memory = Mock(side_effect=AssertionError("Plugin scanned"))

  def test_overflow_retains_latest_inputs_without_memory_scans(self):
    plugin = self.plugin

    for value in range(5):
      plugin.add_inputs({"value": value})

    self.assertEqual(list(plugin.upstream_inputs_deque), [{"value": 3}, {"value": 4}])
    self.assertEqual(plugin.lost_inputs_count, 3)
    self.assertEqual(plugin.queue_full_delays_count, 3)
    plugin.P.assert_called_once()
    plugin._create_abnormal_notification.assert_called_once()
    plugin.get_plugin_queue_memory.assert_not_called()
    plugin.get_plugin_used_memory.assert_not_called()

  def test_warning_is_throttled_and_resumes_after_one_minute(self):
    plugin = self.plugin
    plugin.upstream_inputs_deque.extend([{"value": 0}, {"value": 1}])
    plugin.add_inputs({"value": 2})

    self.clock.return_value = 1060
    plugin.add_inputs({"value": 3})
    plugin._create_abnormal_notification.assert_called_once()

    self.clock.return_value = 1061
    plugin.add_inputs({"value": 4})
    plugin.add_inputs({"value": 5})
    self.assertEqual(plugin._create_abnormal_notification.call_count, 2)
    self.assertEqual(plugin.P.call_count, 2)
    self.assertEqual(plugin.lost_inputs_count, 4)

  def test_single_slot_queue_does_not_report_overflow(self):
    plugin = self.plugin
    plugin.cfg_max_inputs_queue_size = 1
    plugin._BasePluginExecutor__upstream_inputs_deque = deque(maxlen=1)

    plugin.add_inputs({"value": 0})
    plugin.add_inputs({"value": 1})

    self.assertEqual(list(plugin.upstream_inputs_deque), [{"value": 1}])
    self.assertEqual(plugin.lost_inputs_count, 0)
    plugin.P.assert_not_called()
    plugin._create_abnormal_notification.assert_not_called()


if __name__ == "__main__":
  unittest.main()
