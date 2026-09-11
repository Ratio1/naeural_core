import copy
import json
import unittest
from collections import deque
from threading import Lock

from naeural_core import constants as ct
from naeural_core.comm.base.base_comm_thread import BaseCommThread
from naeural_core.comm.communication_manager import CommunicationManager
from naeural_core.comm.message_buffer import ObservableMessageBuffer


class _Log:
  def now_str(self, **kwargs):
    return "2026-08-17 10:00:00"

  def safe_json_dumps(self, value, **kwargs):
    return json.dumps(value)


class _BlockEngine:
  address = "0xai_SENDER"

  def encrypt(self, plaintext, receiver_address):
    return "encrypted:{}:{}".format(receiver_address, plaintext)


class _CommandHarness:
  _prepare_command = BaseCommThread._prepare_command
  send = BaseCommThread.send
  _mark_outbound_dequeued = BaseCommThread._mark_outbound_dequeued
  _set_outbound_current_state = BaseCommThread._set_outbound_current_state
  _finish_outbound_current = BaseCommThread._finish_outbound_current

  def __init__(self, capacity=2, encrypted=False, observable=False):
    self._comm_type = ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL
    self._msg_id = 0
    self._send_buff = (
      ObservableMessageBuffer(capacity=capacity)
      if observable
      else deque(maxlen=capacity)
    )
    self._send_buffer_lock = Lock()
    self._send_buffer_admitted = 0
    self._send_buffer_rejected_full = 0
    self._send_buffer_dequeued = 0
    self._send_buffer_discarded = 0
    self._send_buffer_high_water_mark = 0
    self._send_buffer_degraded = False
    self._outbound_state_lock = Lock()
    self._outbound_current = None
    self._outbound_transport_handoffs = 0
    self._outbound_terminal_discards = 0
    self.cfg_encrypted_comms = encrypted
    self.log = _Log()
    self.bc_engine = _BlockEngine()
    self.messages = []

  def P(self, message, **kwargs):
    self.messages.append((message, kwargs))

  def json_dumps(self, value, **kwargs):
    return json.dumps(value, **kwargs)

  def get_outbound_queue_status(self):
    return BaseCommThread.get_outbound_queue_status(self)


def _command(command_id="cmd-1"):
  return {
    "COMMAND_ID": command_id,
    ct.COMMS.COMM_SEND_MESSAGE.K_ACTION: "UPDATE_CONFIG",
    ct.COMMS.COMM_SEND_MESSAGE.K_PAYLOAD: {
      "PIPELINE": "test",
      "VALUES": [1, 2, 3],
    },
  }


class TestCommandPreparationImmutability(unittest.TestCase):

  def test_prepare_command_does_not_mutate_original_envelope(self):
    harness = _CommandHarness()
    original = _command()
    expected = copy.deepcopy(original)

    prepared = harness._prepare_command(original, receiver_address="0xai_NODE")

    self.assertEqual(original, expected)
    self.assertEqual(prepared["ACTION"], expected["ACTION"])
    self.assertEqual(prepared["PAYLOAD"], expected["PAYLOAD"])

  def test_repeated_preparation_uses_exact_same_original_content(self):
    harness = _CommandHarness(encrypted=True)
    original = _command()

    first = harness._prepare_command(original, receiver_address="0xai_NODE")
    second = harness._prepare_command(original, receiver_address="0xai_NODE")

    self.assertIsNotNone(first)
    self.assertEqual(first, second)
    self.assertIn("UPDATE_CONFIG", first["EE_ENCRYPTED_DATA"])


