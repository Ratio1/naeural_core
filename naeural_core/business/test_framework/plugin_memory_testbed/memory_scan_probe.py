"""Test-only business plugin for full-runtime memory diagnostic validation."""

import gc
import json
import os
from copy import deepcopy
from pathlib import Path
import threading
import time

from naeural_core.business.base import BasePluginExecutor
from memory_load import (
  MAX_SCAN_SECONDS, MIN_OWNED_BYTES, PRESSURE_FRACTION, SHARED_BYTES,
  RetainedLoad, memory_snapshot, resident_buffer,
)


_FORBIDDEN_CLASSES = {
  "module", "BusinessManager", "FileSystemManager", "ConfigManager",
  "CommunicationManager", "R1FSEngine", "BCWrapper", "DefaultBlockEngine",
  "SharedMemoryManager",
}


_CONFIG = {
  **BasePluginExecutor.CONFIG,
  "AI_ENGINE": [],
  "ALLOW_EMPTY_INPUTS": False,
  "RUN_WITHOUT_IMAGE": True,
  "MAX_INPUTS_QUEUE_SIZE": 2,
  "PLUGIN_LOOP_RESOLUTION": 2,
  "PROCESS_DELAY": 0,
}


class _TraversalGuard:
  """Stop a regressed scan before it can walk the real node-wide object graph."""

  def __sizeof__(self):
    raise AssertionError("Shared runtime reference visited by plugin memory scan")


