import importlib.util
import os
import pathlib
import sys
import types
import unittest
from collections import OrderedDict
from datetime import datetime
from unittest import mock

from naeural_core import constants as ct


def _load_net_config_monitor_class():
  module_path = pathlib.Path(__file__).resolve().parents[2] / "business/default/admin/net_config_monitor.py"

  class _FakeNetworkProcessorPlugin:
    CONFIG = {
      "VALIDATION_RULES": {},
    }

    @staticmethod
    def payload_handler(*args, **kwargs):
      def decorator(fn):
        return fn
      return decorator

    @property
    def os_environ(self):
      return os.environ.copy()

    @property
    def datetime(self):
      return datetime

  fake_business = types.ModuleType("naeural_core.business")
  fake_business.__path__ = []
  fake_base = types.ModuleType("naeural_core.business.base")
  fake_base.__path__ = []
  fake_network_processor = types.ModuleType("naeural_core.business.base.network_processor")
  fake_network_processor.NetworkProcessorPlugin = _FakeNetworkProcessorPlugin
  module_name = "_ecomms_test_net_config_monitor"
  spec = importlib.util.spec_from_file_location(module_name, module_path)
  module = importlib.util.module_from_spec(spec)
  with mock.patch.dict(sys.modules, {
    "naeural_core.business": fake_business,
    "naeural_core.business.base": fake_base,
    "naeural_core.business.base.network_processor": fake_network_processor,
  }):
    spec.loader.exec_module(module)
  return module.NetConfigMonitorPlugin


class _AuthBlockEngine:
  def __init__(self, oracles=None, eth_oracles=None, whitelist=None, raise_oracles=False):
    self._oracles = oracles or []
    self._eth_oracles = eth_oracles or []
    self._whitelist = whitelist or []
    self._raise_oracles = raise_oracles

  def maybe_remove_addr_prefix(self, addr):
    return addr.replace("0xai_", "", 1)

  def maybe_remove_prefix(self, addr):
    return self.maybe_remove_addr_prefix(addr)

  def maybe_add_prefix(self, addr):
    return addr if addr.startswith("0xai_") else "0xai_" + addr

  def get_oracles(self):
    if self._raise_oracles:
      raise RuntimeError("oracle registry unavailable")
    return self._oracles, []

  def get_eth_oracles(self):
    if self._raise_oracles:
      raise RuntimeError("oracle registry unavailable")
    return self._eth_oracles

  def node_address_to_eth_address(self, addr):
    return "0xETH_" + self.maybe_remove_addr_prefix(addr)

  def eth_addr_to_checksum_address(self, addr):
    return str(addr).lower()

  def get_whitelist(self):
    return self._whitelist


def _plugin(oracles=None, eth_oracles=None, whitelist=None, raise_oracles=False):
  NetConfigMonitorPlugin = _load_net_config_monitor_class()
  plugin = NetConfigMonitorPlugin.__new__(NetConfigMonitorPlugin)
  plugin.bc = _AuthBlockEngine(
    oracles=oracles,
    eth_oracles=eth_oracles,
    whitelist=whitelist,
    raise_oracles=raise_oracles,
  )
  plugin.const = ct
  return plugin


def _is_authorized(plugin, sender_addr, data):
  return plugin._NetConfigMonitorPlugin__is_authorized_netmon_reporter(sender_addr, data)


