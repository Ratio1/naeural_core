from collections import deque
from typing import Any, List

import numpy as np

from naeural_core import constants as ct
from naeural_core.data.base import DataCaptureThread
from naeural_core.data_structures import GeneralPayload

__VER__ = "0.1.0"

_CONFIG = {
  **DataCaptureThread.CONFIG,
  "LOOPBACK_QUEUE_SIZE": 32,
  "VALIDATION_RULES": {
    **DataCaptureThread.CONFIG["VALIDATION_RULES"],
    "LOOPBACK_QUEUE_SIZE": {
      "DESCRIPTION": "Maximum number of payloads cached in the loopback shared-memory queue.",
      "TYPE": "int",
      "MIN_VAL": 1,
      "MAX_VAL": 4096,
    },
  },
}


class LoopbackDataCapture(DataCaptureThread):
  CONFIG = _CONFIG


  def startup(self):
    super().startup()
    self._loopback_key = self._build_loopback_key(self.cfg_name)
    self._ensure_loopback_queue(self._loopback_key)
    self._metadata.update(pipeline_version=__VER__)
    return


  def connect(self):
    return True


  def data_step(self):
    queue = self._get_loopback_queue()
    if queue is None or len(queue) == 0:
      return

    pending_payloads = self._drain_queue(queue)
    if not pending_payloads:
      return

    self.P(f"Dequeued {len(pending_payloads)} payload(s) for stream '{self.cfg_name}'")
    inputs = self._build_inputs(pending_payloads)
    if inputs:
      self.P(f"Emitting {len(inputs)} input(s) downstream for stream '{self.cfg_name}'")
      self._add_inputs(inputs)
    else:
      self.P(f"No valid inputs built from payload batch on stream '{self.cfg_name}'", color='y')
    return


  def _release(self):
    return


  def _build_loopback_key(self, stream_name):
    return f"loopback_dct_{stream_name}"


  def _ensure_loopback_queue(self, key):
    existing = self.shmem.get(key)
    if isinstance(existing, deque):
      if existing.maxlen != self.cfg_loopback_queue_size:
        queue = deque(existing, maxlen=self.cfg_loopback_queue_size)
        self.shmem[key] = queue
      else:
        queue = existing
    else:
      queue = deque(maxlen=self.cfg_loopback_queue_size)
      self.shmem[key] = queue
    self._loopback_queue = queue
    return


  def _get_loopback_queue(self):
    queue = self.shmem.get(self._loopback_key)
    if not isinstance(queue, deque):
      self._ensure_loopback_queue(self._loopback_key)
      queue = self.shmem[self._loopback_key]
    return queue


  def _drain_queue(self, queue: deque):
    payloads: List[Any] = []
    while len(queue) > 0:
      payloads.append(queue.popleft())
    return payloads


  def _build_inputs(self, payloads: List[Any]):
    inputs = []
    for payload in payloads:
      if isinstance(payload, GeneralPayload):
        payload = payload.to_dict()

      if isinstance(payload, dict):
        img = payload.get("IMG")
        if isinstance(img, np.ndarray):
          inputs.append(
            self._new_input(img=img, struct_data=None, metadata=self._metadata.__dict__.copy())
          )
        elif img is not None:
          try:
            has_content = len(img) > 0  # type: ignore
          except TypeError:
            has_content = True
          if has_content:
            inputs.append(
              self._new_input(img=img, struct_data=None, metadata=self._metadata.__dict__.copy())
            )
          else:
            inputs.append(
              self._new_input(img=None, struct_data=payload, metadata=self._metadata.__dict__.copy())
            )
        else:
          inputs.append(
            self._new_input(img=None, struct_data=payload, metadata=self._metadata.__dict__.copy())
          )
      elif isinstance(payload, np.ndarray):
        inputs.append(
          self._new_input(img=payload, struct_data=None, metadata=self._metadata.__dict__.copy())
        )
      else:
        # fallback to structured data for unsupported payload types
        if payload is not None:
          inputs.append(
            self._new_input(img=None, struct_data=payload, metadata=self._metadata.__dict__.copy())
          )
    return inputs
