"""
Offline tests of the chain store's wave 03 switches and of its one counting helper, and the report-mode run of the
chain-store suites.

The suites in test_chain_store_hsync.py and test_chain_store_accounting.py run as they are with every switch 'off'
(the harness default, wave 02 behaviour). This module runs each of their test classes a second time, as a class
named ``Report<name>``, with every switch of every harness in 'report'.
"""
# The data folder of a started store.
import os
# Its temporary place.
import tempfile
# The test framework.
import unittest
# The stand-in for the SerializationManager on_init registers its save with.
from types import SimpleNamespace
# Sets the harness's mode for one test of the report-mode run.
from unittest import mock

# The manager on_init registers its save with, replaced during a start.
from naeural_core.main.serialization_manager import SerializationManager

# One suite whose test classes run again here in report mode.
import test_chain_store_accounting
# The other suite run again here.
import test_chain_store_hsync
# The harness whose mode the report-mode run sets, and the composer of a hash key.
from test_chain_store_hsync import _ChainStoreRuntimeHarness, _hset_key
# The counter attribute's private name, and the harness that runs the real on_init.
from test_chain_store_accounting import STATS, _StartHarness

# The config attribute of each switch, as on_init reads it, and its key in the mode table.
SWITCHES = {
  "cfg_cstore_order": "order", "cfg_cstore_lean_confirm": "lean_confirm", "cfg_cstore_handoff": "handoff",
  "cfg_cstore_save_backoff": "save_backoff",
}


class TestSwitches(unittest.TestCase):
  """on_init reads the four switches once, starts with any of the three modes and stops on any other value."""

  def setUp(self):
    """Give each test an empty data folder and a SerializationManager that records the registrations."""
    # A data folder removed after the test.
    folder = tempfile.TemporaryDirectory()
    # Removed after the test.
    self.addCleanup(folder.cleanup)
    # The folder on_init looks for the replica in.
    self.data_folder = folder.name
    # Its cache folder, as the plugin's persistence folder names it.
    os.makedirs(os.path.join(self.data_folder, "chain_store"))
    # The saves on_init registers.
    self.registered = []
    # The manager in use before the test, put back after it.
    previous = SerializationManager.instance
    # A manager that only records the registrations.
    SerializationManager.instance = SimpleNamespace(register=lambda save, every: self.registered.append(every))
    # Put back after the test.
    self.addCleanup(setattr, SerializationManager, "instance", previous)

  def test_a_switch_outside_the_three_modes_stops_the_start(self):
    """Each switch set to a value other than 'off', 'report' or 'on' makes on_init raise, naming its config key."""
    # Each switch, with values that look close to a mode but are not one.
    for attribute, name in SWITCHES.items():
      # One sub-test per value.
      for value in ("ON", True, None, "x"):
        # One sub-test each.
        with self.subTest(switch=name, value=value):
          # A store whose other switches are valid.
          store = _StartHarness(self.data_folder)
          # This switch set to the bad value.
          setattr(store, attribute, value)
          # The start stops, naming the config key.
          with self.assertRaisesRegex(ValueError, "CSTORE_" + name.upper()):
            # The real on_init.
            store.on_init()
          # The save was registered before the check, so the on_close that follows a failed start finds it; the
          # setter was not published, so no write is taken.
          self.assertEqual((self.registered[-1:], store.CS_SETTER in store.plugins_shmem),
                           ([store.CS_SAVE_INTERVAL], False))

  def test_each_of_the_three_modes_starts_and_is_read_into_the_mode_table(self):
    """All switches 'off', all 'report' and all 'on' start, and the mode table holds what the config said."""
    # The three modes; this commit checks no switch against another.
    for mode in ("off", "report", "on"):
      # One sub-test each.
      with self.subTest(mode=mode):
        # A store to start.
        store = _StartHarness(self.data_folder)
        # Every switch in this mode.
        for attribute in SWITCHES:
          # One switch.
          setattr(store, attribute, mode)
        # The real on_init: it starts and publishes the setter.
        store.on_init()
        # The table every later step and every CSTORE_STATS line read.
        self.assertEqual((store._ChainStoreBasePlugin__cs_modes, store.CS_SETTER in store.plugins_shmem),
                         (dict.fromkeys(SWITCHES.values(), mode), True))


class TestCountingHelper(unittest.TestCase):
  """The one counting helper labels a count by bucket, origin and event, or by the event alone."""

  def test_labels_of_a_count_of_the_store_and_of_a_write(self):
    """A store-wide count has the event as its label; a write's count is '<bucket>/<origin>/<event>'."""
    # A store with no counts yet.
    store = _ChainStoreRuntimeHarness("node")
    # The helper, by its private name.
    count = store._ChainStoreBasePlugin__cs_count
    # A field of a hash namespace and its bucket, the 14-character "hs:" prefix.
    field = _hset_key("players", "alpha")
    # A store-wide count, twice; a sum of 180; a hash-namespace write; a plain-key write.
    count("saves")
    # The second one.
    count("saves")
    # A sum, as milliseconds are counted.
    count("save_cpu_ms", n=180)
    # A remote write of the hash field.
    count("changed", field, "remote")
    # A local write of a plain key.
    count("refused_token", "plain-key", "local")
    # Each label with its count.
    self.assertEqual(getattr(store, STATS), {
      "saves": 2, "save_cpu_ms": 180, field[:14] + "/remote/changed": 1, "plain/local/refused_token": 1,
    })


def _report_mode(case):
  """
  Build the report-mode copy of a test class of the chain-store suites.

  The copy is a subclass named ``Report<name>`` whose ``setUp`` sets ``_ChainStoreRuntimeHarness.CSTORE_MODE``
  to ``'report'`` for the test (every harness the test builds, and every start it makes through ``on_init``, then
  has each switch in 'report') and puts it back after the test, then runs the class's own ``setUp``.

  Parameters
  ----------
  case : type
    A ``unittest.TestCase`` subclass of test_chain_store_hsync.py or test_chain_store_accounting.py.

  Returns
  -------
  type
    The subclass, with the same tests.

  Examples
  --------
  >>> _report_mode(test_chain_store_accounting.TestCounters).__name__
  'ReportTestCounters'
  """
  def setUp(self):
    """
    Set every switch of the harnesses to 'report' for this test, then run the class's own setUp.

    Parameters
    ----------
    self : unittest.TestCase
      The running test.

    Returns
    -------
    None
    """
    # The harness default, 'report' until the test ends.
    patcher = mock.patch.object(_ChainStoreRuntimeHarness, "CSTORE_MODE", "report")
    # In force from here.
    patcher.start()
    # Put back after the test, also when it fails.
    self.addCleanup(patcher.stop)
    # The class's own preparation.
    case.setUp(self)
  # The same tests under a name that says the mode.
  return type("Report" + case.__name__, (case,), {"setUp": setUp, "__doc__": "Report mode: " + str(case.__doc__)})


# Every test class of the two suites, once more with every switch in 'report'.
for _module in (test_chain_store_hsync, test_chain_store_accounting):
  # Their names and objects; only the test classes are copied.
  for _name, _case in list(vars(_module).items()):
    if isinstance(_case, type) and issubclass(_case, unittest.TestCase):
      # Found by the test loader under its own name.
      globals()["Report" + _name] = _report_mode(_case)
# Without this the loop variable would keep the last class of the second suite here, and the loader would run that
# class a second time in 'off'.
del _module, _name, _case


if __name__ == "__main__":
  # Run this module alone.
  unittest.main()
