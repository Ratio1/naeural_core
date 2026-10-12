"""Regression tests for plugin memory scan boundaries using the real SDK scanner."""

import ast
from collections import deque
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

from ratio1.logging.logger_mixins.utils_mixin import _UtilsMixin


def _load_diagnostic_methods():
  """Execute the real diagnostic methods without importing the full ML runtime.

  Returns
  -------
  type
    Minimal class retaining the production defining class name and method bodies.
  """
  path = Path(__file__).resolve().parents[1] / "base" / "base_plugin_biz.py"
  source = ast.parse(path.read_text())
  plugin = next(
    node for node in source.body
    if isinstance(node, ast.ClassDef) and node.name == "BasePluginExecutor"
  )
  methods = [
    node for node in plugin.body
    if isinstance(node, ast.FunctionDef) and node.name in {
      "get_plugin_used_memory", "get_plugin_queue_memory",
    }
  ]
  harness = ast.ClassDef(
    name=plugin.name, bases=[], keywords=[], body=methods, decorator_list=[],
  )
  module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
  namespace = {}
  exec(compile(module, str(path), "exec"), namespace)
  return namespace[plugin.name]


BasePluginExecutor = _load_diagnostic_methods()
BasePluginExecutor.upstream_inputs_deque = property(
  lambda self: self._BasePluginExecutor__upstream_inputs_deque
)


class _MemoryLogger:
  """Supply only logging/locking hooks required by the SDK's unmodified scanner."""

  get_obj_size = _UtilsMixin.get_obj_size

  def __init__(self):
    self.events = []

  def managed_lock_resource(self, name):
    return nullcontext()

  def P(self, *args, **kwargs):
    return


class _ForbiddenScan:
  """Fail immediately if the scanner visits a shared infrastructure object."""

  def __sizeof__(self):
    raise AssertionError("Shared infrastructure was visited")


def _make_plugin(cls=BasePluginExecutor):
  plugin = cls()
  plugin.log = _MemoryLogger()
  plugin.start_timer = lambda name: plugin.log.events.append(("start", name))
  plugin.end_timer = lambda name: plugin.log.events.append(("end", name))
  plugin._BasePluginExecutor__upstream_inputs_deque = deque(maxlen=2)
  plugin.owned_state = {"buffer": bytearray(1024)}
  return plugin


