import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


_CORE_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_module(module_name, path, stubs=None):
  """Load a focused module while temporarily installing lightweight stubs."""
  module = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(module_name, path)
  )
  stubs = stubs or {}
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


_LOGGING_UTILS = _load_module(
  "cloudflare_logging_utils_under_test",
  _CORE_ROOT / "utils" / "logging_utils.py",
)


def _common_stubs():
  core_mod = types.ModuleType("naeural_core")
  business_mod = types.ModuleType("naeural_core.business")
  base_mod = types.ModuleType("naeural_core.business.base")
  utils_mod = types.ModuleType("naeural_core.utils")

  class _BasePluginExecutor:
    CONFIG = {"VALIDATION_RULES": {}}

  base_mod.BasePluginExecutor = _BasePluginExecutor
  return {
    "naeural_core": core_mod,
    "naeural_core.business": business_mod,
    "naeural_core.business.base": base_mod,
    "naeural_core.utils": utils_mod,
    "naeural_core.utils.logging_utils": _LOGGING_UTILS,
  }


def _load_base_tunnel_module():
  stubs = _common_stubs()
  mixins_mod = types.ModuleType("naeural_core.business.mixins_libs")
  ngrok_mod = types.ModuleType("naeural_core.business.mixins_libs.ngrok_mixin")
  cloudflare_mod = types.ModuleType("naeural_core.business.mixins_libs.cloudflare_mixin")

  class _NgrokMixinPlugin:
    pass

  class _CloudflareMixinPlugin:
    pass

  ngrok_mod._NgrokMixinPlugin = _NgrokMixinPlugin
  cloudflare_mod._CloudflareMixinPlugin = _CloudflareMixinPlugin
  stubs.update({
    "naeural_core.business.mixins_libs": mixins_mod,
    "naeural_core.business.mixins_libs.ngrok_mixin": ngrok_mod,
    "naeural_core.business.mixins_libs.cloudflare_mixin": cloudflare_mod,
  })
  return _load_module(
    "cloudflare_base_tunnel_under_test",
    _CORE_ROOT / "business" / "base" / "web_app" / "base_tunnel_engine_plugin.py",
    stubs,
  )


_BASE_TUNNEL_MODULE = _load_base_tunnel_module()
BaseTunnelEnginePlugin = _BASE_TUNNEL_MODULE.BaseTunnelEnginePlugin


def _load_base_web_app_module():
  stubs = _common_stubs()
  web_app_mod = types.ModuleType("naeural_core.business.base.web_app")
  tunnel_mod = types.ModuleType(
    "naeural_core.business.base.web_app.base_tunnel_engine_plugin"
  )
  jinja_mod = types.ModuleType("jinja2")
  jinja_mod.Environment = Mock()
  jinja_mod.FileSystemLoader = Mock()
  psutil_mod = types.ModuleType("psutil")
  tunnel_mod.BaseTunnelEnginePlugin = BaseTunnelEnginePlugin
  stubs.update({
    "jinja2": jinja_mod,
    "psutil": psutil_mod,
    "naeural_core.business.base.web_app": web_app_mod,
    "naeural_core.business.base.web_app.base_tunnel_engine_plugin": tunnel_mod,
  })
  return _load_module(
    "cloudflare_base_web_app_under_test",
    _CORE_ROOT / "business" / "base" / "web_app" / "base_web_app_plugin.py",
    stubs,
  )


_BASE_WEB_APP_MODULE = _load_base_web_app_module()
BaseWebAppPlugin = _BASE_WEB_APP_MODULE.BaseWebAppPlugin


def _load_config_check_module():
  stubs = _common_stubs()
  constants_mod = types.ModuleType("naeural_core.constants")
  constants_mod.STATUS_TYPE = types.SimpleNamespace(
    STATUS_EXCEPTION="STATUS_EXCEPTION",
  )
  constants_mod.CONFIG_STREAM = types.SimpleNamespace(
    NAME="NAME",
    TYPE="TYPE",
    ALLOWED_PLUGINS="ALLOWED_PLUGINS",
    PLUGINS="PLUGINS",
    INSTANCES="INSTANCES",
  )
  constants_mod.PAYLOAD_DATA = types.SimpleNamespace(
    SIGNATURE="SIGNATURE",
    INITIATOR_ID="INITIATOR_ID",
    SESSION_ID="SESSION_ID",
  )
  constants_mod.PLUGIN_INFO = types.SimpleNamespace(
    SIGNATURE="SIGNATURE",
    INSTANCE_ID="INSTANCE_ID",
  )
  constants_mod.COLORS = types.SimpleNamespace(DCT="DCT")
  stubs["naeural_core"].constants = constants_mod
  stubs["naeural_core.constants"] = constants_mod
  return _load_module(
    "cloudflare_config_check_under_test",
    _CORE_ROOT / "config" / "mixins" / "config_manager_check_mixin.py",
    stubs,
  )


