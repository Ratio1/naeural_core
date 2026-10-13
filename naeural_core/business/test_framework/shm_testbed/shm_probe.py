"""Full-runtime CV business consumer and sanitized incremental evidence."""

import json
import os
from pathlib import Path
import time

from naeural_core.business.base import CVPluginExecutor


_CONFIG = {**CVPluginExecutor.CONFIG, "AI_ENGINE": ["shm_covered_detector"],
  "ALLOW_EMPTY_INPUTS": False, "PROCESS_DELAY": 0, "PLUGIN_LOOP_RESOLUTION": 10,
  "OBJECT_TYPE": [], "CONFIDENCE_THRESHOLD": 0, "TRACKING_ENABLED": False,
  "MAX_INPUTS_QUEUE_SIZE": 100, "RUN_WITHOUT_IMAGE": True}


class ShmProbePlugin(CVPluginExecutor):
  """Observe real inference delivery without bypassing production CV handling."""

  CONFIG = _CONFIG

  def on_init(self):
    """Prepare bounded result summaries, with no retained input buffers."""
    self._samples = []
    self._phase_counts = {}

  def _process(self):
    """Check computed inference values and record consumer delivery timings."""
    full = self.dataapi_full_input()
    inputs = full.get("INPUTS", [])
    metadata = next((row.get("METADATA", {}) for row in inputs), {})
    phase = metadata.get("phase", "unknown")
    if phase == "shutdown" and self.cfg_instance_id == "direct":
      self.cmdapi_stop_current_box()
      return
    results = self.dataapi_inferences()
    counts = {str(key): len(value) for key, value in results.items()}
    scores = [box["PROB_PRC"] for groups in results.values()
      for boxes in groups for box in boxes]
    assert len(scores) == sum(counts.values()), "Expected one computed box per image result"
    assert all(abs(score - 128 / 255) < 0.00001 for score in scores), scores[:2]
    self._phase_counts[phase] = self._phase_counts.get(phase, 0) + 1
    self._samples.append({"phase": phase, "time": time.time(), "counts": counts,
      "images": len(inputs), "scores": len(scores),
      "delivery_seconds": time.time() - metadata.get("captured_at", time.time())})
    self._samples = self._samples[-256:]
    target = Path(os.environ["R1_SHM_EVIDENCE"]) / ("probe-" + self.cfg_instance_id + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"samples": self._samples, "phase_counts": self._phase_counts}))
    temporary.replace(target)
    return self._create_payload(test_phase=phase, test_counts=counts)