class TestCommandQueueAdmission(unittest.TestCase):

  def test_full_queue_rejects_newest_without_evicting_accepted_commands(self):
    harness = _CommandHarness(capacity=2)

    self.assertTrue(harness.send(_command("cmd-1")))
    self.assertTrue(harness.send(_command("cmd-2")))
    self.assertFalse(harness.send(_command("cmd-3")))

    queued_ids = [entry[1]["COMMAND_ID"] for entry in harness._send_buff]
    self.assertEqual(queued_ids, ["cmd-1", "cmd-2"])
    status = harness.get_outbound_queue_status()
    self.assertEqual(status["depth"], 2)
    self.assertEqual(status["admitted"], 2)
    self.assertEqual(status["rejected_full"], 1)
    self.assertTrue(status["degraded"])

  def test_enqueue_snapshots_command_before_caller_can_mutate_it(self):
    harness = _CommandHarness(capacity=2)
    command = _command()

    self.assertTrue(harness.send(command))
    command["PAYLOAD"]["VALUES"].append(999)
    command["ACTION"] = "MUTATED"

    queued = harness._send_buff[0][1]
    self.assertEqual(queued["ACTION"], "UPDATE_CONFIG")
    self.assertEqual(queued["PAYLOAD"]["VALUES"], [1, 2, 3])

  def test_closed_command_buffer_rejects_post_stop_admission(self):
    harness = _CommandHarness(capacity=2, observable=True)
    harness._recv_buff = ObservableMessageBuffer(capacity=1)
    harness._stop = False
    harness._stop_heartbeat_ingress_worker = lambda **kwargs: None

    class _ThreadProbe:
      def join(self):
        return

    harness._thread = _ThreadProbe()

    BaseCommThread.stop(harness)

    self.assertFalse(harness.send(_command("late-command")))
    snapshot = harness._send_buff.snapshot()
    self.assertEqual(snapshot.depth, 0)
    self.assertEqual(snapshot.rejected_closed, 1)

  def test_dequeued_retry_is_visible_without_exposing_command_payload(self):
    harness = _CommandHarness(capacity=2, observable=True)
    self.assertTrue(harness.send(_command("cmd-held")))
    age_at_dequeue = harness._send_buff.snapshot().oldest_age_seconds
    entry = harness._send_buff.popleft()
    harness._mark_outbound_dequeued(entry[0], age_at_dequeue)
    harness._set_outbound_current_state(
      "retry_pending",
      retry_targets=["0xai_NODE"],
      increment_retry=True,
    )

    status = harness.get_outbound_queue_status()

    self.assertEqual(status["depth"], 0)
    self.assertEqual(status["current"]["message_id"], entry[0])
    self.assertEqual(status["current"]["state"], "retry_pending")
    self.assertEqual(status["current"]["retry_attempts"], 1)
    self.assertNotIn("PAYLOAD", status["current"])
    self.assertTrue(status["completion_conserved"])

    harness._finish_outbound_current(transport_handoff=True)
    completed = harness.get_outbound_queue_status()
    self.assertIsNone(completed["current"])
    self.assertEqual(completed["transport_handoff_completed"], 1)
    self.assertTrue(completed["completion_conserved"])

  def test_non_command_send_preserves_legacy_deque_contract(self):
    harness = _CommandHarness(capacity=1)
    harness._comm_type = ct.COMMS.COMMUNICATION_DEFAULT

    first_result = harness.send({"PAYLOAD": "first"})
    second_result = harness.send({"PAYLOAD": "second"})

    self.assertIsNone(first_result)
    self.assertIsNone(second_result)
    self.assertEqual(len(harness._send_buff), 1)
    self.assertEqual(harness._send_buff[0][1], {"PAYLOAD": "second"})


class _AdmissionComm:
  def __init__(self, accepted):
    self.accepted = accepted

  def send(self, data):
    return self.accepted


class TestCommandAdmissionPropagation(unittest.TestCase):

  def test_manager_reports_partial_local_and_central_admission(self):
    manager = CommunicationManager.__new__(CommunicationManager)
    manager._CommunicationManager__local_communication_enabled = True
    manager._CommunicationManager__local_communication_active = True
    manager._dct_comm_plugins = {
      ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL: _AdmissionComm(True),
      "L_" + ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL: _AdmissionComm(False),
    }

    result = manager._CommunicationManager__select_communicators_and_send(
      data=("node", "0xai_NODE", _command()),
      communicator_name=ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL,
      return_admission=True,
    )

    self.assertEqual(result["attempted"], ["local", "central"])
    self.assertEqual(result["accepted_by"], ["central"])
    self.assertEqual(result["rejected_by"], ["local"])
    self.assertTrue(result["any_admitted"])
    self.assertFalse(result["fully_admitted"])


if __name__ == "__main__":
  unittest.main()
