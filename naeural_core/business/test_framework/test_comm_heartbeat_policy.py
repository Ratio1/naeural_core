import copy
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

from naeural_core import constants as ct
from naeural_core.comm.communication_manager import CommunicationManager


def _load_mqtt_comm_thread_class():
  module_path = pathlib.Path(__file__).resolve().parents[2] / "comm/default/mqtt.py"

  class _FakeBaseCommThread:
    CONFIG = {
      "VALIDATION_RULES": {},
    }

  fake_base = types.ModuleType("naeural_core.comm.base")
  fake_base.BaseCommThread = _FakeBaseCommThread
  module_name = "_ecomms_test_mqtt_comm_thread"
  spec = importlib.util.spec_from_file_location(module_name, module_path)
  module = importlib.util.module_from_spec(spec)
  with mock.patch.dict(sys.modules, {"naeural_core.comm.base": fake_base}):
    spec.loader.exec_module(module)
  return module.MQTTCommThread


class _FakeLog:
  def str_to_bool(self, value):
    if isinstance(value, str):
      return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


class _PolicyHarness:
  def __init__(self, env=None):
    self.manager = CommunicationManager.__new__(CommunicationManager)
    self.manager._environment_variables = env or {}
    self.manager.log = _FakeLog()
    self.manager.messages = []
    self.manager.P = lambda message, **kwargs: self.manager.messages.append((message, kwargs))


