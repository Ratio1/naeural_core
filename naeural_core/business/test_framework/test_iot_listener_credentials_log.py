import importlib.util
import json
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock


def _load_iot_listener_module():
  """Load BaseIoTQueueListenerDataCapture with lightweight stubs."""
  path = pathlib.Path(__file__).resolve().parents[2] / "data" / "base" / "base_iot_queue_listener.py"
  module_name = "base_iot_queue_listener_under_test"
  module = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location(module_name, path)
  )

  ct_mod = types.ModuleType("naeural_core.constants")
  ct_mod.COMMS = SimpleNamespace(
    EE_ID="EE_ID",
    EE_ADDR="EE_ADDR",
    HOST="HOST",
    PORT="PORT",
    USER="USER",
    PASS="PASS",
    QOS="QOS",
    SECURED="SECURED",
  )

  class _DataCaptureThread:
    CONFIG = {"VALIDATION_RULES": {}}

  core_mod = types.ModuleType("naeural_core")
  core_mod.constants = ct_mod
  dct_mod = types.ModuleType("naeural_core.data.base.base_plugin_dct")
  dct_mod.DataCaptureThread = _DataCaptureThread
  formatter_mod = types.ModuleType("naeural_core.io_formatters.io_formatter_manager")
  formatter_mod.IOFormatterManager = object
  comm_mod = types.ModuleType("naeural_core.comm")
  comm_mod.AMQPWrapper = object
  comm_mod.MQTTWrapper = object

  stubs = {
    "naeural_core": core_mod,
    "naeural_core.constants": ct_mod,
    "naeural_core.data.base.base_plugin_dct": dct_mod,
    "naeural_core.io_formatters.io_formatter_manager": formatter_mod,
    "naeural_core.comm": comm_mod,
  }
  old_modules = {
    name: sys.modules.get(name)
    for name in stubs
  }
  try:
    sys.modules.update(stubs)
    module.__spec__.loader.exec_module(module)
  finally:
    for name, old_module in old_modules.items():
      if old_module is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = old_module
  return module


_IOT_MODULE = _load_iot_listener_module()


class _IoTListener(_IOT_MODULE.BaseIoTQueueListenerDataCapture):
  def _filter_message(self, unfiltered_message):
    return unfiltered_message

  def _parse_message(self, filtered_message):
    return filtered_message

  def _parse_and_filter_message(self, message):
    return message


class TestIoTListenerCredentialsLog(unittest.TestCase):

  def _make_listener(self, user, password):
    listener = _IoTListener.__new__(_IoTListener)
    listener.messages = []
    listener.P = lambda msg, *args, **kwargs: listener.messages.append(str(msg))
    listener.json_dumps = lambda dct, **kwargs: json.dumps(dct, **kwargs)
    listener.log = None
    listener.message_queue = []
    listener.bc = SimpleNamespace(address="0xai_node")
    listener.os_environ = {}
    listener._device_id = "node"
    listener.cfg_name = "iot"
    listener.cfg_disable_addressed_payload_subs = False
    listener.cfg_stream_config_metadata = {}
    listener.config = {
      key: "#DEFAULT"
      for key in ["HOST", "PORT", "USER", "PASS", "QOS", "SECURED"]
    }
    listener.shmem = {
      "io_formatter_manager": None,
      "config_communication": {
        "TYPE": "mqtt",
        "PARAMS": {
          "EE_ID": "node",
          "HOST": "broker.example",
          "PORT": 8883,
          "USER": user,
          "PASS": password,
          "QOS": 2,
          "SECURED": True,
          "PAYLOADS_CHANNEL": {"TOPIC": "payloads"},
        },
      },
    }
    listener._maybe_reconnect_to_controller_server = mock.Mock()
    return listener

  def test_init_redacts_broker_credentials_in_log(self):
    listener = self._make_listener(user="broker-user", password="broker-secret")

    with mock.patch.object(_IOT_MODULE, "MQTTWrapper") as wrapper:
      listener._init()

    logged = "\n".join(listener.messages)
    self.assertNotIn("broker-user", logged)
    self.assertNotIn("broker-secret", logged)
    self.assertIn('"USER": "[REDACTED]"', logged)
    self.assertIn('"PASS": "[REDACTED]"', logged)
    self.assertIn("broker.example", logged)
    # the wrapper still receives the real credentials
    wrapper_config = wrapper.call_args.kwargs["config"]
    self.assertEqual(wrapper_config["USER"], "broker-user")
    self.assertEqual(wrapper_config["PASS"], "broker-secret")
    return

  def test_init_logs_missing_credentials_as_is(self):
    listener = self._make_listener(user=None, password=None)

    with mock.patch.object(_IOT_MODULE, "MQTTWrapper"):
      listener._init()

    logged = "\n".join(listener.messages)
    self.assertIn('"USER": null', logged)
    self.assertIn('"PASS": null', logged)
    return


if __name__ == "__main__":
  unittest.main()
