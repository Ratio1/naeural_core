"""No import re-seeds the node's random generator, and a supervisor logs its forced-restart offset at every start."""

# The epoch end the update monitor compares the restart time with.
import datetime
# The update monitor is loaded from its file, without the node runtime.
import importlib.util
# The path of the interpreter and the table of loaded modules.
import sys
# A fresh interpreter for the import check.
import subprocess
# The test framework.
import unittest
# The tree's root and the plugin's file.
from pathlib import Path
# Stand-ins for the plugin's base module and collaborators.
from types import ModuleType, SimpleNamespace
# The plugin's log method is recorded, and the base module is replaced while the plugin loads.
from unittest.mock import Mock, patch

# The package folder, parent of utils/ and business/.
_PACKAGE = Path(__file__).resolve().parents[2]


def _load_update_monitor():
  """
  Load the update monitor plugin class from its file, with an empty stand-in for its base class.

  Returns
  -------
  type
    ``UpdateMonitor01Plugin``, whose instances need no node runtime.

  Examples
  --------
  >>> plugin = _load_update_monitor()()
  """
  # The stand-in base module: the plugin only needs a base class with a CONFIG.
  base = ModuleType("naeural_core.business.base")
  # An empty base class.
  base.BasePluginExecutor = type("BasePluginExecutor", (), {"CONFIG": {"VALIDATION_RULES": {}}})
  # The plugin's source file.
  path = _PACKAGE / "business" / "default" / "admin" / "update_monitor_01.py"
  # A module object for it, under a name of its own.
  spec = importlib.util.spec_from_file_location("update_monitor_offset_under_test", path)
  # The module, not yet executed.
  module = importlib.util.module_from_spec(spec)
  # Its import of the base module finds the stand-in.
  with patch.dict(sys.modules, {"naeural_core.business.base": base}):
    # Run the module's code.
    spec.loader.exec_module(module)
  # The plugin class.
  return module.UpdateMonitor01Plugin


class TestNoSeedAtImport(unittest.TestCase):

  def test_importing_the_tracker_module_does_not_seed_the_global_generator(self):
    """A fresh interpreter that imports naeural_core.utils.sort makes no np.random.seed() call."""
    # In the new interpreter: count every seeding of numpy's global generator, import the module that every
    # plugin load imports, and print the count.
    code = (
      "import numpy as np\n"
      "calls = []\n"
      "np.random.seed = lambda *args, **kwargs: calls.append(args)\n"
      "import naeural_core.utils.sort\n"
      "print('SEED_CALLS', len(calls))\n"
    )
    # A fresh interpreter: in this one the module may already be imported.
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=_PACKAGE.parent)
    # The import worked.
    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
    # No seeding: the generator keeps the seed the process took from the operating system, so two nodes draw
    # different numbers (a seed of 0 here gave every node the same forced-restart offset and the same ports).
    self.assertIn("SEED_CALLS 0", result.stdout)


class TestSupervisorRestartOffsetIsLogged(unittest.TestCase):

  # Seconds into a day of the epoch end the tests use: 16:00 UTC.
  EPOCH_END = 16 * 3600
  # The random part of the offset in these tests.
  DRAW = 1000
  # The offset before any shift: 36 hours plus the random part.
  BASE_OFFSET = 36 * 3600 + DRAW

  def plugin(self, supervisor, now):
    """
    Build an update monitor whose random draw, clock and epoch end are fixed.

    Parameters
    ----------
    supervisor : bool
      Whether the node is a supervisor (only a supervisor configures the offset).
    now : int
      The plugin's current time, in seconds.

    Returns
    -------
    object
      The plugin, with ``P`` replaced by a recorder.

    Examples
    --------
    >>> plugin = self.plugin(supervisor=True, now=0)
    """
    # The real plugin class on an empty base.
    plugin = _load_update_monitor()()
    # The node's role.
    plugin.is_supervisor_node = supervisor
    # The random draw is fixed, so the offset is known.
    plugin.np = SimpleNamespace(random=SimpleNamespace(randint=lambda low, high: self.DRAW))
    # The plugin's clock.
    plugin.time = lambda: now
    # The epoch ends at 16:00 UTC.
    epoch_end = datetime.datetime(2026, 1, 1, 16, 0, 0, tzinfo=datetime.timezone.utc)
    # The network monitor's epoch manager, as the plugin reads it.
    plugin.netmon = SimpleNamespace(epoch_manager=SimpleNamespace(get_current_epoch_end=lambda: epoch_end))
    # The plugin's log lines.
    plugin.P = Mock()
    # The plugin under test.
    return plugin

  def lines(self, plugin):
    """
    The offset lines the plugin logged.

    Parameters
    ----------
    plugin : object
      A plugin built by ``plugin``.

    Returns
    -------
    list of str
      Every logged line that reports the restart offset.

    Examples
    --------
    >>> self.lines(plugin)
    ['Supervisor node restart offset configured to 130600 seconds']
    """
    # The first argument of every log call that names the offset.
    return [call.args[0] for call in plugin.P.call_args_list if "restart offset" in call.args[0]]

  def test_the_offset_is_logged_when_no_shift_applies(self):
    """A supervisor whose restart time is far from the epoch end logs its offset (before, it logged nothing)."""
    # A start time that puts the restart two hours after the epoch end.
    now = self.EPOCH_END - self.BASE_OFFSET + 2 * 86400 + 7200
    # A supervisor.
    plugin = self.plugin(supervisor=True, now=now)
    # What on_init calls.
    plugin._UpdateMonitor01Plugin__maybe_config_supervisor_restart_offset()
    # One line, with the offset as drawn.
    self.assertEqual(self.lines(plugin), ["Supervisor node restart offset configured to {} seconds".format(self.BASE_OFFSET)])
    # And that is the offset the plugin keeps.
    self.assertEqual(plugin._UpdateMonitor01Plugin__restart_offset, self.BASE_OFFSET)

  def test_the_logged_offset_is_the_one_in_force_after_the_three_hour_shift(self):
    """When the restart would fall within 30 minutes of the epoch end, the line shows the offset with its 3 h shift."""
    # A start time that puts the restart exactly at the epoch end.
    now = self.EPOCH_END - self.BASE_OFFSET + 2 * 86400
    # A supervisor.
    plugin = self.plugin(supervisor=True, now=now)
    # What on_init calls.
    plugin._UpdateMonitor01Plugin__maybe_config_supervisor_restart_offset()
    # One line, with the shifted offset: the value that decides the restart.
    self.assertEqual(
      self.lines(plugin), ["Supervisor node restart offset configured to {} seconds".format(self.BASE_OFFSET + 3 * 3600)]
    )
    # And that is the offset the plugin keeps.
    self.assertEqual(plugin._UpdateMonitor01Plugin__restart_offset, self.BASE_OFFSET + 3 * 3600)

  def test_a_node_that_is_not_a_supervisor_logs_nothing(self):
    """Any other node configures no offset and logs no offset line."""
    # Not a supervisor.
    plugin = self.plugin(supervisor=False, now=0)
    # on_init sets the offset to None before this call.
    plugin._UpdateMonitor01Plugin__restart_offset = None
    # What on_init calls.
    plugin._UpdateMonitor01Plugin__maybe_config_supervisor_restart_offset()
    # No line.
    self.assertEqual(self.lines(plugin), [])
    # No offset.
    self.assertIsNone(plugin._UpdateMonitor01Plugin__restart_offset)


if __name__ == "__main__":
  # Run this file's tests when it is started directly.
  unittest.main()
