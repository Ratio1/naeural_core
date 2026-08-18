import importlib.util
import pathlib
from types import SimpleNamespace
import unittest


MODULE_PATH = (
  pathlib.Path(__file__).resolve().parents[2]
  / "comm"
  / "communication_roles.py"
)
SPEC = importlib.util.spec_from_file_location(
  "communication_roles_under_test",
  MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TestCommunicationReceiveRoles(unittest.TestCase):

  def test_legacy_crossed_topology_maps_receive_roles_by_channel(self):
    instances = {
      "COMMANDCONTROL": {
        "SEND_TO": "CONFIG_CHANNEL",
        "RECV_FROM": "CTRL_CHANNEL",
      },
      "HEARTBEATS": {
        "SEND_TO": "CTRL_CHANNEL",
        "RECV_FROM": "CONFIG_CHANNEL",
      },
    }

    roles = MODULE.resolve_receive_roles(instances)

    self.assertEqual(roles["heartbeat"], "COMMANDCONTROL")
    self.assertEqual(roles["command"], "HEARTBEATS")

  def test_regrouped_topology_maps_receive_roles_by_channel(self):
    instances = {
      "COMMANDCONTROL": {
        "SEND_TO": "CONFIG_CHANNEL",
        "RECV_FROM": "CONFIG_CHANNEL",
      },
      "HEARTBEATS": {
        "SEND_TO": "CTRL_CHANNEL",
        "RECV_FROM": "CTRL_CHANNEL",
      },
    }

    roles = MODULE.resolve_receive_roles(instances)

    self.assertEqual(roles["heartbeat"], "HEARTBEATS")
    self.assertEqual(roles["command"], "COMMANDCONTROL")

  def test_duplicate_receive_owner_fails_closed(self):
    instances = {
      "COMMANDCONTROL": {"RECV_FROM": "CTRL_CHANNEL"},
      "HEARTBEATS": {"RECV_FROM": "CTRL_CHANNEL"},
    }

    with self.assertRaisesRegex(ValueError, "CTRL_CHANNEL"):
      MODULE.resolve_receive_roles(instances)

  def test_runtime_selector_uses_communicator_channel_not_legacy_name(self):
    communicators = {
      "COMMANDCONTROL": SimpleNamespace(
        recv_channel_name="CONFIG_CHANNEL",
      ),
      "HEARTBEATS": SimpleNamespace(
        recv_channel_name="CTRL_CHANNEL",
      ),
    }

    selected = MODULE.select_receive_communicators(
      communicators,
      "CONFIG_CHANNEL",
    )

    self.assertEqual(selected, [communicators["COMMANDCONTROL"]])

  def test_disabled_ctrl_receive_is_allowed_for_non_observer(self):
    instances = {
      "COMMANDCONTROL": {
        "SEND_TO": "CONFIG_CHANNEL",
        "RECV_FROM": "CONFIG_CHANNEL",
      },
      "HEARTBEATS": {
        "SEND_TO": "CTRL_CHANNEL",
        "RECV_FROM": None,
      },
    }

    roles = MODULE.resolve_receive_roles(
      instances,
      require_heartbeat_receiver=False,
    )

    self.assertIsNone(roles["heartbeat"])
    self.assertEqual(roles["command"], "COMMANDCONTROL")


if __name__ == "__main__":
  unittest.main()
