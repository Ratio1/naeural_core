import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock
from naeural_core.utils.process_credentials import child_environment, credential_env_names, redact_command


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


def _load_base_web_app_module():
  """Load BaseWebAppPlugin with lightweight dependencies for log tests."""
  path = (
    pathlib.Path(__file__).resolve().parents[2]
    / "business" / "base" / "web_app" / "base_web_app_plugin.py"
  )
  module_name = "base_web_app_plugin_under_test"
  module = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(module_name, path)
  )

  class _BasePluginExecutor:
    CONFIG = {"VALIDATION_RULES": {}}

  core_mod = types.ModuleType("naeural_core")
  business_mod = types.ModuleType("naeural_core.business")
  base_mod = types.ModuleType("naeural_core.business.base")
  base_mod.BasePluginExecutor = _BasePluginExecutor
  web_app_mod = types.ModuleType("naeural_core.business.base.web_app")
  tunnel_mod = types.ModuleType(
    "naeural_core.business.base.web_app.base_tunnel_engine_plugin"
  )
  tunnel_mod.BaseTunnelEnginePlugin = BaseTunnelEnginePlugin
  jinja_mod = types.ModuleType("jinja2")
  jinja_mod.Environment = mock.Mock()
  jinja_mod.FileSystemLoader = mock.Mock()
  psutil_mod = types.ModuleType("psutil")
  stubs = {
    "jinja2": jinja_mod,
    "psutil": psutil_mod,
    "naeural_core": core_mod,
    "naeural_core.business": business_mod,
    "naeural_core.business.base": base_mod,
    "naeural_core.business.base.web_app": web_app_mod,
    "naeural_core.business.base.web_app.base_tunnel_engine_plugin": tunnel_mod,
  }
  old_modules = {name: sys.modules.get(name) for name in stubs}
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


