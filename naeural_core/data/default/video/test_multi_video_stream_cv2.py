import time
import unittest
from unittest import mock

import cv2
import numpy as np
import sys
import types

from naeural_core import constants as ct


_serving_utils_stub = types.ModuleType("naeural_core.serving.ai_engines.utils")
_serving_utils_stub.get_serving_process_given_ai_engine = lambda *args, **kwargs: None
_serving_utils_stub.get_ai_engine_given_serving_process = lambda *args, **kwargs: None
_serving_utils_stub.get_params_given_ai_engine = lambda *args, **kwargs: {}
sys.modules.setdefault("naeural_core.serving.ai_engines.utils", _serving_utils_stub)

from naeural_core.data.default.video.multi_video_stream_cv2 import MultiVideoStreamCv2DataCapture


class DummyBC:
  address = "0x0"

  def sign(self, *args, **kwargs):
    return "signature"

  def verify(self, *args, **kwargs):
    return True

  def encrypt(self, *args, **kwargs):
    return "encrypted"

  def decrypt(self, *args, **kwargs):
    return "decrypted"

  def encrypt_for_multi(self, *args, **kwargs):
    return "encrypted"

  @property
  def whitelist(self):
    return []


class MockCapture:
  def __init__(self, frame, fail_open=False, fail_reads=0):
    self._frame = frame
    self._opened = not fail_open
    self._fail_reads = fail_reads
    self._buffersize = 1

  def isOpened(self):
    return self._opened

  def read(self):
    if not self._opened:
      return False, None
    if self._fail_reads > 0:
      self._fail_reads -= 1
      return False, None
    return True, self._frame.copy()

  def grab(self):
    if not self._opened:
      raise RuntimeError("capture not open")
    return True

  def retrieve(self):
    return self.read()

  def set(self, *_args, **_kwargs):
    return True

  def get(self, prop):
    if prop == cv2.CAP_PROP_FPS:
      return 25
    if prop == cv2.CAP_PROP_FRAME_HEIGHT:
      return self._frame.shape[0]
    if prop == cv2.CAP_PROP_FRAME_WIDTH:
      return self._frame.shape[1]
    if prop == cv2.CAP_PROP_FRAME_COUNT:
      return 100
    if prop == cv2.CAP_PROP_BUFFERSIZE:
      return self._buffersize
    return 0

  def release(self):
    self._opened = False


class MockVideoCaptureFactory:
  def __init__(self):
    self._registry = {}

  def register(self, url, frame, succeed=True, fail_reads=0):
    self._registry[url] = {
      "frame": frame,
      "succeed": succeed,
      "fail_reads": fail_reads,
    }

  def set_succeed(self, url, succeed, fail_reads=0):
    entry = self._registry[url]
    entry["succeed"] = succeed
    entry["fail_reads"] = fail_reads

  def __call__(self, url, *_args, **_kwargs):
    entry = self._registry[url]
    return MockCapture(
      frame=entry["frame"],
      fail_open=not entry["succeed"],
      fail_reads=entry["fail_reads"],
    )


def wait_for(predicate, timeout=1.0, step=0.01):
  start = time.time()
  while time.time() - start < timeout:
    if predicate():
      return True
    time.sleep(step)
  return predicate()


