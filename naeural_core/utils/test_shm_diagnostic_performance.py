"""Measure always-on diagnostics against the real pre-change disabled path.

Run from the workspace root::

  python3 naeural_core/naeural_core/utils/test_shm_diagnostic_performance.py

The baseline utility and selected methods come from commit ``6d3d888`` with
``EE_SHM_DIAGNOSTICS`` absent. The candidate comes from the working tree. Both
execute their exact manager send, worker receive/send and manager result bodies,
including actual NumPy admission copies and SHM reconstruction. Unrelated runtime
imports/startup are excluded by AST extraction. One in-memory envelope transport
and ordinary NumPy backing storage replace IPC/OS-SHM allocation; no model, GPU,
network, serialization or production prediction performance is measured.

Each case warms both variants and alternates their order in paired rounds. The
44-frame HD case has 243.3 MB of input plus slot storage, far below one GB. The
same 44-frame tiny case exposes absolute diagnostic CPU cost. JSON stdout contains
only aggregate measurements, source hashes and bounded diagnostic counters.
The source guard pins conversion wrappers, placeholders, NumPy binding and
buffer admission/read dependencies. Allocation/shutdown are excluded because
the fixture constructs an instance without startup and supplies NumPy storage;
their lifecycle changes must be verified separately.
"""

import argparse
import ast
import hashlib
import json
import os
import pathlib
import platform
import statistics
import subprocess
import sys
import threading
import time
import traceback
import types
from unittest import mock

import numpy as np


_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
_BASELINE = "6d3d888"
_PATHS = ("utils/shm_diagnostics.py", "utils/system_shared_memory.py",
          "serving/serving_manager.py", "serving/base/base_serving_process.py")
_SM = types.SimpleNamespace(SHM="SHM", COMM="COMM", TYPE="TYPE", DATA="DATA",
                           SERVER_NAME="SERVER_NAME", CMD_ID="CMD_ID", TIMESTAMP="TIMESTAMP",
                           PREDICT="PREDICT", RESULT="RESULT", SERVER="SERVER",
                           COVERED_BY="COVERED_BY")


def _sources(baseline=False):
  """Read one variant without switching branches or importing the runtime.

  Parameters
  ----------
  baseline : bool
      Read the historical committed files rather than the working tree.

  Returns
  -------
  dict
      Relative paths mapped to their complete source text.
  """
  if not baseline:
    return {path: (_ROOT / path).read_text() for path in _PATHS}
  return {path: subprocess.run(
    ["git", "show", _BASELINE + ":naeural_core/" + path], cwd=_REPO,
    check=True, capture_output=True, text=True,
  ).stdout for path in _PATHS}


def _module(name, source):
  """Compile a complete source module into an isolated benchmark namespace.

  Parameters
  ----------
  name, source : str
      Unique module name and original complete source.

  Returns
  -------
  module
      Module whose functions retain the provided source bodies.
  """
  module = types.ModuleType(name)
  exec(compile(source, name, "exec"), module.__dict__)
  return module