class TestNetConfigMonitorSummaryAuth(unittest.TestCase):

  def test_non_supervisor_report_is_rejected(self):
    plugin = _plugin(oracles=["0xai_ORACLE"])

    with mock.patch.object(ct, "ETH_ENABLED", True):
      self.assertFalse(_is_authorized(plugin, "0xai_ORACLE", {"IS_SUPERVISOR": False}))

  def test_non_evm_local_testbed_accepts_supervisor_marker(self):
    plugin = _plugin()

    with mock.patch.object(ct, "ETH_ENABLED", False), mock.patch.dict(
      "os.environ", {"EE_NETMON_ACCEPT_LOCAL_SUPERVISOR_SUMMARY": "1"}
    ):
      self.assertTrue(_is_authorized(plugin, "0xai_LOCAL_ORACLE", {"IS_SUPERVISOR": "true"}))

  def test_non_evm_rejects_supervisor_marker_without_local_opt_in(self):
    plugin = _plugin()

    with mock.patch.object(ct, "ETH_ENABLED", False), mock.patch.dict("os.environ", {}, clear=True):
      self.assertFalse(_is_authorized(plugin, "0xai_LOCAL_ORACLE", {"IS_SUPERVISOR": "true"}))

  def test_evm_network_accepts_registered_oracle(self):
    plugin = _plugin(eth_oracles=["0xETH_ORACLE"])

    with mock.patch.object(ct, "ETH_ENABLED", True):
      self.assertTrue(_is_authorized(plugin, "0xai_ORACLE", {"IS_SUPERVISOR": True}))
      self.assertFalse(_is_authorized(plugin, "0xai_NOT_ORACLE", {"IS_SUPERVISOR": True}))

  def test_malformed_reporter_is_rejected_without_prefix_normalization(self):
    plugin = _plugin(eth_oracles=["0xETH_ORACLE"])

    with mock.patch.object(ct, "ETH_ENABLED", True):
      self.assertFalse(_is_authorized(plugin, object(), {"IS_SUPERVISOR": True}))

  def test_evm_network_rejects_whitelisted_non_oracle_when_oracle_list_empty(self):
    plugin = _plugin(eth_oracles=[], whitelist=["WHITELISTED"])

    with mock.patch.object(ct, "ETH_ENABLED", True):
      self.assertFalse(_is_authorized(plugin, "0xai_WHITELISTED", {"IS_SUPERVISOR": True}))
      self.assertFalse(_is_authorized(plugin, "0xai_OTHER", {"IS_SUPERVISOR": True}))

  def test_evm_network_rejects_when_oracle_registry_unavailable(self):
    plugin = _plugin(whitelist=["WHITELISTED"], raise_oracles=True)

    with mock.patch.object(ct, "ETH_ENABLED", True):
      self.assertFalse(_is_authorized(plugin, "0xai_WHITELISTED", {"IS_SUPERVISOR": True}))

  def test_unauthorized_netmon_report_does_not_mutate_allowed_nodes(self):
    plugin = _plugin(oracles=[], whitelist=["WHITELISTED"])
    calls = []
    plugin.log = None
    plugin.ee_addr = "0xai_SELF"
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.json_dumps = lambda *args, **kwargs: "{}"
    plugin.netmon = types.SimpleNamespace(
      register_network_status_snapshot=lambda **kwargs: calls.append(kwargs)
    )
    plugin._NetConfigMonitorPlugin__allowed_nodes = {
      "REMOTE": {
        "is_online": True,
        "whitelist": ["OLD"],
      }
    }
    plugin._NetConfigMonitorPlugin__debug_netmon_count = 0
    plugin._NetConfigMonitorPlugin__new_nodes_this_iter = 0

    payload = {
      ct.PAYLOAD_DATA.EE_SENDER: "0xai_WHITELISTED",
      ct.PAYLOAD_DATA.EE_ID: "fake-oracle",
      "IS_SUPERVISOR": True,
      ct.PAYLOAD_DATA.NETMON_CURRENT_NETWORK: {
        "new-node": {
          ct.PAYLOAD_DATA.NETMON_ADDRESS: "0xai_NEW_NODE",
          ct.PAYLOAD_DATA.NETMON_EEID: "new-node",
          ct.PAYLOAD_DATA.NETMON_STATUS_KEY: ct.DEVICE_STATUS_ONLINE,
          ct.PAYLOAD_DATA.NETMON_WHITELIST: ["SELF"],
        },
        "remote": {
          ct.PAYLOAD_DATA.NETMON_ADDRESS: "0xai_REMOTE",
          ct.PAYLOAD_DATA.NETMON_EEID: "remote",
          ct.PAYLOAD_DATA.NETMON_STATUS_KEY: "LOST STATUS",
          ct.PAYLOAD_DATA.NETMON_WHITELIST: ["SELF"],
        },
      },
    }

    with mock.patch.object(ct, "ETH_ENABLED", True):
      plugin.netmon_handler(payload)

    self.assertEqual(len(calls), 1)
    self.assertFalse(calls[0]["reporter_is_authorized"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes, {
      "REMOTE": {
        "is_online": True,
        "whitelist": ["OLD"],
      }
    })

  def test_malformed_current_network_entries_are_skipped_before_side_effects(self):
    plugin = _plugin()
    calls = []
    plugin.log = None
    plugin.ee_addr = "0xai_SELF"
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.json_dumps = lambda *args, **kwargs: "{}"
    plugin.time = lambda: 123
    plugin.OrderedDict = OrderedDict
    plugin.netmon = types.SimpleNamespace(
      register_network_status_snapshot=lambda **kwargs: calls.append(kwargs)
    )
    plugin._NetConfigMonitorPlugin__allowed_nodes = {}
    plugin._NetConfigMonitorPlugin__debug_netmon_count = 0
    plugin._NetConfigMonitorPlugin__new_nodes_this_iter = 0

    payload = {
      ct.PAYLOAD_DATA.EE_SENDER: "0xai_ORACLE",
      ct.PAYLOAD_DATA.EE_ID: "oracle",
      "IS_SUPERVISOR": True,
      ct.PAYLOAD_DATA.NETMON_CURRENT_NETWORK: {
        "scalar": 123,
        "bad-address": {
          ct.PAYLOAD_DATA.NETMON_ADDRESS: object(),
          ct.PAYLOAD_DATA.NETMON_EEID: "bad",
          ct.PAYLOAD_DATA.NETMON_STATUS_KEY: ct.DEVICE_STATUS_ONLINE,
        },
        "good": {
          ct.PAYLOAD_DATA.NETMON_ADDRESS: "0xai_REMOTE",
          ct.PAYLOAD_DATA.NETMON_EEID: "remote",
          ct.PAYLOAD_DATA.NETMON_STATUS_KEY: ct.DEVICE_STATUS_ONLINE,
          ct.PAYLOAD_DATA.NETMON_WHITELIST: ["SELF"],
        },
      },
    }

    with mock.patch.object(ct, "ETH_ENABLED", False), mock.patch.dict(
      "os.environ", {"EE_NETMON_ACCEPT_LOCAL_SUPERVISOR_SUMMARY": "1"}
    ):
      plugin.netmon_handler(payload)

    self.assertEqual(len(calls), 1)
    self.assertTrue(calls[0]["reporter_is_authorized"])
    self.assertEqual(sorted(calls[0]["current_network"]), ["good"])
    self.assertEqual(
      calls[0]["current_network"]["good"][ct.PAYLOAD_DATA.NETMON_ADDRESS],
      "REMOTE",
    )
    self.assertIn("REMOTE", plugin._NetConfigMonitorPlugin__allowed_nodes)


if __name__ == "__main__":
  unittest.main()
