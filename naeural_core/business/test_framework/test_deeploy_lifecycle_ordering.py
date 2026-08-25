import copy
import importlib.util
import json
from pathlib import Path
import unittest

from naeural_core import constants as ct
from naeural_core.config.config_manager_commands import ConfigCommandHandlers


_CMDAPI_SPEC = importlib.util.spec_from_file_location(
  "_deeploy_lifecycle_cmdapi",
  Path(__file__).resolve().parents[1] / "mixins_base" / "cmdapi.py",
)
_CMDAPI_MODULE = importlib.util.module_from_spec(_CMDAPI_SPEC)
_CMDAPI_SPEC.loader.exec_module(_CMDAPI_MODULE)
_CmdAPIMixin = _CMDAPI_MODULE._CmdAPIMixin


class _NetMonHarness:

  def network_node_eeid(self, node_address):
    return f"box-{node_address}"


class _LogHarness:

  def now_str(self, nice_print=True):
    return "2026-05-29 00:00:00"


class _CmdApiHarness(_CmdAPIMixin):

  def __init__(self):
    self._commands = []
    self.commands_deque = []
    self.net_mon = _NetMonHarness()
    self.log = _LogHarness()
    self.use_local_comms_only = False
    self.node_addr = "local-node"
    self.logs = []

  def P(self, msg, *args, **kwargs):
    self.logs.append(str(msg))

  def get_stream_id(self):
    return "current-stream"


class _ConfigManagerHarness(ConfigCommandHandlers):

  def __init__(self, streams=None):
    self.admin_pipeline_name = "admin_pipeline"
    self.dct_config_streams = copy.deepcopy(streams or {})
    self.deleted_streams = []
    self.saved_streams = []
    self.notifications = []
    self.logs = []

  def P(self, msg, *args, **kwargs):
    self.logs.append(str(msg))

  def _create_notification(self, **kwargs):
    self.notifications.append(kwargs)

  def _delete_stream_config(self, stream_name):
    self.deleted_streams.append(stream_name)

  def _save_stream_config(self, config_stream):
    self.saved_streams.append(copy.deepcopy(config_stream))

  def _check_duplicate_last(self, payload, payload_type):
    return False

  def _apply_delta_to_config(self, original_config, delta_config, ignore_fields=None):
    ignored = set(ignore_fields or [])
    result = copy.deepcopy(original_config)
    for key, value in delta_config.items():
      if key not in ignored:
        result[key] = value
    return result

  def keep_good_stream(self, config_stream):
    return config_stream


def _deeploy_stream(name="deeploy-app", generation=2, date_updated=200.0):
  return {
    ct.CONFIG_STREAM.NAME: name,
    ct.CONFIG_STREAM.TYPE: "Void",
    ct.CONFIG_STREAM.INITIATOR_ID: "manager-node",
    ct.CONFIG_STREAM.SESSION_ID: "session-1",
    ct.CONFIG_STREAM.DEEPLOY_SPECS: {
      "lifecycle_generation": generation,
      "date_updated": date_updated,
      "job_id": 2002,
    },
  }


def _delete_payload(name="deeploy-app", generation=1, date_updated=100.0):
  return {
    ct.CONFIG_STREAM.NAME: name,
    ct.CONFIG_STREAM.DEEPLOY_SPECS: {
      "lifecycle_generation": generation,
      "date_updated": date_updated,
      "job_id": 2002,
    },
  }


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "deeploy_lifecycle_ordering"