def _conversion_sources(source):
  """Select exact source dependencies executed by the conversion fixture.

  Parameters
  ----------
  source : str
      Complete shared-memory utility snapshot.

  Returns
  -------
  dict
      Qualified dependency names mapped to exact original source segments.

  Notes
  -----
  Both variants bypass initialization/shutdown and mock ``_add_warning``.
  Diagnostic ``array_observation`` intentionally differs between variants;
  its import binding is pinned here and its complete utility is hashed in the
  report. Any conversion/dependency body change requires baseline review.
  """
  tree = ast.parse(source)
  functions = ("ndarray_to_shm", "replace_ndarray_with_shm",
               "shm_to_ndarray", "replace_shm_with_ndarray")
  buffer_methods = ("check_data", "first_buffer_position", "add_data_to_buffer", "read_buffer_data")
  selected = {}
  for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in functions:
      assert not node.decorator_list, "conversion decorators require baseline review"
      selected[node.name] = ast.get_source_segment(source, node)
    elif isinstance(node, ast.ClassDef) and node.name == "NumpySharedMemoryPlaceholder":
      selected[node.name] = ast.get_source_segment(source, node)
    elif isinstance(node, ast.ClassDef) and node.name == "NumpySharedMemory":
      for method in node.body:
        if isinstance(method, ast.FunctionDef) and method.name in buffer_methods:
          assert not method.decorator_list, "buffer conversion decorators require baseline review"
          selected[node.name + "." + method.name] = ast.get_source_segment(source, method)
    elif isinstance(node, ast.Import) and any(alias.name == "numpy" for alias in node.names):
      selected["numpy_import"] = ast.get_source_segment(source, node)
    elif isinstance(node, ast.ImportFrom) and node.module == "naeural_core.utils.shm_diagnostics":
      selected["diagnostic_import"] = ast.get_source_segment(source, node)
  expected = set(functions) | {"NumpySharedMemory." + name for name in buffer_methods} | {
    "NumpySharedMemoryPlaceholder", "numpy_import", "diagnostic_import",
  }
  assert set(selected) == expected, "conversion dependency missing; baseline comparison requires review"
  return selected


def _methods(source, class_name, names, namespace):
  """Extract exact method bodies while preserving private-name mangling.

  Parameters
  ----------
  source, class_name : str
      Complete original source and owning class name.
  names : tuple of str
      Existing methods required for the measured transport path.
  namespace : dict
      Explicit globals replacing only unrelated imports/startup.

  Returns
  -------
  type
      Original class name with selected method bodies and no runtime bases.
  """
  tree = ast.parse(source)
  original = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == class_name)
  methods = [node for node in original.body
             if isinstance(node, ast.FunctionDef) and node.name in names]
  assert {node.name for node in methods} == set(names)
  original.body, original.bases = methods, []
  original.keywords, original.decorator_list = [], []
  exec(compile(ast.Module(body=[original], type_ignores=[]), class_name, "exec"), namespace)
  return namespace[class_name]


class _Log:
  """Keep scalar diagnostic output; production timer calls are identical no-ops."""

  def __init__(self, blocked=False):
    """Initialize optional blocking output without blocking timer operations."""
    self.records = []
    self.entered, self.release = threading.Event(), threading.Event()
    self.blocked = blocked

  def P(self, message, **kwargs):
    """Optionally block the diagnostic sink until the caller releases it."""
    self.entered.set()
    if self.blocked:
      self.release.wait()
    assert message.startswith("[SHM_DIAG] "), "unexpected product log on healthy path"
    self.records.append(json.loads(message[len("[SHM_DIAG] "):]))

  def start_timer(self, name):
    """Exclude production timer implementation equally from both variants."""

  def stop_timer(self, name):
    """Exclude production timer implementation equally from both variants."""


class _Comm:
  """One-envelope transport fixture; no IPC serialization cost is claimed."""

  def __init__(self):
    """Initialize an empty in-memory envelope slot."""
    self.payload = None

  def send(self, payload):
    """Retain the actual envelope emitted by the extracted sender."""
    assert self.payload is None
    self.payload = payload

  def poll(self, wait):
    """Expose availability while retaining the production method signature."""
    return self.payload is not None

  def recv(self):
    """Transfer the original envelope to the extracted receiver unchanged."""
    assert self.payload is not None
    payload, self.payload = self.payload, None
    return payload


