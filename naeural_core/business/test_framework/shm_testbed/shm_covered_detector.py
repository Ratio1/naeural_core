"""Real CPU inference in a production serving process, with controlled faults."""

import json
import os
from pathlib import Path
import time

import numpy as np

from naeural_core.serving.base.base_serving_process import ModelServingProcess


_CONFIG = {**ModelServingProcess.CONFIG, "MAX_WAIT_TIME": 2,
  "COVERED_SERVERS": ["SHM_TOY_DETECTOR"]}


class ShmToyDetector(ModelServingProcess):
  """Evaluate a deterministic Torch convolution, without network artifacts."""

  CONFIG = {**ModelServingProcess.CONFIG, "MAX_WAIT_TIME": 2}

  def startup(self):
    """Create a small real CPU model and record this worker's identity."""
    import torch
    self._torch = torch
    torch.set_num_threads(1)
    self._model = torch.nn.Sequential(torch.nn.Conv2d(3, 1, 1, bias=False),
      torch.nn.AdaptiveAvgPool2d(1)).eval()
    with torch.no_grad():
      self._model[0].weight.fill_(1 / 3)
    self._phase = "normal"
    directory = Path(os.environ["R1_SHM_EVIDENCE"])
    (directory / ("worker-" + str(os.getpid()) + ".json")).write_text(json.dumps({
      "pid": os.getpid(), "server": self.server_name, "started": time.time(),
      "model": "deterministic CPU Torch Conv2d + AdaptiveAvgPool2d",
    }))

  def pre_process(self, inputs):
    """Convert real reconstructed images into CPU tensors."""
    self._phase = json.loads(Path(os.environ["R1_SHM_CONTROL"]).read_text())["phase"]
    if self._phase == "timeout":
      time.sleep(8)
    return self._torch.from_numpy(np.stack(inputs["DATA"])).permute(0, 3, 1, 2).float() / 255

  def predict(self, prep_inputs):
    """Run actual Torch inference rather than returning precomputed responses."""
    with self._torch.inference_mode():
      return self._model(prep_inputs).flatten().tolist()

  def post_process(self, preds):
    """Encode a known box and computed confidence in the detector schema."""
    return [[{"TLBR_POS": [4, 4, 60, 60], "TYPE": "person", "PROB_PRC": score}]
      for score in preds]

  def _pack_results(self, results):
    """Inject one deliberate cardinality error at the real result boundary."""
    packed = super()._pack_results(results)
    if self._phase == "mismatch" and packed["INFERENCES"]:
      packed["INFERENCES"][0] = packed["INFERENCES"][0][:-1]
    return packed


class ShmCoveredDetector(ShmToyDetector):
  """Cover the base detector alias through real serving-manager registration."""

  CONFIG = _CONFIG
