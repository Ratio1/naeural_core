import os
import sys
import threading
import time
from collections import deque
from datetime import datetime as dt
from copy import deepcopy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from naeural_core import Logger
from naeural_core.constants import HB, PAYLOAD_DATA
from naeural_core.main.net_mon import NetworkMonitor, NETMON_MUTEX, UNUSEFULL_HB_KEYS


class DummyEpochManager:
  def register_data(self, addr, data):
    return


class UnsafeNetworkMonitor(NetworkMonitor):
  """
  Reintroduce the old in-place mutation behavior to reproduce the race.
  """
  def unsafe_register_heartbeat(self, addr, data, sleep_between=0.0005):
    # mimic NetworkMonitor.register_heartbeat + old __register_heartbeat body
    data[HB.RECEIVED_TIME] = dt.now().strftime(HB.TIMESTAMP_FORMAT)

    if HB.ENCODED_DATA in data:
      str_data = data.pop(HB.ENCODED_DATA)
      dct_hb = self.log.decompress_text(str_data)
      data = {
        **data,
        **dct_hb,
      }

    __addr_no_prefix = self._NetworkMonitor__remove_address_prefix(addr)

    for key_to_delete in UNUSEFULL_HB_KEYS:
      data.pop(key_to_delete, None)

    with self.log.managed_lock_resource(NETMON_MUTEX):
      if __addr_no_prefix not in self._NetworkMonitor__network_heartbeats:
        self._NetworkMonitor__network_heartbeats[__addr_no_prefix] = deque(maxlen=self.HB_HISTORY)
      # append shared dict
      self._NetworkMonitor__network_heartbeats[__addr_no_prefix].append(data)
      # widen race window
      time.sleep(sleep_between)
      # mutate stored dict in place (old behavior)
      self._NetworkMonitor__maybe_register_hb_pipelines(addr, data)
      # mutate previous hb in place (old behavior)
      if len(self._NetworkMonitor__network_heartbeats[__addr_no_prefix]) >= 2:
        self._NetworkMonitor__pop_repeating_info_from_heartbeat(
          self._NetworkMonitor__network_heartbeats[__addr_no_prefix][-2]
        )


def _make_logger():
  return Logger(
    lib_name="NMON_TEST",
    base_folder=".",
    app_folder="_local_cache",
    no_folders_no_save=True,
    max_lines=50,
    DEBUG=False,
  )


def _make_hb(addr, extra_keys=500):
  hb = {
    HB.EE_ADDR: addr,
    HB.CURRENT_TIME: dt.now().strftime(HB.TIMESTAMP_FORMAT),
    PAYLOAD_DATA.EE_TIMESTAMP: dt.utcnow().strftime(HB.TIMESTAMP_FORMAT),
    PAYLOAD_DATA.EE_TIMEZONE: "UTC",
    f"{HB.PREFIX_EE_NODETAG}DC": "TEST_DC",
    f"{HB.PREFIX_EE_NODETAG}REG": "EU",
  }
  # inflate dict to slow deepcopy and increase race probability
  for i in range(extra_keys):
    hb[f"K{i}"] = i
  return hb


def _run_race(netmon, addr, writer_fn, duration_sec=2.0):
  stop = threading.Event()
  error = {"exc": None}

  def reader():
    while not stop.is_set():
      try:
        netmon.get_network_node_tags(addr)
      except Exception as exc:  # capture RuntimeError
        error["exc"] = exc
        stop.set()
        return

  def writer():
    while not stop.is_set():
      hb = _make_hb(addr)
      writer_fn(addr, hb)

  t_r = threading.Thread(target=reader, daemon=True)
  t_w = threading.Thread(target=writer, daemon=True)
  t_r.start()
  t_w.start()

  stop.wait(duration_sec)
  stop.set()
  t_r.join(timeout=1.0)
  t_w.join(timeout=1.0)

  return error["exc"]


def test_race_repro_old_behavior():
  log = _make_logger()
  netmon = UnsafeNetworkMonitor(
    log=log,
    node_name="test_node",
    node_addr="aixp_test_node",
    epoch_manager=DummyEpochManager(),
  )
  addr = "aixp_test_node"

  # seed with one heartbeat
  netmon.unsafe_register_heartbeat(addr, _make_hb(addr))

  exc = _run_race(
    netmon=netmon,
    addr=addr,
    writer_fn=netmon.unsafe_register_heartbeat,
    duration_sec=2.0,
  )

  if exc is None:
    raise AssertionError(
      "Expected RuntimeError from in-place mutation race, but none occurred."
    )
  if "dictionary changed size during iteration" not in str(exc):
    raise AssertionError(f"Unexpected exception: {exc}")


def test_race_fixed_behavior():
  log = _make_logger()
  netmon = NetworkMonitor(
    log=log,
    node_name="test_node",
    node_addr="aixp_test_node",
    epoch_manager=DummyEpochManager(),
  )
  addr = "aixp_test_node"

  # seed with one heartbeat
  netmon.register_heartbeat(addr, _make_hb(addr))

  exc = _run_race(
    netmon=netmon,
    addr=addr,
    writer_fn=netmon.register_heartbeat,
    duration_sec=2.0,
  )

  if exc is not None:
    raise AssertionError(f"Did not expect exception with fixed behavior: {exc}")


def main():
  # Ensure we are importing from the local workspace, not a site-packages install.
  import naeural_core as _nc
  if not os.path.abspath(_nc.__file__).startswith(REPO_ROOT):
    raise RuntimeError(f"naeural_core import is not local: {_nc.__file__}")
  tests = [
    test_race_repro_old_behavior,
    test_race_fixed_behavior,
  ]
  failures = 0
  for test in tests:
    name = test.__name__
    try:
      test()
      print(f"PASS: {name}")
    except Exception as exc:
      failures += 1
      print(f"FAIL: {name}: {exc}")
  if failures:
    raise SystemExit(1)


if __name__ == "__main__":
  main()