_CONFIG_CHECK_MODULE = _load_config_check_module()
ConfigManagerCheckMixin = _CONFIG_CHECK_MODULE._ConfigManagerCheckMixin
CLOUDFLARE_LOG_REDACTION = _LOGGING_UTILS.CLOUDFLARE_LOG_REDACTION
redact_cloudflare_tokens = _LOGGING_UTILS.redact_cloudflare_tokens


class CloudflareLogRedactionTests(unittest.TestCase):

  def test_redacts_cloudflare_fields_without_mutating_source(self):
    config = {
      "CLOUDFLARE_TOKEN": "plugin-token",
      "nested": {
        "EE_CLOUDFLARE_TOKEN_DEEPLOY_MANAGER": "manager-token",
        "CF_TUNNEL_TOKEN": "job-token",
        "OTHER_TOKEN": "not-in-scope",
      },
    }

    redacted = redact_cloudflare_tokens(config)

    self.assertEqual(redacted["CLOUDFLARE_TOKEN"], CLOUDFLARE_LOG_REDACTION)
    self.assertEqual(
      redacted["nested"]["EE_CLOUDFLARE_TOKEN_DEEPLOY_MANAGER"],
      CLOUDFLARE_LOG_REDACTION,
    )
    self.assertEqual(redacted["nested"]["CF_TUNNEL_TOKEN"], CLOUDFLARE_LOG_REDACTION)
    self.assertEqual(redacted["nested"]["OTHER_TOKEN"], "not-in-scope")
    self.assertEqual(config["CLOUDFLARE_TOKEN"], "plugin-token")
    self.assertEqual(config["nested"]["CF_TUNNEL_TOKEN"], "job-token")

  def test_redacts_cloudflared_token_argument_forms_only(self):
    commands = [
      "cloudflared tunnel run --token token-with-space --url http://localhost:1",
      "cloudflared tunnel run --token=token-with-equals --url http://localhost:2",
      "cloudflared tunnel run --token 'quoted-token' --url http://localhost:3",
      "cloudflared tunnel run --token \\"
      "\ncontinued-token --url http://localhost:4",
      "CLOUDFLARE_TOKEN=environment-token cloudflared tunnel run",
    ]

    redacted = redact_cloudflare_tokens(commands)

    self.assertNotIn("token-with-space", str(redacted))
    self.assertNotIn("token-with-equals", str(redacted))
    self.assertNotIn("quoted-token", str(redacted))
    self.assertNotIn("continued-token", str(redacted))
    self.assertNotIn("environment-token", str(redacted))
    self.assertEqual(str(redacted).count(CLOUDFLARE_LOG_REDACTION), 5)
    self.assertEqual(
      redact_cloudflare_tokens("other-cli --token keep-this"),
      "other-cli --token keep-this",
    )

  def test_redacts_cloudflared_argv_without_mutating_execution_value(self):
    command = ["cloudflared", "tunnel", "run", "--token", "argv-token"]

    redacted = redact_cloudflare_tokens(command)

    self.assertEqual(redacted[-1], CLOUDFLARE_LOG_REDACTION)
    self.assertEqual(command[-1], "argv-token")

    env_command = [
      "env",
      "CLOUDFLARE_TOKEN=argv-environment-token",
      "cloudflared",
      "tunnel",
      "run",
    ]
    redacted_env_command = redact_cloudflare_tokens(env_command)
    self.assertNotIn("argv-environment-token", str(redacted_env_command))
    self.assertIn(CLOUDFLARE_LOG_REDACTION, str(redacted_env_command))

  def test_tunnel_runner_logs_redacted_command_but_executes_original(self):
    token = "tunnel-runner-secret"
    command = f"cloudflared tunnel run --token {token} --url http://localhost:1"
    plugin = BaseTunnelEnginePlugin.__new__(BaseTunnelEnginePlugin)
    logs = []
    process = Mock(stdout=Mock(), stderr=Mock())
    plugin.P = lambda message, **kwargs: logs.append(message)
    plugin.LogReader = Mock(side_effect=lambda *args, **kwargs: Mock())
    plugin._remember_process_group = Mock()

    with patch.object(_BASE_TUNNEL_MODULE.subprocess, "Popen", return_value=process) as popen:
      result = plugin.run_tunnel_command(command)

    self.assertIs(result, process)
    self.assertEqual(popen.call_args.kwargs["args"], command)
    self.assertNotIn(token, "\n".join(logs))
    self.assertIn(CLOUDFLARE_LOG_REDACTION, "\n".join(logs))

  def test_start_command_payload_and_logs_are_redacted(self):
    token = "start-command-secret"
    command = f"cloudflared tunnel run --token={token} --url http://localhost:1"
    plugin = BaseWebAppPlugin.__new__(BaseWebAppPlugin)
    logs = []
    payloads = []
    executed = []
    process = Mock()
    times = iter((0, 10))

    plugin.failed = False
    plugin.start_commands_started = [False]
    plugin.start_commands_finished = [False]
    plugin.start_commands_processes = [None]
    plugin.start_commands_start_time = [0]
    plugin.dct_logs_reader = {}
    plugin.dct_err_logs_reader = {}
    plugin.prepared_env = {}
    plugin.get_start_commands = lambda: [command]
    plugin.P = lambda message, **kwargs: logs.append(message)
    plugin.time = lambda: next(times)
    plugin.add_payload_by_fields = lambda **kwargs: payloads.append(kwargs)
    plugin._BaseWebAppPlugin__run_command = (
      lambda cmd, env: (executed.append(cmd) or process, None, None)
    )
    plugin._BaseWebAppPlugin__wait_for_command = lambda process, timeout: (False, False)

    plugin._BaseWebAppPlugin__maybe_run_nth_start_command(0, timeout=5)

    self.assertEqual(executed, [command])
    self.assertNotIn(token, "\n".join(logs))
    self.assertNotIn(token, str(payloads))
    self.assertIn(CLOUDFLARE_LOG_REDACTION, str(payloads))

  def test_config_validation_logs_and_notifications_are_redacted(self):
    token = "invalid-config-secret"
    plugin = ConfigManagerCheckMixin.__new__(ConfigManagerCheckMixin)
    logs = []
    messages = []
    plugin.P = lambda message, **kwargs: logs.append(message)

    plugin._ConfigManagerCheckMixin__append_exception_message(
      messages,
      "Invalid configuration",
      {"CLOUDFLARE_TOKEN": token},
    )

    self.assertNotIn(token, str(logs))
    self.assertNotIn(token, str(messages))
    self.assertIn(CLOUDFLARE_LOG_REDACTION, str(messages))

  def test_duplicate_plugin_diagnostic_is_redacted_without_mutating_config(self):
    token = "duplicate-plugin-secret"
    config = {
      "NAME": "test-stream",
      "TYPE": "VOID",
      "PLUGINS": [
        {
          "SIGNATURE": "DUPLICATE",
          "INSTANCES": [{"INSTANCE_ID": "first", "CLOUDFLARE_TOKEN": token}],
        },
        {
          "SIGNATURE": "DUPLICATE",
          "INSTANCES": [{"INSTANCE_ID": "second"}],
        },
      ],
    }
    plugin = ConfigManagerCheckMixin.__new__(ConfigManagerCheckMixin)
    logs = []
    notifications = []
    plugin.P = lambda message, **kwargs: logs.append(message)
    plugin.log = types.SimpleNamespace(P=lambda message, **kwargs: logs.append(message))
    plugin._check_config_stream = lambda stream: True
    plugin._keep_plugin_good_instances = lambda stream_name, config_plugin: config_plugin
    plugin._create_notification = lambda **kwargs: notifications.append(kwargs)

    plugin._keep_stream_good_plugins(config)

    self.assertEqual(config["PLUGINS"][0]["INSTANCES"][0]["CLOUDFLARE_TOKEN"], token)
    self.assertNotIn(token, str(logs))
    self.assertNotIn(token, str(notifications))
    self.assertIn(CLOUDFLARE_LOG_REDACTION, str(notifications))


if __name__ == "__main__":
  unittest.main()
