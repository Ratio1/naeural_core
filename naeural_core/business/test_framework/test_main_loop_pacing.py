"""Main-loop pacing: the rate callback every plugin reads on every lap, and the end-of-cycle wait."""
import unittest
from types import SimpleNamespace
from unittest import mock

from naeural_core.main import orchestrator as orchestrator_module
from naeural_core.main.orchestrator import Orchestrator


class TestMainLoopPacing(unittest.TestCase):
  def test_mean_loop_freq_is_recomputed_at_most_once_a_second(self):
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config_data = {"MAIN_LOOP_RESOLUTION": 20}
    timer_means = iter([0.2, 0.5])
    orchestrator.log = SimpleNamespace(get_timer_mean=lambda _name: next(timer_means))
    clock = [100.0]
    with mock.patch.object(orchestrator_module, "monotonic", lambda: clock[0]):
      first = orchestrator._get_mean_loop_freq()
      clock[0] = 100.9
      cached = orchestrator._get_mean_loop_freq()
      clock[0] = 101.0
      refreshed = orchestrator._get_mean_loop_freq()
    self.assertEqual((first, cached, refreshed), (5.0, 5.0, 2.0))

  def test_main_loop_delay_sleeps_once_for_the_rest_of_the_cycle(self):
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.config_data = {"MAIN_LOOP_RESOLUTION": 20}  # 50 ms cycle
    orchestrator.log = SimpleNamespace(start_timer=lambda _name: None, end_timer=lambda _name: None)
    orchestrator._comm_manager = SimpleNamespace(avg_comm_loop_timings=None)
    orchestrator.loop_timings = []
    clock = [10.0]
    sleeps = []

    def fake_sleep(seconds):
      sleeps.append(round(seconds, 6))
      clock[0] += seconds

    # 10 ms into the cycle: one 40 ms sleep; an overrun cycle still yields 1 ms
    for elapsed, expected in ((0.010, [0.04]), (0.200, [0.001])):
      orchestrator._last_main_loop_pass_time = clock[0] - elapsed
      sleeps.clear()
      with mock.patch.object(orchestrator_module, "perf_counter", lambda: clock[0]), \
           mock.patch.object(orchestrator_module, "sleep", fake_sleep):
        orchestrator._maybe_delay_main_loop()
      self.assertEqual(sleeps, expected)
    self.assertAlmostEqual(orchestrator.loop_timings[0], 0.05)
    # with the real clocks the single sleep never ends a cycle early
    orchestrator.loop_timings = []
    for _ in range(5):
      orchestrator._last_main_loop_pass_time = orchestrator_module.perf_counter()
      orchestrator._maybe_delay_main_loop()
    self.assertGreaterEqual(min(orchestrator.loop_timings), 0.05)


if __name__ == "__main__":
  unittest.main()