class TestPluginMemoryScan(unittest.TestCase):
  """Check traversal boundaries and retained plugin-state accounting."""

  def test_mangled_shared_memory_does_not_reach_manager_or_cached_module(self):
    plugin = _make_plugin()
    cached_module = ModuleType("memory_scan_fixture")
    cached_module.large_buffer = bytearray(1024 * 1024)
    manager = SimpleNamespace(plugin_locations_cache={"fixture": cached_module})
    plugin._BasePluginExecutor__global_shmem = {"business_manager": manager}

    size, tree, top = plugin.get_plugin_used_memory(return_tree=True)

    self.assertGreater(size, 1024)
    self.assertIsInstance(top, list)
    self.assertFalse(any("__global_shmem" in row["NAME"] for row in tree))
    self.assertFalse(any(row["CLASS_NAME"] == "module" for row in tree))
    self.assertTrue(any("owned_state" in row["NAME"] for row in tree))

  def test_each_shared_reference_is_excluded_independently(self):
    attributes = (
      "_BasePluginExecutor__global_shmem",
      "_BasePluginExecutor__plugins_shmem",
      "_BasePluginExecutor__blockchain_manager",
      "_BasePluginExecutor__bc",
      "_BasePluginExecutor__r1fs",
      "_file_system_manager",
      "plugins_shared_mem",
    )
    for attribute in attributes:
      with self.subTest(attribute=attribute):
        plugin = _make_plugin()
        setattr(plugin, attribute, _ForbiddenScan())
        self.assertIsInstance(plugin.get_plugin_used_memory(), int)

  def test_growing_shared_state_does_not_change_plugin_estimate(self):
    plugin = _make_plugin()
    shared = {"other_plugin": bytearray(1024)}
    plugin._BasePluginExecutor__global_shmem = shared
    before = plugin.get_plugin_used_memory()

    shared["other_plugin"] = bytearray(8 * 1024 * 1024)

    self.assertEqual(plugin.get_plugin_used_memory(), before)

  def test_shared_memory_facade_cannot_reenter_other_plugin_state(self):
    plugin = _make_plugin()
    shared = {"other_plugin": bytearray(1024)}
    plugin._BasePluginExecutor__plugins_shmem = shared
    plugin.plugins_shared_mem = SimpleNamespace(_shm=shared)
    before = plugin.get_plugin_used_memory()

    shared["other_plugin"] = bytearray(1024 * 1024)

    self.assertEqual(plugin.get_plugin_used_memory(), before)

  def test_growing_owned_state_remains_counted(self):
    plugin = _make_plugin()
    before = plugin.get_plugin_used_memory()

    plugin.owned_state["buffer"] = bytearray(2 * 1024 * 1024)

    self.assertGreaterEqual(plugin.get_plugin_used_memory() - before, 2 * 1024 * 1024 - 1024)

  def test_similarly_named_owned_attributes_remain_counted(self):
    plugin = _make_plugin()
    before = plugin.get_plugin_used_memory()

    plugin.global_shmem_snapshot = bytearray(1024 * 1024)
    plugin._DerivedPlugin__global_shmem_statistics = bytearray(1024 * 1024)

    self.assertGreaterEqual(plugin.get_plugin_used_memory() - before, 2 * 1024 * 1024)

  def test_inherited_plugin_uses_base_class_private_boundary(self):
    class DerivedPlugin(BasePluginExecutor):
      pass

    plugin = _make_plugin(DerivedPlugin)
    plugin._BasePluginExecutor__global_shmem = _ForbiddenScan()

    self.assertIsInstance(plugin.get_plugin_used_memory(), int)

  def test_input_queue_is_separate_from_plugin_estimate(self):
    plugin = _make_plugin()
    before_plugin = plugin.get_plugin_used_memory()
    before_queue = plugin.get_plugin_queue_memory()

    plugin.upstream_inputs_deque.append({"data": bytearray(1024 * 1024)})

    self.assertEqual(plugin.get_plugin_used_memory(), before_plugin)
    self.assertGreaterEqual(plugin.get_plugin_queue_memory() - before_queue, 1024 * 1024)

  def test_existing_infrastructure_exclusions_are_retained(self):
    plugin = _make_plugin()
    for attribute in ("_painter", "thread", "owner", "global_shmem"):
      setattr(plugin, attribute, _ForbiddenScan())
    plugin.log.shared_runtime = _ForbiddenScan()

    self.assertIsInstance(plugin.get_plugin_used_memory(), int)

  def test_scalar_and_tree_contracts_and_timer_pairing(self):
    plugin = _make_plugin()

    scalar = plugin.get_plugin_used_memory()
    result = plugin.get_plugin_used_memory(return_tree=True)

    self.assertIsInstance(scalar, int)
    self.assertEqual(len(result), 3)
    self.assertEqual(result[0], scalar)
    self.assertEqual(plugin.log.events, [
      ("start", "get_plugin_memory"), ("end", "get_plugin_memory"),
      ("start", "get_plugin_memory"), ("end", "get_plugin_memory"),
    ])

  def test_scanner_failure_closes_timer_and_preserves_exception(self):
    plugin = _make_plugin()
    plugin.log.get_obj_size = Mock(side_effect=RuntimeError("scanner failed"))

    with self.assertRaisesRegex(RuntimeError, "scanner failed"):
      plugin.get_plugin_used_memory()

    self.assertEqual(plugin.log.events, [
      ("start", "get_plugin_memory"), ("end", "get_plugin_memory"),
    ])


if __name__ == "__main__":
  unittest.main()
