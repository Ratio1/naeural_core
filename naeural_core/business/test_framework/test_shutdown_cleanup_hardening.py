import importlib.util
import pathlib
import subprocess
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock


def _load_capture_manager_module():
  """Load CaptureManager with lightweight stubs for focused cleanup tests."""
  path = pathlib.Path(__file__).resolve().parents[2] / "data" / "capture_manager.py"
  module_name = "capture_manager_under_test"
  module = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(module_name, path)
  )

  ct = SimpleNamespace(
    CAPTURE_MANAGER="CAPTURE_MANAGER",
    NR_CAPTURES="NR_CAPTURES",
    TYPE="TYPE",
    NAME="NAME",
    URL="URL",
    CONST_ADMIN_PIPELINE_NAME="admin_pipeline",
    CAPTURE_STATS_DISPLAY="CAPTURE_STATS_DISPLAY",
    CAPTURE_STATS_DISPLAY_DEFAULT=60,
    EE_ALIAS_MAX_SIZE=20,
    STATUS_TYPE=SimpleNamespace(
      STATUS_NORMAL="STATUS_NORMAL",
      STATUS_EXCEPTION="STATUS_EXCEPTION",
      STATUS_ABNORMAL_FUNCTIONING="STATUS_ABNORMAL_FUNCTIONING",
    ),
    NOTIFICATION_CODES=SimpleNamespace(
      PIPELINE_OK="PIPELINE_OK",
      PIPELINE_FAILED="PIPELINE_FAILED",
    ),
    PAYLOAD_DATA=SimpleNamespace(
      SESSION_ID="SESSION_ID",
      INITIATOR_ID="INITIATOR_ID",
    ),
    PLUGIN_SEARCH=SimpleNamespace(
      LOC_DATA_ACQUISITION_PLUGINS=[],
      SUFFIX_DATA_ACQUISITION_PLUGINS="",
      SAFE_LOC_DATA_ACQUISITION_PLUGINS=[],
      SAFE_LOC_DATA_ACQUISITION_IMPORTS=[],
    ),
    CONFIG_STARTUP_v2=SimpleNamespace(K_EE_ID="EE_ID"),
  )

  class _Manager:
    pass

  class _ConfigHandlerMixin:
    pass

  class _Logger:
    pass

  pandas_mod = types.ModuleType("pandas")
  pandas_mod.DataFrame = lambda obj: obj
  pandas_mod.set_option = lambda *args, **kwargs: None

  ct_mod = types.ModuleType("naeural_core.constants")
  for name, value in ct.__dict__.items():
    setattr(ct_mod, name, value)

  core_mod = types.ModuleType("naeural_core")
  core_mod.constants = ct_mod
  core_mod.Logger = _Logger
  manager_mod = types.ModuleType("naeural_core.manager")
  manager_mod.Manager = _Manager
  libraries_mod = types.ModuleType("naeural_core.local_libraries")
  libraries_mod._ConfigHandlerMixin = _ConfigHandlerMixin

  stubs = {
    "pandas": pandas_mod,
    "naeural_core": core_mod,
    "naeural_core.constants": ct_mod,
    "naeural_core.manager": manager_mod,
    "naeural_core.local_libraries": libraries_mod,
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

  core_mod = types.ModuleType("naeural_core")
  business_mod = types.ModuleType("naeural_core.business")
  base_mod = types.ModuleType("naeural_core.business.base")
  base_mod.BasePluginExecutor = _BasePluginExecutor
  mixins_mod = types.ModuleType("naeural_core.business.mixins_libs")
  ngrok_mod = types.ModuleType("naeural_core.business.mixins_libs.ngrok_mixin")
  ngrok_mod._NgrokMixinPlugin = _NgrokMixinPlugin
  cloudflare_mod = types.ModuleType("naeural_core.business.mixins_libs.cloudflare_mixin")
  cloudflare_mod._CloudflareMixinPlugin = _CloudflareMixinPlugin

  stubs = {
    "naeural_core": core_mod,
    "naeural_core.business": business_mod,
    "naeural_core.business.base": base_mod,
    "naeural_core.business.mixins_libs": mixins_mod,
    "naeural_core.business.mixins_libs.ngrok_mixin": ngrok_mod,
    "naeural_core.business.mixins_libs.cloudflare_mixin": cloudflare_mod,
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


_CAPTURE_MANAGER_MODULE = _load_capture_manager_module()
ct = _CAPTURE_MANAGER_MODULE.ct
CaptureManager = _CAPTURE_MANAGER_MODULE.CaptureManager
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

  def test_update_streams_retries_pending_killed_capture_cleanup(self):
    pending = _Capture([True])
    manager = _make_capture_manager({})
    manager._dct_config_streams = {}
    manager._dct_killed_captures["stream"] = pending

    manager.update_streams({})

    self.assertIsNone(manager._dct_killed_captures["stream"])
    self.assertEqual(pending.stop_calls, [10])
    return

  def test_update_streams_throttles_repeated_pending_cleanup_failures(self):
    pending = _Capture([False, True])
    manager = _make_capture_manager({})
    manager._dct_config_streams = {}
    manager._dct_killed_captures["stream"] = pending

    with mock.patch.object(_CAPTURE_MANAGER_MODULE, "time", side_effect=[100, 101, 131]):
      manager.update_streams({})
      manager.update_streams({})
      manager.update_streams({})

    self.assertIsNone(manager._dct_killed_captures["stream"])
    self.assertEqual(pending.stop_calls, [10, 10])
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


class _StoppedProcess:
  def __init__(self, pgid):
    self.pid = pgid
    self._r1_process_group_id = pgid
    self.terminated = False
    self.killed = False

  def poll(self):
    return 0

  def terminate(self):
    self.terminated = True
    return

  def kill(self):
    self.killed = True
    return

  def wait(self, timeout=None):
    return 0


def _fake_proc_stat_open(proc_entries):
  def fake_open(path, *args, **kwargs):
    pid_name = pathlib.Path(path).parent.name
    if pid_name not in proc_entries:
      raise FileNotFoundError(path)
    entry = proc_entries[pid_name]
    if isinstance(entry, Exception):
      raise entry
    if len(entry) == 2:
      state, pgid = entry
      comm = "cloudflared"
    else:
      state, pgid, comm = entry
    stat = f"{pid_name} ({comm}) {state} 1 {pgid} 0 0\n"
    return mock.mock_open(read_data=stat)()
  return fake_open


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

  def test_zombie_only_process_group_is_treated_as_stopped(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {"456": ("Z", 123)}

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertTrue(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_live_process_group_member_keeps_shutdown_failed(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {"456": ("S", 123)}

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertFalse(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_mixed_zombie_and_live_members_keep_shutdown_failed(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {
      "456": ("Z", 123),
      "789": ("S", 123),
    }

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertFalse(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_stopped_or_traced_group_members_keep_shutdown_failed(self):
    for state in ("T", "t"):
      with self.subTest(state=state):
        plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
        plugin.messages = []
        plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
        process = _StoppedProcess(pgid=123)
        proc_entries = {"456": (state, 123)}

        with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
             mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
             mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
             mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
             mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
          self.assertFalse(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)
    return

  def test_vanished_proc_entry_does_not_block_zombie_only_group(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {"456": ("Z", 123)}

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=["456", "789"]), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertTrue(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_mismatched_process_group_members_are_ignored(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {
      "456": ("Z", 123),
      "789": ("S", 999),
    }

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertTrue(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_unreadable_proc_entry_does_not_block_zombie_only_group(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {
      "456": ("Z", 123),
      "789": RuntimeError("broken stat"),
    }

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertTrue(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_process_group_with_no_matching_proc_members_remains_conservative(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {"456": ("S", 999)}

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertFalse(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))

    self.assertFalse(process.terminated)
    self.assertFalse(process.killed)
    return

  def test_proc_stat_parser_handles_command_names_with_parentheses(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)
    proc_entries = {"456": ("Z", 123, "cloud flared (worker)")}

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=True), \
         mock.patch.object(_TUNNEL_MODULE.os, "listdir", return_value=list(proc_entries)), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None), \
         mock.patch("builtins.open", _fake_proc_stat_open(proc_entries)):
      self.assertTrue(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))
    return

  def test_proc_unavailable_keeps_process_group_conservatively_failed(self):
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    process = _StoppedProcess(pgid=123)

    with mock.patch.object(_TUNNEL_MODULE.os, "name", "posix"), \
         mock.patch.object(_TUNNEL_MODULE.os.path, "isdir", return_value=False), \
         mock.patch.object(_TUNNEL_MODULE.os, "killpg", return_value=None):
      self.assertFalse(plugin._terminate_subprocess_tree(process, terminate_timeout=0, kill_timeout=0))
    return


if __name__ == "__main__":
  unittest.main()