_BASE_WEB_APP_MODULE = _load_base_web_app_module()
BaseWebAppPlugin = _BASE_WEB_APP_MODULE.BaseWebAppPlugin


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

  def test_managed_web_app_tunnel_relaunch_uses_a_new_stdin_pipe(self):
    """Verify token delivery and argument isolation on initial launch and relaunch.

    The subprocess fixture reads the real pipe supplied by Popen and checks
    that both managed launches use the node's configured installation.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestTunnelShutdownHardening().test_managed_web_app_tunnel_relaunch_uses_a_new_stdin_pipe()
    """
    # Build a plugin without network or application startup.
    plugin = BaseWebAppPlugin.__new__(BaseWebAppPlugin)
    # The configured tunnel command carries only the token-file switch.
    command = "cloudflared tunnel --no-autoupdate run --token-file /dev/stdin --url http://127.0.0.1:8080"
    # Both launches consume the same configured fixture through independent pipes.
    token = "managed test credential; one opaque value"
    plugin._get_cloudflare_start_command = lambda: command
    plugin.get_cloudflare_token = lambda: token
    plugin.os_path = os.path
    plugin.LogReader = mock.Mock()
    process = mock.Mock()
    pipe_reads = []
    with tempfile.TemporaryDirectory() as folder:
      # Place the node installation and app working directory in separate locations.
      root = pathlib.Path(folder)
      node_bin = root / "node-bin"
      node_bin.mkdir()
      app_dir = root / "app"
      app_dir.mkdir()
      # Make an executable marker for resolution only. Popen is mocked and never executes it.
      connector = node_bin / "cloudflared"
      connector.touch()
      connector.chmod(0o700)
      # Real web-app commands use the script directory for their current directory.
      plugin.script_temp_dir = str(app_dir)
      # Capture Popen's stdin during launch, before the parent releases its copy.
      popen = mock.Mock(side_effect=lambda **kwargs: (pipe_reads.append(os.read(kwargs["stdin"], 4096)) or process))
      # Simulate the operator making its connector available to the node process.
      with mock.patch.dict(os.environ, {"PATH": str(node_bin)}), mock.patch.object(_BASE_WEB_APP_MODULE.subprocess, "Popen", popen):
        for _ in range(2):
          # The app's scoped tokens and the tunnel credential cannot become connector environment variables.
          plugin._BaseWebAppPlugin__run_command(command, {
            "PATH": str(app_dir), "APP_TOKEN": "app-secret", "ALIAS": token, "FLAG": "normal",
          })
    # Both pipes contained the full token, and their process arguments were identical and token-free.
    self.assertEqual(pipe_reads, [token.encode(), token.encode()])
    # The connector runs from the node installation with only safe inherited settings.
    args = [str(connector), *command.split()[1:]]
    self.assertEqual([call.kwargs["args"] for call in popen.call_args_list], [args, args])
    self.assertEqual([call.kwargs["env"] for call in popen.call_args_list], [{"PATH": str(node_bin), "FLAG": "normal"}] * 2)

  def test_managed_connector_requires_node_installation_only_with_token(self):
    """Require a managed connector before staging a token.

    Tokenless and ordinary app launches keep the search path of the caller.
    """
    # Construct the launch boundary without starting a web app or connection.
    plugin = BaseWebAppPlugin.__new__(BaseWebAppPlugin)
    managed = "cloudflared tunnel --no-autoupdate run --token-file /dev/stdin --url http://127.0.0.1:8080"
    plugin._get_cloudflare_start_command = lambda: managed
    plugin.get_cloudflare_token = lambda: "synthetic-token"
    plugin.os_path = os.path
    with tempfile.TemporaryDirectory() as folder:
      # Set the node search path to a directory with no installed connector.
      plugin.script_temp_dir = folder
      parent_path = {"PATH": folder}
      app_env = {"PATH": os.path.join(folder, "app-bin"), "FLAG": "normal"}
      with mock.patch.dict(os.environ, parent_path):
        # Observe the credential helper to prove no managed pipe is opened without an installation.
        with mock.patch.object(_BASE_WEB_APP_MODULE, "launch_with_tunnel_token") as launch:
          with self.assertRaisesRegex(FileNotFoundError, "Install cloudflared for the node"):
            plugin._BaseWebAppPlugin__run_command(managed, app_env, False, False)
          launch.assert_not_called()
        # Capture normal launch options without executing either fixture command.
        with mock.patch.object(_BASE_WEB_APP_MODULE.subprocess, "Popen") as popen:
          for command in ("cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8080", "python app.py"):
            with self.subTest(command=command):
              plugin._BaseWebAppPlugin__run_command(command, app_env, False, False)
              self.assertEqual(popen.call_args.kwargs["args"], command.split())
              self.assertEqual(popen.call_args.kwargs["env"], app_env)
              self.assertNotIn("stdin", popen.call_args.kwargs)

  def test_command_redaction_displays_safe_arguments_and_discoverable_names(self):
    """Cover explicit credential names, keyword discovery, and whole command arguments.

    Synthetic values mimic configured and inherited credentials.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestTunnelShutdownHardening().test_command_redaction_displays_safe_arguments_and_discoverable_names()
    """
    # MQTT has no keyword, while the installed app credential is discovered by name.
    environment = {"PATH": "/bin", "EE_MQTT": "mnemonic-fixture", "APP_SECRET": "synthetic-key", "ALIAS": "synthetic-key"}
    self.assertEqual(credential_env_names(environment), {"EE_MQTT", "APP_SECRET"})
    self.assertEqual(child_environment(environment), {"PATH": "/bin"})
    # The original namespace must remain available to node-side providers.
    self.assertEqual(environment["APP_SECRET"], "synthetic-key")
    # A short credential can be embedded in a larger setting. Preserve empty settings and case variants.
    composite = {"APP_TOKEN": "q+", "ENDPOINT": "origin/q+/path", "SCOPED_ALIAS": "origin/Z7/path",
                 "NORMAL_PATH": "origin/qq/Q+/path", "EMPTY_TOKEN": "", "OPTIONAL_VALUE": ""}
    self.assertEqual(child_environment(composite, extra_values=("Z7",)),
                     {"NORMAL_PATH": "origin/qq/Q+/path", "OPTIONAL_VALUE": ""})
    self.assertEqual(
      redact_command("tool --token 'first part'last --database-password=other --node target mnemonic-fixture", environment),
      "tool --token [REDACTED] --database-password=[REDACTED] --node target [REDACTED]",
    )
    self.assertEqual(
      redact_command(["tool", "--api-key", "whole argument", "ENV_SECRET=multiple words", "--port", "4000"], environment),
      ["tool", "--api-key", "[REDACTED]", "ENV_SECRET=[REDACTED]", "--port", "4000"],
    )
    # Embedded URL authentication disappears while its destination stays visible.
    self.assertEqual(redact_command("tool https://user:secret@example.test/path", {}),
                     "tool https://[REDACTED]@example.test/path")

  def test_application_snapshots_use_only_safe_settings(self):
    """Keep an explicitly configured app credential in memory and off disk.

    Exercises the setup and start environments plus all three launcher
    snapshots on the normal asset initialization path.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestTunnelShutdownHardening().test_application_snapshots_use_only_safe_settings()
    """
    # Use synthetic node and app credentials that must never reach an unrelated child or a snapshot.
    node_secret, app_secret = "node-store-fixture", "scoped-app-fixture"
    plugin = BaseWebAppPlugin.__new__(BaseWebAppPlugin)
    plugin.P = mock.Mock()
    plugin.os_path = os.path
    plugin.os_environ = {"PATH": "/bin", "REDMESH_SECRET_STORE_KEY": node_secret, "EE_MQTT": "broker-fixture"}
    plugin.str_unique_identification = "fixture"
    plugin.plugins_shmem = {"USED_PORTS": {"fixture": 8080}}
    plugin.cfg_env_vars = {}
    plugin.get_additional_env_vars = lambda: {}
    plugin.plugin_id = "fixture"
    with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as destination:
      # A deployed app can still supply its own credential through its dotenv asset.
      pathlib.Path(source, ".env").write_text(f"APP_TOKEN={app_secret}\nFLAG=visible\n", encoding="utf-8")
      plugin.script_temp_dir = destination
      plugin.get_output_folder = lambda: destination
      app_environment = plugin._BaseWebAppPlugin__prepare_env(source)
      plugin.initialize_assets(source, destination, {})
      # The app receives its own token, while setup retains ordinary environment settings.
      self.assertEqual(app_environment["APP_TOKEN"], app_secret)
      self.assertEqual(plugin.base_env["PATH"], "/bin")
      self.assertNotIn("REDMESH_SECRET_STORE_KEY", app_environment)
      self.assertNotIn("EE_MQTT", app_environment)
      self.assertEqual(plugin.os_environ["REDMESH_SECRET_STORE_KEY"], node_secret)
      for name in (".env", ".start_env_used", ".setup_env_used"):
        # Each file retains safe settings and neither parent nor scoped credentials.
        snapshot = pathlib.Path(destination, name).read_text(encoding="utf-8")
        self.assertIn("PATH=/bin\n", snapshot)
        self.assertNotIn(node_secret, snapshot)
        self.assertNotIn(app_secret, snapshot)

  def test_cloudflare_tunnel_start_redacts_token_but_executes_original_command(self):
    token = "cloudflare-tunnel-secret"
    command = (
      "cloudflared tunnel --no-autoupdate run "
      f"--token {token} --url http://127.0.0.1:8080"
    )
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    plugin.LogReader = mock.Mock(side_effect=lambda *args, **kwargs: mock.Mock())
    plugin._remember_process_group = mock.Mock()
    process = mock.Mock(stdout=mock.Mock(), stderr=mock.Mock())

    with mock.patch.object(_TUNNEL_MODULE.subprocess, "Popen", return_value=process) as popen:
      result = plugin.run_tunnel_command(command)

    self.assertIs(result, process)
    self.assertEqual(popen.call_args.kwargs["args"], command)
    self.assertNotIn(token, "\n".join(plugin.messages))
    self.assertIn("--token [REDACTED]", "\n".join(plugin.messages))
    return

  def test_web_app_tunnel_start_logs_are_redacted_but_execution_is_unchanged(self):
    """Check how the web-app start sequence presents a tunnel command.

    Capture the command at the execution boundary and compare it with the
    original. The setup and start logs and the status payload must omit the
    token while retaining safe fields such as the origin URL.

    Parameters
    ----------
    None

    Returns
    -------
    None

    Examples
    --------
    >>> TestTunnelShutdownHardening().test_web_app_tunnel_start_logs_are_redacted_but_execution_is_unchanged()  # doctest: +SKIP
    """
    token = "cloudflare-web-app-secret"
    command = (
      "cloudflared tunnel --no-autoupdate run "
      f"--token {token} --url http://127.0.0.1:8080"
    )
    plugin = BaseWebAppPlugin.__new__(BaseWebAppPlugin)
    plugin.messages = []
    plugin.P = lambda msg, *args, **kwargs: plugin.messages.append(str(msg))
    plugin.str_unique_identification = "test-web-app"
    plugin.plugins_shmem = {"USED_PORTS": {"test-web-app": 8080}}
    plugin.deque = deque
    plugin.failed = False
    plugin.get_setup_commands = lambda: []
    plugin.get_start_commands = lambda: [command]
    plugin.prepared_env = {}
    plugin.add_payload_by_fields = mock.Mock()
    plugin.time = mock.Mock(side_effect=[0, 10])
    process = mock.Mock()
    executed = []
    plugin._BaseWebAppPlugin__run_command = (
      lambda cmd, env: (executed.append(cmd) or process, None, None)
    )
    plugin._BaseWebAppPlugin__wait_for_command = lambda process, timeout: (False, False)

    plugin._BaseWebAppPlugin__setup_commands()
    plugin._BaseWebAppPlugin__maybe_run_nth_start_command(0, timeout=5)

    serialized_logs = "\n".join(plugin.messages)
    self.assertEqual(executed, [command])
    self.assertNotIn(token, serialized_logs)
    self.assertEqual(serialized_logs.count("[REDACTED]"), 3)
    # The command status uses the same safe rendering as the logs.
    status_command = plugin.add_payload_by_fields.call_args.kwargs["command_str"]
    self.assertNotIn(token, status_command)
    self.assertIn("--url http://127.0.0.1:8080", status_command)
    return

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