class MemoryScanProbePlugin(BasePluginExecutor):
  """Force queue pressure and inspect the production scanner in a live plugin."""

  CONFIG = _CONFIG

  def on_init(self):
    self._probe_processes = 0
    self._probe_main_scans = 0
    self._probe_first_overflow = None
    self._probe_max_scan_seconds = 0.0
    self._probe_owned_buffer = bytearray(256 * 1024)
    self._probe_checks = None
    self._probe_load_enabled = os.environ.get("R1_MEMORY_SCENARIO") == "memory-growth"
    self._probe_retained_load = RetainedLoad()
    self._probe_main_scan_samples = []
    self._probe_main_delivery_samples = []
    self._probe_pressure = {
      "phase": "warmup", "complete": False, "growth_samples": [],
      "diagnostics": [], "hold_samples": [], "started": None, "released": None,
      "latency_violations": [],
    }
    return

  def get_plugin_used_memory(self, return_tree=False):
    """Measure real overflow calls while retaining the production implementation.

    Parameters
    ----------
    return_tree : bool, optional
      Forward the production return format without conversion.

    Returns
    -------
    int or tuple
      The unchanged production scanner result.
    """
    started = time.perf_counter()
    main_thread = threading.current_thread() is threading.main_thread()
    pressure_memory = memory_snapshot() if main_thread and self._probe_load_enabled else None
    result = super().get_plugin_used_memory(return_tree=return_tree)
    elapsed = time.perf_counter() - started
    if main_thread:
      self._probe_main_scans += 1
      self._probe_first_overflow = self._probe_first_overflow or time.time()
      self._probe_max_scan_seconds = max(
        self._probe_max_scan_seconds, elapsed,
      )
      self._probe_main_scan_samples.append({
        "seconds": elapsed, "estimate_bytes": result[0] if return_tree else result,
        "owned_payload_bytes": self._probe_retained_load.payload_bytes,
        "phase": self._probe_pressure["phase"], "memory": pressure_memory,
      })
    return result

  def add_inputs(self, inputs):
    """Inject queue pressure at the real business-manager delivery boundary.

    Parameters
    ----------
    inputs : dict
      Actual capture inputs supplied by the production business manager.

    Returns
    -------
    None
      Delegates to the production overflow diagnostic and enqueue logic.

    Notes
    -----
    CaptureManager normally stops delivery before overflow. Refill this test
    queue once after its availability check to model pressure arriving during
    handoff; all diagnostic code still executes in the real main thread.
    """
    pressure_ready = (not getattr(self, "_probe_load_enabled", False)
      or getattr(self, "_probe_pressure", {}).get("phase") == "hold")
    if inputs and getattr(self, "_probe_checks", None) and self._probe_main_scans == 0 and pressure_ready:
      queue = self.upstream_inputs_deque
      while len(queue) < self.cfg_max_inputs_queue_size:
        queue.append(deepcopy(inputs))
    before_scans = getattr(self, "_probe_main_scans", 0)
    started = time.perf_counter()
    result = super().add_inputs(inputs)
    elapsed = time.perf_counter() - started
    if getattr(self, "_probe_main_scans", 0) > before_scans:
      self._probe_main_delivery_samples.append({
        "seconds": elapsed, "phase": self._probe_pressure["phase"],
        "owned_payload_bytes": self._probe_retained_load.payload_bytes,
      })
    return result

  def _check_scan_boundary(self):
    """Validate real stored references and owned/shared growth in a live node.

    Returns
    -------
    dict
      Sanitized accounting and tree evidence; raises on a broken boundary.
    """
    self._maybe_init_file_system_manager()
    expected = (
      "_BasePluginExecutor__global_shmem", "_BasePluginExecutor__plugins_shmem",
      "_BasePluginExecutor__blockchain_manager", "_BasePluginExecutor__bc",
      "_BasePluginExecutor__r1fs", "_file_system_manager",
      "plugins_shared_mem",
    )
    assert all(attribute in vars(self) for attribute in expected)
    guards = []
    guard_key = "_plugin_memory_test_guard"
    try:
      for attribute in expected:
        shared = vars(self)[attribute]
        mapping = shared if isinstance(shared, dict) else vars(shared)
        if any(mapping is previous for previous in guards):
          continue
        assert guard_key not in mapping
        mapping[guard_key] = _TraversalGuard()
        guards.append(mapping)
      size, tree, _ = self.get_plugin_used_memory(return_tree=True)
    finally:
      for mapping in guards:
        mapping.pop(guard_key, None)
    leaked = [row["NAME"] for row in tree if row["CLASS_NAME"] in _FORBIDDEN_CLASSES]
    assert not leaked, "Shared objects in plugin scan: " + repr(leaked[:5])
    assert any("_probe_owned_buffer" in row["NAME"] for row in tree)
    assert not any("__upstream_inputs_deque" in row["NAME"] for row in tree)
    assert not any(row["NAME"] in {"<root>." + attr for attr in expected} for row in tree)

    before = self.get_plugin_used_memory()
    try:
      self.global_shmem["memory_test_external_buffer"] = bytearray(16 * 1024 * 1024)
      shared_delta = self.get_plugin_used_memory() - before
    finally:
      self.global_shmem.pop("memory_test_external_buffer", None)
    assert abs(shared_delta) < 65536, "Shared growth contaminated the plugin estimate"
    before_owned = self.get_plugin_used_memory()
    self._probe_owned_buffer = bytearray(2 * 1024 * 1024)
    owned_delta = self.get_plugin_used_memory() - before_owned
    assert owned_delta >= (2 * 1024 * 1024 - 256 * 1024), "Owned growth was hidden"
    return {
      "passed": True, "initial_bytes": size, "tree_objects": len(tree),
      "shared_growth_bytes": 16 * 1024 * 1024, "shared_estimate_delta": shared_delta,
      "owned_estimate_delta": owned_delta, "forbidden_objects": leaked,
      "business_cache_entries": len(self.global_shmem["business_manager"].plugin_locations_cache),
      "actual_shared_attributes": list(expected),
    }

  def _pressure_diagnostic(self):
    """Inspect the full owned graph at measured container memory pressure.

    Returns
    -------
    None
      Stores only small accounting/timing summaries, never the scanner tree.
    """
    started = time.perf_counter()
    estimate, tree, _ = self.get_plugin_used_memory(return_tree=True)
    elapsed = time.perf_counter() - started
    load = self._probe_retained_load
    payload = load.payload_bytes
    baseline = self._probe_pressure["baseline_estimate_bytes"]
    error = abs(estimate - baseline - payload) / payload
    assert error < 0.05, "Pressure diagnostic lost owned-state accounting"
    assert len(tree) >= 3 * len(load.records), "Nested cache graph was not scanned"
    assert not any(row["CLASS_NAME"] in _FORBIDDEN_CLASSES for row in tree)
    assert not any("upstream_inputs_deque" in row["NAME"] for row in tree)
    self._probe_pressure["diagnostics"].append({
      "timestamp": time.time(), "seconds": elapsed, "estimate_bytes": estimate,
      "owned_payload_bytes": payload, "records": len(load.records),
      "tree_objects": len(tree), "relative_error": error, "memory": memory_snapshot(),
    })
    if elapsed >= MAX_SCAN_SECONDS:
      self._probe_pressure["latency_violations"].append({"phase": "pressure", "seconds": elapsed,
        "owned_payload_bytes": payload, "limit_seconds": MAX_SCAN_SECONDS})

  def _run_pressure_step(self):
    """Grow, hold and release genuine resident memory over processing cycles.

    Returns
    -------
    None
      Advances the test state using kernel metrics, with bounded allocations.

    Notes
    -----
    The native RAM monitor measures host memory. This test deliberately triggers
    the unmodified plugin diagnostic from actual cgroup pressure instead of
    exhausting the host or falsifying that monitor's readings.
    """
    pressure = self._probe_pressure
    load = self._probe_retained_load
    if pressure["phase"] == "warmup" and self._probe_processes >= 8:
      pressure["initial_memory"] = memory_snapshot()
      before_shared = self.get_plugin_used_memory()
      self.global_shmem["memory_test_pressure_buffer"] = resident_buffer(SHARED_BYTES)
      shared_delta = self.get_plugin_used_memory() - before_shared
      assert abs(shared_delta) < 65536, "Large shared growth contaminated accounting"
      pressure["shared_growth_bytes"] = SHARED_BYTES
      pressure["shared_estimate_delta"] = shared_delta
      pressure["baseline_estimate_bytes"] = self.get_plugin_used_memory()
      pressure["phase"] = "growing"

    if pressure["phase"] == "growing":
      payload = load.grow()
      started = time.perf_counter()
      estimate = self.get_plugin_used_memory()
      elapsed = time.perf_counter() - started
      snapshot = memory_snapshot()
      error = abs(estimate - pressure["baseline_estimate_bytes"] - payload) / payload
      assert error < 0.05, "Growing owned state was hidden or grossly overcounted"
      pressure["growth_samples"].append({
        "owned_payload_bytes": payload, "records": len(load.records),
        "estimate_bytes": estimate, "relative_error": error,
        "scan_seconds": elapsed, "memory": snapshot,
      })
      if elapsed >= MAX_SCAN_SECONDS:
        pressure["latency_violations"].append({"phase": "growth", "seconds": elapsed,
          "owned_payload_bytes": payload, "limit_seconds": MAX_SCAN_SECONDS})
      if payload >= MIN_OWNED_BYTES and snapshot["utilization"] >= PRESSURE_FRACTION:
        initial = pressure["initial_memory"]
        assert snapshot["rss_bytes"] - initial["rss_bytes"] >= 0.90 * payload
        assert snapshot["anonymous_bytes"] - initial["anonymous_bytes"] >= 0.90 * payload
        pressure["started"] = time.time()
        pressure["phase"] = "hold"
        pressure["peak_owned_payload_bytes"] = payload
        self._pressure_diagnostic()
      return

    if pressure["phase"] == "hold":
      snapshot = memory_snapshot()
      assert PRESSURE_FRACTION - 0.02 <= snapshot["utilization"] < 0.95
      pressure["hold_samples"].append(snapshot)
      if time.time() - pressure["diagnostics"][-1]["timestamp"] >= 5:
        self._pressure_diagnostic()
      if (time.time() - pressure["started"] >= 30 and self._probe_main_scans > 0
          and len(pressure["diagnostics"]) >= 3):
        pressure["memory_before_release"] = memory_snapshot()
        load.clear()
        self.global_shmem.pop("memory_test_pressure_buffer")
        gc.collect()
        pressure["released"] = time.time()
        pressure["release_process_count"] = self._probe_processes
        pressure["phase"] = "recovery"
      return

    if pressure["phase"] == "recovery" and self._probe_processes - pressure["release_process_count"] >= 12:
      recovered = memory_snapshot()
      estimate = self.get_plugin_used_memory()
      reclaimed = pressure["memory_before_release"]["rss_bytes"] - recovered["rss_bytes"]
      assert reclaimed >= 0.8 * pressure["peak_owned_payload_bytes"], "Resident state did not recover"
      assert abs(estimate - pressure["baseline_estimate_bytes"]) < 1024 * 1024, "Estimate did not recover"
      initial = pressure["initial_memory"]
      assert recovered["oom"] == initial["oom"] and recovered["oom_kill"] == initial["oom_kill"]
      pressure.update(
        complete=True, phase="complete", recovered_memory=recovered,
        recovered_estimate_bytes=estimate, reclaimed_rss_bytes=reclaimed,
      )

  def _process(self):
    """Consume real capture inputs slowly enough to trigger queue diagnostics.

    Returns
    -------
    None
      Writes only sanitized test evidence; does not publish application data.
    """
    self._probe_processes += 1
    try:
      if self._probe_checks is None:
        self._probe_checks = self._check_scan_boundary()
      if self._probe_load_enabled:
        self._run_pressure_step()
    except Exception as error:
      target = Path(os.environ["R1_MEMORY_EVIDENCE"]) / "failure.json"
      target.write_text(json.dumps({"error_type": type(error).__name__, "error": str(error)}))
      raise
    time.sleep(0.5)
    report = {
      "checks": self._probe_checks, "process_count": self._probe_processes,
      "main_thread_scans": self._probe_main_scans,
      "first_overflow_time": self._probe_first_overflow,
      "max_scan_seconds": self._probe_max_scan_seconds,
      "queue_overflows": self.queue_full_delays_count,
      "lost_inputs": self.lost_inputs_count, "timestamp": time.time(),
      "main_scan_samples": self._probe_main_scan_samples,
      "main_delivery_samples": self._probe_main_delivery_samples,
      "pressure": self._probe_pressure if self._probe_load_enabled else None,
    }
    target = Path(os.environ["R1_MEMORY_EVIDENCE"]) / "probe.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(target)
    return
