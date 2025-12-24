import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from enum import IntEnum


class _FakeOnnxNode:
  def __init__(self, name):
    self.name = name


class _FakeOnnxGraph:
  def __init__(self):
    self.input = [_FakeOnnxNode("images")]
    self.output = [_FakeOnnxNode("output")]


class _FakeOnnxModel:
  def __init__(self):
    self.graph = _FakeOnnxGraph()


def _make_fake_onnx():
  mod = types.ModuleType("onnx")

  def load(_path):
    return _FakeOnnxModel()

  def save(_model, path):
    with open(path, "wb") as handle:
      handle.write(b"fake_onnx")

  mod.load = load
  mod.save = save
  return mod


def _make_fake_google():
  google_mod = types.ModuleType("google")
  protobuf_mod = types.ModuleType("google.protobuf")
  json_format_mod = types.ModuleType("google.protobuf.json_format")

  def MessageToDict(obj, preserving_proto_field_name=False):  # pylint: disable=unused-argument
    if isinstance(obj, _FakeOnnxModel):
      return {
        "metadata_props": [
          {
            "key": "onnx_metadata",
            "value": json.dumps({"precision": "fp32"})
          }
        ]
      }
    return {"name": getattr(obj, "name", "")}

  json_format_mod.MessageToDict = MessageToDict
  protobuf_mod.json_format = json_format_mod
  google_mod.protobuf = protobuf_mod
  return {
    "google": google_mod,
    "google.protobuf": protobuf_mod,
    "google.protobuf.json_format": json_format_mod,
  }


def _make_fake_torch():
  mod = types.ModuleType("torch")

  class _Version:
    cuda = "12.2"

  class device:
    def __init__(self, index=None):
      self.index = index

  class _Cuda:
    @staticmethod
    def is_available():
      return False

    @staticmethod
    def device_count():
      return 0

    @staticmethod
    def current_device():
      return 0

    @staticmethod
    def get_device_properties(_idx):
      return None

    @staticmethod
    def get_device_name(_device):
      return "fake-gpu"

    @staticmethod
    def empty_cache():
      return None

    @staticmethod
    def set_device(_device):
      return None

  mod.__version__ = "2.0.0"
  mod.version = _Version()
  mod.device = device
  mod.cuda = _Cuda

  mod.bool = object()
  mod.uint8 = object()
  mod.int8 = object()
  mod.int16 = object()
  mod.int32 = object()
  mod.int64 = object()
  mod.float16 = object()
  mod.float32 = object()
  mod.float64 = object()
  mod.complex64 = object()
  mod.complex128 = object()
  return mod


