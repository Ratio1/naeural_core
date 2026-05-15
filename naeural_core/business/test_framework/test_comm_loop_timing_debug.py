import importlib.util
import os
import pathlib
import unittest
from unittest import mock

_COMM_LOOP_TIMING_PATH = (
  pathlib.Path(__file__).resolve().parents[2] / "comm" / "base" / "comm_loop_timing_debug.py"
)
_COMM_LOOP_TIMING_SPEC = importlib.util.spec_from_file_location(
  "comm_loop_timing_debug_under_test",
  _COMM_LOOP_TIMING_PATH,
)
_COMM_LOOP_TIMING_MODULE = importlib.util.module_from_spec(_COMM_LOOP_TIMING_SPEC)
_COMM_LOOP_TIMING_SPEC.loader.exec_module(_COMM_LOOP_TIMING_MODULE)
_CommLoopTimingDebugMixin = _COMM_LOOP_TIMING_MODULE._CommLoopTimingDebugMixin

_TIMING_ENV_KEYS = (
  "EE_DEBUG_COMM_LOOP_TIMINGS",
  "EE_DEBUG_COMM_LOOP_TIMINGS_INTERVAL",
  "EE_DEBUG_COMM_LOOP_TIMINGS_SLOW_SECONDS",
)


def _without_timing_env():
  return mock.patch.dict(os.environ, {key: "" for key in _TIMING_ENV_KEYS})


class _TimingHarness(_CommLoopTimingDebugMixin):
  """Small harness for BaseCommThread timing helpers without starting comms."""

  def __init__(self, config=None):
    self._config = config or {}
    self._comm_type = "COMMAND_AND_CONTROL"
    self.messages = []
    self._configure_comm_loop_timing_debug()

  def P(self, s, color=None, **kwargs):
    self.messages.append((s, color, kwargs))
    return


class TestCommLoopTimingDebug(unittest.TestCase):

  def test_timing_debug_is_disabled_by_default(self):
    with _without_timing_env():
      harness = _TimingHarness()
      harness._comm_loop_timing_count("messages")
      harness._comm_loop_timing_add("register_heartbeat", 0.5)
      harness._debug_comm_loop_timings_since -= 60
      harness._maybe_report_comm_loop_timings()

    self.assertFalse(harness._debug_comm_loop_timings_enabled)
    self.assertEqual(harness._debug_comm_loop_timing_counters, {})
    self.assertEqual(harness._debug_comm_loop_timing_stats, {})
    self.assertEqual(harness.messages, [])
    return

  def test_timing_debug_reports_aggregate_counters_and_phases(self):
    with _without_timing_env():
      harness = _TimingHarness({
        "DEBUG_COMM_LOOP_TIMINGS": True,
        "DEBUG_COMM_LOOP_TIMINGS_INTERVAL": 10,
        "DEBUG_COMM_LOOP_TIMINGS_SLOW_SECONDS": 0.1,
      })

      harness._comm_loop_timing_count("messages_received", 2)
      harness._comm_loop_timing_add("register_heartbeat", 0.05)
      harness._comm_loop_timing_add("register_heartbeat", 0.20)
      report_time = harness._debug_comm_loop_timings_since + 11
      harness._maybe_report_comm_loop_timings(now=report_time)

    self.assertTrue(harness._debug_comm_loop_timings_enabled)
    self.assertTrue(any("timing debug enabled" in msg for msg, _, _ in harness.messages))
    report = harness.messages[-1][0]
    self.assertIn("messages_received=2", report)
    self.assertIn("register_heartbeat", report)
    self.assertIn("n=2", report)
    self.assertIn("slow=1", report)
    self.assertEqual(harness._debug_comm_loop_timing_counters, {})
    self.assertEqual(harness._debug_comm_loop_timing_stats, {})
    return

  def test_timing_debug_can_be_enabled_from_environment(self):
    with _without_timing_env():
      os.environ["EE_DEBUG_COMM_LOOP_TIMINGS"] = "true"
      harness = _TimingHarness({"DEBUG_COMM_LOOP_TIMINGS": False})
      self.assertTrue(harness._debug_comm_loop_timings_enabled)
    return


if __name__ == "__main__":
  unittest.main()
