import os
import random
import time
import unittest
from datetime import datetime, timedelta, timezone
from threading import Lock

from naeural_core import constants as ct
from naeural_core.core_logging import Logger
from naeural_core.main.epochs_manager import EPCT, EpochsManager
from naeural_core.main.net_mon import NetworkMonitor

N_NODES = 1000
N_HEARTBEATS = 24 * 60 * 6
STRESS_TEST_ENV = "EE_EPOCHS_STRESS_TEST"
DEFAULT_STRESS_TEST = "1"  # change to "0" in order to disable this by default
DEFAULT_SHOW_TEST_LOGS = "0"  # enable by setting this to "1"


class _DummyLock:
  def __init__(self, lock):
    self._lock = lock

  def __enter__(self):
    self._lock.acquire()
    return self

  def __exit__(self, exc_type, exc, tb):
    self._lock.release()
    return False


class _StubLogger:
  def __init__(self):
    self._lock = Lock()

  def P(self, *args, **kwargs):  # pylint: disable=unused-argument
    if os.environ.get("EE_TEST_LOGS", DEFAULT_SHOW_TEST_LOGS) == "1":
      print(*args)
    return

  def start_timer(self, *args, **kwargs):  # pylint: disable=unused-argument
    return

  def stop_timer(self, *args, **kwargs):  # pylint: disable=unused-argument
    return

  def save_pickle_to_data(self, *args, **kwargs):  # pylint: disable=unused-argument
    return

  def load_pickle_from_data(self, *args, **kwargs):  # pylint: disable=unused-argument
    return None

  def get_data_file(self, *args, **kwargs):  # pylint: disable=unused-argument
    return None

  def managed_lock_resource(self, *args, **kwargs):  # pylint: disable=unused-argument
    return _DummyLock(self._lock)

  def str_to_date(self, date_str):
    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

  def elapsed_to_str(self, seconds):  # pylint: disable=unused-argument
    return str(seconds)


class _StubOwner:
  def __init__(self, node_addr="0xNODE1", node_name="NODE1"):
    self.node_addr = node_addr
    self.node_name = node_name

  def node_address_to_eth_address(self, node_addr):
    return f"eth_{node_addr}"

  def network_node_eeid(self, node_addr):
    return f"EE_{node_addr}"

  def network_node_hb_interval(self, addr=None):  # pylint: disable=unused-argument
    return 10

  def network_node_is_online(self, node_addr, dt_now=None):  # pylint: disable=unused-argument
    return True

  def network_node_version(self, node_addr):  # pylint: disable=unused-argument
    return "0.0.0"

  def network_node_last_seen(self, node_addr, dt_now=None, as_sec=False):  # pylint: disable=unused-argument
    return 0 if as_sec else None

  def network_node_total_mem(self, node_addr):  # pylint: disable=unused-argument
    return 0

  def network_node_avail_mem(self, node_addr):  # pylint: disable=unused-argument
    return 0

  def network_node_total_cpu_cores(self, node_addr):  # pylint: disable=unused-argument
    return 0

  def network_node_avail_cpu_cores(self, node_addr):  # pylint: disable=unused-argument
    return 0

  def network_node_total_disk(self, node_addr):  # pylint: disable=unused-argument
    return 0

  def network_node_avail_disk(self, node_addr):  # pylint: disable=unused-argument
    return 0

  def network_node_default_gpu_name(self, node_addr):  # pylint: disable=unused-argument
    return None

  def network_node_default_gpu_total_mem(self, node_addr):  # pylint: disable=unused-argument
    return None

  def network_node_default_gpu_avail_mem(self, node_addr):  # pylint: disable=unused-argument
    return None

  def network_node_default_gpu_usage(self, node_addr):  # pylint: disable=unused-argument
    return None

  def network_node_default_cuda(self, node_addr, as_int=False):  # pylint: disable=unused-argument
    return -1 if as_int else "N/A"

  def get_network_node_tags(self, node_addr):  # pylint: disable=unused-argument
    return {}

  def network_node_last_gpu_status(self, node_addr):  # pylint: disable=unused-argument
    return None

  def network_node_gpu_summary(self, node_addr):  # pylint: disable=unused-argument
    return {}


