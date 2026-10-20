"""Check version-fetch timeout recovery without starting the node runtime."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


class UpdateMonitorTimeoutTests(unittest.TestCase):
  """Exercise the real update monitor with a failing HTTP transport."""

  def test_timeout_returns_none_and_next_check_can_fetch_version(self):
    """A timed-out fetch must allow the next scheduled check to succeed."""
    base = ModuleType("naeural_core.business.base")
    base.BasePluginExecutor = type(
      "BasePluginExecutor", (), {"CONFIG": {"VALIDATION_RULES": {}}}
    )
    path = (
      Path(__file__).resolve().parents[1]
      / "default/admin/update_monitor_01.py"
    )
    spec = importlib.util.spec_from_file_location("update_monitor_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"naeural_core.business.base": base}):
      spec.loader.exec_module(module)

    plugin = module.UpdateMonitor01Plugin()
    plugin.runs_in_docker = True
    plugin.docker_branch = "main"
    plugin.cfg_version_token = None
    plugin.cfg_version_url = "https://example.test/{}/ver.py"
    plugin.cfg_use_yaml = False
    plugin.P = Mock()
    plugin.requests = SimpleNamespace(get=Mock(side_effect=[
      TimeoutError("TLS handshake timed out"),
      SimpleNamespace(status_code=200, content=b"__VER__ = '2.10.510'\n"),
    ]))

    self.assertIsNone(plugin.get_git_server_version())
    self.assertIn("TLS handshake timed out", plugin.P.call_args.args[0])
    self.assertEqual(plugin.get_git_server_version(), "2.10.510")
    self.assertEqual(plugin.requests.get.call_args_list, [
      call("https://example.test/main/ver.py", headers={}, timeout=60),
      call("https://example.test/main/ver.py", headers={}, timeout=60),
    ])


if __name__ == "__main__":
  unittest.main()
