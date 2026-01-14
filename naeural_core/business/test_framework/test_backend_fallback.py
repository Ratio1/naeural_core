import sys
import types
import unittest

from naeural_core.serving.base import basic_th
from naeural_core.serving.mixins_base import trt_mixin as trt_mixin_module


class _FallbackHarness(basic_th.UnifiedFirstStage):
  def __init__(self):
    self._messages = []
    self._environment_variables = {}
    self._upstream_config = {}
    self.cfg_allow_backend_fallback = True
    self.cfg_strict_backend = False
    self.server_name = "TEST_BACKEND"
    self._platform_backend_priority = []
    return

  def P(self, msg, color=None, **kwargs):  # pylint: disable=unused-argument
    self._messages.append(msg)
    return


class _TrtSkipHarness(trt_mixin_module.TensortRTMixin):
  def __init__(self):
    self._messages = []
    self.server_name = "TEST_TRT_SKIP"
    self.version = "0.0.0"
    self.dev = 0
    self.cfg_fp16 = False
    self.cfg_model_trt_filename = "model.onnx"
    self.cfg_trt_url = "minio:dummy/model.onnx"
    return

  def P(self, msg, color=None, **kwargs):  # pylint: disable=unused-argument
    self._messages.append(msg)
    return

  def download_model_for_backend(self, *args, **kwargs):  # pylint: disable=unused-argument
    raise AssertionError("TRT compatibility guard should skip before downloads")


class _FakeDeviceProps:
  major = 12
  minor = 0


class _FakeCuda:
  @staticmethod
  def is_available():
    return True

  @staticmethod
  def device_count():
    return 1

  @staticmethod
  def current_device():
    return 0

  @staticmethod
  def get_device_properties(_idx):
    return _FakeDeviceProps()


class _FakeTorch:
  cuda = _FakeCuda

  class device:
    def __init__(self, index=None):
      self.index = index


class TestBackendFallback(unittest.TestCase):
  def test_prepare_model_falls_back_to_ths(self):
    harness = _FallbackHarness()
    harness._platform_backend_priority = ["trt", "ths"]

    def _prepare_trt_model(*args, **kwargs):  # pylint: disable=unused-argument
      raise RuntimeError("TRT build failed")

    def _prepare_ts_model(*args, **kwargs):  # pylint: disable=unused-argument
      return "ths-model", {"ok": True}

    harness._prepare_trt_model = _prepare_trt_model
    harness._prepare_ts_model = _prepare_ts_model

    backend_map = {
      "trt": ("model_trt.onnx", "minio:dummy/model_trt.onnx"),
      "ths": ("model.ths", "minio:dummy/model.ths"),
    }

    model, _config, _fn = harness.prepare_model(
      backend_model_map=backend_map,
      return_config=True
    )
    self.assertEqual(model, "ths-model")
    self.assertIn("falling back to ths", " ".join(harness._messages).lower())

  def test_prepare_model_strict_backend_raises(self):
    harness = _FallbackHarness()
    harness._platform_backend_priority = ["trt", "ths"]

    def _prepare_trt_model(*args, **kwargs):  # pylint: disable=unused-argument
      raise RuntimeError("TRT build failed")

    harness._prepare_trt_model = _prepare_trt_model

    backend_map = {
      "trt": ("model_trt.onnx", "minio:dummy/model_trt.onnx"),
    }

    with self.assertRaises(RuntimeError):
      harness.prepare_model(
        backend_model_map=backend_map,
        forced_backend="trt",
        return_config=True
      )

  def test_trt_skips_unsupported_sm(self):
    fake_trt = types.ModuleType("tensorrt")
    fake_trt.__version__ = "8.6.1"

    original_trt = sys.modules.get("tensorrt")
    original_th = trt_mixin_module.th
    sys.modules["tensorrt"] = fake_trt
    trt_mixin_module.th = _FakeTorch()
    try:
      harness = _TrtSkipHarness()
      result = harness._prepare_trt_model(
        url="minio:dummy/model_trt.onnx",
        fn_model="model_trt.onnx",
        return_config=True,
        batch_size=1,
        allow_backend_fallback=True,
        strict_backend=False
      )
      self.assertEqual(result, (None, None))
      self.assertIn("does not support sm", harness._last_backend_error.lower())
    finally:
      if original_trt is None:
        sys.modules.pop("tensorrt", None)
      else:
        sys.modules["tensorrt"] = original_trt
      trt_mixin_module.th = original_th