class _StubBlockEngine:
  def maybe_remove_prefix(self, addr):
    return addr

  def maybe_remove_addr_prefix(self, addr):
    return addr

  def _add_prefix(self, addr):
    return addr

  def node_address_to_eth_address(self, addr):
    return f"eth_{addr}"


class _StubEpochManager:
  def __init__(self):
    self.calls = []

  def register_data(self, addr, data):
    self.calls.append((addr, dict(data)))
    return


class TestEpochsManager(unittest.TestCase):
  def setUp(self):
    if hasattr(EpochsManager, "_instance"):
      delattr(EpochsManager, "_instance")
    self._env_backup = {}
    for key in (ct.BASE_CT.EE_EPOCH_INTERVALS_KEY, ct.BASE_CT.EE_EPOCH_INTERVAL_SECONDS_KEY):
      if key in os.environ:
        self._env_backup[key] = os.environ[key]
      else:
        self._env_backup[key] = None
    os.environ[ct.BASE_CT.EE_EPOCH_INTERVALS_KEY] = "48"
    os.environ[ct.BASE_CT.EE_EPOCH_INTERVAL_SECONDS_KEY] = "3600"
    self.log = _StubLogger()
    self.owner = _StubOwner()
    genesis = datetime.strptime(ct.DEFAULT_GENESYS_EPOCH_DATE, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    debug_date = genesis + timedelta(seconds=1)
    self.manager = EpochsManager(log=self.log, owner=self.owner, debug=False, debug_date=debug_date)
    self.manager._EpochsManager__initialize_new_node(self.owner.node_addr)
    self.manager.maybe_close_epoch()

  def tearDown(self):
    if hasattr(EpochsManager, "_instance"):
      delattr(EpochsManager, "_instance")
    for key, value in self._env_backup.items():
      if value is None:
        os.environ.pop(key, None)
      else:
        os.environ[key] = value

  def _make_hb(self, ts):
    """
    Build a heartbeat payload for a given timestamp.

    Parameters
    ----------
    ts : datetime
      Timestamp for the heartbeat.

    Returns
    -------
    dict
      Heartbeat payload.
    """
    return {
      ct.PAYLOAD_DATA.EE_TIMESTAMP: ts.strftime(ct.HB.TIMESTAMP_FORMAT),
      ct.PAYLOAD_DATA.EE_TIMEZONE: "UTC+0",
    }
  
  def _generate_hb_data(
    self, n_nodes, n_heartbeats,
    start=None, step_seconds=10,
    jitter_seconds=0, gap_every=0,
    out_of_order_prob=0.0, seed=42,
  ):
    """
    Generate heartbeat streams for stress testing.

    Parameters
    ----------
    n_nodes : int
      Number of nodes.
    n_heartbeats : int
      Number of heartbeats per node.
    start : datetime or None
      Start timestamp for heartbeats.
    step_seconds : int
      Base step between heartbeats.
    jitter_seconds : int
      Random jitter added to timestamps.
    gap_every : int
      Insert a larger gap after every N heartbeats (0 disables).
    out_of_order_prob : float
      Probability of swapping adjacent heartbeats to simulate out-of-order delivery.
    seed : int
      Random seed for reproducibility.

    Returns
    -------
    dict
      Mapping of node address to list of (node_addr, heartbeat_payload).
    """
    rng = random.Random(seed)
    genesis = self.manager.genesis_date
    start = start or (genesis + timedelta(seconds=1))
    data = {}
    for node_idx in range(n_nodes):
      node_addr = f"0xNODE{node_idx}"
      ts = start
      node_hb = []
      for _ in range(n_heartbeats):
        jitter = rng.randint(-jitter_seconds, jitter_seconds) if jitter_seconds else 0
        node_hb.append((node_addr, self._make_hb(ts + timedelta(seconds=jitter))))
        ts = ts + timedelta(seconds=step_seconds)
        if gap_every and (len(node_hb) % gap_every == 0):
          ts = ts + timedelta(seconds=step_seconds * 6)
      if out_of_order_prob > 0 and len(node_hb) > 1:
        for idx in range(1, len(node_hb)):
          if rng.random() < out_of_order_prob:
            node_hb[idx - 1], node_hb[idx] = node_hb[idx], node_hb[idx - 1]
        if node_idx == 0:
          first_ts_str = node_hb[0][1][ct.PAYLOAD_DATA.EE_TIMESTAMP]
          first_ts = datetime.strptime(first_ts_str, ct.HB.TIMESTAMP_FORMAT)
          node_hb[1] = (node_addr, self._make_hb(first_ts - timedelta(seconds=1)))
      data[node_addr] = node_hb
    return data

  def test_epochs_range_matches_slice(self):
    node_addr = self.owner.node_addr
    for epoch in range(1, 6):
      self.manager.update_epoch_availability(
        epoch=epoch,
        availability_table={node_addr: epoch * 10},
        agreement_signatures={"sig": "x"},
        agreement_cid="cid",
        signatures_cid="scid",
        debug=False,
      )
    self.manager.maybe_update_cached_data(force=True)

    full_epochs = self.manager.get_node_epochs(node_addr, autocomplete=True, as_list=False)
    range_epochs = self.manager.get_node_epochs_range(
      node_addr=node_addr,
      start_epoch=2,
      end_epoch=4,
      as_list=False,
      autocomplete=True,
    )
    expected = {ep: full_epochs.get(ep, 0) for ep in range(2, 5)}
    self.assertEqual(range_epochs, expected)

  def test_accumulator_matches_legacy(self):
    node_addr = self.owner.node_addr
    genesis = self.manager.genesis_date
    start = genesis + timedelta(seconds=1)
    timestamps = [
      start,
      start + timedelta(seconds=10),
      start + timedelta(seconds=20),
      start + timedelta(seconds=60),
      start + timedelta(seconds=70),
    ]
    for ts in timestamps:
      self.manager.register_data(node_addr, self._make_hb(ts))

    legacy_avail = self.manager._EpochsManager__calc_node_avail_seconds(
      node_addr, time_between_heartbeats=10
    )
    accum_avail = self.manager._EpochsManager__get_accumulated_avail_seconds(node_addr)
    self.assertAlmostEqual(legacy_avail, accum_avail, places=4)
    self.manager._EpochsManager__recalculate_current_epoch_for_node(node_addr)
    local_epochs = self.manager._EpochsManager__data[node_addr][EPCT.LOCAL_EPOCHS]
    self.assertIn(self.manager.get_time_epoch(), local_epochs)

  def test_stress_generator(self):
    genesis = self.manager.genesis_date
    early_ts = genesis + timedelta(seconds=30)
    older_ts = genesis + timedelta(seconds=20)
    self.manager.register_data("0xNODE0", self._make_hb(early_ts))
    self.manager.register_data("0xNODE0", self._make_hb(older_ts))
    self.assertTrue(
      self.manager._EpochsManager__data["0xNODE0"].get(EPCT.CURR_NEEDS_RECALC, False)
    )

    hb_data = self._generate_hb_data(
      N_NODES, N_HEARTBEATS,
      start=genesis + timedelta(seconds=40),
      jitter_seconds=2,
      gap_every=120,
      out_of_order_prob=0.01,
    )
    self.assertEqual(len(hb_data), N_NODES)
    for node_addr, entries in hb_data.items():
      self.assertEqual(len(entries), N_HEARTBEATS)
      self.assertEqual(entries[0][0], node_addr)

  def test_stress_accumulator_vs_legacy(self):
    if os.environ.get(STRESS_TEST_ENV, DEFAULT_STRESS_TEST) != "1":
      self.skipTest(f"Set {STRESS_TEST_ENV}=1 to enable stress test")

    hb_data = self._generate_hb_data(N_NODES, N_HEARTBEATS)
    start_register = time.time()
    for _node_addr, entries in hb_data.items():
      for node_addr, hb in entries:
        self.manager.register_data(node_addr, hb)
    register_time = time.time() - start_register

    start_accum = time.time()
    accum_avails = {}
    for node_addr in hb_data:
      accum_avails[node_addr] = self.manager._EpochsManager__get_accumulated_avail_seconds(node_addr)
    accum_time = time.time() - start_accum

    start_legacy = time.time()
    legacy_avails = {}
    for node_addr in hb_data:
      legacy_avails[node_addr] = self.manager._EpochsManager__calc_node_avail_seconds(
        node_addr, time_between_heartbeats=10
      )
    legacy_time = time.time() - start_legacy

    nodes_needing_recalc = 0
    for node_addr in hb_data:
      needs_recalc = self.manager._EpochsManager__data[node_addr].get(EPCT.CURR_NEEDS_RECALC, False)
      if needs_recalc:
        nodes_needing_recalc += 1
        continue
      self.assertAlmostEqual(legacy_avails[node_addr], accum_avails[node_addr], places=4)
    self.log.P(f"Nodes needing recalc: {nodes_needing_recalc}")

    self.log.P(
      f"Stress timings register={register_time:.2f}s, "
      f"accum_avail={accum_time:.2f}s, legacy_avail={legacy_time:.2f}s"
    )


class TestNetMonDuplicates(unittest.TestCase):
  def setUp(self):
    self.log = Logger(
      lib_name="TEST_NMON",
      base_folder=".",
      app_folder="_local_cache",
      no_folders_no_save=True,
      DEBUG=False,
    )
    self.epoch_manager = _StubEpochManager()
    self.netmon = NetworkMonitor(
      log=self.log,
      node_name="SELF",
      node_addr="0xSELF",
      epoch_manager=self.epoch_manager,
      blockchain_manager=_StubBlockEngine(),
    )

  def _make_hb(self, ts):
    return {
      ct.PAYLOAD_DATA.EE_TIMESTAMP: ts.strftime(ct.HB.TIMESTAMP_FORMAT),
      ct.PAYLOAD_DATA.EE_TIMEZONE: "UTC+0",
      ct.HB.CURRENT_TIME: ts.strftime(ct.HB.TIMESTAMP_FORMAT),
      ct.EE_ID: "EE_NODE",
    }

  def test_register_heartbeat_drops_recent_duplicate(self):
    ts = datetime(2026, 1, 1, 0, 0, 0)
    hb = self._make_hb(ts)
    self.netmon.register_heartbeat("0xNODE1", dict(hb))
    self.netmon.register_heartbeat("0xNODE1", dict(hb))

    stored = self.netmon.get_box_heartbeats("0xNODE1")
    self.assertEqual(len(stored), 1)
    self.assertEqual(len(self.epoch_manager.calls), 1)

  def test_replay_path_preserves_received_time(self):
    ts = datetime(2026, 1, 1, 0, 0, 0)
    hb = self._make_hb(ts)
    hb[ct.HB.RECEIVED_TIME] = "2026-01-01 00:00:05"
    accepted = self.netmon._NetworkMonitor__register_heartbeat(
      "0xNODE2", dict(hb), update_received_time=False
    )

    self.assertTrue(accepted)
    stored = self.netmon.get_box_heartbeats("0xNODE2")
    self.assertEqual(stored[-1][ct.HB.RECEIVED_TIME], "2026-01-01 00:00:05")
