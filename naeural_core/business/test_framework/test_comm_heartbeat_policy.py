import copy
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock

from naeural_core import constants as ct
from naeural_core.comm.base.base_comm_thread import BaseCommThread
from naeural_core.comm.communication_manager import CommunicationManager
from naeural_core.comm.message_buffer import ObservableMessageBuffer
from naeural_core.comm.mixins.heartbeats_comm_mixin import _HeartbeatsCommMixin


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

  def test_command_consumer_is_selected_by_config_channel_after_regrouping(self):
    class _Communicator:
      def __init__(self, recv_channel_name, messages):
        self.recv_channel_name = recv_channel_name
        self.messages = list(messages)

      def get_messages(self):
        messages = list(self.messages)
        self.messages.clear()
        return messages

    manager = CommunicationManager.__new__(CommunicationManager)
    manager._dct_comm_plugins = {
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL: _Communicator(
        ct.COMMS.COMMUNICATION_CONFIG_CHANNEL,
        [{"ACTION": "NEW_TOPOLOGY"}],
      ),
      ct.COMMS.COMMUNICATION_HEARTBEATS: _Communicator(
        ct.COMMS.COMMUNICATION_CTRL_CHANNEL,
        [{"EE_EVENT_TYPE": "HEARTBEAT"}],
      ),
    }
    processed = []
    manager.process_command_message = processed.append
    manager.process_commands_from_self = lambda: None
    manager.get_received_commands = lambda: processed

    result = manager.maybe_process_incoming()

    self.assertEqual(result, [{"ACTION": "NEW_TOPOLOGY"}])
    self.assertEqual(
      manager._dct_comm_plugins[
        ct.COMMS.COMMUNICATION_HEARTBEATS
      ].messages,
      [{"EE_EVENT_TYPE": "HEARTBEAT"}],
    )

  def test_heartbeat_ingress_runtime_env_overrides_are_normalized(self):
    comm = BaseCommThread.__new__(BaseCommThread)
    comm._environment_variables = {
      "EE_HEARTBEAT_INGRESS_QUEUE_SIZE": "7500",
      "EE_HEARTBEAT_AUTH_MODE": "ENFORCE",
      "EE_HEARTBEAT_INGRESS_WORKER_ENABLED": "false",
    }
    comm._config = {
      "HEARTBEAT_INGRESS_QUEUE_SIZE": 10000,
      "HEARTBEAT_AUTH_MODE": "shadow",
      "HEARTBEAT_INGRESS_WORKER_ENABLED": True,
    }
    comm._recv_channel_name = ct.COMMS.COMMUNICATION_CTRL_CHANNEL

    self.assertEqual(comm.cfg_heartbeat_ingress_queue_size, 7500)
    self.assertEqual(comm.cfg_heartbeat_auth_mode, "enforce")
    self.assertFalse(comm.heartbeat_ingress_worker_enabled)

  def test_worker_disabled_ctrl_owner_processes_one_raw_heartbeat_synchronously(self):
    comm = BaseCommThread.__new__(BaseCommThread)
    comm._environment_variables = {
      "EE_HEARTBEAT_INGRESS_WORKER_ENABLED": "false",
    }
    comm._config = {}
    comm._recv_channel_name = ct.COMMS.COMMUNICATION_CTRL_CHANNEL
    comm._recv_buff = ObservableMessageBuffer(capacity=2)
    comm._heartbeat_ingress_processor = object()
    comm._process_raw_heartbeat_message = mock.Mock(return_value="committed")
    raw_heartbeat = '{"EE_EVENT_TYPE":"HEARTBEAT"}'
    comm._recv_buff.append(raw_heartbeat)

    outcome = comm._process_next_heartbeat_synchronously()

    self.assertEqual(outcome, "committed")
    comm._process_raw_heartbeat_message.assert_called_once_with(raw_heartbeat)
    self.assertEqual(comm._recv_buff.snapshot().depth, 0)

  def test_worker_disabled_start_initializes_processor_without_starting_worker(self):
    comm = BaseCommThread.__new__(BaseCommThread)
    comm._environment_variables = {
      "EE_HEARTBEAT_INGRESS_WORKER_ENABLED": "false",
    }
    comm._config = {"HEARTBEAT_AUTH_MODE": "shadow"}
    comm._recv_channel_name = ct.COMMS.COMMUNICATION_CTRL_CHANNEL
    comm._heartbeat_ingress_processor = None
    comm._heartbeat_ingress_worker = None
    comm._network_monitor = mock.Mock()
    comm._io_formatter_manager = mock.Mock()
    comm.log = mock.Mock()
    comm.P = mock.Mock()

    with mock.patch(
      "naeural_core.comm.base.base_comm_thread.HeartbeatIngressProcessor",
    ) as processor_cls:
      comm._start_heartbeat_ingress_worker()

    processor_cls.assert_called_once()
    self.assertIsNone(comm._heartbeat_ingress_worker)
    self.assertIn(
      "processed synchronously",
      comm.P.call_args.args[0],
    )

  def test_heartbeat_ingress_worker_adjusts_max_in_flight_when_too_low(self):
    worker_arguments = []

    def _worker_ctor(**kwargs):
      worker_arguments.append(kwargs)
      worker = mock.Mock()
      worker.is_alive.return_value = False
      return worker

    comm = BaseCommThread.__new__(BaseCommThread)
    comm._environment_variables = {
      "EE_HEARTBEAT_AUTH_WORKERS": "4",
      "EE_HEARTBEAT_AUTH_MAX_IN_FLIGHT": "1",
    }
    comm._config = {"HEARTBEAT_AUTH_MODE": "shadow"}
    comm._recv_channel_name = ct.COMMS.COMMUNICATION_CTRL_CHANNEL
    comm._heartbeat_ingress_worker = None
    comm._heartbeat_ingress_processor = object()
    comm._recv_buff = mock.Mock()
    comm.P = mock.Mock()

    with mock.patch(
      "naeural_core.comm.base.base_comm_thread.HeartbeatIngressWorker",
      side_effect=_worker_ctor,
    ):
      comm._start_heartbeat_ingress_worker()

    self.assertEqual(len(worker_arguments), 1)
    self.assertEqual(worker_arguments[0]["prepare_workers"], 4)
    self.assertEqual(worker_arguments[0]["max_in_flight"], 4)
    self.assertTrue(
      any("using 4" in call.args[0] for call in comm.P.call_args_list),
    )

  def test_regrouped_heartbeat_loop_runs_worker_off_fallback(self):
    class _Harness(_HeartbeatsCommMixin):
      def __init__(self):
        self._stop = False
        self._send_buff = []
        self._last_read = 0
        self.has_recv_conn = True
        self.has_send_conn = False
        self.loop_resolution = 10
        self.fallback_calls = 0

      def _init(self):
        return

      def _start_heartbeat_ingress_worker(self):
        return

      def _maybe_reconnect_send(self):
        return

      def _maybe_reconnect_recv(self):
        return

      def maybe_fill_recv_buffer_wrapper(self):
        return

      def _process_next_heartbeat_synchronously(self):
        self.fallback_calls += 1
        self._stop = True

      def _stop_heartbeat_ingress_worker(self, **kwargs):
        return True

      def _release(self):
        return

      def P(self, *args, **kwargs):
        return

    comm = _Harness()

    with mock.patch(
      "naeural_core.comm.mixins.heartbeats_comm_mixin.sleep",
      return_value=None,
    ):
      comm._run_thread_heartbeats()

    self.assertEqual(comm.fallback_calls, 1)
    self.assertTrue(comm._thread_stopped)

  def test_worker_disabled_shutdown_flushes_direct_verification_stats(self):
    events = []

    class _BlockEngine:
      def flush_verify_canon_stats(self, wait=False, timeout=None):
        events.append(("flush", wait, timeout))

    comm = BaseCommThread.__new__(BaseCommThread)
    comm._heartbeat_ingress_worker = None
    comm._heartbeat_ingress_processor = object()
    comm.shmem = {ct.BLOCKCHAIN_MANAGER: _BlockEngine()}

    stopped = comm._stop_heartbeat_ingress_worker(timeout=2.0)

    self.assertTrue(stopped)
    self.assertEqual(events, [("flush", True, 2.0)])

  def test_heartbeat_verification_defers_canon_stats_persistence(self):
    comm = BaseCommThread.__new__(BaseCommThread)
    block_engine = mock.Mock()
    comm.shmem = {ct.BLOCKCHAIN_MANAGER: block_engine}
    message = {"EE_EVENT_TYPE": "HEARTBEAT"}

    comm._verify_heartbeat_message(message)

    block_engine.verify.assert_called_once_with(
      message,
      return_full_info=True,
      verify_allowed=False,
      log_hash_sign_fails=False,
      persist_canon_stats=False,
    )

  def test_heartbeat_shutdown_flushes_canon_stats_after_worker_drain(self):
    events = []

    class _Buffer:
      def close(self, discard=False):
        events.append(("close", discard))

    class _Worker:
      def stop(self, drain=True, timeout=None):
        events.append(("stop", drain, timeout))

      def is_alive(self):
        return False

    class _BlockEngine:
      def flush_verify_canon_stats(self, wait=False, timeout=None):
        events.append(("flush", wait, timeout))
        return True

    comm = BaseCommThread.__new__(BaseCommThread)
    comm._recv_buff = _Buffer()
    comm._heartbeat_ingress_worker = _Worker()
    comm.shmem = {ct.BLOCKCHAIN_MANAGER: _BlockEngine()}

    stopped = comm._stop_heartbeat_ingress_worker(drain=True, timeout=3.0)

    self.assertTrue(stopped)
    self.assertEqual(events, [
      ("close", False),
      ("stop", True, 3.0),
      ("flush", True, 3.0),
    ])

  def test_heartbeat_shutdown_does_not_flush_while_worker_is_active(self):
    events = []

    class _Buffer:
      def close(self, discard=False):
        events.append(("close", discard))

    class _Worker:
      def stop(self, drain=True, timeout=None):
        events.append(("stop", drain, timeout))

      def is_alive(self):
        return True

    class _BlockEngine:
      def flush_verify_canon_stats(self, wait=False, timeout=None):
        events.append(("flush", wait, timeout))

    comm = BaseCommThread.__new__(BaseCommThread)
    comm._recv_buff = _Buffer()
    comm._heartbeat_ingress_worker = _Worker()
    comm.shmem = {ct.BLOCKCHAIN_MANAGER: _BlockEngine()}
    comm.P = lambda *args, **kwargs: events.append(("warning", args[0]))

    stopped = comm._stop_heartbeat_ingress_worker(drain=True, timeout=3.0)

    self.assertFalse(stopped)
    self.assertTrue(comm._heartbeat_ingress_stop_timed_out)
    self.assertEqual(events, [
      ("close", False),
      ("stop", True, 3.0),
      ("warning", "Heartbeat ingress did not stop within 3.0s; verifier stats were not flushed and shutdown drain is incomplete."),
    ])


if __name__ == "__main__":
  unittest.main()
