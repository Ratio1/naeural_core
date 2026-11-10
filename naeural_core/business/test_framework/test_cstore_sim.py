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
  cmd = [sys.executable, str(DRIVER), "--scenario", scenario, "--variant", variant, "--scale", str(scale)]
  if base_src:
    cmd += ["--base-src", base_src]
  out = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
  if out.returncode != 0:
    raise AssertionError("simulator failed:\n" + out.stderr[-2000:])
  return json.loads(out.stdout[out.stdout.index("{"):])


@unittest.skipUnless(os.environ.get("EE_RUN_CSTORE_SIM") == "1", "set EE_RUN_CSTORE_SIM=1 to run the fleet simulator")
class TestChainStoreFleet(unittest.TestCase):

  def assertHealthy(self, node, name):
    self.assertNotIn("error", node, name)
    self.assertEqual(node["lost_inputs"], 0, name)
    self.assertEqual(node["loop_errors"], 0, name)
    self.assertLess(node["queue_end"], 5, name)

  def test_healthy_fleet_converges(self):
    result = _run("healthy", 0.4)
    self.assertTrue(result["replicas_identical"])
    for name, node in result["nodes"].items():
      self.assertHealthy(node, name)
      self.assertGreater(node["loop_hz_mean"], 10, name)
      self.assertLess(node["save_thread_max_s"], 0.5, name)       # the plugin thread no longer writes the replica
      self.assertEqual(node["manager"]["errors"], 0, name)
      self.assertEqual(node["manager"]["inline"], 0, name)
      self.assertLess(node["worker"]["rss_kb"], 40 * 1024, name)
    writer = result["nodes"]["n0"]
    self.assertEqual(writer["op_write"]["ok_pct"], 100.0)
    self.assertGreater(writer["plugin_stats"]["same_value_skipped"], 0)

  def test_saturated_oracle_keeps_up_with_the_incident_stream(self):
    result = _run("incident", 0.5)
    slow = result["nodes"]["n4"]
    self.assertNotIn("error", slow)
    self.assertEqual((slow["lost_inputs"], slow["loop_errors"]), (0, 0))
    self.assertLess(slow["queue_end"], 20)                            # the old code ends this run with over 100 queued
    self.assertGreaterEqual(slow["local_write"]["ok_pct"], 80.0)      # the old code confirms none
    self.assertGreaterEqual(2 * slow["hsync"]["ok"], slow["hsync"]["n"])
    self.assertLess(slow["save_thread_max_s"], 2.0)                   # the old code spends seconds per save on this thread

  def test_killed_worker_and_killed_oracle_recover(self):
    result = _run("crash", 1.0)
    for name, node in result["nodes"].items():
      self.assertNotIn("error", node, name)
      self.assertEqual(node["loop_errors"], 0, name)
    killed_worker = result["nodes"]["n2"]["manager"]
    self.assertEqual(killed_worker["errors"], 0)
    self.assertGreaterEqual(killed_worker["inline"], 1)           # a save without the worker was written by the feeder
    self.assertGreaterEqual(killed_worker["worker_starts"], 2)
    restarted = result["nodes"]["n3"]
    self.assertGreater(restarted["loaded_keys"], 0)               # the replica on disk survived the kill and loaded
    self.assertGreater(restarted["hsync"]["ok"], 0)
    self.assertTrue(result["replicas_identical"])                 # and the refresh brought back what it had missed

  @unittest.skipUnless(os.environ.get("EE_CSTORE_SIM_BASE_SRC"), "set EE_CSTORE_SIM_BASE_SRC to an older checkout")
  def test_old_and_new_oracles_interoperate(self):
    result = _run("healthy", 0.4, variant="mixed", base_src=os.environ["EE_CSTORE_SIM_BASE_SRC"])
    self.assertTrue(result["replicas_identical"])
    for name, node in result["nodes"].items():
      self.assertHealthy(node, name)
      for tag in ("op_write", "local_write"):
        if tag in node:
          self.assertEqual(node[tag]["ok_pct"], 100.0, name)


if __name__ == "__main__":
  unittest.main()