class DeeployLifecycleOrderingTests(unittest.TestCase):

  def test_stale_deeploy_delete_does_not_remove_newer_stream(self):
    manager = _ConfigManagerHarness({"deeploy-app": _deeploy_stream()})

    manager.delete_config_stream(
      stream_name=_delete_payload(generation=1, date_updated=100.0),
      initiator_id="manager-node",
      session_id="session-1",
    )

    self.assertIn("deeploy-app", manager.dct_config_streams)
    self.assertEqual(manager.deleted_streams, [])
    self.assertTrue(any("stale" in log.lower() for log in manager.logs))

  def test_fixture_old_delete_after_new_update_keeps_newer_stream(self):
    with (FIXTURE_DIR / "old_delete_after_new_update.json").open(encoding="utf-8") as stream:
      fixture = json.load(stream)
    stream_name = fixture["stream_name"]
    manager = _ConfigManagerHarness({stream_name: fixture["current_update"]})

    manager.delete_config_stream(
      stream_name=fixture["stale_delete"],
      initiator_id="manager-old",
      session_id="session-old",
    )

    self.assertIn(stream_name, manager.dct_config_streams)
    self.assertEqual(manager.deleted_streams, [])
    self.assertEqual(
      manager.dct_config_streams[stream_name][ct.CONFIG_STREAM.DEEPLOY_SPECS]["lifecycle_generation"],
      2,
    )

  def test_current_generation_deeploy_delete_removes_stream(self):
    manager = _ConfigManagerHarness({"deeploy-app": _deeploy_stream()})

    manager.delete_config_stream(
      stream_name=_delete_payload(generation=2, date_updated=200.0),
      initiator_id="manager-node",
      session_id="session-1",
    )

    self.assertNotIn("deeploy-app", manager.dct_config_streams)
    self.assertEqual(manager.deleted_streams, ["deeploy-app"])

  def test_legacy_string_delete_remains_unconditional(self):
    manager = _ConfigManagerHarness({"deeploy-app": _deeploy_stream()})

    manager.delete_config_stream(
      stream_name="deeploy-app",
      initiator_id="manager-node",
      session_id="session-1",
    )

    self.assertNotIn("deeploy-app", manager.dct_config_streams)
    self.assertEqual(manager.deleted_streams, ["deeploy-app"])

  def test_stale_deeploy_update_does_not_overwrite_newer_stream(self):
    manager = _ConfigManagerHarness({"deeploy-app": _deeploy_stream()})
    update_payload = _deeploy_stream(generation=1, date_updated=100.0)
    update_payload[ct.CONFIG_STREAM.INITIATOR_ID] = "manager-node"

    manager.update_config_stream(
      delta_config_stream=update_payload,
      initiator_id="manager-node",
      session_id="session-1",
    )

    self.assertEqual(
      manager.dct_config_streams["deeploy-app"][ct.CONFIG_STREAM.DEEPLOY_SPECS]["lifecycle_generation"],
      2,
    )
    self.assertEqual(manager.saved_streams, [])
    self.assertTrue(any("stale" in log.lower() for log in manager.logs))

  def test_newer_deeploy_update_after_delete_tombstone_is_accepted_as_new_stream(self):
    manager = _ConfigManagerHarness({"deeploy-app": _deeploy_stream(generation=2)})

    manager.delete_config_stream(
      stream_name=_delete_payload(generation=2, date_updated=200.0),
      initiator_id="manager-node",
      session_id="session-1",
    )
    manager.update_config_stream(
      delta_config_stream=_deeploy_stream(generation=3, date_updated=300.0),
      initiator_id="manager-node",
      session_id="session-2",
    )

    self.assertIn("deeploy-app", manager.dct_config_streams)
    self.assertEqual(
      manager.dct_config_streams["deeploy-app"][ct.CONFIG_STREAM.DEEPLOY_SPECS]["lifecycle_generation"],
      3,
    )
    self.assertNotIn("lifecycle_generation", manager.dct_config_streams["deeploy-app"])
    self.assertEqual(
      manager.saved_streams[-1][ct.CONFIG_STREAM.DEEPLOY_SPECS]["lifecycle_generation"],
      3,
    )

  def test_cmdapi_stop_pipeline_can_send_structured_delete_payload(self):
    cmdapi = _CmdApiHarness()
    payload = _delete_payload(generation=2, date_updated=200.0)

    cmdapi.cmdapi_stop_pipeline(
      node_address="node-1",
      name="deeploy-app",
      command_content=payload,
    )

    self.assertEqual(len(cmdapi.commands_deque), 1)
    sent = cmdapi.commands_deque[0][0]
    self.assertEqual(sent[1], "node-1")
    self.assertEqual(sent[3], ct.COMMANDS.DELETE_CONFIG)
    self.assertEqual(sent[4][ct.CONFIG_STREAM.NAME], "deeploy-app")
    self.assertIn(ct.PAYLOAD_DATA.TIME, sent[4])


if __name__ == "__main__":
  unittest.main()
