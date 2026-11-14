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

  def _make_listener(self, user, password, os_environ=None, comm_environment=None):
    """
    Build a listener without its thread, on a shared communication config whose broker is "broker.example".

    Parameters
    ----------
    user : str or None
      Broker user of the shared communication config.
    password : str or None
      Broker password of the shared communication config.
    os_environ : dict, optional
      The process environment the listener sees. Default None: empty.
    comm_environment : dict, optional
      The COMMUNICATION_ENVIRONMENT section of the node's startup config. Default None: no such section.

    Returns
    -------
    _IoTListener
      The listener, ready for ``_init()``; its log lines are collected in ``messages``.
    """
    # No constructor: _init() needs only the members set below.
    listener = _IoTListener.__new__(_IoTListener)
    # The lines the listener logs.
    listener.messages = []
    # Record each line as text.
    listener.P = lambda msg, *args, **kwargs: listener.messages.append(str(msg))
    # The JSON helper of the plugin base.
    listener.json_dumps = lambda dct, **kwargs: json.dumps(dct, **kwargs)
    # The node logger as the listener reads it: only the startup config.
    listener.log = SimpleNamespace(
      config_data={} if comm_environment is None else {"COMMUNICATION_ENVIRONMENT": comm_environment}
    )
    # The queue the wrapper would fill.
    listener.message_queue = []
    # The node's address.
    listener.bc = SimpleNamespace(address="0xai_node")
    # The process environment.
    listener.os_environ = dict(os_environ or {})
    # The node's name, part of the connection name.
    listener._device_id = "node"
    # The stream's name.
    listener.cfg_name = "iot"
    # Addressed payload subscriptions stay on.
    listener.cfg_disable_addressed_payload_subs = False
    # No per-stream connection settings.
    listener.cfg_stream_config_metadata = {}
    # Every connection property is taken from the shared communication config.
    listener.config = {
      key: "#DEFAULT"
      for key in ["HOST", "PORT", "USER", "PASS", "QOS", "SECURED"]
    }
    # The shared objects: the communication config with its broker.
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
    # No connection is made in these tests.
    listener._maybe_reconnect_to_controller_server = mock.Mock()
    # The listener, before _init().
    return listener

  def _wrapper_hosts(self, listener):
    """
    Run the listener's ``_init`` and return the broker candidates its wrapper was given.

    Parameters
    ----------
    listener : _IoTListener
      A listener built by ``_make_listener``.

    Returns
    -------
    str or None
      The ``HOSTS`` value of the wrapper's config, or None when the config has none.
    """
    # The wrapper class is replaced: its constructor arguments are what the test reads.
    with mock.patch.object(_IOT_MODULE, "MQTTWrapper") as wrapper:
      # The real initialisation of the listener.
      listener._init()
    # The candidates in the config handed to the wrapper.
    return wrapper.call_args.kwargs["config"].get("HOSTS")

  # The dAuth candidate list as the environment holds it: JSON text with the allocated primary first.
  HOSTS = '["broker.example", "second.example"]'

  def test_the_listener_gets_the_dauth_broker_candidates(self):
    """With its broker the primary dAuth allocated, the listener's wrapper gets EE_MQTT_HOSTS, and the log shows it."""
    # The environment after dAuth: the primary is the broker of the shared config, with a second candidate.
    listener = self._make_listener("u", "p", os_environ={"EE_MQTT_HOST": "broker.example", "EE_MQTT_HOSTS": self.HOSTS})
    # The wrapper can fail over: it has the candidate list (before, it had only the primary).
    self.assertEqual(self._wrapper_hosts(listener), self.HOSTS)
    # The logged connection config names the second candidate.
    self.assertIn("second.example", "\n".join(listener.messages))

  def test_no_candidates_for_another_broker_or_without_a_list(self):
    """A listener on a broker that is not the allocated primary, or with no list in the environment, gets no HOSTS."""
    # The listener's broker is not the one dAuth allocated: a custom or local broker keeps its single host.
    listener = self._make_listener("u", "p", os_environ={"EE_MQTT_HOST": "other.example", "EE_MQTT_HOSTS": self.HOSTS})
    # No candidates.
    self.assertIsNone(self._wrapper_hosts(listener))
    # The allocated primary, but dAuth gave no candidate list (a node with one broker).
    listener = self._make_listener("u", "p", os_environ={"EE_MQTT_HOST": "broker.example"})
    # No candidates.
    self.assertIsNone(self._wrapper_hosts(listener))

  def test_the_listener_follows_the_communicators_rule_on_the_same_inputs(self):
    """For every combination of startup config and environment, the listener and the comm manager choose the same HOSTS."""
    # The process environment of the comm manager is patched below. Imported here, like the two imports after
    # it: this file loads the listener on stubs, and these need the real package.
    import os
    # The helper the manager's settings parser uses.
    from naeural_core import Logger
    # The real communication manager, whose rule the listener must follow.
    from naeural_core.comm.communication_manager import CommunicationManager
    # The environment after dAuth for the listener's broker, and for another one.
    allocated = {"EE_MQTT_HOST": "broker.example", "EE_MQTT_HOSTS": self.HOSTS}
    # The same list allocated to another primary.
    elsewhere = {"EE_MQTT_HOST": "other.example", "EE_MQTT_HOSTS": self.HOSTS}
    # (process environment, COMMUNICATION_ENVIRONMENT, expected HOSTS)
    cases = [
      # The process environment alone decides.
      (allocated, None, self.HOSTS),
      # Another primary: nothing.
      (elsewhere, None, None),
      # The startup config's values come before the process environment's: its primary is another broker.
      (allocated, {"EE_MQTT_HOST": "other.example"}, None),
      # The startup config's primary is the listener's broker, the process environment's is not.
      (elsewhere, {"EE_MQTT_HOST": "broker.example"}, self.HOSTS),
      # The startup config carries the list itself.
      ({"EE_MQTT_HOST": "broker.example"}, {"EE_MQTT_HOSTS": '["broker.example", "third.example"]'},
       '["broker.example", "third.example"]'),
      # A HOSTS entry in the startup config switches the dAuth list off.
      (allocated, {"HOSTS": '["x.example"]'}, None),
      # No list anywhere.
      ({"EE_MQTT_HOST": "broker.example"}, None, None),
    ]
    # Every combination.
    for os_environ, comm_environment, expected in cases:
      # Each combination is reported on its own.
      with self.subTest(os_environ=os_environ, comm_environment=comm_environment):
        # The listener's choice.
        listener = self._make_listener("u", "p", os_environ=os_environ, comm_environment=comm_environment)
        # What its wrapper gets.
        chosen = self._wrapper_hosts(listener)
        # The comm manager, with the startup config's section as its environment variables.
        manager = CommunicationManager.__new__(CommunicationManager)
        # As the orchestrator passes them.
        manager._environment_variables = dict(comm_environment or {})
        # The helper the manager's settings parser uses.
        manager.log = SimpleNamespace(str_to_bool=Logger.str_to_bool)
        # The same process environment for the manager.
        with mock.patch.dict(os.environ, os_environ, clear=True):
          # The manager's choice for a communicator on the same broker.
          prepared = manager._prepare_comm_config_instance({"HOST": "broker.example"})
        # The expected value, and the same from both.
        self.assertEqual((chosen, prepared.get("HOSTS")), (expected, expected))

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
