import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

from naeural_core import constants as ct
from naeural_core.core_logging import Logger
from naeural_core.main.net_mon import NetworkMonitor, NetMonCt, MISSING_ID


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
    self.local_calls = []

  def register_data(self, addr, data):
    self.calls += 1

  def register_local_self_data(self, data):
    self.local_calls.append(dict(data))


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
    "main_loop_avg_time": 0.25,
    "total_cpu_cores": 16,
    "avail_cpu_cores": 12.0,
    "avg_avail_cpu_cores": 10.5,
    "cpu_used": 25.0,
    "total_mem": 64.0,
    "avail_mem": 48.0,
    "avail_mem_prc": 0.75,
    "total_disk": 1000.0,
    "avail_disk": 850.0,
    "avail_disk_prc": 0.85,
    "has_did": True,
    "tags": ["KYB", "DC:LOCAL"],
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

  def test_runtime_environment_summary_ttl_overrides_process_environment(self):
    netmon, patcher = _make_netmon(
      ttl_seconds=120,
      runtime_env={
        "EE_NETMON_SUMMARY_TTL_SECONDS": "17",
      },
    )
    self.addCleanup(patcher.stop)

    self.assertEqual(netmon.network_summary_ttl_seconds, 17)

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
    self.assertTrue(netmon.network_node_is_online_for_control("0xai_REMOTE"))
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
    self.assertEqual(netmon.network_node_main_loop("0xai_REMOTE"), 0.25)
    self.assertEqual(netmon.network_node_total_cpu_cores("0xai_REMOTE"), 16)
    self.assertEqual(netmon.network_node_avail_cpu_cores("0xai_REMOTE"), 12.0)
    self.assertEqual(netmon.network_node_get_cpu_avail_cores("0xai_REMOTE"), 10.5)
    self.assertEqual(netmon.network_node_total_mem("0xai_REMOTE"), 64.0)
    self.assertEqual(netmon.network_node_available_memory("0xai_REMOTE"), 48.0)
    self.assertEqual(netmon.network_node_available_memory("0xai_REMOTE", norm=True), 0.75)
    self.assertEqual(netmon.network_node_total_disk("0xai_REMOTE"), 1000.0)
    self.assertEqual(netmon.network_node_available_disk("0xai_REMOTE"), 850.0)
    self.assertEqual(netmon.network_node_available_disk("0xai_REMOTE", norm=True), 0.85)
    self.assertTrue(netmon.network_node_has_did("0xai_REMOTE"))
    self.assertEqual(sorted(netmon.get_network_node_tags("0xai_REMOTE")), ["DC:LOCAL", "KYB"])
    self.assertEqual(list(netmon.get_box_heartbeats("0xai_REMOTE")), [])
    self.assertEqual(netmon.network_node_last_heartbeat("0xai_REMOTE"), {})
    self.assertEqual(netmon.network_node_history("0xai_REMOTE")["timestamps"], [])
    self.assertEqual(list(netmon.network_node_today_heartbeats("0xai_REMOTE")), [])
    self.assertEqual(netmon.epoch_manager.calls, 0)

  def test_summary_does_not_populate_pipeline_cache(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)
    node = _summary_node()
    node["pipelines"] = [{"NAME": "spoofed-from-summary"}]
    node["plugins"] = [{"SIGNATURE": "spoofed-plugin"}]
    node["plugins_statuses"] = [{"INSTANCE_ID": "spoofed-status"}]
    node[ct.HB.CONFIG_STREAMS] = [{"NAME": "spoofed-config-stream"}]
    node[ct.PLUGINS] = [{"SIGNATURE": "spoofed-upper-plugin"}]
    node[NetMonCt.LAST_CONFIG] = {"spoofed": True}
    node[NetMonCt.DEEPLOY_SPECS] = {"spoofed": True}
    node[NetMonCt.INSTANCE_CONF] = {"spoofed": True}
    node[NetMonCt.INITIATOR] = "spoofed-initiator"
    node[NetMonCt.OWNER] = "spoofed-owner"
    node[NetMonCt.PIPELINE_DATA] = {"spoofed": True}

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=datetime.now(),
      full_coverage=False,
    )

    status = netmon.network_node_status("0xai_REMOTE")
    self.assertNotIn("pipelines", status)
    self.assertNotIn("plugins", status)
    self.assertNotIn("plugins_statuses", status)
    self.assertNotIn(ct.HB.CONFIG_STREAMS, status)
    self.assertNotIn(ct.PLUGINS, status)
    self.assertNotIn(NetMonCt.LAST_CONFIG, status)
    self.assertNotIn(NetMonCt.DEEPLOY_SPECS, status)
    self.assertNotIn(NetMonCt.INSTANCE_CONF, status)
    self.assertNotIn(NetMonCt.INITIATOR, status)
    self.assertNotIn(NetMonCt.OWNER, status)
    self.assertNotIn(NetMonCt.PIPELINE_DATA, status)
    self.assertEqual(netmon.network_node_pipelines("0xai_REMOTE"), [])
    self.assertEqual(netmon.network_known_pipelines(), {})
    self.assertEqual(netmon.network_known_apps(), {})

  def test_net_config_cache_is_visible_for_summary_online_nodes(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()
    pipelines = [
      {ct.CONFIG_STREAM.K_NAME: "admin_pipeline", ct.PLUGINS: []},
      {ct.CONFIG_STREAM.K_NAME: "worker-pipeline", ct.PLUGINS: []},
    ]

    netmon.register_node_pipelines("0xai_REMOTE", pipelines=pipelines)
    self.assertEqual(netmon.network_known_pipelines(), {})

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node()},
      reporter_is_authorized=True,
      received_at=received_at,
      full_coverage=False,
    )

    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=received_at))
    self.assertTrue(netmon.network_node_is_online_for_control("0xai_REMOTE", dt_now=received_at))
    self.assertEqual(
      netmon.network_known_pipelines(),
      {"0xai_REMOTE": [{ct.CONFIG_STREAM.K_NAME: "worker-pipeline", ct.PLUGINS: []}]},
    )
    self.assertIn("0xai_REMOTE", netmon.network_known_apps())

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
    self.assertEqual(netmon.network_node_main_loop("0xai_REMOTE"), 0.25)
    self.assertEqual(netmon.network_node_total_cpu_cores("0xai_REMOTE"), 16)
    self.assertEqual(netmon.network_node_available_memory("0xai_REMOTE"), 48.0)
    self.assertEqual(sorted(netmon.get_network_node_tags("0xai_REMOTE")), ["DC:LOCAL", "KYB"])
    self.assertEqual(netmon.network_node_r1fs_id("0xai_REMOTE"), "r1fs-summary")
    self.assertTrue(netmon.network_node_r1fs_online("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_r1fs_relay("0xai_REMOTE"), "relay-summary")
    self.assertEqual(netmon.network_node_comm_relay("0xai_REMOTE"), "comm-summary")
    self.assertTrue(netmon.network_node_is_secured("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_whitelist("0xai_REMOTE"), ["SELF"])
    self.assertFalse(netmon.network_node_is_supervisor("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_local_tz("0xai_REMOTE"), "UTC")
    self.assertEqual(netmon.network_node_local_tz("0xai_REMOTE", as_zone=False), "UTC+0")

  def test_fresh_direct_resources_win_over_summary_resources(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()
    heartbeat = {
      ct.EE_ID: "remote",
      ct.HB.EE_ADDR: "0xai_REMOTE",
      ct.PAYLOAD_DATA.EE_TIMESTAMP: "2026-05-28 10:00:00",
      ct.HB.DEVICE_STATUS: ct.DEVICE_STATUS_ONLINE,
      ct.HB.CURRENT_TIME: "2026-05-28 09:59:00",
      ct.HB.CPU_NR_CORES: 4,
      ct.HB.CPU_USED: 50,
      ct.HB.MACHINE_MEMORY: 16.0,
      ct.HB.AVAILABLE_MEMORY: 8.0,
      ct.HB.TOTAL_DISK: 500.0,
      ct.HB.AVAILABLE_DISK: 250.0,
      ct.HB.LOOPS_TIMINGS: {"main_loop_avg_time": 0.5},
      f"{ct.HB.PREFIX_EE_NODETAG}{ct.HB.TAG_DC}": "DIRECT",
      f"{ct.HB.PREFIX_EE_NODETAG}{ct.HB.TAG_IS_KYB}": True,
      ct.HB.DID: False,
    }
    netmon.register_heartbeat("0xai_REMOTE", heartbeat)
    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node(last_seen=2)},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    self.assertTrue(netmon.network_node_is_online("0xai_REMOTE", dt_now=received_at))
    self.assertEqual(netmon.network_node_main_loop("0xai_REMOTE"), 0.5)
    self.assertEqual(netmon.network_node_total_cpu_cores("0xai_REMOTE"), 4)
    self.assertEqual(netmon.network_node_avail_cpu_cores("0xai_REMOTE"), 2)
    self.assertEqual(netmon.network_node_total_mem("0xai_REMOTE"), 16.0)
    self.assertEqual(netmon.network_node_available_memory("0xai_REMOTE"), 8.0)
    self.assertEqual(netmon.network_node_total_disk("0xai_REMOTE"), 500.0)
    self.assertEqual(netmon.network_node_available_disk("0xai_REMOTE"), 250.0)
    self.assertFalse(netmon.network_node_has_did("0xai_REMOTE"))
    self.assertEqual(sorted(netmon.get_network_node_tags("0xai_REMOTE")), ["DC:DIRECT", "IS_KYB"])

  def test_malformed_summary_tags_do_not_fall_back_to_stale_direct_tags(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()
    heartbeat = {
      ct.EE_ID: "remote",
      ct.HB.EE_ADDR: "0xai_REMOTE",
      ct.PAYLOAD_DATA.EE_TIMESTAMP: "2026-05-28 10:00:00",
      ct.HB.DEVICE_STATUS: ct.DEVICE_STATUS_ONLINE,
      ct.HB.CURRENT_TIME: "2026-05-28 09:59:00",
      f"{ct.HB.PREFIX_EE_NODETAG}{ct.HB.TAG_DC}": "STALE",
      f"{ct.HB.PREFIX_EE_NODETAG}{ct.HB.TAG_IS_KYB}": True,
    }
    netmon.register_heartbeat("0xai_REMOTE", heartbeat)
    live_history = netmon.get_box_heartbeats("0xai_REMOTE", return_copy=False)
    live_history[-1][ct.HB.RECEIVED_TIME] = (received_at - timedelta(minutes=10)).strftime(ct.HB.TIMESTAMP_FORMAT)
    node = _summary_node(last_seen=2)
    node["tags"] = "KYB"

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    self.assertEqual(netmon.network_node_status("0xai_REMOTE", dt_now=received_at)["tags"], [])
    self.assertEqual(netmon.get_network_node_tags("0xai_REMOTE"), [])

  def test_summary_missing_display_fields_do_not_fall_back_to_stale_direct_values(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()
    heartbeat = {
      ct.EE_ID: "stale-remote",
      ct.HB.EE_ADDR: "0xai_REMOTE",
      ct.PAYLOAD_DATA.EE_TIMESTAMP: "2026-05-28 10:00:00",
      ct.HB.DEVICE_STATUS: ct.DEVICE_STATUS_ONLINE,
      ct.HB.CURRENT_TIME: "2026-05-28 09:59:00",
      ct.HB.VERSION: "stale-version",
      ct.HB.PY_VER: "stale-py",
      ct.HB.GIT_BRANCH: "stale-branch",
      ct.PAYLOAD_DATA.EE_TZ: "stale-zone",
      ct.PAYLOAD_DATA.EE_TIMEZONE: "stale-utc",
      ct.HB.R1FS_ID: "stale-r1fs",
      ct.HB.R1FS_ONLINE: True,
      ct.HB.R1FS_RELAY: "stale-r1fs-relay",
      ct.HB.COMM_RELAY: "stale-comm-relay",
      ct.HB.EE_WHITELIST: ["SELF"],
      ct.HB.SECURED: True,
    }
    netmon.register_heartbeat("0xai_REMOTE", heartbeat)
    live_history = netmon.get_box_heartbeats("0xai_REMOTE", return_copy=False)
    live_history[-1][ct.HB.RECEIVED_TIME] = (received_at - timedelta(minutes=10)).strftime(ct.HB.TIMESTAMP_FORMAT)
    node = _summary_node(last_seen=2)
    for key in (
      ct.PAYLOAD_DATA.NETMON_EEID,
      ct.PAYLOAD_DATA.NETMON_NODE_VERSION,
      "py_ver",
      "last_remote_time",
      "deployment",
      "node_tz",
      "node_utc",
      ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ID,
      ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ONLINE,
      ct.PAYLOAD_DATA.NETMON_NODE_R1FS_RELAY,
      ct.PAYLOAD_DATA.NETMON_NODE_COMM_RELAY,
      ct.PAYLOAD_DATA.NETMON_WHITELIST,
      ct.PAYLOAD_DATA.NETMON_NODE_SECURED,
      ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR,
    ):
      node.pop(key)

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    status = netmon.network_node_status("0xai_REMOTE", dt_now=received_at)
    self.assertEqual(status["netmon_data_source"], "summary")
    self.assertEqual(status["eeid"], MISSING_ID)
    self.assertIsNone(status["version"])
    self.assertIsNone(status["py_ver"])
    self.assertIsNone(status["last_remote_time"])
    self.assertIsNone(status["deployment"])
    self.assertIsNone(status["node_tz"])
    self.assertIsNone(status["node_utc"])
    self.assertIsNone(status[ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ID])
    self.assertIsNone(status[ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ONLINE])
    self.assertIsNone(status[ct.PAYLOAD_DATA.NETMON_NODE_R1FS_RELAY])
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_NODE_COMM_RELAY], "")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_WHITELIST], [])
    self.assertFalse(status[ct.PAYLOAD_DATA.NETMON_NODE_SECURED])
    self.assertEqual(netmon.network_node_eeid("0xai_REMOTE"), MISSING_ID)
    self.assertIsNone(netmon.network_node_version("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_py_ver("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_remote_time("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_deploy_type("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_local_tz("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_local_tz("0xai_REMOTE", as_zone=False))
    self.assertIsNone(netmon.network_node_r1fs_id("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_r1fs_online("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_r1fs_relay("0xai_REMOTE"))
    self.assertEqual(netmon.network_node_comm_relay("0xai_REMOTE"), "")
    self.assertEqual(netmon.network_node_whitelist("0xai_REMOTE"), [])
    self.assertFalse(netmon.network_node_is_secured("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_is_supervisor("0xai_REMOTE"))

  def test_selected_summary_missing_or_malformed_supervisor_fails_closed_unknown(self):
    bad_supervisor_values = (None, "", "maybe", object())
    for bad_supervisor in bad_supervisor_values:
      with self.subTest(bad_supervisor=type(bad_supervisor).__name__):
        netmon, patcher = _make_netmon(summary_enabled=True)
        self.addCleanup(patcher.stop)
        node = _summary_node()
        if bad_supervisor is None:
          node.pop(ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR)
        else:
          node[ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR] = bad_supervisor

        netmon.register_network_status_snapshot(
          reporter_addr="0xai_ORACLE",
          current_network={"node": node},
          reporter_is_authorized=True,
          received_at=datetime.now(),
        )

        status = netmon.network_node_status("0xai_REMOTE")
        self.assertIsNone(status[ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR])
        self.assertIsNone(netmon.network_node_is_supervisor("0xai_REMOTE"))

  def test_selected_summary_missing_resources_fail_closed_without_direct_fallback(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)
    node = _summary_node()
    for key in [
      "main_loop_avg_time",
      "total_cpu_cores",
      "avail_cpu_cores",
      "avg_avail_cpu_cores",
      "cpu_used",
      "total_mem",
      "avail_mem",
      "avail_mem_prc",
      "total_disk",
      "avail_disk",
      "avail_disk_prc",
      "has_did",
      "tags",
    ]:
      node.pop(key)

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=datetime.now(),
    )

    self.assertEqual(netmon.network_node_main_loop("0xai_REMOTE"), 1e10)
    self.assertIsNone(netmon.network_node_total_cpu_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_avail_cpu_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_get_cpu_avail_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_total_mem("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_memory("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_memory("0xai_REMOTE", norm=True))
    self.assertIsNone(netmon.network_node_total_disk("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_disk("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_disk("0xai_REMOTE", norm=True))
    self.assertFalse(netmon.network_node_is_ok_cpu_used("0xai_REMOTE"))
    self.assertFalse(netmon.network_node_has_did("0xai_REMOTE"))
    self.assertEqual(netmon.get_network_node_tags("0xai_REMOTE"), [])

  def test_selected_summary_malformed_resources_fail_closed(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)
    node = _summary_node()
    node.update({
      "main_loop_avg_time": "bad-loop",
      "total_cpu_cores": "bad-cpu",
      "avail_cpu_cores": float("inf"),
      "avg_avail_cpu_cores": "bad-average",
      "cpu_used": "bad-used",
      "total_mem": "bad-mem",
      "avail_mem": "bad-avail-mem",
      "avail_mem_prc": "bad-avail-mem-prc",
      "total_disk": "bad-disk",
      "avail_disk": "bad-avail-disk",
      "avail_disk_prc": "bad-avail-disk-prc",
      "has_did": "false",
      "tags": "KYB",
      ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR: "true",
      ct.PAYLOAD_DATA.NETMON_NODE_SECURED: "no",
      ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ONLINE: "false",
    })

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=datetime.now(),
    )

    self.assertEqual(netmon.network_node_main_loop("0xai_REMOTE"), 1e10)
    self.assertIsNone(netmon.network_node_total_cpu_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_avail_cpu_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_get_cpu_avail_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_total_mem("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_memory("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_memory("0xai_REMOTE", norm=True))
    self.assertIsNone(netmon.network_node_total_disk("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_disk("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_disk("0xai_REMOTE", norm=True))
    self.assertFalse(netmon.network_node_is_ok_cpu_used("0xai_REMOTE"))
    self.assertFalse(netmon.network_node_has_did("0xai_REMOTE"))
    self.assertEqual(netmon.get_network_node_tags("0xai_REMOTE"), [])
    self.assertTrue(netmon.network_node_is_supervisor("0xai_REMOTE"))
    self.assertFalse(netmon.network_node_is_secured("0xai_REMOTE"))
    status = netmon.network_node_status("0xai_REMOTE")
    self.assertFalse(status["has_did"])
    self.assertEqual(status["tags"], [])
    self.assertTrue(status[ct.PAYLOAD_DATA.NETMON_IS_SUPERVISOR])
    self.assertFalse(status[ct.PAYLOAD_DATA.NETMON_NODE_SECURED])
    self.assertFalse(status[ct.PAYLOAD_DATA.NETMON_NODE_R1FS_ONLINE])
    self.assertFalse(netmon.network_node_r1fs_online("0xai_REMOTE"))
    self.assertEqual(status["main_loop_avg_time"], 1e10)
    for key in (
      "total_cpu_cores",
      "avail_cpu_cores",
      "avg_avail_cpu_cores",
      "cpu_used",
      "total_mem",
      "avail_mem",
      "avail_mem_prc",
      "total_disk",
      "avail_disk",
      "avail_disk_prc",
    ):
      self.assertIsNone(status[key])

  def test_selected_summary_negative_resources_fail_closed(self):
    netmon, patcher = _make_netmon(summary_enabled=True)
    self.addCleanup(patcher.stop)
    node = _summary_node()
    node.update({
      "main_loop_avg_time": -0.1,
      "total_cpu_cores": -16,
      "avail_cpu_cores": -12.0,
      "avg_avail_cpu_cores": -10.5,
      "cpu_used": -1,
      "total_mem": -64.0,
      "avail_mem": -48.0,
      "avail_mem_prc": -0.75,
      "total_disk": -1000.0,
      "avail_disk": -850.0,
      "avail_disk_prc": -0.85,
    })

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": node},
      reporter_is_authorized=True,
      received_at=datetime.now(),
    )

    self.assertEqual(netmon.network_node_main_loop("0xai_REMOTE"), 1e10)
    self.assertIsNone(netmon.network_node_total_cpu_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_avail_cpu_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_get_cpu_avail_cores("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_total_mem("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_memory("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_memory("0xai_REMOTE", norm=True))
    self.assertIsNone(netmon.network_node_total_disk("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_disk("0xai_REMOTE"))
    self.assertIsNone(netmon.network_node_available_disk("0xai_REMOTE", norm=True))
    self.assertFalse(netmon.network_node_is_ok_cpu_used("0xai_REMOTE"))
    status = netmon.network_node_status("0xai_REMOTE")
    self.assertEqual(status["main_loop_avg_time"], 1e10)
    for key in (
      "total_cpu_cores",
      "avail_cpu_cores",
      "avg_avail_cpu_cores",
      "cpu_used",
      "total_mem",
      "avail_mem",
      "avail_mem_prc",
      "total_disk",
      "avail_disk",
      "avail_disk_prc",
    ):
      self.assertIsNone(status[key])

  def test_malformed_summary_working_status_fails_closed(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    received_at = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node(working=float("nan"), last_seen=1)},
      reporter_is_authorized=True,
      received_at=received_at,
    )

    status = netmon.network_node_status("0xai_REMOTE", dt_now=received_at)
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], "LOST STATUS")
    self.assertEqual(netmon.network_node_simple_status("0xai_REMOTE", dt_now=received_at), "LOST STATUS")
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=received_at, allow_summary=True))

  def test_summary_last_seen_with_non_numeric_node_age_fails_closed(self):
    for bad_last_seen in ("not-a-number", "nan", float("nan"), "inf", float("inf")):
      with self.subTest(bad_last_seen=bad_last_seen):
        netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
        self.addCleanup(patcher.stop)
        received_at = datetime.now()

        netmon.register_network_status_snapshot(
          reporter_addr="0xai_ORACLE",
          current_network={"node": _summary_node(last_seen=bad_last_seen)},
          reporter_is_authorized=True,
          received_at=received_at,
        )

        dt_now = received_at + timedelta(seconds=20)
        status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

        self.assertEqual(status["netmon_data_source"], "summary")
        self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], "LOST STATUS")
        self.assertGreater(netmon.network_node_last_seen("0xai_REMOTE", dt_now=dt_now), 60)
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_malformed_newer_summary_does_not_mask_well_formed_summary(self):
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
      current_network={"node": _summary_node(working=ct.DEVICE_STATUS_ONLINE, last_seen=float("nan"))},
      reporter_is_authorized=True,
      received_at=received_at + timedelta(seconds=5),
    )

    dt_now = received_at + timedelta(seconds=6)
    status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

    self.assertEqual(status["netmon_reporter"], "ORACLE_1")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], ct.DEVICE_STATUS_ONLINE)
    self.assertTrue(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_newer_almost_stale_summary_does_not_mask_fresher_effective_summary(self):
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
      current_network={"node": _summary_node(working=ct.DEVICE_STATUS_ONLINE, last_seen=59)},
      reporter_is_authorized=True,
      received_at=received_at + timedelta(seconds=5),
    )

    dt_now = received_at + timedelta(seconds=6)
    status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

    self.assertEqual(status["netmon_reporter"], "ORACLE_1")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_LAST_SEEN], 7)
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], ct.DEVICE_STATUS_ONLINE)
    self.assertTrue(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_future_reporter_timestamp_is_not_accepted_as_fresh_summary(self):
    netmon, patcher = _make_netmon(summary_enabled=True, ttl_seconds=300)
    self.addCleanup(patcher.stop)
    dt_now = datetime.now()

    netmon.register_network_status_snapshot(
      reporter_addr="0xai_ORACLE",
      current_network={"node": _summary_node(working=ct.DEVICE_STATUS_ONLINE, last_seen=90)},
      reporter_is_authorized=True,
      received_at=dt_now + timedelta(seconds=45),
    )

    self.assertEqual(netmon.network_node_simple_status("0xai_REMOTE", dt_now=dt_now), "LOST STATUS")
    self.assertFalse(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

  def test_malformed_working_summary_does_not_mask_well_formed_summary(self):
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
      current_network={"node": _summary_node(working=float("nan"), last_seen=1)},
      reporter_is_authorized=True,
      received_at=received_at + timedelta(seconds=5),
    )

    dt_now = received_at + timedelta(seconds=6)
    status = netmon.network_node_status("0xai_REMOTE", dt_now=dt_now)

    self.assertEqual(status["netmon_reporter"], "ORACLE_1")
    self.assertEqual(status[ct.PAYLOAD_DATA.NETMON_STATUS_KEY], ct.DEVICE_STATUS_ONLINE)
    self.assertTrue(netmon.network_node_is_online("0xai_REMOTE", dt_now=dt_now, allow_summary=True))

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

  def test_local_self_heartbeat_registers_owner_epoch_from_production_payload(self):
    netmon, patcher = _make_netmon(
      summary_enabled=True,
      extra_env={"EE_SUPERVISOR": "false"},
      clear_env=True,
    )
    self.addCleanup(patcher.stop)
    heartbeat = {
      ct.EE_ID: "SELF",
      ct.HB.EE_ADDR: "0xai_SELF",
      ct.HB.CURRENT_TIME: "2026-05-28 10:00:00",
    }

    netmon.register_local_heartbeat("SELF", heartbeat)

    self.assertIn("SELF", netmon.all_nodes)
    self.assertEqual(netmon.network_node_eeid("0xai_SELF"), "SELF")
    self.assertEqual(netmon.epoch_manager.calls, 0)
    self.assertEqual(len(netmon.epoch_manager.local_calls), 1)
    epoch_heartbeat = netmon.epoch_manager.local_calls[0]
    self.assertEqual(
      epoch_heartbeat[ct.PAYLOAD_DATA.EE_TIMESTAMP],
      heartbeat[ct.HB.CURRENT_TIME],
    )
    self.assertEqual(epoch_heartbeat[ct.PAYLOAD_DATA.EE_TIMEZONE], netmon.log.utc_offset)
    self.assertIn(ct.HB.RECEIVED_TIME, netmon.network_node_last_heartbeat("0xai_SELF"))

  def test_local_self_heartbeat_rejects_non_owner_addresses_before_mutation(self):
    invalid_heartbeats = (
      ("0xai_REMOTE", {ct.HB.EE_ADDR: "0xai_SELF"}),
      ("0xai_SELF", {ct.HB.EE_ADDR: "0xai_REMOTE"}),
      (None, {ct.HB.EE_ADDR: "0xai_SELF"}),
    )
    for addr, heartbeat_fields in invalid_heartbeats:
      with self.subTest(addr=addr, embedded_addr=heartbeat_fields[ct.HB.EE_ADDR]):
        netmon, patcher = _make_netmon(
          summary_enabled=True,
          extra_env={"EE_SUPERVISOR": "false"},
          clear_env=True,
        )
        self.addCleanup(patcher.stop)
        heartbeat = {
          ct.EE_ID: "SELF",
          ct.HB.CURRENT_TIME: "2026-05-28 10:00:00",
          **heartbeat_fields,
        }

        netmon.register_local_heartbeat(addr, heartbeat)

        self.assertEqual(netmon.all_nodes, [])
        self.assertEqual(netmon.epoch_manager.calls, 0)
        self.assertEqual(netmon.epoch_manager.local_calls, [])

  def test_local_self_heartbeat_duplicate_only_updates_epoch_once(self):
    netmon, patcher = _make_netmon(
      summary_enabled=True,
      extra_env={"EE_SUPERVISOR": "false"},
      clear_env=True,
    )
    self.addCleanup(patcher.stop)
    heartbeat = {
      ct.EE_ID: "SELF",
      ct.HB.EE_ADDR: "0xai_SELF",
      ct.HB.CURRENT_TIME: "2026-05-28 10:00:00",
    }

    netmon.register_local_heartbeat("0xai_SELF", heartbeat)
    netmon.register_local_heartbeat("0xai_SELF", heartbeat)

    self.assertEqual(len(netmon.get_box_heartbeats("0xai_SELF")), 1)
    self.assertEqual(len(netmon.epoch_manager.local_calls), 1)

  def test_supervisor_local_registration_is_noop_before_broker_echo(self):
    netmon, patcher = _make_netmon(
      summary_enabled=False,
      extra_env={"EE_SUPERVISOR": "true"},
      clear_env=True,
    )
    self.addCleanup(patcher.stop)
    local_heartbeat = {
      ct.EE_ID: "SELF",
      ct.HB.EE_ADDR: "0xai_SELF",
      ct.HB.CURRENT_TIME: "2026-05-28 10:00:00",
    }

    netmon.register_local_heartbeat("0xai_SELF", local_heartbeat)

    self.assertEqual(netmon.all_nodes, [])
    self.assertEqual(netmon.epoch_manager.local_calls, [])

    broker_heartbeat = {
      **local_heartbeat,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: local_heartbeat[ct.HB.CURRENT_TIME],
      ct.PAYLOAD_DATA.EE_TIMEZONE: "UTC+0",
    }
    netmon.register_heartbeat("0xai_SELF", broker_heartbeat)

    self.assertEqual(len(netmon.get_box_heartbeats("0xai_SELF")), 1)
    self.assertEqual(netmon.epoch_manager.calls, 1)


if __name__ == "__main__":
  unittest.main()