class MockLogger:
  def __init__(self):
    self._logger = self
    self.config_data = {}
    self._timers = {}

  def P(self, *_args, **_kwargs):
    return

  def set_nice_prints(self):
    return

  def name_abbreviation(self, text):
    return text[:8]

  def dict_pretty_format(self, data):
    return str(data)

  def start_timer(self, sname=None, section=None, **_kwargs):
    key = (section, sname)
    entry = self._timers.setdefault(key, {"durations": [], "start": None})
    entry["start"] = time.time()

  def end_timer(self, sname=None, section=None, skip_first_timing=True, periodic=False):
    key = (section, sname)
    entry = self._timers.get(key)
    if entry and entry.get("start") is not None:
      duration = max(time.time() - entry["start"], 0.0)
      entry["durations"].append(duration)
      entry["start"] = None
      return duration
    return 0.0

  def get_timer(self, skey=None, section=None):
    key = (section, skey)
    entry = self._timers.get(key)
    if not entry or not entry["durations"]:
      return {"MEAN": 0.0, "COUNT": 0}
    durations = entry["durations"]
    return {"MEAN": sum(durations) / len(durations), "COUNT": len(durations)}

  def now_str(self, nice_print=True, short=False):
    return time.strftime("%Y-%m-%d %H:%M:%S")

  def get_processor_platform(self):
    return "intel"

  def check_folder_data(self, *_args, **_kwargs):
    return "."

  def maybe_download(self, *_args, **_kwargs):
    return None

  def get_class_methods(self, cls, include_parent=False):
    methods = []
    if include_parent:
      mro = cls.mro()
    else:
      mro = [cls]
    for klass in mro:
      for name in dir(klass):
        attr = getattr(klass, name)
        if callable(attr):
          methods.append((name, attr))
    return methods


class MultiVideoStreamCv2Tests(unittest.TestCase):
  def setUp(self):
    self.logger = MockLogger()
    self.shmem = {
      ct.BLOCKCHAIN_MANAGER: DummyBC(),
      ct.CAPTURE_MANAGER: {ct.NR_CAPTURES: 1},
      ct.CALLBACKS.PIPELINE_CONFIG_SAVER_CALLBACK: lambda *args, **kwargs: None,
    }

  def tearDown(self):
    self.logger = None

  def _build_config(self):
    return {
      "NAME": "test_multi",
      "CAP_RESOLUTION": 1,
      "SOURCES": [
        {"NAME": "cam_a", "URL": "mock://camera_a"},
        {"NAME": "cam_b", "URL": "mock://camera_b"},
      ],
      "SLEEP_TIME_ON_ERROR": 1,
      "MAX_RETRIES": 2,
    }

  def test_async_multi_stream_reconnection(self):
    factory = MockVideoCaptureFactory()
    frame_a = np.zeros((8, 8, 3), dtype=np.uint8)
    frame_b = np.ones((8, 8, 3), dtype=np.uint8) * 255
    factory.register("mock://camera_a", frame_a, succeed=True)
    factory.register("mock://camera_b", frame_b, succeed=False)

    def no_sleep(self, _seconds):
      return

    config = self._build_config()

    with mock.patch("naeural_core.data.default.video.multi_vide_stream_cv2.cv2.VideoCapture", side_effect=factory), \
         mock.patch.object(MultiVideoStreamCv2DataCapture, "sleep", new=no_sleep):
      dct = MultiVideoStreamCv2DataCapture(
        log=self.logger,
        default_config=MultiVideoStreamCv2DataCapture.CONFIG,
        upstream_config=config,
        environment_variables={},
        shmem=self.shmem,
        signature="MULTI_VIDE_STREAM_CV2",
        fn_loop_stage_callback=lambda *args, **kwargs: None,
      )

      dct.is_intel = lambda: True

      try:
        self.assertTrue(wait_for(lambda: dct._streams["cam_a"].has_connection))
        self.assertFalse(dct._streams["cam_b"].has_connection)

        dct._run_data_aquisition_step()
        self.assertTrue(dct.has_data)
        data = dct.get_data_capture()
        inputs = data["INPUTS"]
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]["METADATA"]["source_name"], "cam_a")

        factory.set_succeed("mock://camera_b", True)
        self.assertTrue(wait_for(lambda: dct._streams["cam_b"].has_connection))

        dct._run_data_aquisition_step()
        self.assertTrue(dct.has_data)
        data2 = dct.get_data_capture()
        names = {inp["METADATA"]["source_name"] for inp in data2["INPUTS"]}
        self.assertEqual(names, {"cam_a", "cam_b"})
      finally:
        dct._stop = True
        dct._release()


if __name__ == "__main__":
  unittest.main()
