"""Cross-process evidence checks using real Pipe and OS shared memory."""

import importlib.util
import multiprocessing as mp
from multiprocessing import shared_memory
import pathlib
import sys
import types
import unittest

import numpy as np


_PATH = pathlib.Path(__file__).with_name("test_shm_diagnostics.py")
_SPEC = importlib.util.spec_from_file_location("shm_transport_fixture", _PATH)
_FIXTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURE)
sys.modules["shm_test_subject"] = _FIXTURE._SHM


def _storage(names=None):
  """Attach independent NumPy views to real kernel SHM in each process."""
  shm = _FIXTURE._buffer()
  segments = []
  for field, shape, dtype in (("_np_mem", (50, 48), np.uint8),
                              ("_np_mem_additional", (50, 11), np.int32)):
    segment = shared_memory.SharedMemory(
      name=None if names is None else names[len(segments)], create=names is None,
      size=int(np.prod(shape)) * np.dtype(dtype).itemsize)
    segments.append(segment)
    view = np.ndarray(shape, dtype=dtype, buffer=segment.buf)
    if names is None:
      view[:] = 0
    setattr(shm, field, view)
  return shm, segments


def _child(pipe, names, target, enabled):
  """Restore the production request and send a synthetic model response."""
  harness = _FIXTURE.ShmDiagnosticsTests()
  harness.setUp()
  harness.diagnostics(enabled)
  shm, segments = _storage(names)
  worker = harness.worker(shm, pipe)
  worker.server_name = target
  try:
    command, data, requested, cmd_id, timestamp = worker._get_message(wait=True)
    values = [None if entry["INPUTS"][0]["IMG"] is None
              else entry["INPUTS"][0]["IMG"].tolist() for entry in data]
    result = {"INFERENCES": [[] if value is None else [[{"TYPE": "synthetic"}]]
                              for value in values]}
    worker._send_message("RESULT", result, cmd_id=cmd_id)
    pipe.send({"command": command, "requested": requested, "values": values,
               "records": worker.log.records(),
               "observations": worker.log.observations,
               "occupied": int(np.count_nonzero(shm._np_mem_additional[:, 0]))})
  finally:
    del shm._np_mem, shm._np_mem_additional
    for segment in segments:
      segment.close()
    pipe.close()
    harness.doCleanups()


class DiagnosticTransportTests(unittest.TestCase):
  """Real cross-process boundary; model/lifecycle initialization is isolated."""

  def _run(self, count, enabled, alias=False):
    harness = _FIXTURE.ShmDiagnosticsTests()
    harness.setUp()
    target, requested = ("TH_YFS_MSPN", "TH_YF8S") if alias else ("DETECTOR", "DETECTOR")
    harness.diagnostics(enabled)
    shm, segments = _storage()
    context = mp.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_child,
      args=(child, [segment.name for segment in segments], target, enabled))
    manager = harness.manager(shm, parent)
    try:
      process.start()
      child.close()
      entry = manager._servers.pop("DETECTOR")
      entry.update(SERVER=types.SimpleNamespace(server_name=target, pid=process.pid,
                                             comm_method="shm"), PID=process.pid)
      if alias:
        entry["COVERED_BY"] = target
      manager._servers[requested] = entry
      inputs = _FIXTURE._request(count)
      manager._send_message(requested, "PREDICT", inputs)
      envelope = manager._wait_for_comm_message(requested, max_wait_time=15)
      self.assertIsNotNone(envelope)
      self.assertEqual(envelope["CMD_ID"], 1)
      self.assertTrue(parent.poll(15))
      evidence = parent.recv()
      process.join(15)
      self.assertEqual(process.exitcode, 0)
      self.assertEqual(evidence["command"], "PREDICT")
      self.assertEqual(evidence["requested"], requested)
      self.assertEqual(evidence["occupied"], 0)
      expected = [entry["INPUTS"][0]["IMG"].tolist() if i < 50 else None
                  for i, entry in enumerate(inputs)]
      self.assertEqual(evidence["values"], expected)
      records = manager.log.records()
      if enabled:
        self.assertEqual({r["event"] for r in records}, {"send", "result"} if count > 50 else set())
        observations = manager.log.observations
        self.assertTrue(all(r["target"] == target for r in observations + evidence["observations"]))
        self.assertTrue(all(r["requested_server"] == requested for r in observations))
        received = next(r for r in evidence["observations"] if r["event"] == "receive")
        self.assertEqual(received["requested_server"], requested)
        result = next(r for r in observations if r["event"] == "result")
        self.assertTrue(result["correlated"])
        self.assertEqual(result["expected_entries"], count)
        self.assertEqual(result.get("reason"), "cardinality" if count > 50 else None)
      else:
        self.assertEqual(records + evidence["records"], [])
      return evidence["values"], envelope, evidence["occupied"]
    finally:
      if process.is_alive():
        process.terminate()
        process.join(5)
      parent.close()
      child.close()
      del shm._np_mem, shm._np_mem_additional
      for segment in segments:
        segment.close()
        segment.unlink()
      harness.doCleanups()

  def test_capacity_failure_is_observed_without_changing_data_or_slots(self):
    for count in (50, 51):
      with self.subTest(count=count):
        disabled, enabled = self._run(count, False), self._run(count, True)
        self.assertEqual(disabled[0], enabled[0])
        self.assertEqual(disabled[1]["DATA"], enabled[1]["DATA"])
        self.assertEqual(set(disabled[1]), set(enabled[1]))
        self.assertEqual(disabled[2], enabled[2])

  def test_actual_worker_selection_captures_alias_at_both_boundaries(self):
    for count in (1, 51):
      with self.subTest(count=count):
        self._run(count, True, alias=True)


if __name__ == "__main__":
  unittest.main()
