import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

from naeural_core import constants as ct
from naeural_core.core_logging import Logger
from naeural_core.main.net_mon import NetworkMonitor


class _StubBlockEngine:
  def maybe_remove_prefix(self, addr):
    return addr.replace("0xai_", "", 1)

  def maybe_remove_addr_prefix(self, addr):
    return self.maybe_remove_prefix(addr)

  def _add_prefix(self, addr):
    return addr if addr.startswith("0xai_") else "0xai_" + addr

  def node_address_to_eth_address(self, addr):
    return "eth_" + self.maybe_remove_prefix(addr)


class _CountingEpochManager:
  def __init__(self):
    self.calls = 0

  def register_data(self, addr, data):
    self.calls += 1


def _make_netmon(
  summary_enabled=True,
  ttl_seconds=120,
  extra_env=None,
  clear_env=False,
  runtime_env=None,
):
  env = {
    "EE_NETMON_SUMMARY_TTL_SECONDS": str(ttl_seconds),
  }
  if summary_enabled is not None:
    env["EE_NETMON_USE_SUMMARY_STATUS"] = "1" if summary_enabled else "0"
  if extra_env:
    env.update(extra_env)
  patcher = mock.patch.dict(os.environ, env, clear=clear_env)
  patcher.start()
  log = Logger(
    lib_name="TEST_NMON_SUMMARY",
    base_folder=".",
    app_folder="_local_cache",
    no_folders_no_save=True,
    DEBUG=False,
  )
  netmon = NetworkMonitor(
    log=log,
    node_name="SELF",
    node_addr="0xai_SELF",
    epoch_manager=_CountingEpochManager(),
    blockchain_manager=_StubBlockEngine(),
    environment_variables=runtime_env,
  )
  return netmon, patcher


def _summary_node(addr="0xai_REMOTE", eeid="remote", working=ct.DEVICE_STATUS_ONLINE, last_seen=7.5):
  return {
    ct.PAYLOAD_DATA.NETMON_ADDRESS: addr,
    ct.PAYLOAD_DATA.NETMON_EEID: eeid,
    ct.PAYLOAD_DATA.NETMON_STATUS_KEY: working,
    ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR: False,
    ct.PAYLOAD_DATA.NETMON_LAST_SEEN: last_seen,
    ct.PAYLOAD_DATA.NETMON_NODE_VERSION: "v-summary",
    ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ID: "r1fs-summary",
    ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ONLINE: True,
    ct.PAYLOAD_DATA.NETMON_NODE_R1FS_RELAY: "relay-summary",
    ct.PAYLOAD_DATA.NETMON_NODE_COMM_RELAY: "comm-summary",
    ct.PAYLOAD_DATA.NETMON_NODE_SECURED: True,
    ct.PAYLOAD_DATA.NETMON_WHITELIST: ["SELF"],
    "py_ver": "3.11-summary",
    "last_remote_time": "2026-05-29 10:00:00",
    "deployment": "summary-branch",
    "node_tz": "UTC",
    "node_utc": "UTC+0",
    "SCORE": 88,
    "trusted": True,
    "trust": 0.9,
  }


