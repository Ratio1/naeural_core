"""Test-only synthetic source using the real capture thread and input builder."""

from naeural_core.data.base import DataCaptureThread


_CONFIG = {
  **DataCaptureThread.CONFIG,
  "CAP_RESOLUTION": 20,
}


class MemoryCounterDataCapture(DataCaptureThread):
  """Generate structured inputs for queue-pressure validation without external I/O."""

  CONFIG = _CONFIG

  def on_init(self):
    self._memory_counter = 0
    return

  def connect(self):
    return True

  def data_step(self):
    self._memory_counter += 1
    self._add_inputs([
      self._new_input(struct_data={"counter": self._memory_counter}, metadata={}),
    ])
    return

  def _release(self):
    return
