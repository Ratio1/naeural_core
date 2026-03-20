import copy
import json
import unittest
from unittest import mock

from naeural_core import constants as ct
from ratio1.const import HB, PAYLOAD_DATA


class _FakeLog:
  def compress_text(self, text):
    import base64
    import zlib

    return base64.b64encode(zlib.compress(text.encode("utf-8"), level=9)).decode("utf-8")

  def decompress_text(self, text):
    import base64
    import zlib

    try:
      return zlib.decompress(base64.b64decode(text)).decode("utf-8")
    except Exception:
      return None


def build_v1_netmon_payload():
  return {
    PAYLOAD_DATA.EE_ID: "oracle-1",
    PAYLOAD_DATA.EE_SENDER: "0xoracle",
    PAYLOAD_DATA.EE_PAYLOAD_PATH: ["0xoracle", "admin_pipeline", "NET_MON_01", "NETMON_01_INST"],
    PAYLOAD_DATA.STREAM_NAME: "admin_pipeline",
    PAYLOAD_DATA.SIGNATURE: "NET_MON_01",
    PAYLOAD_DATA.INSTANCE_ID: "NETMON_01_INST",
    PAYLOAD_DATA.SESSION_ID: "sess-1",
    PAYLOAD_DATA.INITIATOR_ID: "sdk-user",
    PAYLOAD_DATA.INITIATOR_ADDR: "0xself",
    PAYLOAD_DATA.MODIFIED_BY_ID: "sdk-user",
    PAYLOAD_DATA.MODIFIED_BY_ADDR: "0xself",
    "USE_LOCAL_COMMS_ONLY": False,
    PAYLOAD_DATA.NETMON_CURRENT_NETWORK: {
      "node-1": {
        PAYLOAD_DATA.NETMON_ADDRESS: "0xpeer",
        PAYLOAD_DATA.NETMON_EEID: "peer-1",
        PAYLOAD_DATA.NETMON_STATUS_KEY: PAYLOAD_DATA.NETMON_STATUS_ONLINE,
        PAYLOAD_DATA.NETMON_WHITELIST: [0],
      }
    },
    PAYLOAD_DATA.NETMON_WHITELIST_MAP: {
      "0xself": 0,
    },
    "CURRENT_ALERTED": {},
    "CURRENT_RANKING": [],
    "CURRENT_NEW": [],
    "STATUS": "ok",
    "MESSAGE": "ok",
    "SEND_CURRENT_NETWORK_EACH": 0,
    "IS_SUPERVISOR": True,
  }


def build_v2_netmon_payload(log=None):
  log = log or _FakeLog()
  return PAYLOAD_DATA.maybe_encode_netmon_payload(copy.deepcopy(build_v1_netmon_payload()), log=log)


class TestNetmonCompressionHelpers(unittest.TestCase):

  def setUp(self):
    self.log = _FakeLog()

  def test_core_contract_keeps_transport_fields_outside_encoded_body(self):
    payload = build_v1_netmon_payload()

    encoded = PAYLOAD_DATA.maybe_encode_netmon_payload(copy.deepcopy(payload), log=self.log)

    self.assertEqual(encoded[PAYLOAD_DATA.NETMON_VERSION], PAYLOAD_DATA.NETMON_VERSION_V2)
    self.assertIn(HB.ENCODED_DATA, encoded)
    self.assertNotIn(PAYLOAD_DATA.NETMON_CURRENT_NETWORK, encoded)
    self.assertEqual(encoded[PAYLOAD_DATA.STREAM_NAME], payload[PAYLOAD_DATA.STREAM_NAME])
    self.assertEqual(encoded[PAYLOAD_DATA.SIGNATURE], payload[PAYLOAD_DATA.SIGNATURE])
    self.assertEqual(encoded[PAYLOAD_DATA.INSTANCE_ID], payload[PAYLOAD_DATA.INSTANCE_ID])
    self.assertEqual(encoded[PAYLOAD_DATA.SESSION_ID], payload[PAYLOAD_DATA.SESSION_ID])

  def test_core_decode_then_whitelist_conversion_restores_current_shape(self):
    payload = build_v2_netmon_payload(self.log)

    decoded = PAYLOAD_DATA.maybe_decode_netmon_payload(payload, log=self.log)
    with mock.patch.object(ct, "ETH_ENABLED", True):
      converted = ct.PAYLOAD_DATA.maybe_convert_netmon_whitelist(decoded)

    node_data = converted[PAYLOAD_DATA.NETMON_CURRENT_NETWORK]["node-1"]
    self.assertEqual(node_data[PAYLOAD_DATA.NETMON_WHITELIST], ["0xself"])
    self.assertEqual(converted["STATUS"], "ok")
    self.assertEqual(converted["MESSAGE"], "ok")

  def test_core_decode_failure_is_non_fatal(self):
    payload = {
      PAYLOAD_DATA.EE_ID: "oracle-1",
      PAYLOAD_DATA.NETMON_VERSION: PAYLOAD_DATA.NETMON_VERSION_V2,
      HB.ENCODED_DATA: "bad-data",
    }

    decoded = PAYLOAD_DATA.maybe_decode_netmon_payload(copy.deepcopy(payload), log=self.log)

    self.assertEqual(decoded, payload)
    self.assertNotIn(PAYLOAD_DATA.NETMON_CURRENT_NETWORK, decoded)

  def test_sender_wrapper_keeps_payload_json_serializable(self):
    class _FakePayload:
      def __init__(self, payload_data):
        self.payload_data = payload_data

      def to_dict(self):
        return copy.deepcopy(self.payload_data)

    class _FakePlugin:
      compress_netmon = True
      const = ct
      log = _FakeLog()

      @staticmethod
      def deepcopy(obj):
        return copy.deepcopy(obj)

    payload = _FakePayload(build_v1_netmon_payload())
    plugin = _FakePlugin()
    original_to_dict = payload.to_dict
    payload_dict = original_to_dict()
    encoded_payload = plugin.const.PAYLOAD_DATA.maybe_encode_netmon_payload(
      payload_dict,
      log=plugin.log,
    )

    def compressed_to_dict():
      return plugin.deepcopy(encoded_payload)

    payload.to_dict = compressed_to_dict
    encoded = payload.to_dict()

    self.assertNotIn("to_dict", encoded)
    json.dumps(encoded)
    self.assertEqual(encoded[PAYLOAD_DATA.NETMON_VERSION], PAYLOAD_DATA.NETMON_VERSION_V2)
    self.assertIn(HB.ENCODED_DATA, encoded)


if __name__ == "__main__":
  unittest.main()