def _variant(label, sources, blocked=False):
  """Load exact utility/conversion/method sources with a process-local logger.

  Parameters
  ----------
  label : str
      Benchmark variant identity.
  sources : dict
      Complete source snapshot for this variant.
  blocked : bool
      Hold diagnostic log I/O during the independent nonblocking probe.

  Returns
  -------
  SimpleNamespace
      Utility, original SHM class and exact manager/worker method classes.
  """
  # Removing the legacy key before importing the OLD utility proves that its
  # actual disabled path is the baseline; the candidate has no activation gate.
  with mock.patch.dict(os.environ):
    os.environ.pop("EE_SHM_DIAGNOSTICS", None)
    diag = _module("shm_diag_benchmark_" + label, sources["utils/shm_diagnostics.py"])
  package = types.ModuleType("naeural_core")
  package.DecentrAIObject = object
  with mock.patch.dict(sys.modules, {"naeural_core": package,
                                    "naeural_core.utils.shm_diagnostics": diag}):
    shm = _module("shm_benchmark_" + label, sources["utils/system_shared_memory.py"])
  env = {"shm_diag": diag, "SMConst": _SM, "time": time.time,
         "sleep": lambda _: None, "traceback": traceback,
         "replace_ndarray_with_shm": shm.replace_ndarray_with_shm,
         "replace_shm_with_ndarray": shm.replace_shm_with_ndarray}
  manager = _methods(sources["serving/serving_manager.py"], "ServingManager",
                     ("_send_message", "_wait_for_comm_message", "get_diagnostic_server_name"),
                     dict(env))
  worker = _methods(sources["serving/base/base_serving_process.py"], "ModelServingProcess",
                    ("_get_message", "_send_message"), dict(env))
  log = _Log(blocked)
  if hasattr(diag, "enabled"):
    assert not diag.enabled(), "baseline diagnostics must actually be disabled"
  else:
    diag.start(log)
  return types.SimpleNamespace(diag=diag, shm=shm, manager=manager, worker=worker, log=log)


def _fixture(variant, storage):
  """Configure extracted manager/worker instances against shared NumPy storage.

  Parameters
  ----------
  variant : SimpleNamespace
      Loaded source snapshot.
  storage : ndarray
      Shared backing storage reused across variants to bound memory usage.

  Returns
  -------
  tuple
      Manager, worker and original SHM buffer instance.
  """
  shm = object.__new__(variant.shm.NumpySharedMemory)
  shm.initialized, shm.is_buffer = True, True
  shm.maxlen, shm.shape_offset, shm._max_element_size = storage.shape[0], 10, storage.shape[1]
  shm._np_mem, shm._np_mem_additional = storage, np.zeros((storage.shape[0], 11), dtype=np.int32)
  shm._init_np_shape, shm._mem_name, shm.log = (storage.shape[1],), "synthetic-benchmark", variant.log
  shm._add_warning = lambda _: None
  comm = _Comm()
  manager, worker = variant.manager(), variant.worker()
  manager.log, manager.full_debug, manager.P = variant.log, False, variant.log.P
  manager.get_server_name, manager.show_servers = lambda name: name.upper(), lambda: None
  manager._servers = {"DETECTOR": {"SHM": shm, "COMM": comm, "SERVER": worker}}
  manager._ServingManager__cmd_count = 0
  worker.log, worker._full_debug, worker.P = variant.log, False, variant.log.P
  worker.server_name, worker.npy_shm, worker.comm_eng = "DETECTOR", shm, comm
  worker.time = time.time
  return manager, worker, shm


def _roundtrip(fixture, request, result):
  """Run actual source methods, including request copies and result observation.

  Parameters
  ----------
  fixture : tuple
      Extracted manager, worker and original buffer.
  request : list
      Distinct synthetic image-entry dictionaries.
  result : dict
      Prebuilt cardinality-correct synthetic response; no inference is executed.

  Returns
  -------
  tuple
      Reconstructed request and manager result for verification outside timing.
  """
  manager, worker, _ = fixture
  manager._send_message("DETECTOR", "PREDICT", request)
  _, received, _, cmd_id, _ = worker._get_message(wait=True)
  worker._send_message("RESULT", result, cmd_id=cmd_id)
  payload = manager._wait_for_comm_message("DETECTOR", max_wait_time=0)
  return received, payload


