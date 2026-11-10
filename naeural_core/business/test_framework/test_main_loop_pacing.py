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


if __name__ == "__main__":
  unittest.main()
