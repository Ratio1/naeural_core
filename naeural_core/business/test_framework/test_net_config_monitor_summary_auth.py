import importlib.util
import os
import pathlib
import sys
import types
import unittest
from collections import OrderedDict
from datetime import datetime
from unittest import mock

def _load_constants_module():
  module = types.ModuleType("_ecomms_test_constants")
  module.NET_CONFIG_MONITOR_SHOW_EACH = 1
  module.DEVICE_STATUS_ONLINE = "ONLINE"
  module.ETH_ENABLED = False
  module.CONFIG_STREAM = types.SimpleNamespace(K_NAME="NAME")
  module.NET_CONFIG = types.SimpleNamespace(
    REQUEST_COMMAND="GET_CONFIG",
    STORE_COMMAND="SET_CONFIG",
    NET_CONFIG_DATA="NET_CONFIG_DATA",
    OPERATION="OP",
    DESTINATION="DEST",
  )
  module.PAYLOAD_DATA = types.SimpleNamespace(
    EE_SENDER="EE_SENDER",
    EE_DESTINATION="EE_DEST",
    EE_PAYLOAD_PATH="EE_PAYLOAD_PATH",
    EE_IS_ENCRYPTED="EE_IS_ENCRYPTED",
    EE_ID="EE_ID",
    NETMON_CURRENT_NETWORK="NETMON_CURRENT_NETWORK",
    NETMON_ADDRESS="address",
    NETMON_EEID="eeid",
    NETMON_STATUS_KEY="working",
    NETMON_WHITELIST="whitelist",
    NETMON_LAST_SEEN="last_seen_sec",
  )
  module.PAYLOAD_DATA.maybe_decode_netmon_payload = lambda payload, log=None: payload
  module.PAYLOAD_DATA.maybe_convert_netmon_whitelist = lambda payload: payload
  return module


