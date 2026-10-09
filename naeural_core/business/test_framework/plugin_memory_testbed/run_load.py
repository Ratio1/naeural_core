"""Opt-in GiB-scale isolated regression; deliberately absent from unittest discovery."""

import argparse
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

from memory_load import (
  MAX_SCAN_SECONDS, MIB, MIN_OWNED_BYTES, PRESSURE_FRACTION, SHARED_BYTES,
  RetainedLoad, memory_snapshot, resident_buffer,
)


def main():
  """Exercise production diagnostic bodies and the real SDK under resident load.

  Returns
  -------
  None
    Writes accounting, traversal, pressure and recovery evidence or raises.
  """
  parser = argparse.ArgumentParser()
  parser.add_argument("--report", type=Path, required=True)
  arguments = parser.parse_args()
  fixture = Path(__file__).resolve().parent
  sys.path.insert(0, str(fixture.parent))
  from test_plugin_memory_scan import _ForbiddenScan, _make_plugin

  plugin = _make_plugin()
  plugin.retained_load = RetainedLoad()
  shared = {"traversal_guard": _ForbiddenScan()}
  plugin._BasePluginExecutor__global_shmem = shared
  report = {"passed": False, "samples": [], "latency_violations": []}
  try:
    baseline = plugin.get_plugin_used_memory()
    initial = memory_snapshot()
    shared["external_buffer"] = resident_buffer(SHARED_BYTES)
    assert plugin.get_plugin_used_memory() == baseline, "Shared growth changed the estimate"
    for _ in range(2):
      plugin.upstream_inputs_deque.append({"data": resident_buffer(32 * MIB)})
    queue_bytes = plugin.get_plugin_queue_memory()
    assert queue_bytes >= 64 * MIB
    assert plugin.get_plugin_used_memory() == baseline, "Queue leaked into plugin accounting"

    while True:
      payload = plugin.retained_load.grow()
      started = time.perf_counter()
      estimate = plugin.get_plugin_used_memory()
      elapsed = time.perf_counter() - started
      error = abs(estimate - baseline - payload) / payload
      snapshot = memory_snapshot()
      assert error < 0.05, "Owned allocation was hidden or grossly overcounted"
      report["samples"].append({
        "owned_payload_bytes": payload, "records": len(plugin.retained_load.records),
        "estimate_bytes": estimate, "relative_error": error,
        "scan_seconds": elapsed, "memory": snapshot,
      })
      if elapsed >= MAX_SCAN_SECONDS:
        report["latency_violations"].append({"phase": "growth", "seconds": elapsed,
          "owned_payload_bytes": payload, "limit_seconds": MAX_SCAN_SECONDS})
      if payload >= MIN_OWNED_BYTES and snapshot["utilization"] >= PRESSURE_FRACTION:
        break

    peak = memory_snapshot()
    assert peak["rss_bytes"] - initial["rss_bytes"] >= 0.90 * payload
    assert peak["anonymous_bytes"] - initial["anonymous_bytes"] >= 0.90 * payload
    pressure_scans = []
    for _ in range(3):
      started = time.perf_counter()
      size, tree, _ = plugin.get_plugin_used_memory(return_tree=True)
      elapsed = time.perf_counter() - started
      assert abs(size - baseline - payload) / payload < 0.05
      assert not any("__global_shmem" in row["NAME"] or "upstream_inputs_deque" in row["NAME"] for row in tree)
      assert len(tree) >= 3 * len(plugin.retained_load.records), "Nested graph was not traversed"
      pressure_scans.append({"seconds": elapsed, "tree_objects": len(tree), "memory": memory_snapshot()})
      if elapsed >= MAX_SCAN_SECONDS:
        report["latency_violations"].append({"phase": "pressure", "seconds": elapsed,
          "owned_payload_bytes": payload, "limit_seconds": MAX_SCAN_SECONDS})
      del tree
    plugin.retained_load.clear()
    plugin.upstream_inputs_deque.clear()
    shared.pop("external_buffer")
    gc.collect()
    recovered_estimate = plugin.get_plugin_used_memory()
    recovered = memory_snapshot()
    reclaimed = peak["rss_bytes"] - recovered["rss_bytes"]
    assert abs(recovered_estimate - baseline) < 65536, "Owned estimate did not recover"
    assert reclaimed >= 0.8 * payload, "Resident load did not recover"
    assert recovered["oom"] == initial["oom"] and recovered["oom_kill"] == initial["oom_kill"]
    source = fixture.parents[1] / "base/base_plugin_biz.py"
    report.update(
      baseline_bytes=baseline, shared_growth_bytes=SHARED_BYTES,
      shared_estimate_delta=0, queue_bytes=queue_bytes, peak=peak,
      pressure_scans=pressure_scans, recovered=recovered,
      recovered_estimate_bytes=recovered_estimate, reclaimed_rss_bytes=reclaimed,
      source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
      python=sys.version, ratio1=importlib.metadata.version("ratio1"),
    )
    assert not report["latency_violations"], "Diagnostic exceeded two seconds; see retained timing violations"
    report["passed"] = True
  except Exception as error:
    report["failure"] = {"type": type(error).__name__, "message": str(error)}
    raise
  finally:
    plugin.retained_load.clear()
    plugin.upstream_inputs_deque.clear()
    shared.clear()
    arguments.report.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
  main()
