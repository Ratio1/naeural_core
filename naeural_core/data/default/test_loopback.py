import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _load_loopback_module():
  """Load `loopback.py` through the import system with stubbed dependencies."""
  fake_core = types.ModuleType("naeural_core")
  fake_core.__path__ = []  # type: ignore[attr-defined]
  fake_constants = types.ModuleType("naeural_core.constants")
  fake_constants.PAYLOAD_DATA = SimpleNamespace()
  fake_core.constants = fake_constants
  fake_core.data = types.ModuleType("naeural_core.data")
  fake_core.data.__path__ = []  # type: ignore[attr-defined]

  fake_data_base = types.ModuleType("naeural_core.data.base")

  class _FakeDataCaptureThread:
    CONFIG = {"VALIDATION_RULES": {}}

    def __init__(self, **kwargs):
      self.cfg_loopback_queue_size = kwargs.get("LOOPBACK_QUEUE_SIZE", 32)

  fake_data_base.DataCaptureThread = _FakeDataCaptureThread

  fake_data_structures = types.ModuleType("naeural_core.data_structures")

  class _FakeGeneralPayload:
    def __init__(self, **kwargs):
      self.__dict__.update(kwargs)

    def to_dict(self):
      return dict(self.__dict__)

  fake_data_structures.GeneralPayload = _FakeGeneralPayload

  module_path = Path(__file__).resolve().with_name("loopback.py")
  spec = importlib.util.spec_from_file_location("loopback_under_test", module_path)
  module = importlib.util.module_from_spec(spec)
  assert spec is not None and spec.loader is not None
  with patch.dict(
    sys.modules,
    {
      "naeural_core": fake_core,
      "naeural_core.constants": fake_constants,
      "naeural_core.data": fake_core.data,
      "naeural_core.data.base": fake_data_base,
      "naeural_core.data_structures": fake_data_structures,
    },
  ):
    spec.loader.exec_module(module)
  return module


LoopbackModule = _load_loopback_module()


class _FakeLoopback:
  def __init__(self):
    self._metadata = SimpleNamespace(dummy="value")

  def _new_input(self, img=None, struct_data=None, metadata=None):
    return {
      "IMG": img,
      "STRUCT_DATA": struct_data,
      "METADATA": metadata,
      "TYPE": "STRUCT_DATA" if struct_data is not None else "IMG",
    }


class LoopbackDataCaptureTests(unittest.TestCase):
  def test_build_inputs_unwraps_struct_data_field(self):
    fake = _FakeLoopback()
    payload = {
      "request_id": "rf_1234",
      "STRUCT_DATA": {
        "SepalLengthCm": 5.1,
        "SepalWidthCm": 3.5,
      },
      "metadata": {"source": "local"},
    }

    inputs = LoopbackModule.LoopbackDataCapture._build_inputs(fake, [payload])  # pylint: disable=protected-access

    self.assertEqual(len(inputs), 1)
    self.assertEqual(
      inputs[0]["STRUCT_DATA"],
      {
        "SepalLengthCm": 5.1,
        "SepalWidthCm": 3.5,
      },
    )
    self.assertEqual(inputs[0]["TYPE"], "STRUCT_DATA")

  def test_build_inputs_keeps_raw_payload_when_struct_data_missing(self):
    fake = _FakeLoopback()
    payload = {
      "request_id": "rf_1234",
      "SepalLengthCm": 5.1,
      "SepalWidthCm": 3.5,
    }

    inputs = LoopbackModule.LoopbackDataCapture._build_inputs(fake, [payload])  # pylint: disable=protected-access

    self.assertEqual(len(inputs), 1)
    self.assertEqual(inputs[0]["STRUCT_DATA"], payload)


if __name__ == "__main__":
  unittest.main()