def _stats(samples):
  """Return aggregate transport latency and serial request/image throughput.

  Parameters
  ----------
  samples : list of float
      Per-roundtrip elapsed seconds from ``perf_counter``.

  Returns
  -------
  dict
      Millisecond latency and requests/images per second.
  """
  throughput = len(samples) / sum(samples)
  return {"samples": len(samples), "median_ms": statistics.median(samples) * 1000,
          "p95_ms": float(np.percentile(samples, 95)) * 1000,
          "requests_per_second": throughput, "images_per_second": throughput * 44}


def _measure(variants, shape, rounds, requests, warmup):
  """Measure paired alternating rounds with identical distinct frame inputs.

  Parameters
  ----------
  variants : dict
      Old-disabled and current-always-on loaded source snapshots.
  shape : tuple
      Synthetic frame shape.
  rounds, requests, warmup : int
      Paired rounds, requests per variant/round and unmeasured warmup requests.

  Returns
  -------
  dict
      Paired timing summaries and bounded diagnostic evidence.
  """
  images = [np.full(shape, index, dtype=np.uint8) for index in range(44)]
  request = [{"STREAM_NAME": str(index), "INPUTS": [{"TYPE": "IMG", "IMG": image}]}
             for index, image in enumerate(images)]
  storage = np.empty((44, images[0].size), dtype=np.uint8)
  fixtures = {name: _fixture(variant, storage) for name, variant in variants.items()}
  result = {"INFERENCES": [[[]] for _ in images]}
  samples, medians = {name: [] for name in variants}, {name: [] for name in variants}
  for fixture in fixtures.values():
    for _ in range(warmup):
      _roundtrip(fixture, request, result)
  for index in range(rounds):
    order = list(variants) if index % 2 == 0 else list(reversed(variants))
    for name in order:
      current = []
      for _ in range(requests):
        started = time.perf_counter()
        received, payload = _roundtrip(fixtures[name], request, result)
        current.append(time.perf_counter() - started)
      samples[name].extend(current)
      medians[name].append(statistics.median(current))
      # Pixel comparison stays outside timing and proves real admission/read;
      # counting observe() calls alone could not satisfy this verification.
      assert payload["DATA"] is result
      assert len(received) == 44
      for entry, image in zip(received, images):
        assert np.array_equal(entry["INPUTS"][0]["IMG"], image)
      assert not fixtures[name][2]._np_mem_additional[:, 0].any()
  before, after = _stats(samples["baseline_off"]), _stats(samples["always_on"])
  paired = [(new / old - 1) * 100 for old, new in
            zip(medians["baseline_off"], medians["always_on"])]
  diag = variants["always_on"].diag
  with diag._LOCK:
    history = list(diag._HISTORY)
    counters = dict(diag._COUNTERS)
    pending = sum(len(items) for items in diag._PENDING.values())
  assert history and len(history) <= diag._HISTORY_LIMIT
  assert {item["event"] for item in history} >= {"send", "receive", "result"}
  assert all(item.get("missing", 0) == 0 and not item.get("reason") for item in history)
  assert pending == 0, "completed synthetic requests must consume pending tracking"
  assert all(record["event"] in ("startup", "configuration")
             for record in variants["always_on"].log.records)
  assert not variants["baseline_off"].log.records
  return {"shape": list(shape), "distinct_frames": 44,
          "input_plus_buffer_bytes": 2 * storage.nbytes, "rounds": rounds,
          "requests_per_round": requests, "warmup_per_variant": warmup,
          "baseline_off": before, "always_on": after,
          "throughput_delta_percent": (after["requests_per_second"] /
                                       before["requests_per_second"] - 1) * 100,
          "median_delta_us": (after["median_ms"] - before["median_ms"]) * 1000,
          "p95_delta_percent": (after["p95_ms"] / before["p95_ms"] - 1) * 100,
          "paired_round_median_delta_percent": statistics.median(paired),
          "paired_round_delta_range_percent": [min(paired), max(paired)],
          "history_records": len(history), "healthy_output_records": 0,
          "pending_requests_after_completion": pending, "diagnostic_counters": counters}


