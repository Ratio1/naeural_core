import os
import unittest
from unittest.mock import patch

from naeural_core import constants as ct
from naeural_core.comm.base.base_comm_thread import BaseCommThread


NODE_ADDRESS = "0xai_A_b3ELqPfiAjV7iBSiOyUEO7gWOQG9WUsUuojPWbiMiF"


class _BlockchainEngine:
  address = NODE_ADDRESS

  def maybe_add_prefix(self, address):
    return address


class _RoutingHarness(BaseCommThread):
  def __init__(self, mirror_enabled=False, targeted_topic=True):
    ctrl_channel = {
      "TOPIC": "ratio1/ctrl",
    }
    if targeted_topic:
      ctrl_channel["TARGETED_TOPIC"] = "ratio1/ctrl/{}"
    self._config = {
      "CTRL_CHANNEL": ctrl_channel,
      "HEARTBEAT_TARGETED_MIRROR_ENABLED": mirror_enabled,
    }
    self._send_channel_name = "CTRL_CHANNEL"
    self._bc_engine = _BlockchainEngine()
    self.sent = []
    self.messages = []

  @property
  def bc_engine(self):
    return self._bc_engine

  def P(self, message, **kwargs):
    self.messages.append((message, kwargs))

  def _init(self):
    return

  def _maybe_reconnect_send(self):
    return

  def _maybe_reconnect_recv(self):
    return

  def _send(self, data, send_to=None):
    self.sent.append((data, send_to))

  def _maybe_fill_recv_buffer(self):
    return

  def _release(self):
    return


def _heartbeat():
  return {
    ct.PAYLOAD_DATA.EE_EVENT_TYPE: "HEARTBEAT",
    ct.PAYLOAD_DATA.EE_SENDER: NODE_ADDRESS,
  }


class TestHeartbeatTargetedMirror(unittest.TestCase):

  def test_mirror_is_disabled_by_default(self):
    comm = _RoutingHarness(mirror_enabled=False)

    targets = comm._resolve_publish_targets(_heartbeat())

    self.assertEqual(targets, [None])

  def test_enabled_mirror_adds_only_the_publishers_own_target(self):
    comm = _RoutingHarness(mirror_enabled=True)

    targets = comm._resolve_publish_targets(_heartbeat())

    self.assertEqual(targets, [None, NODE_ADDRESS])

  def test_explicit_environment_false_overrides_enabled_persisted_config(self):
    comm = _RoutingHarness(mirror_enabled=True)

    with patch.dict(
        os.environ,
        {"EE_HEARTBEAT_TARGETED_MIRROR_ENABLED": "0"},
    ):
      targets = comm._resolve_publish_targets(_heartbeat())

    self.assertEqual(targets, [None])

  def test_mirror_reuses_the_exact_signed_serialization(self):
    comm = _RoutingHarness(mirror_enabled=True)
    serialized = '{"signed":"heartbeat"}'

    result = comm._publish_serialized_message(
      message=serialized,
      data=_heartbeat(),
    )

    self.assertEqual(
      comm.sent,
      [(serialized, None), (serialized, NODE_ADDRESS)],
    )
    self.assertEqual(result["published_bytes"], len(serialized) * 2)

  def test_enabled_mirror_without_targeted_topic_stays_global_and_warns(self):
    comm = _RoutingHarness(mirror_enabled=True, targeted_topic=False)

    targets = comm._resolve_publish_targets(_heartbeat())

    self.assertEqual(targets, [None])
    self.assertTrue(any("TARGETED_TOPIC" in message for message, _ in comm.messages))


if __name__ == "__main__":
  unittest.main()
