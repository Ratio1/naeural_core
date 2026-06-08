import contextlib
import copy
import gc
import json
import os
import pickle
import time
import tracemalloc
import unittest
from collections import deque
from datetime import datetime, timedelta
from threading import Thread
from unittest import mock

from naeural_core import constants as ct
from naeural_core.core_logging import Logger
from naeural_core.main.net_mon import NetworkMonitor


PROFILE_NODES_ENV = "EE_NETMON_PROFILE_NODES"
PROFILE_HEARTBEATS_ENV = "EE_NETMON_PROFILE_HEARTBEATS"
PROFILE_PAYLOAD_SCALE_ENV = "EE_NETMON_PROFILE_PAYLOAD_SCALE"
PROFILE_WORKER_DELAY_ENV = "EE_NETMON_PROFILE_WORKER_DELAY_SECONDS"
PROFILE_REPLAY_DB_ENV = "EE_NETMON_PROFILE_REPLAY_DB"
PROFILE_REPLAY_MODE_ENV = "EE_NETMON_PROFILE_REPLAY_MODE"
PROFILE_REPLAY_LIMIT_ENV = "EE_NETMON_PROFILE_REPLAY_LIMIT"
PROFILE_REPLAY_COMPRESS_ENV = "EE_NETMON_PROFILE_REPLAY_COMPRESS"

DEFAULT_PROFILE_NODES = 24
DEFAULT_PROFILE_HEARTBEATS = 10
DEFAULT_PROFILE_PAYLOAD_SCALE = 8
DEFAULT_PROFILE_WORKER_DELAY_SECONDS = "0.0002"
DEFAULT_PROFILE_REPLAY_MODE = "latest"


def _env_int(name, default):
  try:
    return int(os.environ.get(name, default))
  except Exception:
    return default


def _env_float(name, default):
  try:
    return float(os.environ.get(name, default))
  except Exception:
    return float(default)


def _env_bool(name, default):
  value = os.environ.get(name)
  if value is None:
    return default
  return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


class _StubBlockEngine:
  def maybe_remove_prefix(self, addr):
    return addr.replace("0xai_", "", 1)

  def maybe_remove_addr_prefix(self, addr):
    return self.maybe_remove_prefix(addr)

  def _add_prefix(self, addr):
    return addr if addr.startswith("0xai_") else "0xai_" + addr

  def node_address_to_eth_address(self, addr):
    return f"eth_{addr}"


class _CountingEpochManager:
  def __init__(self):
    self.calls = 0
    self.last_timestamp_by_addr = {}
    self.last_data_by_addr = {}

  def register_data(self, addr, data):
    self.calls += 1
    self.last_timestamp_by_addr[addr] = data.get(ct.PAYLOAD_DATA.EE_TIMESTAMP)
    self.last_data_by_addr[addr] = dict(data)
    return


