"""Regressions for bounded SHM evidence, correlation and worker aliases."""

import importlib.util
import pathlib
import time
import types
import threading
import unittest
from unittest import mock


_PATH = pathlib.Path(__file__).with_name("test_shm_diagnostics.py")
_SPEC = importlib.util.spec_from_file_location("shm_evidence_fixture", _PATH)
_FIXTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURE)
_DIAG = _FIXTURE._DIAG


class DiagnosticEvidenceTests(_FIXTURE.ShmDiagnosticsTests):
  """Reuse the real-method fixtures and differential transport regressions."""

  def test_suppressed_failure_is_retained_after_recovery(self):
    log = _FIXTURE._Log()
    for instant, command, fields in (
        (10, 1, {"missing": 0}),
        (10.1, 2, {"missing": 3, "reason": "no_free_slot"}),
        (10.2, 3, {"missing": 0}),
        (11.1, 4, {"missing": 0})):
      with mock.patch.object(_DIAG.time, "monotonic", return_value=instant):
        _DIAG.observe(log, "DETECTOR", "send", command, **fields)
    records = log.records()
    self.assertEqual(len(records), 2)
    summary = records[-1]
    self.assertFalse(summary["anomaly"])
    self.assertEqual(summary["failures"], 1)
    self.assertEqual(summary["fail_no_free_slot"], 1)
    self.assertEqual(summary["missing_total"], 3)
    self.assertEqual(summary["first_failed_cmd_id"], 2)
    self.assertEqual(summary["last_failed_cmd_id"], 2)

  def test_result_matches_expected_request_and_reports_cardinality(self):
    for observed_id, rows, reason in (
        (1, [[[]]], None),
        (99, [[[]]], "cmd_id_mismatch"),
        (1, [], "cardinality"),
        (1, [[]], "cardinality")):
      with self.subTest(observed_id=observed_id, rows=rows):
        _DIAG._STATE.clear()
        _DIAG.clear_requests("DETECTOR")
        comm = _FIXTURE._Comm()
        manager = self.manager(None, comm)
        manager._send_message("DETECTOR", "PREDICT", _FIXTURE._request(1))
        payload = {"TYPE": "RESULT", "DATA": {"INFERENCES": rows}, "CMD_ID": observed_id}
        comm.payload = payload
        result = manager._wait_for_result("DETECTOR", max_wait_time=1)
        self.assertIs(result[1], payload["DATA"])
        record = next(r for r in manager.log.records() if r["event"] == "result")
        self.assertEqual(record.get("reason"), reason)
        self.assertEqual(record["expected_cmd_id"], 1)
        self.assertEqual(record["correlated"], observed_id == 1)
        self.assertEqual(record["expected_entries"], 1)
        self.assertEqual(record["expected_images"], 1)

  def test_timeout_snapshot_precedes_original_cleanup(self):
    shm, comm = _FIXTURE._buffer(), _FIXTURE._Comm()
    manager = self.manager(shm, comm)
    worker = types.SimpleNamespace(server_name="DETECTOR", pid=77,
                                   exitcode=None, is_alive=lambda: True)
    manager._servers["DETECTOR"].update(SERVER=worker, PID=77)
    manager._send_message("DETECTOR", "PREDICT", _FIXTURE._request(1))
    manager._server_wait_time = lambda _: 5
    manager._create_notification = lambda **kwargs: None
    snapshots = []
    def cleanup(*args, **kwargs):
      snapshots.extend(r for r in manager.log.records() if r.get("reason") == "response_timeout")
      shm._np_mem_additional[:] = 0
    manager._cleanup_server = cleanup
    manager._close_server_on_null("DETECTOR", max_wait_time=5)
    self.assertEqual(len(snapshots), 1)
    record = snapshots[0]
    self.assertEqual(record["expected_cmd_id"], 1)
    self.assertEqual(record["pending_count"], 1)
    self.assertEqual(record["worker_pid"], 77)
    self.assertTrue(record["worker_alive"])
    self.assertIsNone(record["worker_exitcode"])
    self.assertEqual(record["occupied_before"], 1)
    self.assertEqual(record["free_slots"], 49)
    self.assertEqual(_DIAG.pending_observation("DETECTOR")["pending_count"], 0)

  def test_recent_result_cannot_suppress_first_timeout_snapshot(self):
    manager = self.manager(_FIXTURE._buffer(3), _FIXTURE._Comm())
    manager._server_wait_time = lambda _: 5
    manager._create_notification = lambda **kwargs: None
    snapshots = []
    manager._cleanup_server = lambda *args, **kwargs: snapshots.extend(
      r for r in manager.log.records() if r["event"] == "timeout")
    with mock.patch.object(_DIAG.time, "monotonic", return_value=10):
      _DIAG.observe(manager.log, "DETECTOR", "result", 1, missing=0)
    with mock.patch.object(_DIAG.time, "monotonic", return_value=10.1):
      manager._close_server_on_null("DETECTOR", max_wait_time=5)
    self.assertEqual(len(snapshots), 1)
    self.assertEqual(snapshots[0]["occupied_before"], 3)

  def test_rapid_timeout_repeats_retain_snapshot_within_rate_cap(self):
    log = _FIXTURE._Log()
    for instant, worker_pid, occupied in ((10, 1, 2), (10.1, 2, 3), (11.1, 3, 4)):
      with mock.patch.object(_DIAG.time, "monotonic", return_value=instant):
        _DIAG.observe(log, "DETECTOR", "timeout", correlated=False,
                      reason="response_timeout", worker_pid=worker_pid, occupied_before=occupied)
    records = log.records()
    self.assertEqual(len(records), 2)
    self.assertEqual(records[-1]["worker_pid"], 3)
    self.assertEqual(records[-1]["suppressed_timeout_worker_pid"], 2)
    self.assertEqual(records[-1]["suppressed_timeout_occupied_before"], 3)

  def test_request_tracking_is_bounded_and_expires(self):
    for command in range(200):
      _DIAG.remember_request("DETECTOR", command, "PREDICT", "DETECTOR",
                             _FIXTURE._request(1))
    pending = _DIAG.pending_observation("DETECTOR")
    self.assertEqual(pending["pending_count"], 64)
    self.assertEqual(pending["tracking_evictions"], 136)
    def assert_scalar_tree(value):
      if isinstance(value, dict):
        for key, item in value.items():
          assert_scalar_tree(key)
          assert_scalar_tree(item)
      elif isinstance(value, tuple):
        for item in value:
          assert_scalar_tree(item)
      else:
        self.assertIn(type(value), (int, float, str, type(None)))
    assert_scalar_tree(_DIAG._PENDING)
    _DIAG._DEADLINE = 0
    self.assertFalse(_DIAG.enabled())
    self.assertEqual(_DIAG._PENDING, {})

  def test_pending_aliases_share_worker_without_false_correlation(self):
    _DIAG.remember_request("DETECTOR", 1, "PREDICT", "ALIAS_A", _FIXTURE._request(1))
    _DIAG.remember_request("DETECTOR", 2, "PREDICT", "ALIAS_B", _FIXTURE._request(2))
    payload = {"TYPE": "RESULT", "CMD_ID": 2, "DATA": {"INFERENCES": [[[]], [[]]]}}
    wrong = _DIAG.result_observation("DETECTOR", payload, "ALIAS_A")
    self.assertEqual(wrong["expected_cmd_id"], 1)
    self.assertFalse(wrong["correlated"])
    self.assertEqual(wrong["reason"], "cmd_id_mismatch")
    self.assertEqual(_DIAG.pending_observation("DETECTOR")["pending_count"], 1)

  def test_sustained_errors_keep_ten_second_repeat_cadence(self):
    log = _FIXTURE._Log()
    for instant in (10, 11, 12, 19, 20):
      with mock.patch.object(_DIAG.time, "monotonic", return_value=instant):
        _DIAG.observe(log, "DETECTOR", "send", int(instant), reason="no_free_slot", missing=1)
    self.assertEqual(len(log.records()), 2)
    self.assertEqual(log.records()[-1]["fail_no_free_slot"], 4)

  def test_error_marker_and_non_image_requests_are_observational(self):
    inputs = [{"STREAM_NAME": "test", "INPUTS": [{"TYPE": "STRUCT_DATA", "STRUCT_DATA": 5}]}]
    _DIAG.remember_request("DETECTOR", 1, "PREDICT", "DETECTOR", inputs)
    payload = {"CMD_ID": 1, "DATA": {"INFERENCES": [[5]],
               "INFERENCES_META": {"ERROR": "secret_canary"}}}
    fields = _DIAG.result_observation("DETECTOR", payload, "DETECTOR")
    self.assertTrue(fields["correlated"])
    self.assertTrue(fields["error_marker"])
    self.assertNotIn("reason", fields)
    self.assertIsNone(fields["expected_images"])
    log = _FIXTURE._Log()
    _DIAG.observe(log, "DETECTOR", "result", 1, **fields)
    self.assertNotIn("secret_canary", "\n".join(log.lines))
    self.assertTrue(log.records()[0]["anomaly"])

  def test_unknown_results_and_failed_sends_do_not_leave_pending_frames(self):
    comm = _FIXTURE._Comm(failure=BrokenPipeError("secret_canary"))
    manager = self.manager(_FIXTURE._buffer(), comm)
    with self.assertRaises(ValueError):
      manager._send_message("DETECTOR", "PREDICT", _FIXTURE._request(1))
    self.assertEqual(_DIAG.pending_observation("DETECTOR")["pending_count"], 0)
    fields = _DIAG.result_observation("DETECTOR", {"CMD_ID": None, "DATA": {"INFERENCES": []}}, "DETECTOR")
    self.assertFalse(fields["correlated"])
    self.assertEqual(fields["reason"], "unmatched_response")

  def test_fork_reset_and_output_cap_discard_pending_state(self):
    _DIAG.remember_request("DETECTOR", 1, "PREDICT", "DETECTOR", _FIXTURE._request(1))
    _DIAG._after_fork()
    self.assertEqual(_DIAG._PENDING, {})
    self.assertEqual(_DIAG._CONFIGURED, {})
    _DIAG.remember_request("DETECTOR", 2, "PREDICT", "DETECTOR", _FIXTURE._request(1))
    _DIAG._LIMIT = 128
    _DIAG.observe(_FIXTURE._Log(), "DETECTOR", "send", 2)
    self.assertEqual(_DIAG._PENDING, {})
    self.assertFalse(_DIAG.enabled())

  def test_output_cap_cannot_be_repopulated_by_inflight_request_inspection(self):
    inspecting, resume = threading.Event(), threading.Event()
    original = _DIAG.time.monotonic
    calls = [0]
    def clock():
      calls[0] += 1
      if calls[0] == 2:
        inspecting.set()
        if not resume.wait(3):
          raise RuntimeError("test synchronization timed out")
      return original()
    worker = threading.Thread(target=_DIAG.remember_request,
      args=("DETECTOR", 1, "PREDICT", "DETECTOR", _FIXTURE._request(1)))
    with mock.patch.object(_DIAG.time, "monotonic", side_effect=clock):
      worker.start()
      try:
        self.assertTrue(inspecting.wait(3))
        _DIAG._LIMIT = 128
        _DIAG.observe(_FIXTURE._Log(), "DETECTOR", "send", 1)
      finally:
        resume.set()
        worker.join(3)
    self.assertFalse(worker.is_alive())
    self.assertEqual(_DIAG._PENDING, {})
    self.assertFalse(_DIAG.enabled())

  def test_configuration_cap_record_does_not_restore_configuration_cache(self):
    _DIAG._LIMIT = 128
    worker = types.SimpleNamespace(pid=77, comm_method="shm")
    log = _FIXTURE._Log()
    _DIAG.configuration_observation(log, "DETECTOR", "DETECTOR", worker,
                                    _FIXTURE._buffer(), _FIXTURE._Comm())
    self.assertEqual(log.records()[0]["event"], "output_cap")
    self.assertEqual(_DIAG._CONFIGURED, {})

  def test_actual_worker_selection_covers_alias_and_configuration(self):
    _DIAG._TARGETS = ("TH_YFS_MSPN",)
    shm, comm = _FIXTURE._buffer(), _FIXTURE._Comm()
    manager = self.manager(shm, comm)
    worker = types.SimpleNamespace(server_name="TH_YFS_MSPN", pid=88)
    manager._servers = {"TH_YF8S": {"SHM": shm, "COMM": comm, "SERVER": worker, "PID": 88,
                                   "COVERED_BY": "TH_YFS_MSPN"}}
    manager._send_message("TH_YF8S", "PREDICT", _FIXTURE._request(1))
    comm.payload = {"TYPE": "RESULT", "DATA": {"INFERENCES": [[[]]]}, "CMD_ID": 1}
    manager._wait_for_result("TH_YF8S", max_wait_time=1)
    records = manager.log.records(include_configuration=True)
    self.assertEqual({r["event"] for r in records}, {"configuration", "send", "result"})
    self.assertTrue(all(r["target"] == "TH_YFS_MSPN" for r in records))
    self.assertTrue(all(r["requested_server"] == "TH_YF8S" for r in records))
    configuration = next(r for r in records if r["event"] == "configuration")
    self.assertEqual(configuration["worker_pid"], 88)
    self.assertEqual(configuration["capacity"], 50)
    self.assertEqual(configuration["max_elements"], 48)
    cv = self.consumer("lowres_general_detector", {}, {"lowres_general_detector": [[]]}, [])
    cv.global_shmem = {"serving_manager": manager}
    with mock.patch.dict(_FIXTURE._CV._pre_process.__globals__, {
        "get_serving_process_given_ai_engine": _FIXTURE._engine_utils.get_serving_process_given_ai_engine}):
      cv._pre_process()
    self.assertEqual(cv.log.records()[0]["target"], "TH_YFS_MSPN")
    self.assertEqual(cv.log.records()[0]["requested_server"], "TH_YF8S")


if __name__ == "__main__":
  unittest.main()