def _blocked_probe(sources):
  """Prove actual transport completes while diagnostic log I/O remains blocked.

  Parameters
  ----------
  sources : dict
      Current working-tree source snapshot.

  Returns
  -------
  dict
      Completed transport count, elapsed time and bounded output queue depth.
  """
  variant = _variant("blocked", sources, blocked=True)
  assert variant.log.entered.wait(2), "emitter did not enter the blocking sink"
  try:
    fixture = _fixture(variant, np.empty((44, 48), dtype=np.uint8))
    request = [{"STREAM_NAME": str(index),
                "INPUTS": [{"TYPE": "IMG", "IMG": np.full((4, 4, 3), index, dtype=np.uint8)}]}
               for index in range(44)]
    result = {"INFERENCES": [[[]] for _ in request]}
    # Independent failure targets fill the pending output deque while the sink
    # stays blocked. This pressure must affect diagnostics rather than transport.
    for index in range(variant.diag._QUEUE_LIMIT + 8):
      variant.diag.observe(variant.log, "FAIL_" + str(index), "receive", index, missing=1)
    started = time.perf_counter()
    for _ in range(50):
      _roundtrip(fixture, request, result)
    elapsed = time.perf_counter() - started
    assert not variant.log.release.is_set() and not variant.log.records
    with variant.diag._LOCK:
      depth = len(variant.diag._OUTPUT)
      drops = variant.diag._COUNTERS["output_drops"]
    assert depth <= variant.diag._QUEUE_LIMIT and drops > 0
    return {"completed_roundtrips_while_sink_blocked": 50,
            "elapsed_seconds": elapsed, "output_queue_depth": depth,
            "output_queue_limit": variant.diag._QUEUE_LIMIT, "output_drops": drops}
  finally:
    variant.log.release.set()


def main():
  """Print reproducible paired benchmark evidence without touching Git state."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--rounds", type=int, default=12)
  parser.add_argument("--requests", type=int, default=20)
  parser.add_argument("--warmup", type=int, default=4)
  args = parser.parse_args()
  if not (2 <= args.rounds <= 32 and 1 <= args.requests <= 100 and 1 <= args.warmup <= 20):
    parser.error("rounds must be 2..32, requests 1..100 and warmup 1..20")
  started = time.perf_counter()
  baseline, candidate = _sources(True), _sources()
  baseline_conversion = _conversion_sources(baseline["utils/system_shared_memory.py"])
  candidate_conversion = _conversion_sources(candidate["utils/system_shared_memory.py"])
  changed_dependencies = [name for name in baseline_conversion
                          if baseline_conversion[name] != candidate_conversion[name]]
  assert not changed_dependencies, \
    "conversion implementation changed; baseline comparison requires review: " + ", ".join(changed_dependencies)
  variants = {"baseline_off": _variant("baseline", baseline),
              "always_on": _variant("candidate", candidate)}
  report = {"baseline_commit": _BASELINE, "python": platform.python_version(),
            "numpy": np.__version__, "paired_order": "off/on, on/off, repeated",
            "scope": "synthetic extracted real methods; NumPy storage/in-memory envelopes; no model/GPU/IPC",
            "candidate_source_sha256": {path: hashlib.sha256(source.encode()).hexdigest()
                                        for path, source in candidate.items()},
            "matched_conversion_source_sha256": {
              name: hashlib.sha256(source.encode()).hexdigest()
              for name, source in candidate_conversion.items()
            },
            "cases": [_measure(variants, shape, args.rounds, args.requests, args.warmup)
                      for shape in ((720, 1280, 3), (4, 4, 3))],
            "blocked_sink_probe": _blocked_probe(candidate)}
  assert candidate == _sources(), "working-tree source changed during benchmark; rerun frozen snapshot"
  report["elapsed_seconds"] = time.perf_counter() - started
  print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
  main()