class _NetmonHarness:
  def __init__(self):
    self.log = Logger(
      lib_name="TEST_NMON_PROFILE",
      base_folder=".",
      app_folder="_local_cache",
      no_folders_no_save=True,
      DEBUG=False,
    )
    self.epoch_manager = _CountingEpochManager()
    self.netmon = NetworkMonitor(
      log=self.log,
      node_name="SELF",
      node_addr="0xSELF",
      epoch_manager=self.epoch_manager,
      blockchain_manager=_StubBlockEngine(),
    )

  def make_heartbeat(self, addr, node_idx, hb_idx, payload_scale=DEFAULT_PROFILE_PAYLOAD_SCALE):
    body = self.make_heartbeat_body(
      addr=addr,
      node_idx=node_idx,
      hb_idx=hb_idx,
      payload_scale=payload_scale,
    )
    return {
      ct.HB.ENCODED_DATA: self.log.compress_text(json.dumps(body)),
      ct.EE_ID: body[ct.EE_ID],
      ct.HB.EE_ADDR: addr,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: body[ct.PAYLOAD_DATA.EE_TIMESTAMP],
      ct.PAYLOAD_DATA.EE_TIMEZONE: body[ct.PAYLOAD_DATA.EE_TIMEZONE],
      ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
    }

  def make_heartbeat_body(self, addr, node_idx, hb_idx, payload_scale=DEFAULT_PROFILE_PAYLOAD_SCALE):
    ts = datetime(2026, 1, 1, 0, 0, 0) + timedelta(seconds=10 * hb_idx)
    str_ts = ts.strftime(ct.HB.TIMESTAMP_FORMAT)
    active_plugins = [
      {
        "STREAM_NAME": f"pipeline-{plugin_idx % 3}",
        "SIGNATURE": f"PLUGIN_{plugin_idx}",
        "INSTANCE_ID": f"inst-{node_idx}-{plugin_idx}",
        "STATUS": "running",
        "LAST_ERROR": None,
        "STATS": {
          "processed": hb_idx * (plugin_idx + 1),
          "latencies": [round((plugin_idx + 1) * 0.01, 4)] * payload_scale,
          "labels": [f"label-{idx}" for idx in range(payload_scale)],
        },
      }
      for plugin_idx in range(payload_scale)
    ]
    pipelines = [
      {
        "NAME": f"pipeline-{pipe_idx}",
        "PLUGINS": [
          {
            "SIGNATURE": f"PLUGIN_{pipe_idx}_{plugin_idx}",
            "INSTANCE_ID": f"inst-{pipe_idx}-{plugin_idx}",
            "CONFIG": {"thresholds": list(range(payload_scale))},
          }
          for plugin_idx in range(2)
        ],
      }
      for pipe_idx in range(max(1, payload_scale // 4))
    ]
    return {
      ct.EE_ID: f"node-{node_idx}",
      ct.HB.EE_ADDR: addr,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: str_ts,
      ct.PAYLOAD_DATA.EE_TIMEZONE: "UTC+0",
      ct.HB.CURRENT_TIME: str_ts,
      ct.HB.EE_HB_TIME: 10,
      ct.HB.MACHINE_MEMORY: 64.0,
      ct.HB.AVAILABLE_MEMORY: 52.0 - (node_idx % 5),
      ct.HB.PROCESS_MEMORY: 2.5 + (node_idx % 3) * 0.1,
      ct.HB.TOTAL_DISK: 1000.0,
      ct.HB.AVAILABLE_DISK: 850.0,
      ct.HB.CPU_USED: 10.0 + (node_idx % 7),
      ct.HB.UPTIME: 3600 + hb_idx * 10,
      ct.HB.DEVICE_STATUS: ct.DEVICE_STATUS_ONLINE,
      ct.HB.GPUS: [
        {
          "NAME": "RTX_TEST",
          "GPU_USED": node_idx % 20,
          "FREE_MEM": 21.5,
          "TOTAL_MEM": 24.0,
          "GPU_TEMP": 45 + (node_idx % 5),
          "GPU_TEMP_MAX": 90,
          "ALLOCATED_MEM": 0.5,
        }
      ],
      ct.HB.GPU_INFO: {"driver": "test", "devices": 1},
      ct.HB.DEFAULT_CUDA: "cuda:0",
      ct.HB.LOOPS_TIMINGS: {
        "main_loop_avg_time": 0.1 + (hb_idx % 3) * 0.01,
        "main_loop_freq": 10.0,
      },
      ct.HB.TEMPERATURE_INFO: {
        "temperatures": [40 + idx for idx in range(payload_scale)],
        "max_temp": 40 + payload_scale,
        "max_temp_sensor": "cpu-test",
      },
      ct.HB.ACTIVE_PLUGINS: active_plugins,
      ct.HB.PIPELINES: pipelines,
      ct.HB.COMM_STATS: {
        ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL: {
          ct.HB.COMM_INFO.IN_KB: 123.4,
          ct.HB.COMM_INFO.OUT_KB: 12.3,
          "FAILS": 0,
        },
      },
      ct.HB.EE_WHITELIST: ["0xSELF", "0xOTHER"],
    }


def _build_messages(harness, n_nodes, n_heartbeats, payload_scale):
  messages = []
  expected_latest_ts = {}
  for hb_idx in range(n_heartbeats):
    for node_idx in range(n_nodes):
      addr = f"0xNODE{node_idx:04d}"
      hb = harness.make_heartbeat(addr, node_idx, hb_idx, payload_scale=payload_scale)
      messages.append((addr, hb))
      expected_latest_ts[addr] = hb[ct.PAYLOAD_DATA.EE_TIMESTAMP]
  return messages, expected_latest_ts


def _heartbeat_as_compressed_envelope(harness, addr, hb):
  """
  Convert a saved netmon heartbeat back into the compressed MQTT envelope shape.

  Saved ``network_monitor/db.pkl`` entries are already decoded snapshots. The
  communication receive path normally sees ``ENCODED_DATA``, so replay profiles
  default to re-compressing the saved body before timed registration.
  """
  hb_body = copy.deepcopy(hb)
  return {
    ct.HB.ENCODED_DATA: harness.log.compress_text(json.dumps(hb_body, default=str)),
    ct.EE_ID: hb_body.get(ct.EE_ID),
    ct.HB.EE_ADDR: hb_body.get(ct.HB.EE_ADDR, addr),
    ct.PAYLOAD_DATA.EE_TIMESTAMP: hb_body.get(ct.PAYLOAD_DATA.EE_TIMESTAMP),
    ct.PAYLOAD_DATA.EE_TIMEZONE: hb_body.get(ct.PAYLOAD_DATA.EE_TIMEZONE),
    ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
  }


def _build_replay_messages(harness, replay_db, mode, limit, compress):
  with open(replay_db, "rb") as fh:
    network_heartbeats = pickle.load(fh)

  if not isinstance(network_heartbeats, dict):
    raise ValueError(f"Expected dict netmon db, got {type(network_heartbeats)}")

  mode = (mode or DEFAULT_PROFILE_REPLAY_MODE).strip().lower()
  if mode not in {"latest", "all"}:
    raise ValueError(f"Unsupported replay mode {mode!r}; expected 'latest' or 'all'")

  messages = []
  expected_latest_ts = {}
  skipped = 0
  source_heartbeats = 0
  for addr, hb_history in network_heartbeats.items():
    try:
      hbs = list(hb_history)
    except Exception:
      skipped += 1
      continue
    source_heartbeats += len(hbs)
    if mode == "latest":
      hbs = hbs[-1:] if hbs else []
    for hb in hbs:
      if not isinstance(hb, dict):
        skipped += 1
        continue
      if hb.get(ct.PAYLOAD_DATA.EE_TIMESTAMP) is None:
        skipped += 1
        continue
      hb_for_replay = (
        _heartbeat_as_compressed_envelope(harness, addr, hb)
        if compress
        else copy.deepcopy(hb)
      )
      messages.append((addr, hb_for_replay))
      expected_latest_ts[addr] = hb.get(ct.PAYLOAD_DATA.EE_TIMESTAMP)
      if limit and len(messages) >= limit:
        return messages, expected_latest_ts, {
          "replay_db": replay_db,
          "replay_mode": mode,
          "replay_compressed": compress,
          "replay_limit": limit,
          "replay_source_nodes": len(network_heartbeats),
          "replay_source_heartbeats": source_heartbeats,
          "replay_skipped": skipped,
        }

  return messages, expected_latest_ts, {
    "replay_db": replay_db,
    "replay_mode": mode,
    "replay_compressed": compress,
    "replay_limit": limit,
    "replay_source_nodes": len(network_heartbeats),
    "replay_source_heartbeats": source_heartbeats,
    "replay_skipped": skipped,
  }


def _profile_register_sequence(netmon, messages):
  gc.collect()
  tracemalloc.start()
  start = time.perf_counter()
  for addr, hb in messages:
    netmon.register_heartbeat(addr, dict(hb))
  elapsed = time.perf_counter() - start
  current_bytes, peak_bytes = tracemalloc.get_traced_memory()
  tracemalloc.stop()
  return {
    "elapsed_seconds": round(elapsed, 6),
    "messages": len(messages),
    "messages_per_second": round(len(messages) / elapsed, 2) if elapsed else None,
    "tracemalloc_current_bytes": current_bytes,
    "tracemalloc_peak_bytes": peak_bytes,
    "gc_counts": list(gc.get_count()),
  }


class _FifoBackoffRegistrar:
  def __init__(self, register_fn, worker_delay_seconds):
    self._queue = deque()
    self._register_fn = register_fn
    self._worker_delay_seconds = worker_delay_seconds
    self.registered = 0

  def submit(self, addr, hb):
    self._queue.append((addr, hb))

  def run(self):
    start = time.perf_counter()
    worker = Thread(target=self._drain, name="test-fifo-heartbeat-registrar")
    worker.start()
    worker.join()
    return time.perf_counter() - start

  def _drain(self):
    while self._queue:
      addr, hb = self._queue.popleft()
      if self._worker_delay_seconds > 0:
        time.sleep(self._worker_delay_seconds)
      self._register_fn(addr, dict(hb))
      self.registered += 1


class _LatestWinsBackoffRegistrar:
  def __init__(self, register_fn, worker_delay_seconds):
    self._pending = {}
    self._register_fn = register_fn
    self._worker_delay_seconds = worker_delay_seconds
    self.received = 0
    self.superseded = 0
    self.registered = 0

  def submit(self, addr, hb):
    self.received += 1
    if addr in self._pending:
      self.superseded += 1
    self._pending[addr] = hb

  def run(self):
    start = time.perf_counter()
    worker = Thread(target=self._drain, name="test-latest-wins-heartbeat-registrar")
    worker.start()
    worker.join()
    return time.perf_counter() - start

  def _drain(self):
    items = list(self._pending.items())
    self._pending.clear()
    for addr, hb in items:
      if self._worker_delay_seconds > 0:
        time.sleep(self._worker_delay_seconds)
      self._register_fn(addr, dict(hb))
      self.registered += 1


class TestNetmonRegisterHeartbeatProfile(unittest.TestCase):

  def test_register_heartbeat_profile_emits_baseline_metrics(self):
    n_nodes = _env_int(PROFILE_NODES_ENV, DEFAULT_PROFILE_NODES)
    n_heartbeats = _env_int(PROFILE_HEARTBEATS_ENV, DEFAULT_PROFILE_HEARTBEATS)
    payload_scale = _env_int(PROFILE_PAYLOAD_SCALE_ENV, DEFAULT_PROFILE_PAYLOAD_SCALE)
    harness = _NetmonHarness()
    messages, expected_latest_ts = _build_messages(
      harness=harness,
      n_nodes=n_nodes,
      n_heartbeats=n_heartbeats,
      payload_scale=payload_scale,
    )

    profile = _profile_register_sequence(harness.netmon, messages)

    self.assertEqual(harness.epoch_manager.calls, len(messages))
    self.assertEqual(set(harness.epoch_manager.last_timestamp_by_addr), set(expected_latest_ts))
    for addr, expected_ts in expected_latest_ts.items():
      hb = harness.netmon.network_node_last_heartbeat(addr)
      self.assertEqual(hb[ct.PAYLOAD_DATA.EE_TIMESTAMP], expected_ts)

    profile.update({
      "nodes": n_nodes,
      "heartbeats_per_node": n_heartbeats,
      "payload_scale": payload_scale,
      "epoch_register_calls": harness.epoch_manager.calls,
    })
    print("NETMON_REGISTER_PROFILE " + json.dumps(profile, sort_keys=True))

  def test_saved_heartbeat_replay_profile_if_configured(self):
    replay_db = os.environ.get(PROFILE_REPLAY_DB_ENV)
    if not replay_db:
      self.skipTest(f"Set {PROFILE_REPLAY_DB_ENV} to replay saved netmon heartbeats")

    replay_limit = _env_int(PROFILE_REPLAY_LIMIT_ENV, 0)
    replay_mode = os.environ.get(PROFILE_REPLAY_MODE_ENV, DEFAULT_PROFILE_REPLAY_MODE)
    replay_compress = _env_bool(PROFILE_REPLAY_COMPRESS_ENV, True)
    harness = _NetmonHarness()
    messages, expected_latest_ts, replay_meta = _build_replay_messages(
      harness=harness,
      replay_db=replay_db,
      mode=replay_mode,
      limit=replay_limit,
      compress=replay_compress,
    )

    self.assertGreater(len(messages), 0)

    profile = _profile_register_sequence(harness.netmon, messages)

    self.assertEqual(harness.epoch_manager.calls, len(messages))
    for addr, expected_ts in expected_latest_ts.items():
      hb = harness.netmon.network_node_last_heartbeat(addr)
      self.assertEqual(hb[ct.PAYLOAD_DATA.EE_TIMESTAMP], expected_ts)

    profile.update(replay_meta)
    profile.update({
      "messages": len(messages),
      "nodes": len(expected_latest_ts),
      "epoch_register_calls": harness.epoch_manager.calls,
    })
    print("NETMON_REPLAY_PROFILE " + json.dumps(profile, sort_keys=True))

  def test_latest_wins_backoff_worker_bounds_registration_work_under_stress(self):
    n_nodes = _env_int(PROFILE_NODES_ENV, DEFAULT_PROFILE_NODES)
    n_heartbeats = _env_int(PROFILE_HEARTBEATS_ENV, DEFAULT_PROFILE_HEARTBEATS)
    payload_scale = _env_int(PROFILE_PAYLOAD_SCALE_ENV, DEFAULT_PROFILE_PAYLOAD_SCALE)
    worker_delay_seconds = _env_float(
      PROFILE_WORKER_DELAY_ENV, DEFAULT_PROFILE_WORKER_DELAY_SECONDS
    )

    fifo_harness = _NetmonHarness()
    latest_harness = _NetmonHarness()
    messages, expected_latest_ts = _build_messages(
      harness=fifo_harness,
      n_nodes=n_nodes,
      n_heartbeats=n_heartbeats,
      payload_scale=payload_scale,
    )

    fifo = _FifoBackoffRegistrar(
      register_fn=fifo_harness.netmon.register_heartbeat,
      worker_delay_seconds=worker_delay_seconds,
    )
    latest = _LatestWinsBackoffRegistrar(
      register_fn=latest_harness.netmon.register_heartbeat,
      worker_delay_seconds=worker_delay_seconds,
    )
    for addr, hb in messages:
      fifo.submit(addr, hb)
      latest.submit(addr, hb)

    fifo_elapsed = fifo.run()
    latest_elapsed = latest.run()

    self.assertEqual(fifo.registered, len(messages))
    self.assertEqual(latest.received, len(messages))
    self.assertEqual(latest.registered, n_nodes)
    self.assertEqual(latest.superseded, len(messages) - n_nodes)
    self.assertLess(latest.registered, fifo.registered)
    self.assertLess(latest_elapsed, fifo_elapsed)
    for addr, expected_ts in expected_latest_ts.items():
      hb = latest_harness.netmon.network_node_last_heartbeat(addr)
      self.assertEqual(hb[ct.PAYLOAD_DATA.EE_TIMESTAMP], expected_ts)

    profile = {
      "input_messages": len(messages),
      "nodes": n_nodes,
      "heartbeats_per_node": n_heartbeats,
      "payload_scale": payload_scale,
      "worker_delay_seconds": worker_delay_seconds,
      "fifo_registered": fifo.registered,
      "fifo_elapsed_seconds": round(fifo_elapsed, 6),
      "latest_registered": latest.registered,
      "latest_superseded": latest.superseded,
      "latest_elapsed_seconds": round(latest_elapsed, 6),
      "registration_reduction_ratio": round(1 - (latest.registered / fifo.registered), 6),
    }
    print("NETMON_BACKOFF_PROFILE " + json.dumps(profile, sort_keys=True))

  def test_reader_held_heartbeat_reference_is_not_mutated_by_later_compaction(self):
    harness = _NetmonHarness()
    addr = "0xNODE_RACE"
    first = harness.make_heartbeat(addr, 0, 0, payload_scale=4)
    second = harness.make_heartbeat(addr, 0, 1, payload_scale=4)
    harness.netmon.register_heartbeat(addr, dict(first))
    held_reference = harness.netmon.network_node_last_heartbeat(addr)
    self.assertIn(ct.HB.GPU_INFO, held_reference)

    harness.netmon.register_heartbeat(addr, dict(second))
    stored_history = harness.netmon.get_box_heartbeats(addr)

    self.assertIn(ct.HB.GPU_INFO, held_reference)
    self.assertNotIn(ct.HB.GPU_INFO, stored_history[0])

  def test_public_heartbeat_readers_return_detached_snapshots(self):
    harness = _NetmonHarness()
    addr = "0xNODE_PUBLIC_READS"
    for hb_idx in range(3):
      harness.netmon.register_heartbeat(
        addr,
        harness.make_heartbeat(addr, 0, hb_idx, payload_scale=4),
      )

    latest = harness.netmon.network_node_last_heartbeat(addr)
    latest[ct.HB.GPUS][0]["NAME"] = "MUTATED_LAST"
    latest[ct.HB.GPU_INFO]["driver"] = "mutated"

    box = harness.netmon.get_box_heartbeats(addr)
    box[-1][ct.HB.GPUS][0]["NAME"] = "MUTATED_BOX"

    all_heartbeats = harness.netmon.all_heartbeats
    all_heartbeats[addr][-1][ct.HB.GPUS][0]["NAME"] = "MUTATED_ALL"

    today = list(harness.netmon.network_node_today_heartbeats(
      addr,
      dt_now=datetime(2026, 1, 1, 0, 0, 0),
    ))
    self.assertGreater(len(today), 0)
    today[-1][ct.HB.GPUS][0]["NAME"] = "MUTATED_TODAY"

    fresh_latest = harness.netmon.network_node_last_heartbeat(addr)
    self.assertEqual(fresh_latest[ct.HB.GPUS][0]["NAME"], "RTX_TEST")
    self.assertEqual(fresh_latest[ct.HB.GPU_INFO]["driver"], "test")

  def test_last_heartbeat_public_copy_happens_outside_netmon_lock(self):
    harness = _NetmonHarness()
    addr = "0xNODE_LAST_COPY_LOCK"
    harness.netmon.register_heartbeat(
      addr,
      harness.make_heartbeat(addr, 0, 0, payload_scale=4),
    )

    lock_depth = 0
    original_managed_lock = harness.log.managed_lock_resource

    @contextlib.contextmanager
    def tracked_managed_lock(name):
      nonlocal lock_depth
      with original_managed_lock(name):
        lock_depth += 1
        try:
          yield
        finally:
          lock_depth -= 1

    def deepcopy_must_run_after_lock(obj):
      self.assertEqual(lock_depth, 0)
      return copy.deepcopy(obj)

    harness.log.managed_lock_resource = tracked_managed_lock
    with mock.patch(
      "naeural_core.main.net_mon.deepcopy",
      side_effect=deepcopy_must_run_after_lock,
    ) as patched_deepcopy:
      latest = harness.netmon.network_node_last_heartbeat(addr)

    patched_deepcopy.assert_called()
    self.assertEqual(latest[ct.HB.GPUS][0]["NAME"], "RTX_TEST")

  def test_public_derived_readers_return_detached_mutable_values(self):
    harness = _NetmonHarness()
    addr = "0xNODE_DERIVED_READS"
    for hb_idx in range(3):
      harness.netmon.register_heartbeat(
        addr,
        harness.make_heartbeat(addr, 0, hb_idx, payload_scale=4),
      )
    dt_now = datetime.now()

    whitelist = harness.netmon.network_node_whitelist(addr)
    whitelist.append("0xMUTATED")

    gpu_data = harness.netmon.network_node_default_gpu_data(addr)
    gpu_data["NAME"] = "MUTATED_GPU_DATA"

    gpu_status = harness.netmon.network_node_last_gpu_status(addr)
    gpu_status[0]["NAME"] = "MUTATED_GPU_STATUS"

    gpu_summary = harness.netmon.network_node_gpu_summary(addr)
    gpu_summary["driver"] = "mutated"

    gpu_history = harness.netmon.network_node_default_gpu_history(
      addr=addr,
      minutes=2,
      dt_now=dt_now,
    )
    self.assertGreater(len(gpu_history), 0)
    gpu_history[-1]["NAME"] = "MUTATED_GPU_HISTORY"

    temp_history = harness.netmon.network_node_past_temperatures_history(
      addr=addr,
      minutes=2,
      dt_now=dt_now,
    )
    self.assertGreater(len(temp_history["all_sensors"]), 0)
    temp_history["all_sensors"][-1]["temperatures"][0] = -999

    known_nodes = harness.netmon.network_known_nodes()
    known_nodes[addr]["pipelines"][0]["NAME"] = "MUTATED_KNOWN"

    pipelines = harness.netmon.network_node_pipelines(addr)
    pipelines[0]["NAME"] = "MUTATED_PIPELINES"

    pipeline_info = harness.netmon.network_node_pipeline_info(addr, "pipeline-0")
    pipeline_info["NAME"] = "MUTATED_PIPELINE_INFO"

    self.assertNotIn("0xMUTATED", harness.netmon.network_node_whitelist(addr))
    self.assertEqual(harness.netmon.network_node_default_gpu_data(addr)["NAME"], "RTX_TEST")
    self.assertEqual(harness.netmon.network_node_last_gpu_status(addr)[0]["NAME"], "RTX_TEST")
    self.assertEqual(harness.netmon.network_node_gpu_summary(addr)["driver"], "test")
    self.assertEqual(
      harness.netmon.network_node_default_gpu_history(addr=addr, minutes=2, dt_now=dt_now)[-1]["NAME"],
      "RTX_TEST",
    )
    self.assertNotEqual(
      harness.netmon.network_node_past_temperatures_history(
        addr=addr,
        minutes=2,
        dt_now=dt_now,
      )["all_sensors"][-1]["temperatures"][0],
      -999,
    )
    self.assertEqual(harness.netmon.network_known_nodes()[addr]["pipelines"][0]["NAME"], "pipeline-0")
    self.assertEqual(harness.netmon.network_node_pipelines(addr)[0]["NAME"], "pipeline-0")
    self.assertEqual(harness.netmon.network_node_pipeline_info(addr, "pipeline-0")["NAME"], "pipeline-0")

  def test_register_heartbeat_does_not_mutate_caller_owned_compressed_payload(self):
    harness = _NetmonHarness()
    addr = "0xNODE_INPUT"
    hb = harness.make_heartbeat(addr, 0, 0, payload_scale=4)
    original = json.loads(json.dumps(hb))

    harness.netmon.register_heartbeat(addr, hb)

    self.assertEqual(hb, original)
    self.assertIn(ct.HB.ENCODED_DATA, hb)

  def test_compressed_and_uncompressed_heartbeats_store_equivalent_latest_data(self):
    compressed_harness = _NetmonHarness()
    plain_harness = _NetmonHarness()
    addr = "0xNODE_EQUIV"
    compressed = compressed_harness.make_heartbeat(addr, 0, 0, payload_scale=4)
    plain = plain_harness.make_heartbeat_body(addr, 0, 0, payload_scale=4)
    plain[ct.PAYLOAD_DATA.EE_EVENT_TYPE] = ct.HEARTBEAT

    compressed_harness.netmon.register_heartbeat(addr, dict(compressed))
    plain_harness.netmon.register_heartbeat(addr, dict(plain))

    compressed_latest = dict(compressed_harness.netmon.network_node_last_heartbeat(addr))
    plain_latest = dict(plain_harness.netmon.network_node_last_heartbeat(addr))
    compressed_latest.pop(ct.HB.RECEIVED_TIME, None)
    plain_latest.pop(ct.HB.RECEIVED_TIME, None)
    self.assertEqual(compressed_latest, plain_latest)

  def test_pipeline_cache_and_epoch_registration_survive_compressed_decode(self):
    harness = _NetmonHarness()
    addr = "0xNODE_PIPELINES"
    hb = harness.make_heartbeat(addr, 0, 0, payload_scale=4)

    harness.netmon.register_heartbeat(addr, dict(hb))

    known = harness.netmon.network_known_nodes()[addr]
    self.assertGreater(len(known["pipelines"]), 0)
    self.assertGreater(len(known["plugins_statuses"]), 0)
    self.assertEqual(harness.epoch_manager.calls, 1)
    epoch_data = harness.epoch_manager.last_data_by_addr[addr]
    self.assertEqual(epoch_data[ct.PAYLOAD_DATA.EE_TIMESTAMP], hb[ct.PAYLOAD_DATA.EE_TIMESTAMP])
    self.assertEqual(epoch_data[ct.PAYLOAD_DATA.EE_TIMEZONE], hb[ct.PAYLOAD_DATA.EE_TIMEZONE])

  def test_direct_pipeline_registration_covers_heartbeats_without_pipelines(self):
    harness = _NetmonHarness()
    addr = "0xNODE_DIRECT_PIPELINES"
    body = harness.make_heartbeat_body(addr, 0, 0, payload_scale=4)
    pipelines = body.pop(ct.HB.PIPELINES)
    plugins_statuses = body.pop(ct.HB.ACTIVE_PLUGINS)
    hb = {
      ct.HB.ENCODED_DATA: harness.log.compress_text(json.dumps(body)),
      ct.EE_ID: body[ct.EE_ID],
      ct.HB.EE_ADDR: addr,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: body[ct.PAYLOAD_DATA.EE_TIMESTAMP],
      ct.PAYLOAD_DATA.EE_TIMEZONE: body[ct.PAYLOAD_DATA.EE_TIMEZONE],
      ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
    }

    harness.netmon.register_heartbeat(addr, dict(hb))
    self.assertEqual(harness.netmon.network_node_pipelines(addr), [])

    harness.netmon.register_node_pipelines(
      addr,
      pipelines,
      plugins_statuses=plugins_statuses,
    )
    known = harness.netmon.network_known_nodes()[addr]
    self.assertEqual(known["pipelines"][0]["NAME"], "pipeline-0")
    self.assertEqual(known["plugins_statuses"][0]["STREAM_NAME"], "pipeline-0")

    known["pipelines"][0]["NAME"] = "MUTATED_DIRECT"
    returned_pipelines = harness.netmon.network_node_pipelines(addr)
    returned_pipelines[0]["NAME"] = "MUTATED_RETURNED"

    self.assertEqual(harness.netmon.network_known_nodes()[addr]["pipelines"][0]["NAME"], "pipeline-0")
    self.assertEqual(harness.netmon.network_node_pipelines(addr)[0]["NAME"], "pipeline-0")

  def test_epoch_registration_uses_decoded_compressed_heartbeat_view(self):
    harness = _NetmonHarness()
    addr = "0xNODE_EPOCH_DECODED"
    body = harness.make_heartbeat_body(addr, 0, 5, payload_scale=4)
    envelope_ts = datetime(1999, 1, 1, 0, 0, 0).strftime(ct.HB.TIMESTAMP_FORMAT)
    hb = {
      ct.HB.ENCODED_DATA: harness.log.compress_text(json.dumps(body)),
      ct.EE_ID: "envelope-node-id",
      ct.HB.EE_ADDR: addr,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: envelope_ts,
      ct.PAYLOAD_DATA.EE_TIMEZONE: "UTC+0",
      ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
    }

    harness.netmon.register_heartbeat(addr, dict(hb))

    latest = harness.netmon.network_node_last_heartbeat(addr)
    epoch_data = harness.epoch_manager.last_data_by_addr[addr]
    self.assertEqual(latest[ct.PAYLOAD_DATA.EE_TIMESTAMP], body[ct.PAYLOAD_DATA.EE_TIMESTAMP])
    self.assertEqual(epoch_data[ct.PAYLOAD_DATA.EE_TIMESTAMP], body[ct.PAYLOAD_DATA.EE_TIMESTAMP])
    self.assertEqual(epoch_data[ct.PAYLOAD_DATA.EE_TIMEZONE], body[ct.PAYLOAD_DATA.EE_TIMEZONE])
    self.assertEqual(epoch_data[ct.EE_ID], body[ct.EE_ID])
    self.assertNotEqual(epoch_data[ct.PAYLOAD_DATA.EE_TIMESTAMP], envelope_ts)

  def test_history_and_status_readers_keep_required_fields_after_compaction(self):
    harness = _NetmonHarness()
    addr = "0xNODE_HISTORY"
    first = harness.make_heartbeat(addr, 0, 0, payload_scale=4)
    second = harness.make_heartbeat(addr, 0, 1, payload_scale=4)
    third = harness.make_heartbeat(addr, 0, 2, payload_scale=4)

    harness.netmon.register_heartbeat(addr, dict(first))
    harness.netmon.register_heartbeat(addr, dict(second))
    harness.netmon.register_heartbeat(addr, dict(third))

    # register_heartbeat stamps RECEIVED_TIME using the local clock. Readers
    # prefer that field over remote heartbeat time, so use the same clock domain
    # here instead of the synthetic 2026 heartbeat timestamps.
    dt_now = datetime.now()
    history = harness.netmon.network_node_history(
      addr=addr,
      minutes=2,
      dt_now=dt_now,
      reverse_order=True,
      hb_step=1,
    )
    status = harness.netmon.network_node_status(addr=addr, dt_now=dt_now)

    self.assertEqual(len(history["cpu_hist"]), 3)
    self.assertEqual(len(history["mem_avail_hist"]), 3)
    self.assertEqual(len(history["gpu_load_hist"]), 3)
    self.assertEqual(len(history["max_temperature"]), 3)
    self.assertEqual(status["working"], ct.DEVICE_STATUS_ONLINE)
    self.assertEqual(status["main_loop_freq"], 8.33)

  def test_history_reader_handles_cpu_only_nodes(self):
    harness = _NetmonHarness()
    addr = "0xNODE_CPU_ONLY"
    body = harness.make_heartbeat_body(addr, 0, 0, payload_scale=4)
    body[ct.HB.GPUS] = []
    body.pop(ct.HB.GPU_INFO, None)
    body.pop(ct.HB.DEFAULT_CUDA, None)
    hb = {
      ct.HB.ENCODED_DATA: harness.log.compress_text(json.dumps(body)),
      ct.EE_ID: body[ct.EE_ID],
      ct.HB.EE_ADDR: addr,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: body[ct.PAYLOAD_DATA.EE_TIMESTAMP],
      ct.PAYLOAD_DATA.EE_TIMEZONE: body[ct.PAYLOAD_DATA.EE_TIMEZONE],
      ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
    }

    harness.netmon.register_heartbeat(addr, hb)

    history = harness.netmon.network_node_history(
      addr=addr,
      minutes=2,
      dt_now=datetime.now(),
      reverse_order=True,
      hb_step=1,
    )

    self.assertEqual(len(history["cpu_hist"]), 1)
    self.assertEqual(history["gpu_load_hist"], [])
    self.assertEqual(history["gpu_mem_avail_hist"], [])
    self.assertIsNone(history["gpu_temp_max_allowed"])

  def test_history_reader_keeps_direct_history_for_prefixed_addresses(self):
    harness = _NetmonHarness()
    addr = "0xai_NODE_PREFIXED"
    hb = harness.make_heartbeat(addr, 0, 0, payload_scale=4)

    harness.netmon.register_heartbeat(addr, hb)

    history = harness.netmon.network_node_history(
      addr=addr,
      minutes=2,
      dt_now=datetime.now(),
      reverse_order=True,
      hb_step=1,
    )

    self.assertEqual(len(history["cpu_hist"]), 1)
    self.assertEqual(history["timestamps"], ["2026-01-01 00:00:00"])


if __name__ == "__main__":
  unittest.main()
