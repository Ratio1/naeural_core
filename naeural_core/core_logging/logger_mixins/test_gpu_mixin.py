import contextlib
import pathlib
import subprocess
import sys
import types
import unittest
from importlib import util
from unittest import mock


GPU_MIXIN_PATH = pathlib.Path(__file__).resolve().with_name("gpu_mixin.py")
GPU_MIXIN_SPEC = util.spec_from_file_location("gpu_mixin_under_test", GPU_MIXIN_PATH)
gpu_mixin_under_test = util.module_from_spec(GPU_MIXIN_SPEC)
GPU_MIXIN_SPEC.loader.exec_module(gpu_mixin_under_test)


class _FakeCudaDeviceProperties:
  """
  Minimal CUDA device metadata used by the GPU mixin regression tests.

  Notes
  -----
  The production object is returned by ``torch.cuda.get_device_properties`` and
  carries many more fields.  The timeout regression only depends on the display
  name and total memory, so the fake intentionally exposes only that contract.
  """

  name = "NVIDIA RTX 4000 Ada Generation Laptop GPU"
  total_memory = 12 * 1024 ** 3


class _FakeCuda:
  """
  Small stand-in for ``torch.cuda`` with one visible GPU.

  Notes
  -----
  The test avoids importing torch or requiring CUDA hardware.  These methods
  model only the calls made by ``_GPUMixin.gpu_info`` before and after the
  optional ``nvidia-smi`` UUID lookup.
  """

  @staticmethod
  def device_count():
    """Return one fake CUDA device for the telemetry path under test."""
    return 1

  @staticmethod
  def empty_cache():
    """Mirror the torch API without creating hardware side effects."""
    return None

  @staticmethod
  def get_device_properties(device_id):
    """
    Return deterministic metadata for the fake CUDA device.

    Parameters
    ----------
    device_id : int
      CUDA device index requested by the GPU mixin.

    Returns
    -------
    _FakeCudaDeviceProperties
      Minimal device properties needed by ``gpu_info``.
    """
    assert device_id == 0
    return _FakeCudaDeviceProperties()


class _FakeMemoryInfo:
  """
  NVML memory structure compatible with ``nvmlDeviceGetMemoryInfo``.

  Notes
  -----
  NVML reports bytes.  The mixin converts these values into GB or MB depending
  on the caller's ``mb`` flag, so fixed byte values keep the assertion stable.
  """

  total = 12 * 1024 ** 3
  used = 3 * 1024 ** 3


class _FakeUtilizationRates:
  """
  NVML utilization structure compatible with ``nvmlDeviceGetUtilizationRates``.
  """

  gpu = 42


class _FakePynvml(types.ModuleType):
  """
  Minimal ``pynvml`` module for exercising best-effort GPU telemetry.

  Notes
  -----
  The important contract is that ``gpu_info`` can fall back to
  ``nvmlDeviceGetHandleByIndex`` when UUID mapping is unavailable because
  ``nvidia-smi`` is slow, missing, or unhealthy.
  """

  NVML_TEMPERATURE_GPU = 0
  NVML_TEMPERATURE_THRESHOLD_SHUTDOWN = 1

  def __init__(self):
    super().__init__("pynvml")
    self.handles_by_index = []

  def nvmlInit(self):
    """Initialize the fake NVML module without side effects."""
    return None

  def nvmlDeviceGetHandleByIndex(self, device_id):
    """
    Return a deterministic handle for an NVML index lookup.

    Parameters
    ----------
    device_id : int
      Device index requested by the fallback path.

    Returns
    -------
    str
      Fake NVML handle.
    """
    self.handles_by_index.append(device_id)
    return "handle-{}".format(device_id)

  def nvmlDeviceGetHandleByUUID(self, uuid):
    """Return a deterministic handle for UUID lookups when they are available."""
    return "handle-{}".format(uuid)

  def nvmlDeviceGetMemoryInfo(self, handle):
    """Return fixed memory telemetry for any fake handle."""
    return _FakeMemoryInfo()

  def nvmlDeviceGetUtilizationRates(self, handle):
    """Return fixed utilization telemetry for any fake handle."""
    return _FakeUtilizationRates()

  def nvmlDeviceGetTemperature(self, handle, temperature_type):
    """Return fixed temperature telemetry for any fake handle."""
    return 55

  def nvmlDeviceGetTemperatureThreshold(self, handle, threshold_type):
    """Return a fixed shutdown threshold for any fake handle."""
    return 90

  def nvmlDeviceGetFanSpeed(self, handle):
    """Return a fixed fan speed for any fake handle."""
    return 31

  def nvmlDeviceGetUUID(self, handle):
    """Return a deterministic UUID for process telemetry mapping."""
    return "GPU-fake-uuid"


class _DummyLogger(gpu_mixin_under_test._GPUMixin):
  """
  Logger double that provides the non-GPU services used by ``_GPUMixin``.

  Notes
  -----
  ``_GPUMixin`` is normally mixed into the project logger.  The regression test
  supplies only logging and lock methods so the behavior under test stays
  isolated from the full runtime bootstrap.
  """

  def __init__(self):
    self._done_first_smi_error = False
    self._nvml_initialized = False
    self.messages = []

  def P(self, message, color=None):
    """Capture mixin log messages for assertions."""
    self.messages.append(message)

  @contextlib.contextmanager
  def managed_lock_resource(self, name):
    """Provide the lock context expected by ``gpu_info``."""
    yield


class TestGpuMixin(unittest.TestCase):
  """
  Regression tests for GPU telemetry degradation paths.

  Notes
  -----
  These tests keep optional ``nvidia-smi`` failures from breaking the primary
  CUDA/NVML telemetry path that heartbeats and system-health plugins consume.
  """

  def test_gpu_info_uses_nvml_index_fallback_when_uuid_query_times_out(self):
    """
    Verify ``nvidia-smi`` UUID lookup timeouts do not abort GPU telemetry.

    Notes
    -----
    The observed production log shows a timeout from the optional
    ``--query-gpu=index,uuid`` call.  GPU info should still be returned through
    the NVML index fallback, and the timeout should not be logged as a
    top-level ``gpu_info exception``.
    """
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = _FakeCuda
    fake_pynvml = _FakePynvml()

    def fake_subprocess_run(cmd, capture_output, text, timeout):
      if "--query-gpu=index,uuid" in cmd:
        raise subprocess.TimeoutExpired(cmd, timeout)
      return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with mock.patch.dict(sys.modules, {"torch": fake_torch, "pynvml": fake_pynvml}):
      with mock.patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
          logger = _DummyLogger()

          gpu_info = logger.gpu_info()

    self.assertEqual(len(gpu_info), 1)
    self.assertEqual(gpu_info[0]["NAME"], _FakeCudaDeviceProperties.name)
    self.assertEqual(gpu_info[0]["ALLOCATED_MEM"], 3)
    self.assertEqual(gpu_info[0]["FREE_MEM"], 9)
    self.assertEqual(fake_pynvml.handles_by_index, [0])
    self.assertFalse(
      any("gpu_info exception" in message for message in logger.messages),
      logger.messages,
    )


if __name__ == "__main__":
  unittest.main()
