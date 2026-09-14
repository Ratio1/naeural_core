"""Legacy app-config contracts, independent of broker and deployed credentials."""

import copy
import os
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock

from naeural_core import Logger, constants as ct
from naeural_core.comm.base.base_comm_thread import BaseCommThread
from naeural_core.comm.default import mqtt as mqtt_comm
from naeural_core.comm.communication_manager import CommunicationManager
from naeural_core.comm.communication_roles import resolve_receive_roles
from naeural_core.comm.message_buffer import ObservableMessageBuffer
from ratio1.comm import MQTTWrapper


# Complete pre-regrouping HyFy layout, including the passive DEFAULT copy.
LEGACY_INSTANCES = {
  "COMMANDCONTROL": {"SEND_TO": "CONFIG_CHANNEL", "RECV_FROM": "CTRL_CHANNEL"},
  "DEFAULT": {"SEND_TO": "PAYLOADS_CHANNEL", "RECV_FROM": "CONFIG_CHANNEL"},
  "HEARTBEATS": {"SEND_TO": "CTRL_CHANNEL", "RECV_FROM": "CONFIG_CHANNEL"},
  "NOTIFICATIONS": {"SEND_TO": "NOTIF_CHANNEL"},
}


def manager_for(instances):
  manager = CommunicationManager.__new__(CommunicationManager)
  manager.config = {
    "TYPE": "mqtt",
    "PARAMS": {channel: {"TOPIC": "custom/" + channel} for channel in ct.COMMS.COMMUNICATION_VALID_CHANNELS},
    "INSTANCES": copy.deepcopy(instances),
  }
  manager.config_data = manager.config
  manager._environment_variables = {}
  manager.log = SimpleNamespace(str_to_bool=Logger.str_to_bool)
  manager.errors = []
  manager.add_error = manager.errors.append
  manager.add_warning = mock.Mock()
  manager.P = mock.Mock()
  return manager


