"""2026-10-14 regressions for always-on bounded asynchronous SHM evidence."""

import importlib.util
import itertools
import json
import multiprocessing
import os
import pathlib
import threading
import time
import types
import unittest
from unittest import mock


_PATH = pathlib.Path(__file__).with_name("shm_diagnostics.py")


def _load():
  """Load an isolated utility without importing the heavyweight runtime."""
  spec = importlib.util.spec_from_file_location("always_on_subject", _PATH)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class _Log:
  """Capture daemon output with a condition used only by test waiters."""

  def __init__(self):
    self.records = []
    self.condition = threading.Condition()

  def P(self, line):
    """Capture one structured record and wake test waiters."""
    with self.condition:
      self.records.append(json.loads(line[len("[SHM_DIAG] "):]))
      self.condition.notify_all()

  def wait(self, count):
    """Wait for a bounded number of asynchronously emitted records."""
    with self.condition:
      return self.condition.wait_for(lambda: len(self.records) >= count, timeout=3)


class AlwaysOnDiagnosticsTests(unittest.TestCase):
  """Verify privacy, bounds, rate accounting and nonblocking producers."""

  def setUp(self):
    """Create a separate emitter and fake utility clock for each case."""
    self.diag = _load()
    self.now = [100.0]
    self.diag.time = types.SimpleNamespace(monotonic=lambda: self.now[0])
    self.log = _Log()
    self.diag.start(self.log)
    self.assertTrue(self.log.wait(1))

  def test_legacy_configuration_is_ignored_and_startup_is_once(self):
    """Legacy malformed values cannot select or disable any serving worker."""
    diag, log = _load(), _Log()
    with mock.patch.dict(os.environ, {"EE_SHM_DIAGNOSTICS": "secret malformed config"}):
      self.assertTrue(diag.start(log))
      self.assertFalse(diag.start(log))
    self.assertTrue(diag.observe(log, "UNSELECTED", "send", 1, reason="no_free_slot"))
    self.assertTrue(log.wait(2))
    self.assertEqual(log.records[0]["event"], "startup")
    self.assertTrue(log.records[0]["always_on"])
    self.assertTrue(log.records[0]["legacy_env_ignored"])
    self.assertIsNone(log.records[0]["expiry"])
    self.assertNotIn("secret", json.dumps(log.records))

  def test_thread_creation_failure_does_not_abort_startup(self):
    """Unavailable emitter resources leave diagnostic startup best effort."""
    diag = _load()
    with mock.patch.object(diag.threading, "Thread", side_effect=RuntimeError("resource")):
      self.assertFalse(diag.start(_Log()))
    self.assertFalse(diag._STARTED)
    self.assertEqual(diag._COUNTERS["startup_errors"], 1)
    log = _Log()
    self.assertTrue(diag.start(log))
    self.assertTrue(log.wait(1))
    self.assertEqual(sum(record["event"] == "startup" for record in log.records), 1)
    self.assertFalse(diag._OUTPUT)

  def test_output_budget_renews_and_preserves_dropped_failure_summary(self):
    """Budget loss remains visible and cannot permanently disable failures."""
    self.diag._OUTPUT_RECORDS_PER_SECOND = 1
    self.diag.observe(self.log, "WORKER", "send", 1, reason="no_free_slot")
    deadline = time.monotonic() + 2
    while self.diag._COUNTERS["budget_drops"] == 0 and time.monotonic() < deadline:
      time.sleep(0.01)
    self.assertEqual(self.diag._COUNTERS["budget_drops"], 1)
    self.now[0] += 1.1
    self.diag.observe(self.log, "WORKER", "send", 2, reason="no_free_slot")
    self.assertTrue(self.log.wait(2))
    self.assertEqual(self.log.records[-1]["failures"], 2)
    self.assertEqual(self.log.records[-1]["diagnostic_counters"]["budget_drops"], 1)
    self.assertIn("observed_at", self.log.records[-1])
    self.assertIn("queue_delay_ms", self.log.records[-1])

  def test_history_is_count_age_and_context_bounded(self):
    """Healthy successes stay in memory and expire independently of reporting."""
    for cmd in range(500):
      self.diag.observe(self.log, "WORKER", "send", cmd, arrays=1, admitted=1)
    self.assertEqual(len(self.diag._HISTORY), self.diag._HISTORY_LIMIT)
    self.assertEqual(self.diag._COUNTERS["history_evictions"], 372)
    self.assertEqual(len(self.log.records), 1)
    self.diag.observe(self.log, "WORKER", "receive", 500, reason="slot_unavailable")
    self.assertTrue(self.log.wait(2))
    self.assertEqual(len(self.log.records[-1]["recent"]), 8)
    self.now[0] += 11
    self.diag.observe(self.log, "WORKER", "receive", 501, reason="slot_unavailable")
    self.assertTrue(self.log.wait(3))
    self.assertEqual(self.log.records[-1]["recent"], [])
    self.assertEqual(self.diag._COUNTERS["history_expired"], 128)

  def test_rate_suppression_and_recovery_preserve_failure_counts(self):
    """A recovery summarizes suppressed failures without repeated healthy logs."""
    self.diag.observe(self.log, "WORKER", "send", 1, missing=1, reason="no_free_slot")
    self.assertTrue(self.log.wait(2))
    for cmd in range(2, 12):
      self.diag.observe(self.log, "WORKER", "send", cmd, missing=1, reason="no_free_slot")
    self.assertEqual(len(self.log.records), 2)
    self.now[0] += 1.1
    self.diag.observe(self.log, "WORKER", "send", 12, admitted=1)
    self.assertTrue(self.log.wait(3))
    recovery = self.log.records[-1]
    self.assertTrue(recovery["recovery"])
    self.assertEqual(recovery["failures"], 10)
    self.assertEqual(recovery["first_failed_cmd_id"], 2)
    self.assertEqual(recovery["last_failed_cmd_id"], 11)
    self.assertEqual(recovery["fail_no_free_slot"], 10)
    self.diag.observe(self.log, "WORKER", "send", 13, admitted=1)
    self.assertEqual(len(self.log.records), 3)

  def test_timeout_repeats_retain_suppressed_snapshot(self):
    """Timeout summaries preserve the first independently sampled snapshot."""
    self.diag.observe(self.log, "WORKER", "timeout", reason="response_timeout", expected_cmd_id=1)
    self.diag.observe(self.log, "WORKER", "timeout", reason="response_timeout", expected_cmd_id=2)
    self.now[0] += 10
    self.diag.observe(self.log, "WORKER", "timeout", reason="response_timeout", expected_cmd_id=3)
    self.assertTrue(self.log.wait(3))
    self.assertEqual(self.log.records[-1]["suppressed_timeout_expected_cmd_id"], 2)
    self.assertEqual(self.log.records[-1]["failures"], 2)

  def test_scalar_only_privacy_for_history_and_output(self):
    """Payload references, oversized integers and unknown reason text are omitted."""
    payload = object()
    self.diag.observe(self.log, "WORKER", "send", 1, admitted=1, image=payload,
                      requested_server=payload, bytes=2 ** 1000, _image_counts=(1,))
    self.diag.observe(self.log, "WORKER", "send", 2, reason="raw-secret-exception", image=payload)
    self.assertTrue(self.log.wait(2))
    text = json.dumps(self.log.records)
    self.assertNotIn("raw-secret", text)
    self.assertNotIn("_image_counts", text)
    self.assertNotIn("requested_server", text)
    self.assertNotIn("bytes", text)
    self.assertEqual(self.log.records[-1]["reason"], "other")
    self.assertEqual(self.diag._HISTORY[0]["cmd_id"], 1)

  def test_globally_bounded_tracking_and_rate_state(self):
    """Many worker identities cannot grow tracking or failure state unboundedly."""
    inputs = [{"INPUTS": [{"TYPE": "IMG", "IMG": object()}]}]
    observation = self.diag.request_observation(inputs)
    for index in range(500):
      self.diag.remember_request("WORKER" + str(index), index, "PREDICT", "ALIAS", inputs,
                                 observation=observation)
      self.diag.observe(self.log, "WORKER" + str(index), "result", index, reason="wrong_type")
    self.assertLessEqual(len(self.diag._PENDING), self.diag._MAX_TARGETS)
    self.assertLessEqual(sum(len(value) for value in self.diag._PENDING.values()),
                         self.diag._MAX_PENDING_TOTAL)
    self.assertLessEqual(len(self.diag._STATE), self.diag._MAX_STATES)
    self.assertGreater(self.diag._COUNTERS["state_evictions"], 0)
    for index in range(400):
      self.diag.remember_request("ONE", index, "PREDICT", "ALIAS", inputs, observation=observation)
    self.assertEqual(len(self.diag._PENDING["ONE"]), self.diag._MAX_PENDING)
    self.assertGreater(self.diag._COUNTERS["tracking_evictions"], 0)
    for worker in range(16):
      for cmd in range(64):
        self.diag.remember_request("MANY" + str(worker), cmd, "PREDICT", "ALIAS", inputs,
                                   observation=observation)
    self.assertEqual(sum(len(value) for value in self.diag._PENDING.values()),
                     self.diag._MAX_PENDING_TOTAL)

  def test_request_scan_is_bounded_and_counts_can_be_reused(self):
    """Large input batches explicitly leave image expectations unavailable."""
    inputs = [{"INPUTS": [{"TYPE": "IMG", "IMG": object()}] * 257}]
    fields = self.diag.request_observation(inputs)
    self.assertIsNone(fields["images"])
    self.assertNotIn("_image_counts", fields)
    self.diag.remember_request("ONE", 7, "PREDICT", "ALIAS", inputs, observation=fields)
    result = self.diag.result_observation("ONE", {"CMD_ID": 7, "DATA": {"INFERENCES": [[]]}}, "ALIAS")
    self.assertTrue(result["correlated"])
    self.assertIsNone(result["expected_images"])
    self.assertIsNone(self.diag.request_observation(None)["images"])

  def _remember_image(self, cmd_id):
    """Remember one standard image request with a scalar expected row count."""
    self.diag.remember_request("WORKER", cmd_id, "PREDICT", "ALIAS",
                               [{"INPUTS": [{"TYPE": "IMG", "IMG": object()}]}])

  def _image_result(self, cmd_id, rows=None):
    """Observe a standard image result without executing inference."""
    return self.diag.result_observation("WORKER", {"CMD_ID": cmd_id, "DATA": {
      "INFERENCES": [[{}]] if rows is None else rows}}, "ALIAS")

  def test_lost_result_mutation_invalidates_stale_fifo_expectations(self):
    """A missed response observation cannot manufacture a later ID mismatch."""
    self._remember_image(1)
    with self.diag._LOCK:
      self.assertEqual(self._image_result(1)["tracking_status"], "contended")
    self._remember_image(2)
    fields = self._image_result(2)
    self.assertTrue(fields["correlated"])
    self.assertEqual(fields["tracking_status"], "incomplete_known")
    self.assertNotIn("reason", fields)
    late = self._image_result(1)
    self.assertFalse(late["correlated"])
    self.assertEqual(late["tracking_status"], "incomplete")
    self.assertNotIn("reason", late)

  def test_lost_first_remember_keeps_unseen_identity_incomplete(self):
    """A never-tracked first request cannot become a false later FIFO mismatch."""
    with self.diag._LOCK:
      self._remember_image(1)
    self._remember_image(2)
    late = self._image_result(1)
    self.assertFalse(late["correlated"])
    self.assertEqual(late["tracking_status"], "incomplete")
    self.assertNotIn("reason", late)
    known = self._image_result(2)
    self.assertTrue(known["correlated"])
    self.assertEqual(known["tracking_status"], "incomplete_known")
    self.assertNotIn("reason", known)

  def test_request_eviction_does_not_fabricate_mismatch_for_late_response(self):
    """Evicted and out-of-order responses become explicitly incomplete evidence."""
    for cmd_id in range(65):
      self._remember_image(cmd_id)
    evicted = self._image_result(0)
    self.assertFalse(evicted["correlated"])
    self.assertEqual(evicted["tracking_status"], "incomplete")
    self.assertNotIn("reason", evicted)
    known = self._image_result(2)
    self.assertTrue(known["correlated"])
    self.assertEqual(known["tracking_status"], "incomplete_known")
    self.assertNotIn("reason", known)
    invalid = self._image_result(3, [[]])
    self.assertEqual(invalid["reason"], "cardinality")

  def test_lost_failed_send_cleanup_cannot_poison_later_correlation(self):
    """Failed-send bookkeeping loss remains incomplete until lifecycle cleanup."""
    self._remember_image(1)
    with self.diag._LOCK:
      self.diag.clear_requests("WORKER", 1)
    self._remember_image(2)
    fields = self._image_result(2)
    self.assertEqual(fields["tracking_status"], "incomplete_known")
    self.assertNotIn("reason", fields)
    self.diag.clear_requests("WORKER")
    self._remember_image(3)
    self._remember_image(4)
    mismatch = self._image_result(4)
    self.assertEqual(mismatch["tracking_status"], "known")
    self.assertEqual(mismatch["reason"], "cmd_id_mismatch")
    self.assertEqual(mismatch["expected_cmd_id"], 3)

  def test_complete_tracking_preserves_actual_fifo_mismatch(self):
    """Matching a later known ID cannot conceal ordering failures when complete."""
    self._remember_image(1)
    self._remember_image(2)
    fields = self._image_result(2)
    self.assertFalse(fields["correlated"])
    self.assertEqual(fields["reason"], "cmd_id_mismatch")
    self.assertEqual(fields["expected_cmd_id"], 1)

  def test_unverified_receipt_cannot_claim_cardinality_recovery(self):
    """Unknown receipts retain success history without erasing verified failures."""
    self._remember_image(1)
    failed = self._image_result(1, [[]])
    self.diag.observe(self.log, "WORKER", "result", 1, **failed)
    self.assertTrue(self.log.wait(2))
    self.now[0] += 1.1
    unverified = self._image_result(2, [[]])
    self.assertFalse(self.diag.observe(self.log, "WORKER", "result", 2, **unverified))
    self.assertTrue(self.diag._STATE[("WORKER", "result")]["bad"])
    self.assertEqual(self.diag._HISTORY[-1]["cmd_id"], 2)
    self._remember_image(3)
    valid = self._image_result(3)
    self.assertTrue(self.diag.observe(self.log, "WORKER", "result", 3, **valid))
    self.assertTrue(self.log.wait(3))
    self.assertTrue(self.log.records[-1]["recovery"])

  def test_known_nonimage_contract_can_recover_an_error_marker(self):
    """Verified IPC for a non-image serving can recover an actual error marker."""
    inputs = [{"TEXT": "sample"}]
    self.diag.remember_request("WORKER", 1, "PREDICT", "ALIAS", inputs)
    failed = self.diag.result_observation("WORKER", {"CMD_ID": 1, "DATA": {
      "INFERENCES": {"label": 1}, "INFERENCES_META": {"ERROR": True}}}, "ALIAS")
    self.assertTrue(self.diag.observe(self.log, "WORKER", "result", 1, **failed))
    self.assertTrue(self.log.wait(2))
    self.now[0] += 1.1
    self.diag.remember_request("WORKER", 2, "PREDICT", "ALIAS", inputs)
    valid = self.diag.result_observation("WORKER", {"CMD_ID": 2, "DATA": {
      "INFERENCES": {"label": 1}}}, "ALIAS")
    self.assertEqual(valid["result_contract"], "unverified")
    self.assertTrue(self.diag.observe(self.log, "WORKER", "result", 2, **valid))
    self.assertTrue(self.log.wait(3))
    self.assertTrue(self.log.records[-1]["recovery"])

  def test_mixed_failures_require_verified_image_recovery(self):
    """Neither failure order nor summary emission can erase the image contract.

    Exercise both earlier council reproductions through the actual request and
    result observers. The same requirement holds after a ten-second summary
    resets its counters, and for a wrong result type as well as a missing row.
    """
    cases = itertools.product((False, True), (0.1, 10.1),
                              ("transport_exception", "error_marker"), ([], {}))
    for index, (image_first, gap, nonimage_reason, invalid_rows) in enumerate(cases):
      with self.subTest(image_first=image_first, gap=gap,
                        reason=nonimage_reason, invalid_rows=invalid_rows):
        target = "MIXED" + str(index)
        self.now[0] += 20

        def submit(cmd_id, image, invalid=False, error=False):
          inputs = [{"INPUTS": [{"TYPE": "IMG", "IMG": object()}]}] if image else [{"TEXT": "sample"}]
          self.diag.remember_request(target, cmd_id, "PREDICT", target, inputs)
          data = {"INFERENCES": invalid_rows if invalid else [[{}]] if image else {"label": 1}}
          if error:
            data["INFERENCES_META"] = {"ERROR": True}
          fields = self.diag.result_observation(target, {"CMD_ID": cmd_id, "DATA": data}, target)
          return self.diag.observe(self.log, target, "result", cmd_id, **fields)

        for cmd_id, image_failure in enumerate((image_first, not image_first), 1):
          if cmd_id == 2:
            self.now[0] += gap
          if image_failure:
            submit(cmd_id, image=True, invalid=True)
          elif nonimage_reason == "error_marker":
            submit(cmd_id, image=False, error=True)
          else:
            self.diag.observe(self.log, target, "result", cmd_id, exception_type="BrokenPipeError")

        self.now[0] += 1.1
        self.assertFalse(submit(3, image=False))
        # An observed non-image success can appear in receipt history, but it
        # cannot discharge any unresolved image failure in this episode.
        self.assertTrue(self.diag._STATE[(target, "result")]["image_failure_pending"])
        self.assertTrue(submit(4, image=True))
        self.assertFalse(self.diag._STATE[(target, "result")]["image_failure_pending"])
        self.now[0] += 1.1
        submit(5, image=False, error=True)
        self.now[0] += 1.1
        self.assertTrue(submit(6, image=False))
        # Drain this target before the next matrix case so queue pressure does
        # not make a successful recovery admission nondeterministic.
        deadline = time.monotonic() + 3
        with self.log.condition:
          self.assertTrue(self.log.condition.wait_for(
            lambda: any(row.get("target") == target and row.get("cmd_id") == 6
                        and row.get("recovery") for row in self.log.records),
            timeout=max(0, deadline - time.monotonic())))

  def test_budget_dropped_image_failure_still_requires_image_recovery(self):
    """Re-admitted failure counts cannot lose their image verification need."""
    self.diag._OUTPUT_RECORDS_PER_SECOND = 1
    self._remember_image(1)
    self.diag.observe(self.log, "WORKER", "result", 1, **self._image_result(1, [[]]))
    deadline = time.monotonic() + 2
    while not self.diag._COUNTERS["budget_drops"] and time.monotonic() < deadline:
      time.sleep(0.01)
    self.assertEqual(self.diag._COUNTERS["budget_drops"], 1)
    self.now[0] += 1.1
    inputs = [{"TEXT": "sample"}]
    self.diag.remember_request("WORKER", 2, "PREDICT", "ALIAS", inputs)
    fields = self.diag.result_observation("WORKER", {"CMD_ID": 2, "DATA": {
      "INFERENCES": {"label": 1}}}, "ALIAS")
    self.assertFalse(self.diag.observe(self.log, "WORKER", "result", 2, **fields))
    self._remember_image(3)
    self.assertTrue(self.diag.observe(self.log, "WORKER", "result", 3, **self._image_result(3)))
    self.assertTrue(self.log.wait(2))
    self.assertTrue(self.log.records[-1]["recovery"])
    self.assertEqual(self.log.records[-1]["fail_cardinality"], 1)

  def test_forgotten_identity_defaults_to_incomplete_until_lifecycle_clear(self):
    """The bounded confidence map cannot silently forget an old identity's loss."""
    inputs = [{"INPUTS": [{"TYPE": "IMG", "IMG": object()}]}]
    self._remember_image(1)
    for index in range(65):
      self.diag.remember_request("OTHER" + str(index), index, "PREDICT", "ALIAS", inputs)
    self._remember_image(2)
    self.assertLessEqual(len(self.diag._TRACKING_COMPLETE), self.diag._MAX_TARGETS)
    fields = self._image_result(1)
    self.assertEqual(fields["tracking_status"], "incomplete")
    self.assertNotIn("reason", fields)
    self.diag.clear_requests("WORKER")
    self._remember_image(3)
    fields = self._image_result(4)
    self.assertEqual(fields["reason"], "cmd_id_mismatch")

  def test_busy_state_lock_never_blocks_producers(self):
    """Contended diagnostics report lost evidence without waiting for a holder."""
    with self.diag._LOCK:
      begin = time.monotonic()
      self.assertFalse(self.diag.observe(self.log, "WORKER", "send", reason="no_free_slot"))
      self.assertEqual(self.diag.pending_observation("WORKER"), {"observation_error": True})
      self.diag.clear_requests("WORKER")
      self.diag.remember_request("WORKER", 1, "PREDICT", "WORKER", [])
      self.assertLess(time.monotonic() - begin, 0.1)
    self.assertEqual(self.diag._COUNTERS["contention_drops"], 4)

  def test_blocked_logger_does_not_block_or_grow_producer_queue(self):
    """A stalled diagnostic sink cannot become an inference synchronization point."""
    entered, release = threading.Event(), threading.Event()

    class SlowLog:
      def P(self, line):
        entered.set()
        release.wait(3)

    diag = _load()
    diag.start(SlowLog())
    self.assertTrue(entered.wait(1))
    try:
      begin = time.monotonic()
      for index in range(1000):
        diag.observe(None, "WORKER" + str(index), "send", index, reason="no_free_slot")
      self.assertLess(time.monotonic() - begin, 0.5)
      self.assertLessEqual(len(diag._OUTPUT), diag._QUEUE_LIMIT)
      self.assertGreater(diag._COUNTERS["output_drops"], 0)
    finally:
      release.set()

  @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires POSIX fork")
  def test_fork_replaces_parent_emitter_and_inherited_lock(self):
    """A child starts its own daemon even when the parent's state lock was held."""
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    diag = self.diag

    def run_child():
      log = _Log()
      reset = not diag._STARTED and not diag._HISTORY and not diag._PENDING
      started = diag.start(log)
      ok = diag.observe(log, "CHILD", "send", 1, reason="no_free_slot")
      child.send((reset, started, ok, log.wait(2)))
      child.close()

    with diag._LOCK:
      process = context.Process(target=run_child)
      process.start()
    try:
      self.assertTrue(parent.poll(3))
      self.assertEqual(parent.recv(), (True, True, True, True))
      process.join(3)
      self.assertEqual(process.exitcode, 0)
    finally:
      if process.is_alive():
        process.terminate()
        process.join(3)
      parent.close()
      child.close()


if __name__ == "__main__":
  unittest.main()