class TestCommunicationHeartbeatPolicy(unittest.TestCase):

  def test_channel_qos_overrides_preserve_channel_topics(self):
    harness = _PolicyHarness({
      "EE_MQTT_HEARTBEAT_QOS": "1",
      "EE_MQTT_COMMAND_QOS": "2",
    })
    config = {
      ct.COMMS.COMMUNICATION_CTRL_CHANNEL: {
        ct.COMMS.TOPIC: "root/ctrl",
      },
      ct.COMMS.COMMUNICATION_CONFIG_CHANNEL: {
        ct.COMMS.TOPIC: "root/{}/config",
      },
    }

    prepared = harness.manager._prepare_comm_config_instance(copy.deepcopy(config))

    self.assertEqual(prepared[ct.COMMS.COMMUNICATION_CTRL_CHANNEL][ct.COMMS.TOPIC], "root/ctrl")
    self.assertEqual(prepared[ct.COMMS.COMMUNICATION_CTRL_CHANNEL][ct.COMMS.QOS], 1)
    self.assertEqual(prepared[ct.COMMS.COMMUNICATION_CONFIG_CHANNEL][ct.COMMS.TOPIC], "root/{}/config")
    self.assertEqual(prepared[ct.COMMS.COMMUNICATION_CONFIG_CHANNEL][ct.COMMS.QOS], 2)

  def test_channel_qos_overrides_fail_fast_with_old_sdk_wrapper(self):
    harness = _PolicyHarness({
      "EE_MQTT_HEARTBEAT_QOS": "1",
    })
    config = {
      ct.COMMS.COMMUNICATION_CTRL_CHANNEL: {
        ct.COMMS.TOPIC: "root/ctrl",
      },
      ct.COMMS.COMMUNICATION_CONFIG_CHANNEL: {
        ct.COMMS.TOPIC: "root/{}/config",
      },
    }

    with mock.patch("naeural_core.comm.MQTTWrapper", object):
      with self.assertRaises(RuntimeError):
        harness.manager._prepare_comm_config_instance(copy.deepcopy(config))

  def test_static_channel_qos_fails_fast_with_old_sdk_wrapper(self):
    harness = _PolicyHarness()
    config = {
      ct.COMMS.COMMUNICATION_CTRL_CHANNEL: {
        ct.COMMS.TOPIC: "root/ctrl",
        ct.COMMS.QOS: 1,
      },
      ct.COMMS.COMMUNICATION_CONFIG_CHANNEL: {
        ct.COMMS.TOPIC: "root/{}/config",
      },
    }

    with mock.patch("naeural_core.comm.MQTTWrapper", object):
      with self.assertRaises(RuntimeError):
        harness.manager._prepare_comm_config_instance(copy.deepcopy(config))

  def test_non_supervisor_disables_only_ctrl_receive(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "1",
      "IS_SUPERVISOR_NODE": False,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )
    heartbeat_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_HEARTBEATS,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
      },
    )

    self.assertIsNone(command_paths["RECV_FROM"])
    self.assertTrue(harness.manager.should_register_local_self_heartbeat)
    self.assertEqual(command_paths["SEND_TO"], ct.COMMS.COMMUNICATION_CONFIG_CHANNEL)
    self.assertEqual(heartbeat_paths["RECV_FROM"], ct.COMMS.COMMUNICATION_CONFIG_CHANNEL)
    self.assertEqual(heartbeat_paths["SEND_TO"], ct.COMMS.COMMUNICATION_CTRL_CHANNEL)

  def test_high_level_policy_enables_receive_reduction_and_summary_for_normal_node(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
      "IS_SUPERVISOR_NODE": False,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertIsNone(command_paths["RECV_FROM"])
    self.assertTrue(harness.manager.should_register_local_self_heartbeat)
    self.assertTrue(harness.manager.oracle_only_heartbeat_receive_enabled)
    self.assertTrue(harness.manager.netmon_summary_status_enabled)

  def test_low_level_receive_flag_overrides_high_level_policy(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "0",
      "IS_SUPERVISOR_NODE": False,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertEqual(command_paths["RECV_FROM"], ct.COMMS.COMMUNICATION_CTRL_CHANNEL)
    self.assertFalse(harness.manager.oracle_only_heartbeat_receive_enabled)
    self.assertTrue(harness.manager.netmon_summary_status_enabled)

  def test_low_level_summary_flag_overrides_high_level_policy(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
      "EE_NETMON_USE_SUMMARY_STATUS": "0",
      "IS_SUPERVISOR_NODE": False,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertIsNone(command_paths["RECV_FROM"])
    self.assertFalse(harness.manager.netmon_summary_status_enabled)
    self.assertTrue(any("summary status is disabled" in msg for msg, _ in harness.manager.messages))

  def test_non_supervisor_policy_follows_ctrl_channel_not_instance_name(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "1",
      "IS_SUPERVISOR_NODE": False,
    })

    renamed_ctrl_reader = harness.manager._prepare_comm_instance_paths(
      "CUSTOM",
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )
    command_named_config_reader = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
      },
    )

    self.assertIsNone(renamed_ctrl_reader["RECV_FROM"])
    self.assertEqual(command_named_config_reader["RECV_FROM"], ct.COMMS.COMMUNICATION_CONFIG_CHANNEL)

  def test_non_supervisor_policy_normalizes_receive_channel_before_compare(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "1",
      "IS_SUPERVISOR_NODE": False,
    })

    paths = harness.manager._prepare_comm_instance_paths(
      "CUSTOM",
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL.lower(),
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertIsNone(paths["RECV_FROM"])

  def test_local_self_heartbeat_registration_requires_actual_disabled_ctrl_receiver(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "1",
      "IS_SUPERVISOR_NODE": False,
    })

    paths = harness.manager._prepare_comm_instance_paths(
      "CUSTOM",
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
      },
    )

    self.assertEqual(paths["RECV_FROM"], ct.COMMS.COMMUNICATION_CONFIG_CHANNEL)
    self.assertFalse(harness.manager.should_register_local_self_heartbeat)

  def test_supervisor_keeps_ctrl_receive_even_when_flag_enabled(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "1",
      "IS_SUPERVISOR_NODE": True,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertEqual(command_paths["RECV_FROM"], ct.COMMS.COMMUNICATION_CTRL_CHANNEL)
    self.assertEqual(command_paths["SEND_TO"], ct.COMMS.COMMUNICATION_CONFIG_CHANNEL)

  def test_supervisor_keeps_ctrl_receive_and_no_derived_summary_in_policy_mode(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
      "IS_SUPERVISOR_NODE": True,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertEqual(command_paths["RECV_FROM"], ct.COMMS.COMMUNICATION_CTRL_CHANNEL)
    self.assertFalse(harness.manager.oracle_only_heartbeat_receive_enabled)
    self.assertFalse(harness.manager.netmon_summary_status_enabled)

  def test_disabled_flag_keeps_default_ctrl_receive_for_normal_nodes(self):
    harness = _PolicyHarness({
      "EE_NETMON_ORACLE_ONLY_HEARTBEAT_RECEIVE": "0",
      "IS_SUPERVISOR_NODE": False,
    })

    command_paths = harness.manager._prepare_comm_instance_paths(
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      {
        "RECV_FROM": ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        "SEND_TO": ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
      },
    )

    self.assertEqual(command_paths["RECV_FROM"], ct.COMMS.COMMUNICATION_CTRL_CHANNEL)
    self.assertEqual(command_paths["SEND_TO"], ct.COMMS.COMMUNICATION_CONFIG_CHANNEL)

  def test_receive_disabled_comm_thread_does_not_attempt_subscription(self):
    MQTTCommThread = _load_mqtt_comm_thread_class()
    comm = MQTTCommThread.__new__(MQTTCommThread)
    comm._recv_channel_name = None
    comm.has_recv_conn = False
    notifications = []
    comm._create_notification = lambda **kwargs: notifications.append(kwargs)
    comm._maybe_reconnect_to_server = lambda: self.fail("receive-disabled path must not reconnect for subscribe")

    comm._maybe_reconnect_recv()

    self.assertTrue(comm.has_recv_conn)
    self.assertEqual(len(notifications), 1)
    self.assertIn("disabled", notifications[0]["msg"])


if __name__ == "__main__":
  unittest.main()
