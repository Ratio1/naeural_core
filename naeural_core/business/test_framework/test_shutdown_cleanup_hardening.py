import importlib.util
import pathlib
import subprocess
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from naeural_core import constants as ct
from naeural_core.data.capture_manager import CaptureManager


def _load_base_tunnel_module():
  """Load BaseTunnelEnginePlugin with lightweight bases for shutdown tests."""
  path = (
    pathlib.Path(__file__).resolve().parents[2]
    / "business" / "base" / "web_app" / "base_tunnel_engine_plugin.py"
  )
  module_name = "base_tunnel_engine_plugin_under_test"
  module = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(module_name, path)
  )

  class _BasePluginExecutor:
    CONFIG = {"VALIDATION_RULES": {}}

  class _NgrokMixinPlugin:
    pass

  class _CloudflareMixinPlugin:
    pass

  stubs = {
    "naeural_core.business.base": types.SimpleNamespace(BasePluginExecutor=_BasePluginExecutor),
    "naeural_core.business.mixins_libs.ngrok_mixin": types.SimpleNamespace(
      _NgrokMixinPlugin=_NgrokMixinPlugin,
    ),
    "naeural_core.business.mixins_libs.cloudflare_mixin": types.SimpleNamespace(
      _CloudflareMixinPlugin=_CloudflareMixinPlugin,
    ),
  }
  old_modules = {
    name: sys.modules.get(name)
    for name in stubs
  }
  try:
    sys.modules.update(stubs)
    module.__spec__.loader.exec_module(module)
  finally:
    for name, old_module in old_modules.items():
      if old_module is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = old_module
  return module


_TUNNEL_MODULE = _load_base_tunnel_module()
BaseTunnelEnginePlugin = _TUNNEL_MODULE.BaseTunnelEnginePlugin


class _Owner:
  def __init__(self):
    self.stages = []

  def set_loop_stage(self, stage):
    self.stages.append(stage)
    return


class _Capture:
  cfg_type = "TEST_CAPTURE"
  cfg_is_thread = True

  def __init__(self, stop_results):
    self.stop_results = list(stop_results)
    self.stop_calls = []
    self.update_calls = 0

  def stop(self, join_time=10):
    self.stop_calls.append(join_time)
    if self.stop_results:
      return self.stop_results.pop(0)
    return True

  def maybe_update_config(self, config_stream):
    self.update_calls += 1
    return


def _make_capture_manager(captures):
  manager = CaptureManager.__new__(CaptureManager)
  manager._dct_captures = dict(captures)
  manager._dct_killed_captures = {}
  manager._dct_config_streams = {}
  manager.shmem = {ct.CAPTURE_MANAGER: {}}
  manager.owner = _Owner()
  manager.messages = []
  manager.P = lambda msg, *args, **kwargs: manager.messages.append(str(msg))
  manager.log = SimpleNamespace(P=lambda *args, **kwargs: None)
  return manager


class TestCaptureShutdownHardening(unittest.TestCase):

  def test_failed_capture_marker_is_removed_once_stopped(self):
    manager = _make_capture_manager({"bad": None})

    self.assertEqual(manager.get_finished_streams(), ["bad"])
    self.assertTrue(manager.stop_capture("bad"))

    self.assertNotIn("bad", manager._dct_captures)
    self.assertIsNone(manager._dct_killed_captures["bad"])
    self.assertEqual(manager.get_finished_streams(), [])
    return

  def test_none_stop_return_is_treated_as_success(self):
    capture = _Capture([None])
    manager = _make_capture_manager({"meta": capture})

    self.assertTrue(manager.stop_capture("meta"))

    self.assertNotIn("meta", manager._dct_captures)
    self.assertIsNone(manager._dct_killed_captures["meta"])
    self.assertEqual(capture.stop_calls, [10])
    return

  def test_failed_stop_moves_capture_out_of_active_map(self):
    stale = _Capture([False])
    manager = _make_capture_manager({"stream": stale})

    self.assertFalse(manager.stop_capture("stream"))
    self.assertNotIn("stream", manager._dct_captures)
    self.assertIs(manager._dct_killed_captures["stream"], stale)

    fresh = _Capture([True])
    manager._dct_config_streams = {
      "stream": {ct.NAME: "stream", ct.TYPE: "TEST_CAPTURE"}
    }
    manager.start_capture = lambda config: manager._dct_captures.setdefault("stream", fresh) is fresh

    manager._check_captures()

    self.assertIs(manager._dct_captures["stream"], fresh)
    self.assertEqual(stale.update_calls, 0)
    return

  def test_stop_captures_reports_pending_cleanup_failure(self):
    pending = _Capture([False])
    manager = _make_capture_manager({})
    manager._dct_killed_captures["stream"] = pending

    self.assertFalse(manager.stop_captures(shutdown=True))

    self.assertIs(manager._dct_killed_captures["stream"], pending)
    self.assertEqual(pending.stop_calls, [0.1])
    return

  def test_stop_captures_preserves_same_key_pending_cleanup(self):
    pending = _Capture([False])
    fresh = _Capture([True])
    manager = _make_capture_manager({"stream": fresh})
    manager._dct_killed_captures["stream"] = pending

    self.assertFalse(manager.stop_captures(shutdown=True))

    self.assertNotIn("stream", manager._dct_captures)
    self.assertIs(manager._dct_killed_captures["stream"], pending)
    self.assertEqual(fresh.stop_calls, [0.1])
    self.assertEqual(pending.stop_calls, [0.1])
    return


class _FakeProcess:
  def __init__(self):
    self.terminated = False
    self.killed = False
    self.wait_calls = 0

  def poll(self):
    return 0 if self.killed else None

  def terminate(self):
    self.terminated = True
    return

  def kill(self):
    self.killed = True
    return

  def wait(self, timeout=None):
    self.wait_calls += 1
    if self.wait_calls == 1:
      raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
    self.killed = True
    return 0


class TestTunnelShutdownHardening(unittest.TestCase):

  def test_windows_fallback_kills_process_without_sigkill(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _FakeProcess()
    signal_without_sigkill = SimpleNamespace(SIGTERM=_TUNNEL_MODULE.signal.SIGTERM)
    with mock.patch.object(_TUNNEL_MODULE.os, "name", "nt"), \
         mock.patch.object(_TUNNEL_MODULE, "signal", signal_without_sigkill):
      self.assertTrue(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertTrue(process.terminated)
    self.assertTrue(process.killed)
    return


if __name__ == "__main__":
  unittest.main()