ct = _load_constants_module()


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
  fake_core = types.ModuleType("naeural_core")
  fake_core.constants = ct
  fake_base = types.ModuleType("naeural_core.business.base")
  fake_base.__path__ = []
  fake_network_processor = types.ModuleType("naeural_core.business.base.network_processor")
  fake_network_processor.NetworkProcessorPlugin = _FakeNetworkProcessorPlugin
  module_name = "_ecomms_test_net_config_monitor"
  spec = importlib.util.spec_from_file_location(module_name, module_path)
  module = importlib.util.module_from_spec(spec)
  with mock.patch.dict(sys.modules, {
    "naeural_core": fake_core,
    "naeural_core.constants": ct,
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

  def get_whitelist(self, with_prefix=False):
    if not with_prefix:
      return self._whitelist
    return [
      item if item.startswith("0xai_") else "0xai_" + item
      for item in self._whitelist
    ]


class _SummaryControlNetmon:
  def __init__(self, online_for_control=None):
    self.online_for_control = set(online_for_control or [])
    self.direct_calls = []
    self.control_calls = []

  def network_node_is_online(self, addr):
    self.direct_calls.append(addr)
    return False

  def network_node_is_online_for_control(self, addr):
    self.control_calls.append(addr)
    return self._normalize(addr) in self.online_for_control

  def network_node_eeid(self, addr):
    return self._normalize(addr).lower()

  @staticmethod
  def _normalize(addr):
    return str(addr).replace("0xai_", "", 1)


class _SequenceControlNetmon(_SummaryControlNetmon):
  def __init__(self, sequence):
    super().__init__()
    self.sequence = list(sequence)

  def network_node_is_online_for_control(self, addr):
    self.control_calls.append(addr)
    if len(self.sequence) == 0:
      return False
    return self.sequence.pop(0)


class _OldNetmonStub:
  def __init__(self, online=None):
    self.online = set(online or [])
    self.calls = []

  def network_node_is_online(self, addr):
    self.calls.append(addr)
    return self._normalize(addr) in self.online

  def network_node_eeid(self, addr):
    return self._normalize(addr).lower()

  @staticmethod
  def _normalize(addr):
    return str(addr).replace("0xai_", "", 1)


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
  plugin.deepcopy = lambda value: value.copy() if isinstance(value, list) else value
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

  def test_send_config_paths_use_summary_control_liveness_for_lists(self):
    plugin = _plugin()
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE"})
    plugin.Pd = lambda *args, **kwargs: None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None

    get_sent = plugin._NetConfigMonitorPlugin__send_get_cfg(["0xai_REMOTE", "0xai_OFFLINE"])
    set_sent = plugin._NetConfigMonitorPlugin__send_set_cfg(["0xai_REMOTE", "0xai_OFFLINE"])

    self.assertEqual(plugin.netmon.direct_calls, [])
    self.assertEqual(plugin.netmon.control_calls, ["0xai_REMOTE", "0xai_OFFLINE", "0xai_REMOTE", "0xai_OFFLINE"])
    self.assertEqual(get_sent, ["REMOTE"])
    self.assertEqual(set_sent, ["REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls[1]["node_addr"], ["0xai_REMOTE"])

  def test_send_config_paths_noop_when_all_list_destinations_are_control_offline(self):
    plugin = _plugin()
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.Pd = lambda *args, **kwargs: None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None

    plugin._NetConfigMonitorPlugin__send_get_cfg(["OFFLINE"])
    plugin._NetConfigMonitorPlugin__send_set_cfg(["OFFLINE"])

    self.assertEqual(plugin.netmon.direct_calls, [])
    self.assertEqual(plugin.netmon.control_calls, ["OFFLINE", "OFFLINE"])
    self.assertEqual(plugin.send_encrypted_payload_calls, [])

  def test_send_config_paths_fall_back_for_old_netmon_stubs(self):
    plugin = _plugin()
    plugin.netmon = _OldNetmonStub(online={"REMOTE"})
    plugin.Pd = lambda *args, **kwargs: None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None

    plugin._NetConfigMonitorPlugin__send_get_cfg(["REMOTE", "OFFLINE"])
    plugin._NetConfigMonitorPlugin__send_set_cfg(["REMOTE", "OFFLINE"])

    self.assertEqual(plugin.netmon.calls, ["REMOTE", "OFFLINE", "REMOTE", "OFFLINE"])
    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls[1]["node_addr"], ["0xai_REMOTE"])

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
          ct.PAYLOAD_DATA.NETMON_LAST_SEEN: 1,
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

  def test_malformed_summary_whitelist_does_not_allow_config_side_effects(self):
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
        "remote": {
          ct.PAYLOAD_DATA.NETMON_ADDRESS: "0xai_REMOTE",
          ct.PAYLOAD_DATA.NETMON_EEID: "remote",
          ct.PAYLOAD_DATA.NETMON_STATUS_KEY: ct.DEVICE_STATUS_ONLINE,
          ct.PAYLOAD_DATA.NETMON_LAST_SEEN: 1,
          # Strings are iterable in Python; treating them as whitelists would
          # accidentally make "SELF" authorize the local node.
          ct.PAYLOAD_DATA.NETMON_WHITELIST: "SELF",
        },
      },
    }

    with mock.patch.object(ct, "ETH_ENABLED", False), mock.patch.dict(
      "os.environ", {"EE_NETMON_ACCEPT_LOCAL_SUPERVISOR_SUMMARY": "1"}
    ):
      plugin.netmon_handler(payload)

    self.assertEqual(len(calls), 1)
    self.assertTrue(calls[0]["reporter_is_authorized"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes, {})

  def test_malformed_summary_last_seen_does_not_allow_config_side_effects(self):
    bad_last_seen_values = ("nan", float("inf"), -1, 61, None)
    for bad_last_seen in bad_last_seen_values:
      with self.subTest(bad_last_seen=bad_last_seen):
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

        node = {
          ct.PAYLOAD_DATA.NETMON_ADDRESS: "0xai_REMOTE",
          ct.PAYLOAD_DATA.NETMON_EEID: "remote",
          ct.PAYLOAD_DATA.NETMON_STATUS_KEY: ct.DEVICE_STATUS_ONLINE,
          ct.PAYLOAD_DATA.NETMON_WHITELIST: ["SELF"],
        }
        if bad_last_seen is not None:
          node[ct.PAYLOAD_DATA.NETMON_LAST_SEEN] = bad_last_seen
        payload = {
          ct.PAYLOAD_DATA.EE_SENDER: "0xai_ORACLE",
          ct.PAYLOAD_DATA.EE_ID: "oracle",
          "IS_SUPERVISOR": True,
          ct.PAYLOAD_DATA.NETMON_CURRENT_NETWORK: {"remote": node},
        }

        with mock.patch.object(ct, "ETH_ENABLED", False), mock.patch.dict(
          "os.environ", {"EE_NETMON_ACCEPT_LOCAL_SUPERVISOR_SUMMARY": "1"}
        ):
          plugin.netmon_handler(payload)

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["reporter_is_authorized"])
        self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes, {})

  def test_control_offline_node_does_not_consume_config_request_cooldown(self):
    plugin = _plugin()
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_get_config_each = 600
    plugin.cfg_node_request_configs_each = 1200
    plugin._NetConfigMonitorPlugin__last_data_time = 0
    plugin._NetConfigMonitorPlugin__allowed_nodes = {
      "REMOTE": {
        "is_online": True,
        "last_config_get": 0,
      }
    }
    sent = []
    plugin._NetConfigMonitorPlugin__send_get_cfg = lambda node_addr: sent.append(node_addr)

    plugin._NetConfigMonitorPlugin__maybe_send_requests()

    self.assertEqual(sent, [])
    self.assertEqual(plugin.netmon.control_calls, ["REMOTE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes["REMOTE"]["last_config_get"], 0)
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_data_time, 1410)

  def test_liveness_flip_during_send_does_not_consume_node_request_cooldown(self):
    plugin = _plugin()
    # First call passes the request-loop prefilter; second call happens inside
    # __send_get_cfg and simulates the node becoming control-offline before send.
    plugin.netmon = _SequenceControlNetmon([True, False])
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_get_config_each = 600
    plugin.cfg_node_request_configs_each = 1200
    plugin._NetConfigMonitorPlugin__last_data_time = 0
    plugin._NetConfigMonitorPlugin__allowed_nodes = {
      "REMOTE": {
        "is_online": True,
        "last_config_get": 0,
      }
    }
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_requests()

    self.assertEqual(plugin.netmon.control_calls, ["REMOTE", "REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes["REMOTE"]["last_config_get"], 0)
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_data_time, 1410)

  def test_partial_filtered_config_requests_retry_unsent_nodes_soon(self):
    plugin = _plugin()
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE"})
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_get_config_each = 600
    plugin.cfg_node_request_configs_each = 1200
    plugin._NetConfigMonitorPlugin__last_data_time = 0
    plugin._NetConfigMonitorPlugin__allowed_nodes = {
      "REMOTE": {
        "is_online": True,
        "last_config_get": 0,
      },
      "OFFLINE": {
        "is_online": True,
        "last_config_get": 0,
      },
    }
    plugin._NetConfigMonitorPlugin__send_get_cfg = lambda node_addr: ["REMOTE"]

    plugin._NetConfigMonitorPlugin__maybe_send_requests()

    self.assertEqual(plugin.netmon.control_calls, ["REMOTE", "OFFLINE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes["REMOTE"]["last_config_get"], 2000)
    self.assertEqual(plugin._NetConfigMonitorPlugin__allowed_nodes["OFFLINE"]["last_config_get"], 0)
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_data_time, 1410)

  def test_no_due_config_requests_keep_global_request_cadence(self):
    plugin = _plugin()
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE"})
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_get_config_each = 600
    plugin.cfg_node_request_configs_each = 1200
    plugin._NetConfigMonitorPlugin__last_data_time = 0
    plugin._NetConfigMonitorPlugin__allowed_nodes = {
      "REMOTE": {
        "is_online": True,
        "last_config_get": 1900,
      }
    }
    sent = []
    plugin._NetConfigMonitorPlugin__send_get_cfg = lambda node_addr: sent.append(node_addr)

    plugin._NetConfigMonitorPlugin__maybe_send_requests()

    self.assertEqual(sent, [])
    self.assertEqual(plugin.netmon.control_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_data_time, 2000)

  def test_set_distribution_initial_no_eligible_retries_soon(self):
    plugin = _plugin(whitelist=["0xai_REMOTE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = False
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = []
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.netmon.control_calls, ["0xai_REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 1410)

  def test_set_distribution_periodic_no_eligible_consumes_full_cadence(self):
    plugin = _plugin(whitelist=["0xai_REMOTE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = []
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.netmon.control_calls, ["0xai_REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2000)

  def test_set_distribution_config_change_no_eligible_retries_until_visible(self):
    plugin = _plugin(whitelist=["0xai_REMOTE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    now = [2000]
    plugin.time = lambda: now[0]
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 2000
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 1410)

    plugin.netmon.online_for_control = {"REMOTE"}
    now[0] = 2011

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2011)

  def test_set_distribution_config_change_keeps_retrying_within_pending_window(self):
    plugin = _plugin(whitelist=["0xai_REMOTE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    now = [2000]
    plugin.time = lambda: now[0]
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 2000
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()
    now[0] = 2011
    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 1421)

    plugin.netmon.online_for_control = {"REMOTE"}
    now[0] = 2022

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2022)

  def test_set_distribution_semantic_retry_expires_to_full_cadence(self):
    plugin = _plugin(whitelist=["0xai_REMOTE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control=set())
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    now = [2000]
    plugin.time = lambda: now[0]
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 2000
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()
    now[0] = 2061

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2061)
    self.assertEqual(plugin._NetConfigMonitorPlugin__semantic_distribution_pending_nodes, set())

  def test_set_distribution_config_change_retries_only_skipped_recipient(self):
    plugin = _plugin(whitelist=["0xai_REMOTE", "0xai_LATE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE"})
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    now = [2000]
    plugin.time = lambda: now[0]
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 2000
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 1410)

    plugin.netmon.online_for_control = {"REMOTE", "LATE"}
    now[0] = 2011

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls[1]["node_addr"], ["0xai_LATE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2011)

  def test_set_distribution_initial_partial_retry_keeps_pending_recipient_only(self):
    plugin = _plugin(whitelist=["0xai_REMOTE", "0xai_LATE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE"})
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    now = [2000]
    plugin.time = lambda: now[0]
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = False
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = None
    plugin.node_pipelines = [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}]
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(
      plugin._NetConfigMonitorPlugin__last_pipelines,
      [{ct.CONFIG_STREAM.K_NAME: "admin_pipeline"}],
    )

    plugin.netmon.online_for_control = {"REMOTE", "LATE"}
    now[0] = 2011

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.send_encrypted_payload_calls[1]["node_addr"], ["0xai_LATE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2011)

  def test_set_distribution_periodic_stable_offline_whitelist_nodes_do_not_retry_soon(self):
    plugin = _plugin(whitelist=["0xai_REMOTE", "0xai_OFFLINE"])
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE"})
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = []
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.netmon.control_calls, ["0xai_REMOTE", "0xai_OFFLINE", "0xai_REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2000)

  def test_set_distribution_periodic_live_subset_does_not_enter_short_retry_loop(self):
    plugin = _plugin(whitelist=[
      "0xai_ORACLE_A",
      "0xai_ORACLE_B",
      "0xai_ORACLE_C",
      "0xai_ORACLE_D",
      "0xai_STALE_WORKER",
      "0xai_STALE_SUPERVISOR",
    ])
    plugin.netmon = _SummaryControlNetmon(online_for_control={
      "ORACLE_A",
      "ORACLE_B",
      "ORACLE_C",
      "ORACLE_D",
    })
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    now = [2000]
    plugin.time = lambda: now[0]
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = True
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = []
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()
    now[0] = 2011
    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(len(plugin.send_encrypted_payload_calls), 1)
    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], [
      "0xai_ORACLE_A",
      "0xai_ORACLE_B",
      "0xai_ORACLE_C",
      "0xai_ORACLE_D",
    ])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2000)

  def test_set_distribution_send_time_control_race_retries_soon(self):
    plugin = _plugin(whitelist=["0xai_REMOTE"])
    plugin.netmon = _SequenceControlNetmon([True, False])
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = False
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = []
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.netmon.control_calls, ["0xai_REMOTE", "0xai_REMOTE"])
    self.assertEqual(plugin.send_encrypted_payload_calls, [])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 1410)

  def test_set_distribution_success_consumes_full_cadence_once(self):
    plugin = _plugin(whitelist=["0xai_REMOTE", "0xai_OTHER"])
    plugin.netmon = _SummaryControlNetmon(online_for_control={"REMOTE", "OTHER"})
    plugin.P = lambda *args, **kwargs: None
    plugin.Pd = lambda *args, **kwargs: None
    plugin.time = lambda: 2000
    plugin.cfg_send_to_allowed_each = 600
    plugin._NetConfigMonitorPlugin__initial_send = False
    plugin._NetConfigMonitorPlugin__last_sent_to_allowed = 0
    plugin._NetConfigMonitorPlugin__last_pipelines = []
    plugin.node_pipelines = []
    plugin._get_active_plugins_instances = None
    plugin.send_encrypted_payload_calls = []
    plugin.send_encrypted_payload = lambda **kwargs: plugin.send_encrypted_payload_calls.append(kwargs)

    plugin._NetConfigMonitorPlugin__maybe_send_configuration_to_allowed()

    self.assertEqual(plugin.netmon.control_calls, ["0xai_REMOTE", "0xai_OTHER", "0xai_REMOTE", "0xai_OTHER"])
    self.assertEqual(plugin.send_encrypted_payload_calls[0]["node_addr"], ["0xai_REMOTE", "0xai_OTHER"])
    self.assertEqual(plugin._NetConfigMonitorPlugin__last_sent_to_allowed, 2000)


if __name__ == "__main__":
  unittest.main()