class TestNetmonSummaryStatus(unittest.TestCase):

  def test_policy_mode_derives_summary_status_for_non_supervisor(self):
    netmon, patcher = _make_netmon(
      summary_enabled=None,
      extra_env={
        "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
        "EE_SUPERVISOR": "false",
      },
      clear_env=True,
    )
    self.addCleanup(patcher.stop)

    self.assertTrue(netmon.network_summary_status_enabled)

  def test_policy_mode_does_not_derive_summary_status_for_supervisor(self):
    netmon, patcher = _make_netmon(
      summary_enabled=None,
      extra_env={
        "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
        "EE_SUPERVISOR": "true",
      },
      clear_env=True,
    )
    self.addCleanup(patcher.stop)

    self.assertFalse(netmon.network_summary_status_enabled)

  def test_explicit_summary_flag_overrides_policy_mode(self):
    netmon, patcher = _make_netmon(
      summary_enabled=False,
      extra_env={
        "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
        "EE_SUPERVISOR": "false",
      },
      clear_env=True,
    )
    self.addCleanup(patcher.stop)

    self.assertFalse(netmon.network_summary_status_enabled)

  def test_explicit_summary_flag_can_enable_supervisor_debug_mode(self):
    netmon, patcher = _make_netmon(
      summary_enabled=True,
      extra_env={
        "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
        "EE_SUPERVISOR": "true",
      },
      clear_env=True,
    )
    self.addCleanup(patcher.stop)

    self.assertTrue(netmon.network_summary_status_enabled)

  def test_policy_mode_derives_summary_status_from_runtime_environment(self):
    netmon, patcher = _make_netmon(
      summary_enabled=None,
      clear_env=True,
      runtime_env={
        "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
        "IS_SUPERVISOR_NODE": False,
      },
    )
    self.addCleanup(patcher.stop)

    self.assertTrue(netmon.network_summary_status_enabled)

  def test_runtime_environment_summary_override_wins_over_policy_mode(self):
    netmon, patcher = _make_netmon(
      summary_enabled=None,
      clear_env=True,
      runtime_env={
        "EE_NETMON_ORACLE_ONLY_HEARTBEAT_MODE": "1",
        "EE_NETMON_USE_SUMMARY_STATUS": "0",
        "IS_SUPERVISOR_NODE": False,
      },
    )
    self.addCleanup(patcher.stop)

    self.assertFalse(netmon.network_summary_status_enabled)

  def test_authorized_summary_populates_status_without_heartbeat_history(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"spoofable-eeid-key": _summary_node()},
      reporter_is_authorized=True,
      received_at=datetime.now(),
      full_coverage=False,
    )

    self.assertIn("REMOTE", netmon.all_nodes)
    self.assertNotIn("spoofable-eeid-key", netmon.all_nodes)
    self.assertTrue(netmon.network_node_info_available("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_simple_status("0xai_REMOTE"), ct.DEVICE_STATUS_ONLINE)
    self.assertTrue(netmon.network_node_is_online("0xai_REMOTE", allow_summary=True))
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE"))
    self.assertFalse(netmon.network_node_is_available("0xai_REMOTE"))
    self.assertFalse(netmon.network_node_is_accessible("0xai_REMOTE"))
    self.assertNotIn("REMOTE", netmon.available_nodes)
    self.assertNotIn("REMOTE", netmon.accessible_nodes)
    status = netmon.network_node_status("0xai_REMOTE")
    self.assertEqual(status["netmon_data_source"], "summary")
    self.assertEqual(status["netmon_reporter"], "ORACLE")
    self.assertFalse(status["trusted"])
    self.assertEqual(status["SCORE"], 0)
    self.assertEqual(netmon.network_node_addr("remote"), "REMOTE")
    self.assertEqual(netmon.network_node_version("0xai_REMOTE"), "v-summary")
    self.assertEqual(netmon.network_node_py_ver("0xai_REMOTE"), "3.11-summary")
    self.assertEqual(netmon.network_node_remote_time("0xai_REMOTE"), "2026-05-29 10:00:00")
    self.assertEqual(netmon.network_node_deploy_type("0xai_REMOTE"), "summary-branch")
    self.assertEqual(netmon.network_node_local_tz("0xai_REMOTE"), "UTC")
    self.assertEqual(netmon.network_node_local_tz("0xai_REMOTE", as_zone=False), "UTC+0")
    self.assertEqual(netmon.network_node_r1fs_id("0xai_REMOTE"), "r1fs-summary")
    self.assertTrue(netmon.network_node_r1fs_online("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_r1fs_relay("0xai_REMOTE"), "relay-summary")
    self.assertEqual(netmon.network_node_comm_relay("0xai_REMOTE"), "comm-summary")
    self.assertTrue(netmon.network_node_is_secured("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_whitelist("0xai_REMOTE"), ["SELF"])
    self.assertEqual(list(netmon.get_box_heartbeats("0xai_REMOTE")), [])
    self.assertEqual(netmon.network_node_last_heartbeat("0xai_REMOTE"), {})
    self.assertEqual(netmon.network_node_history("0xai_REMOTE")["timestamps"], [])
    self.assertEqual(list(netmon.network_node_today_heartbeats("0xai_REMOTE")), [])
    self.assertEqual(netmon.epoch_manager.calls, 0)

  def test_summary_address_prefix_is_canonicalized(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)

    netmon.register_network_status_snapshot(
      reporter_addr="0XAI_ORACLE",
      current_network={"node": _summary_node(addr="0XAI_REMOTE")},
      reporter_is_authorized=True,
      received_at=datetime.now(),
      full_coverage=False,
    )

    self.assertIn("REMOTE", netmon.all_nodes)
    self.assertNotIn("0XAI_REMOTE", netmon.all_nodes)
    self.assertTrue(netmon.network_node_info_available("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_status("0xai_REMOTE")["address"], "0xai_REMOTE")
    self.assertEqual(netmon.network_node_status("0xai_REMOTE")["netmon_reporter"], "ORACLE")

  def test_malformed_summary_addresses_are_ignored(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)

    self.assertEqual(netmon.register_network_status_snapshot(
      reporter_addr=object(),
      current_network={"node": _summary_node()},
      reporter_is_authorized=True,
      received_at=datetime.now(),
    ), 0)

    registered = netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={
        "bad-int": _summary_node(addr=123),
        "bad-object": _summary_node(addr=object()),
        "good": _summary_node(addr="0xai_REMOTE"),
      },
      reporter_is_authorized=True,
      received_at=datetime.now(),
    )

    self.assertEqual(registered, 1)
    self.assertIn("REMOTE", netmon.all_nodes)
    self.assertNotIn(123, netmon.all_nodes)

  def test_today_heartbeats_skips_compact_entries_without_remote_timestamp(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)

    netmon.register_local_heartbeat(
      "0xai_LOCAL",
      {
        ct.EE_ID: "local",
        ct.HB.CURRENT_TIME: "2026-05-29 10:00:00",
      },
    )

    self.assertEqual(list(netmon.network_node_today_heartbeats("0xai_LOCAL")), [])

  def test_unauthorized_summary_is_ignored(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_NOT_ORACLE",
      current_network={"node": _summary_node()},
      reporter_is_authorized=False,
      received_at=datetime.now(),
    )

    self.assertNotIn("REMOTE", netmon.all_nodes)
    self.assertFalse(netmon.network_node_info_available("0xai_REMOTE"))

  def test_summary_flag_disabled_keeps_snapshot_invisible(self):
    netmon, patcher = _make_netmon(summary_enabled=False)
    self.addCleanup(patcher.stop)

    registered = netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node()},
      reporter_is_authorized=True,
      received_at=datetime.now(),
    )

    self.assertEqual(registered, 0)
    self.assertNotIn("REMOTE", netmon.all_nodes)
    self.assertFalse(netmon.network_node_info_available("0xai_REMOTE"))

  def test_partial_summary_does_not_mark_omitted_nodes_offline(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=120)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"a": _summary_node(addr="0xai_A", eeid="a")},
      reporter_is_authorized=True,
      received_at=received_at,
      full_coverage=False,
    )
    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"b": _summary_node(addr="0xai_B", eeid="b")},
      reporter_is_authorized=True,
      received_at=received_at + timedelta(seconds=30),
      full_coverage=False,
    )

    self.assertTrue(netmon.network_node_is_online(
      "0xai_A", dt_now=received_at + timedelta(seconds=40), allow_summary=True,
    ))
    self.assertTrue(netmon.network_node_is_online(
      "0xai_B", dt_now=received_at + timedelta(seconds=40), allow_summary=True,
    ))

  def test_summary_expires_by_ttl(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=20)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node()},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    self.assertTrue(netmon.network_node_is_online(
      "0xai_REMOTE", dt_now=received_at + timedelta(seconds=10), allow_summary=True,
    ))
    self.assertFalse(netmon.network_node_is_online(
      "0xai_REMOTE", dt_now=received_at + timedelta(seconds=30), allow_summary=True,
    ))

  def test_summary_status_ages_last_seen_before_ttl_expiry(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node(last_seen=50)},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    dt_now = received_at + timedelta(seconds=20)
    status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_LAST_SEEN], 70)
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], "LOST STATUS")
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_fresh_summary_status_overrides_stale_direct_status_only(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()
    heartbeat = {
      ct.EE_ID: "remote",
      ct.HB.EE_ADDR: "0xai_REMOTE",
      ct.PAYLOAD_DATA.EE_TIMESTAMP: "2026-05-28 10:00:00",
      ct.HB.DEVICE_STATUS: "LOST STATUS",
      ct.HB.VERSION: "v-direct-old",
      ct.HB.PY_VER: "3.10-direct-old",
      ct.HB.CURRENT_TIME: "2026-05-28 09:59:00",
      ct.HB.GIT_BRANCH: "direct-old-branch",
      ct.HB.R1FS_ID: "r1fs-direct-old",
      ct.HB.R1FS_ONLINE: False,
      ct.HB.R1FS_RELAY: "relay-direct-old",
      ct.HB.COMM_RELAY: "comm-direct-old",
      ct.HB.SECURED: False,
      ct.HB.EE_WHITELIST: ["OTHER"],
      ct.HB.EE_IS_SUPER: True,
      ct.PAYLOAD_DATA.EE_TZ: "Europe/Bucharest",
      ct.PAYLOAD_DATA.EE_TIMEZONE: "UTC+2",
    }
    netmon.register_heartbeat("0xai_REMOTE", heartbeat)
    live_history = netmon.get_box_heartbeats("0xai_REMOTE", return_copy=False)
    live_history[-1][ct.HB.RECEIVED_TIME] = (received_at - timedelta(minutes=10)).strftime(ct.HB.TIMESTAMP_FORMAT)
    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node(working=ct.DEVICE_STATUS_ONLINE, last_seen=2)},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    status = netmon.network_node_status("0xai_REMOTE", dt_now=received_at)

    self.assertEqual(status["netmon_data_source"], "summary")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], ct.DEVICE_STATUS_ONLINE)
    self.assertEqual(len(netmon.get_box_heartbeats("0xai_REMOTE")), 1)
    self.assertEqual(netmon.network_node_last_seen("0xai_REMOTE", dt_now=received_at), 2)
    self.assertTrue(netmon.network_node_is_recent("0xai_REMOTE", dt_now=received_at, max_recent_minutes=1))
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=received_at))
    self.assertFalse(netmon.network_node_is_accessible("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_eeid("0xai_REMOTE"), "remote")
    self.assertEqual(netmon.network_node_version("0xai_REMOTE"), "v-summary")
    self.assertEqual(netmon.network_node_py_ver("0xai_REMOTE"), "3.11-summary")
    self.assertEqual(netmon.network_node_remote_time("0xai_REMOTE"), "2026-05-29 10:00:00")
    self.assertEqual(netmon.network_node_deploy_type("0xai_REMOTE"), "summary-branch")
    self.assertEqual(netmon.network_node_r1fs_id("0xai_REMOTE"), "r1fs-summary")
    self.assertTrue(netmon.network_node_r1fs_online("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_r1fs_relay("0xai_REMOTE"), "relay-summary")
    self.assertEqual(netmon.network_node_comm_relay("0xai_REMOTE"), "comm-summary")
    self.assertTrue(netmon.network_node_is_secured("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_whitelist("0xai_REMOTE"), ["SELF"])
    self.assertFalse(netmon.network_node_is_supervisor("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_local_tz("0xai_REMOTE"), "UTC")
    self.assertEqual(netmon.network_node_local_tz("0xai_REMOTE", as_zone=False), "UTC+0")

  def test_summary_last_seen_with_non_numeric_node_age_fails_closed(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node(last_seen="not-a-number")},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    dt_now = received_at + timedelta(seconds=20)
    status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

    self.assertEqual(status["netmon_data_source"], "summary")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], "LOST STATUS")
    self.assertGreater(netmon.network_node_last_seen("0xai_REMOTE", dt_now=dt_now), 60)
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_summary_missing_last_seen_fails_closed(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()
    node = _summary_node()
    node.pop(ct.PAYLOAD_DATA.NETMON_LAST_SEEN)

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    dt_now = received_at + timedelta(seconds=20)
    status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

    self.assertEqual(status["netmon_data_source"], "summary")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], "LOST STATUS")
    self.assertGreater(netmon.network_node_last_seen("0xai_REMOTE", dt_now=dt_now), 60)
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_newest_authorized_summary_wins_without_trust_evidence(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE_1",
      current_network={"node": _summary_node(working=ct.DEVICE_STATUS_ONLINE, last_seen=1)},
      reporter_is_authorized=True,
      received_at=received_at,
    )
    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE_2",
      current_network={"node": _summary_node(working="LOST STATUS", last_seen=2)},
      reporter_is_authorized=True,
      received_at=received_at + timedelta(seconds=5),
    )

    status = netmon.network_node_status("0xai_REMOTE", dt_now=received_at + timedelta(seconds=6))

    self.assertEqual(status["netmon_reporter"], "ORACLE_2")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], "LOST STATUS")
    self.assertFalse(status["trusted"])
    self.assertEqual(status["SCORE"], 0)

  def test_direct_heartbeat_still_registers_epoch_when_summary_mode_disabled(self):
    netmon, patcher = _make_netmon(summary_enabled=False)
    self.addCleanup(patcher.stop)
    heartbeat = {
      ct.EE_ID: "remote",
      ct.HB.EE_ADDR: "0xai_REMOTE",
      ct.PAYLOAD_DATA.EE_TIMESTAMP: "2026-05-28 10:00:00",
    }

    netmon.register_heartbeat("0xai_REMOTE", heartbeat)

    self.assertIn("REMOTE", netmon.all_nodes)
    self.assertEqual(netmon.network_node_eeid("0xai_REMOTE"), "remote")
    self.assertEqual(netmon.epoch_manager.calls, 1)

  def test_local_self_heartbeat_bypasses_epoch_but_fuels_self_api(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)
    heartbeat = {
      ct.EE_ID: "SELF",
      ct.HB.EE_ADDR: "0xai_SELF",
      ct.PAYLOAD_DATA.EE_TIMESTAMP: "2026-05-28 10:00:00",
    }

    netmon.register_local_heartbeat("0xai_SELF", heartbeat)

    self.assertIn("SELF", netmon.all_nodes)
    self.assertEqual(netmon.network_node_eeid("0xai_SELF"), "SELF")
    self.assertEqual(netmon.epoch_manager.calls, 0)
    self.assertIn(ct.HB.RECEIVED_TIME, netmon.network_node_last_heartbeat("0xai_SELF"))


if __name__ == "__main__":
  unittest.main()
