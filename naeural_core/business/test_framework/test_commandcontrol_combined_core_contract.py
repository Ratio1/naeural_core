import unittest

from naeural_core import constants as ct
from naeural_core.comm.base.base_comm_thread import BaseCommThread, _CONFIG
from naeural_core.comm.communication_manager import CommunicationManager


class _BlockEngine:
  address = "sender"

  def maybe_add_prefix(self, value):
    return "0xai_{}".format(value)


class TestCombinedCoreContract(unittest.TestCase):

  def test_one_config_exposes_ingress_and_targeted_mirror_controls(self):
    self.assertTrue(_CONFIG["HEARTBEAT_INGRESS_WORKER_ENABLED"])
    self.assertGreaterEqual(_CONFIG["HEARTBEAT_INGRESS_QUEUE_SIZE"], 1)
    self.assertGreaterEqual(_CONFIG["HEARTBEAT_AUTH_WORKERS"], 1)
    self.assertGreaterEqual(_CONFIG["HEARTBEAT_AUTH_MAX_IN_FLIGHT"], 1)
    self.assertGreaterEqual(
      _CONFIG["HEARTBEAT_AUTH_MAX_IN_FLIGHT"],
      _CONFIG["HEARTBEAT_AUTH_WORKERS"],
    )
    self.assertEqual(_CONFIG["HEARTBEAT_AUTH_MODE"], "shadow")
    self.assertFalse(_CONFIG["HEARTBEAT_TARGETED_MIRROR_ENABLED"])

  def test_one_communicator_class_exposes_both_status_boundaries(self):
    self.assertTrue(hasattr(BaseCommThread, "get_heartbeat_ingress_status"))
    self.assertTrue(hasattr(BaseCommThread, "get_outbound_queue_status"))
    self.assertTrue(hasattr(BaseCommThread, "get_transport_delivery_status"))
    self.assertTrue(hasattr(CommunicationManager, "maybe_process_incoming"))

  def test_targeted_heartbeat_and_global_copy_are_resolved_together(self):
    communicator = BaseCommThread.__new__(BaseCommThread)
    communicator._config = {
      "HEARTBEAT_TARGETED_MIRROR_ENABLED": True,
      ct.COMMS.COMMUNICATION_CTRL_CHANNEL: {
        "TARGETED_TOPIC": "ratio1/ctrl/{}",
      },
    }
    communicator._environment_variables = {}
    communicator._send_channel_name = ct.COMMS.COMMUNICATION_CTRL_CHANNEL
    communicator.shmem = {ct.BLOCKCHAIN_MANAGER: _BlockEngine()}
    communicator.P = lambda *args, **kwargs: None

    targets = communicator._resolve_publish_targets({
      ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
    })

    self.assertEqual(targets, [None, "0xai_sender"])


if __name__ == "__main__":
  unittest.main()