@mock.patch.dict(os.environ, {}, clear=True)
class TestLegacyReceiveCompatibility(unittest.TestCase):
  def test_startup_preserves_all_central_and_local_instance_paths(self):
    manager = manager_for(LEGACY_INSTANCES)
    manager.log.config_data = {ct.EE_ID: "compat_node"}
    manager.shmem = {ct.BLOCKCHAIN_MANAGER: SimpleNamespace(address="0xai_COMPAT")}
    created = {}

    class Communicator:
      def __init__(self, **kwargs):
        self.arguments = kwargs

      def validate(self, **kwargs):
        pass

      def start(self):
        pass

    manager._get_plugin_class = lambda name: (Communicator, {})
    manager.add_subaltern = lambda name, comm: created.update({name: comm})
    before = copy.deepcopy(manager.config)
    for local in (False, True):
      manager._CommunicationManager__start_communication(manager.config["PARAMS"], is_local=local)
    self.assertEqual(len(created), 8)
    for name, paths in LEGACY_INSTANCES.items():
      for prefix in ("", "L_"):
        arguments = created[prefix + name].arguments
        self.assertEqual(arguments["send_channel_name"], paths.get("SEND_TO"))
        self.assertEqual(arguments["recv_channel_name"], paths.get("RECV_FROM"))
    self.assertEqual(manager.config, before)

  def test_complete_legacy_config_validates_without_changing_paths(self):
    for lower in (False, True):
      with self.subTest(lower=lower):
        instances = copy.deepcopy(LEGACY_INSTANCES)
        if lower:
          instances = {name.lower(): {k: v.lower() for k, v in paths.items()} for name, paths in instances.items()}
        manager = manager_for(instances)
        before = copy.deepcopy(manager.config)
        manager.validate_macro()
        self.assertEqual(manager.errors, [])
        self.assertEqual(manager.config, before)
        roles = resolve_receive_roles(instances)
        self.assertEqual(roles["command"].upper(), "HEARTBEATS")
        self.assertEqual(roles["heartbeat"].upper(), "COMMANDCONTROL")

  def test_duplicate_command_receivers_outside_legacy_layout_still_fail(self):
    for extra_name in ("NOTIFICATIONS", "COMMANDCONTROL"):
      instances = copy.deepcopy(LEGACY_INSTANCES)
      instances[extra_name]["RECV_FROM"] = "CONFIG_CHANNEL"
      with self.subTest(extra_name=extra_name), self.assertRaises(ValueError):
        resolve_receive_roles(instances)
    mixed = copy.deepcopy(LEGACY_INSTANCES)
    mixed["COMMANDCONTROL"]["RECV_FROM"] = "CONFIG_CHANNEL"
    mixed["HEARTBEATS"]["RECV_FROM"] = "CTRL_CHANNEL"
    with self.assertRaisesRegex(ValueError, "CONFIG_CHANNEL"):
      resolve_receive_roles(mixed)

  def test_legacy_central_and_local_dispatch_skip_passive_copies(self):
    manager = manager_for(LEGACY_INSTANCES)
    manager.validate_macro()
    manager._dct_comm_plugins = {}
    for prefix in ("", "L_"):
      for name, paths in LEGACY_INSTANCES.items():
        manager._dct_comm_plugins[prefix + name] = SimpleNamespace(
          recv_channel_name=paths.get("RECV_FROM"),
          get_messages=mock.Mock(return_value=[prefix + name]),
        )
    processed = []
    manager.process_command_message = processed.append
    manager.process_commands_from_self = mock.Mock()
    manager.get_received_commands = lambda: processed
    self.assertEqual(manager.maybe_process_incoming(), ["HEARTBEATS", "L_HEARTBEATS"])
    for name in ("DEFAULT", "L_DEFAULT", "COMMANDCONTROL", "L_COMMANDCONTROL"):
      manager._dct_comm_plugins[name].get_messages.assert_not_called()
    manager.process_commands_from_self.assert_called_once()

  def test_role_selection_precedes_oracle_receive_suppression(self):
    for legacy in (False, True):
      with self.subTest(legacy=legacy):
        instances = copy.deepcopy(LEGACY_INSTANCES)
        if not legacy:
          instances["DEFAULT"].pop("RECV_FROM")
          instances["COMMANDCONTROL"]["RECV_FROM"] = "CONFIG_CHANNEL"
          instances["HEARTBEATS"]["RECV_FROM"] = "CTRL_CHANNEL"
        manager = manager_for(instances)
        manager.validate_macro()
        manager._environment_variables = {"EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "t"}
        manager._dct_comm_plugins = {}
        for name, paths in instances.items():
          effective = manager._prepare_comm_instance_paths(name, paths)
          manager._dct_comm_plugins[name] = SimpleNamespace(
            recv_channel_name=effective.get("RECV_FROM"),
            get_messages=mock.Mock(return_value=[name]),
          )
        processed = []
        manager.process_command_message = processed.append
        manager.process_commands_from_self = lambda: None
        manager.get_received_commands = lambda: processed
        self.assertEqual(manager.maybe_process_incoming(), ["HEARTBEATS" if legacy else "COMMANDCONTROL"])
        self.assertTrue(manager.should_register_local_self_heartbeat)
        self.assertEqual(manager.config["INSTANCES"], instances)


class TestLegacyPolicyParsing(unittest.TestCase):
  def test_existing_policy_flags_use_actual_logger_converter(self):
    keys = {
      "IS_SUPERVISOR_NODE": "is_supervisor_node",
      "EE_SUPERVISOR": "is_supervisor_node",
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "oracle_only_heartbeat_mode_enabled",
      "NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "oracle_only_heartbeat_mode_enabled",
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "oracle_only_heartbeat_receive_enabled",
      "NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "oracle_only_heartbeat_receive_enabled",
      "EE_NETMON_USE_SUMMARY_STATUS": "netmon_summary_status_enabled",
      "NETMON_USE_SUMMARY_STATUS": "netmon_summary_status_enabled",
    }
    values = [True, False, 0, 1, 2, 1.0, "true", "TRUE", "t", "T", "yes", "y", "1", "on", "false", "0", "off", "enabled", "disabled", " true ", "", "unexpected"]
    with mock.patch.dict(os.environ, {}, clear=True):
      for key, prop in keys.items():
        for value in values:
          for process_env in (False, True):
            with self.subTest(key=key, value=value, process_env=process_env):
              manager = manager_for(LEGACY_INSTANCES)
              source = {key: str(value)} if process_env else {}
              manager._environment_variables = {} if process_env else {key: value}
              with mock.patch.dict(os.environ, source):
                self.assertEqual(getattr(manager, prop), Logger.str_to_bool(value))

  def test_qos_keeps_legacy_coercion_without_mutating_input(self):
    values = [True, False, 0, 1, 2, 1.0, 1.5, 2.9, "01", "+1", "-0", "0_1", " 2 "]
    for value in values:
      with self.subTest(value=value), mock.patch.dict(os.environ, {}, clear=True):
        manager = manager_for(LEGACY_INSTANCES)
        manager._environment_variables = {"EE_MQTT_HEARTBEAT_QOS": value, "MQTT_COMMAND_QOS": value}
        before = copy.deepcopy(manager.config)
        prepared = manager._prepare_comm_config_instance(manager.config["PARAMS"])
        self.assertEqual(prepared["CTRL_CHANNEL"]["QOS"], int(value))
        self.assertEqual(prepared["CONFIG_CHANNEL"]["QOS"], int(value))
        self.assertEqual(manager.config, before)


class TestPassiveReceiveBuffer(unittest.TestCase):
  def test_passive_default_retains_latest_while_active_command_rejects_newest(self):
    logger = Logger("COMPAT", base_folder=".", app_folder="_local_cache", no_folders_no_save=True, DEBUG=False)
    logger.config_data = {}
    formatter = mock.Mock()
    formatter.get_formatter.return_value = (None, None)
    for name in ("DEFAULT", "L_DEFAULT", "HEARTBEATS", "L_HEARTBEATS", "COMMANDCONTROL", "L_COMMANDCONTROL"):
      with self.subTest(name=name):
        comm = BaseCommThread(
          log=logger,
          shmem={"heavy_ops_manager": mock.Mock(), "network_monitor": mock.Mock(), "io_formatter_manager": formatter},
          signature="TEST_MQTT", comm_type=name,
          default_config=BaseCommThread.CONFIG, upstream_config={ct.EE_ID: "compat_node"},
          recv_channel_name="CONFIG_CHANNEL", extra_receive_buffer=2 - ct.COMM_RECV_BUFFER,
        )
        wrapper = MQTTWrapper.__new__(MQTTWrapper)
        wrapper._mqttc = object()
        wrapper._custom_on_message = None
        wrapper._recv_buff = comm._recv_buff
        wrapper._MQTTWrapper__nr_dropped_messages = 0
        comm.P = mock.Mock()
        comm._maybe_reconnect_to_server = mock.Mock()
        with mock.patch.object(mqtt_comm, "MQTTWrapper", return_value=wrapper) as factory:
          mqtt_comm.MQTTCommThread._init(comm)
        wrapper._post_default_on_message = factory.call_args.kwargs["post_default_on_message"]
        self.assertEqual(wrapper._post_default_on_message is not None, name == "DEFAULT")
        comm.P.reset_mock()
        for item in (b"first", b"second", b"third"):
          wrapper._callback_on_message(wrapper._mqttc, None, SimpleNamespace(payload=item))
        if name in ("DEFAULT", "L_DEFAULT"):
          self.assertIsInstance(comm._recv_buff, deque)
          self.assertEqual(list(comm._recv_buff), ["second", "third"])
          if name == "DEFAULT":
            self.assertIn("third", comm.P.call_args.args[0])
          else:
            comm.P.assert_not_called()
          self.assertEqual(wrapper._MQTTWrapper__nr_dropped_messages, 0)
        else:
          self.assertIsInstance(comm._recv_buff, ObservableMessageBuffer)
          self.assertEqual([comm._recv_buff.popleft(), comm._recv_buff.popleft()], ["first", "second"])
          self.assertEqual(comm._recv_buff.snapshot().rejected_full, 1)
          self.assertEqual(wrapper._MQTTWrapper__nr_dropped_messages, 1)


if __name__ == "__main__":
  unittest.main()
