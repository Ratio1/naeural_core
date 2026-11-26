"""
Short runs of the chain-store fleet simulator (see ``cstore_sim/README.md``).

Each test starts several interpreter processes and takes 30-90 s, so they run
only when ``EE_RUN_CSTORE_SIM=1`` is set.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().parent / "cstore_sim" / "driver.py"


def _run(scenario, scale, variant="new", base_src=None):
  """Run a fleet scenario with the test interpreter and decode its result.

  The driver and each simulated oracle run in separate interpreter processes.

  Parameters
  ----------
  scenario : str
    Scenario name accepted by the driver.
  scale : float
    Multiplier for the scenario duration.
  variant : str, optional
    Source selection for the simulated fleet, defaults to the current checkout.
  base_src : str or None, optional
    Base checkout used in comparative scenarios.

  Returns
  -------
  dict
    JSON report with per-node metrics and the convergence result.

  Examples
  --------
  >>> _run("healthy", 0.4)  # doctest: +SKIP
  """
  # Use the test interpreter for the driver and the oracle processes it launches.
  cmd = [sys.executable, str(DRIVER), "--scenario", scenario, "--variant", variant, "--scale", str(scale)]
  if base_src:
    # Mixed and base comparisons use this checkout for their older nodes.
    cmd += ["--base-src", base_src]
  # Bound a failed run while allowing the longer interpreter-pressure scenarios to finish.
  out = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
  if out.returncode != 0:
    raise AssertionError("simulator failed:\n" + out.stderr[-2000:])
  # The driver emits a single JSON result.
  return json.loads(out.stdout[out.stdout.index("{"):])


@unittest.skipUnless(os.environ.get("EE_RUN_CSTORE_SIM") == "1", "set EE_RUN_CSTORE_SIM=1 to run the fleet simulator")
class TestChainStoreFleet(unittest.TestCase):

  def assertHealthy(self, node, name):
    self.assertNotIn("error", node, name)
    self.assertEqual(node["lost_inputs"], 0, name)
    self.assertEqual(node["loop_errors"], 0, name)
    self.assertLess(node["queue_end"], 5, name)

  def test_healthy_fleet_converges(self):
    """Compare all five healthy replicas after writes and periodic saves.

    Verifies nonempty convergence and successful background persistence.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestChainStoreFleet().test_healthy_fleet_converges()  # doctest: +SKIP
    """
    # Run long enough for the configured 5-second save cadence and multiple operation writes.
    result = _run("healthy", 0.4)
    # Every replica converges on actual workload records.
    self.assertTrue(result["replicas_identical"])
    self.assertGreater(min(result["replica_keys"].values()), 0)
    for name, node in result["nodes"].items():
      self.assertHealthy(node, name)
      self.assertGreater(node["loop_hz_mean"], 10, name)
      self.assertLess(node["save_thread_max_s"], 0.5, name)
      self.assertEqual(node["manager"]["errors"], 0, name)
      # The current manager must save from its independent writer thread.
      self.assertEqual(node["manager"]["backend"], "thread", name)
      self.assertGreater(node["manager"]["periodic_saves"], 0, name)
    # The synthetic coordinator's distinct writes must reach quorum.
    writer = result["nodes"]["n0"]
    self.assertEqual(writer["op_write"]["ok_pct"], 100.0)
    # A repeat write uses the current store's identical-write path.
    self.assertTrue(any(name.endswith("/local/identical") and count > 0
                        for name, count in writer["plugin_stats"].items()))

  def test_saturated_oracle_keeps_up_with_the_incident_stream(self):
    """Check that a saturated oracle continues to serve the simulated fleet.

    The constrained oracle must process its input without loss, keep a small
    queue, confirm most local writes and finish at least half its sync calls.
    The save thread must also stay within its timing limit.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestChainStoreFleet().test_saturated_oracle_keeps_up_with_the_incident_stream()  # doctest: +SKIP
    """
    result = _run("incident", 0.5)
    slow = result["nodes"]["n4"]
    self.assertNotIn("error", slow)
    self.assertEqual((slow["lost_inputs"], slow["loop_errors"]), (0, 0))
    self.assertLess(slow["queue_end"], 20)                            # the old code ends this run with over 100 queued
    self.assertGreaterEqual(slow["local_write"]["ok_pct"], 80.0)      # the old code confirms none
    self.assertGreaterEqual(2 * slow["hsync"]["ok"], slow["hsync"]["n"])
    self.assertLess(slow["save_thread_max_s"], 2.0)                   # the old code spends seconds per save on this thread

  def test_killed_oracle_recovers_its_replica(self):
    """Check restart and replica convergence with ordering in report mode.

    Continues to check equality even when a snapshot races with live writes.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestChainStoreFleet().test_killed_oracle_recovers_its_replica()  # doctest: +SKIP
    """
    # Keep the shipped report-mode crash separate from the enforcing comparison.
    self._assert_crash_recovery("crash")

  def test_killed_oracle_recovers_its_ordered_replica(self):
    """Check restart and replica convergence with ordering enforced.

    The recovery workload is the same as the report-mode case.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestChainStoreFleet().test_killed_oracle_recovers_its_ordered_replica()  # doctest: +SKIP
    """
    # This case verifies recovery while stale snapshots are refused.
    self._assert_crash_recovery("crash_ordered")

  def _assert_crash_recovery(self, scenario):
    """Recover the persisted replica and refresh data missed during a crash.

    The current serialization manager uses one thread per save. The process
    crash exercises disk reload and namespace repair on that manager.

    Parameters
    ----------
    scenario : str
      Crash scenario to exercise.

    Returns
    -------
    None

    Examples
    --------
    >>> TestChainStoreFleet()._assert_crash_recovery("crash_ordered")  # doctest: +SKIP
    """
    # Run the full scenario so it includes process death, restart, and refresh.
    result = _run(scenario, 1.0)
    for name, node in result["nodes"].items():
      self.assertNotIn("error", node, name)
      self.assertEqual(node["loop_errors"], 0, name)
      # Reload and repair must leave subsequent persistence healthy.
      self.assertEqual(node["manager"]["errors"], 0, name)
      self.assertGreater(node["manager"]["periodic_saves"], 0, name)
    # Identify the restarted oracle and verify it loaded its saved workload and refreshed after restart.
    restarted = result["nodes"]["n3"]
    self.assertGreater(restarted["loaded_keys"], 0)               # the replica on disk survived the kill and loaded
    self.assertGreater(restarted["hsync"]["ok"], 0)
    self.assertTrue(result["replicas_identical"])                 # and the refresh brought back what it had missed

  @unittest.skipUnless(os.environ.get("EE_CSTORE_SIM_BASE_SRC"), "set EE_CSTORE_SIM_BASE_SRC to an older checkout")
  def test_old_and_new_oracles_interoperate(self):
    """Verify that old and current chain-store peers agree on their records.

    Run the healthy fleet with both checkouts. Every peer must stay healthy,
    every attempted operation or local write must be confirmed, and the
    replicas must converge.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestChainStoreFleet().test_old_and_new_oracles_interoperate()  # doctest: +SKIP
    """
    result = _run("healthy", 0.4, variant="mixed", base_src=os.environ["EE_CSTORE_SIM_BASE_SRC"])
    self.assertTrue(result["replicas_identical"])
    for name, node in result["nodes"].items():
      self.assertHealthy(node, name)
      for tag in ("op_write", "local_write"):
        if tag in node:
          self.assertEqual(node[tag]["ok_pct"], 100.0, name)


if __name__ == "__main__":
  unittest.main()
