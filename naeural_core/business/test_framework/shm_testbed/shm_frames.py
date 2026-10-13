"""Generated images carried by the real capture-to-serving input path."""

import json
import os
from pathlib import Path
import time

import numpy as np

from naeural_core.data.base import DataCaptureThread


_CONFIG = {**DataCaptureThread.CONFIG, "CAP_RESOLUTION": 10}


class ShmFramesDataCapture(DataCaptureThread):
  """Emit bounded image batches selected by the external test controller."""

  CONFIG = _CONFIG

  def connect(self):
    """No external source is needed for generated image capture."""
    return True

  def data_step(self):
    """Publish a timestamped batch using the production capture queue."""
    control = json.loads(Path(os.environ["R1_SHM_CONTROL"]).read_text())
    count = control.get("count", 1)
    self._add_inputs([
      self._new_input(img=np.full((64, 64, 3), 128, dtype=np.uint8),
        metadata={"phase": control["phase"], "captured_at": time.time(), "index": index})
      for index in range(count)
    ])

  def _release(self):
    """This capture owns no external resources."""
    return