def _make_fake_tensorrt():
  trt = types.ModuleType("tensorrt")
  trt.__version__ = "9.0.0"
  trt.last_builder = None

  class ILogger:
    VERBOSE = 0
    INFO = 1
    ERROR = 2

    class Severity:
      VERBOSE = 0
      INFO = 1
      ERROR = 2

    def __init__(self):
      return

  class NetworkDefinitionCreationFlag(IntEnum):
    EXPLICIT_BATCH = 0

  class BuilderFlag(IntEnum):
    FP16 = 0
    TF32 = 1

  class MemoryPoolType(IntEnum):
    WORKSPACE = 0

  class _FakeTensor:
    def __init__(self, name, shape, dtype):
      self.name = name
      self.shape = shape
      self.dtype = dtype

  class _FakeNetwork:
    def __init__(self):
      self._inputs = [_FakeTensor("images", (-1, 3, 640, 640), "float32")]
      self._outputs = [_FakeTensor("output", (-1, 25200, 85), "float32")]

    @property
    def num_inputs(self):
      return len(self._inputs)

    @property
    def num_outputs(self):
      return len(self._outputs)

    def get_input(self, idx):
      return self._inputs[idx]

    def get_output(self, idx):
      return self._outputs[idx]

  class _FakeBuilderConfig:
    def __init__(self):
      self._flags = set()
      self._profiles = []

    def set_memory_pool_limit(self, _pool_type, pool_size=None, **kwargs):
      if pool_size is None:
        pool_size = kwargs.get("pool_size")
      self._pool_size = pool_size
      return

    def set_flag(self, flag):
      self._flags.add(flag)
      return

    def get_flag(self, flag):
      return flag in self._flags

    def add_optimization_profile(self, profile):
      self._profiles.append(profile)
      return

  class _FakeOptimizationProfile:
    def __init__(self):
      self.shapes = {}

    def set_shape(self, name, min, opt, max):  # pylint: disable=redefined-builtin
      self.shapes[name] = (min, opt, max)
      return

  class _FakeEngine:
    def serialize(self):
      return b"engine"

  class OnnxParser:
    def __init__(self, _network, _logger):
      self.num_errors = 0

    def parse_from_file(self, _path):
      return True

    def get_error(self, _idx):
      return "fake-parser-error"

  class Builder:
    def __init__(self, _logger):
      trt.last_builder = self
      self.platform_has_fast_fp16 = False
      self.platform_has_fast_int8 = False
      self.platform_has_tf32 = False
      self.build_engine_calls = 0

    def create_builder_config(self):
      return _FakeBuilderConfig()

    def create_network(self, _flag_mask):
      return _FakeNetwork()

    def create_optimization_profile(self):
      return _FakeOptimizationProfile()

    def build_engine(self, _network, _config):
      self.build_engine_calls += 1
      return _FakeEngine()

  def init_libnvinfer_plugins(_logger, _namespace):
    return True

  trt.ILogger = ILogger
  trt.NetworkDefinitionCreationFlag = NetworkDefinitionCreationFlag
  trt.BuilderFlag = BuilderFlag
  trt.MemoryPoolType = MemoryPoolType
  trt.OnnxParser = OnnxParser
  trt.Builder = Builder
  trt.init_libnvinfer_plugins = init_libnvinfer_plugins
  return trt


class _FakeLogger:
  def __init__(self):
    self.messages = []
    self._timers = {}

  def P(self, msg, color=None):  # pylint: disable=unused-argument
    self.messages.append(msg)
    return

  def start_timer(self, name):
    self._timers[name] = True
    return

  def stop_timer(self, name):
    self._timers.pop(name, None)
    return

  def get_timer_mean(self, _name):
    return "0s"


class TestTensorRTCreateFromOnnx(unittest.TestCase):
  def setUp(self):
    self._modules_backup = {}
    for name in [
      "tensorrt",
      "torch",
      "onnx",
      "google",
      "google.protobuf",
      "google.protobuf.json_format",
    ]:
      self._modules_backup[name] = sys.modules.get(name)

    self.fake_trt = _make_fake_tensorrt()
    self.fake_torch = _make_fake_torch()
    self.fake_onnx = _make_fake_onnx()
    fake_google = _make_fake_google()

    sys.modules["tensorrt"] = self.fake_trt
    sys.modules["torch"] = self.fake_torch
    sys.modules["onnx"] = self.fake_onnx
    sys.modules["google"] = fake_google["google"]
    sys.modules["google.protobuf"] = fake_google["google.protobuf"]
    sys.modules["google.protobuf.json_format"] = fake_google["google.protobuf.json_format"]

    sys.modules.pop("naeural_core.serving.base.backends.trt", None)
    self.trt_backend = importlib.import_module("naeural_core.serving.base.backends.trt")
    return

  def tearDown(self):
    sys.modules.pop("naeural_core.serving.base.backends.trt", None)
    for name, module in self._modules_backup.items():
      if module is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = module
    return

  def test_create_from_onnx_builds_without_context_manager(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      onnx_path = os.path.join(temp_dir, "model.onnx")
      with open(onnx_path, "wb") as handle:
        handle.write(b"fake")

      device = self.trt_backend.th.device(0)
      log = _FakeLogger()
      self.trt_backend.TensorRTModel.create_from_onnx(
        onnx_path=onnx_path,
        half=False,
        max_batch_size=2,
        device=device,
        log=log,
      )

      engine_path, config_path = self.trt_backend.TensorRTModel._get_engine_and_config_path(
        onnx_path,
        False,
        2
      )
      self.assertTrue(os.path.isfile(engine_path))
      self.assertTrue(os.path.isfile(config_path))
      self.assertEqual(self.fake_trt.last_builder.build_engine_calls, 1)
