import unittest

from naeural_core.serving.base.base_serving_process import ModelServingProcess


class BaseServingProcessIdleHookTests(unittest.TestCase):
  """Regression coverage for serving-loop idle extension points."""

  def test_private_idle_hook_dispatches_to_public_override(self):
    """The serving loop hook should reach subclasses through ``on_idle``."""

    class IdleProbe(ModelServingProcess):
      """Minimal probe that records public idle-hook dispatches."""

      def on_idle(self):
        """Record that the serving idle hook reached the subclass override."""

        self.events.append("on_idle")
        return

    serving = object.__new__(IdleProbe)
    serving.events = []

    serving._ModelServingProcess__on_idle()

    self.assertEqual(["on_idle"], serving.events)


if __name__ == "__main__":
  unittest.main()
