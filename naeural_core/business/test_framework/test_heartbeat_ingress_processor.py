import importlib.util
import json
import pathlib
import queue
import threading
import time
import unittest


MODULE_PATH = (
  pathlib.Path(__file__).resolve().parents[2]
  / "comm"
  / "heartbeat_ingress.py"
)
SPEC = importlib.util.spec_from_file_location(
  "heartbeat_ingress_under_test",
  MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HeartbeatIngressProcessor = MODULE.HeartbeatIngressProcessor
HeartbeatIngressWorker = MODULE.HeartbeatIngressWorker


class _VerifyResult:
  def __init__(self, valid=True, sender="0xai_NODE", message="ok"):
    self.valid = valid
    self.sender = sender
    self.message = message


def _heartbeat(**overrides):
  data = {
    "EE_HASH": "hash-1",
    "EE_SIGN": "signature",
    "EE_SENDER": "0xai_NODE",
    "EE_ADDR": "0xai_NODE",
    "EE_EVENT_TYPE": "HEARTBEAT",
    "EE_TIMESTAMP": "2026-08-17 10:00:00.000000",
  }
  data.update(overrides)
  return data


def _normalizer(address):
  if not isinstance(address, str):
    return None
  lowered = address.lower()
  for prefix in ("0xai_", "aixp_"):
    if lowered.startswith(prefix):
      return address[len(prefix):]
  return address


class _Harness:
  def __init__(
      self,
      auth_mode="enforce",
      verify_result=None,
      decoder=None,
      decompress_text=None,
  ):
    self.verify_inputs = []
    self.registered = []
    self.verify_result = verify_result or _VerifyResult()
    self.decoder = decoder or (lambda value: dict(value))
    self.processor = HeartbeatIngressProcessor(
      verify_message=self._verify,
      decode_message=self.decoder,
      register_heartbeat=self._register,
      decompress_text=decompress_text or (lambda value: value),
      normalize_address=_normalizer,
      auth_mode=auth_mode,
      max_seen_hashes=100,
    )

  def _verify(self, value):
    self.verify_inputs.append(dict(value))
    return self.verify_result

  def _register(self, address, value):
    self.registered.append((address, dict(value)))


class TestHeartbeatIngressProcessor(unittest.TestCase):

  def test_valid_heartbeat_is_verified_before_mutating_decode(self):
    original = _heartbeat()

    def mutating_decoder(value):
      value.pop("EE_SIGN")
      return value

    harness = _Harness(decoder=mutating_decoder)
    outcome = harness.processor.process(json.dumps(original))

    self.assertEqual(outcome, "committed")
    self.assertEqual(harness.verify_inputs[0], original)
    self.assertEqual(harness.registered[0][0], "0xai_NODE")
    self.assertEqual(harness.processor.snapshot().committed, 1)
    self.assertTrue(harness.processor.snapshot().conserved)

  def test_invalid_signature_enforce_rejects_without_state_mutation(self):
    harness = _Harness(
      auth_mode="enforce",
      verify_result=_VerifyResult(valid=False, message="tampered"),
    )

    outcome = harness.processor.process(json.dumps(_heartbeat()))

    self.assertEqual(outcome, "auth_rejected")
    self.assertEqual(harness.registered, [])
    snapshot = harness.processor.snapshot()
    self.assertEqual(snapshot.auth_invalid, 1)
    self.assertEqual(snapshot.auth_rejected, 1)
    self.assertTrue(snapshot.conserved)

  def test_invalid_signature_shadow_records_but_preserves_rollout_behavior(self):
    harness = _Harness(
      auth_mode="shadow",
      verify_result=_VerifyResult(valid=False, message="legacy invalid"),
    )

    outcome = harness.processor.process(json.dumps(_heartbeat()))

    self.assertEqual(outcome, "committed")
    self.assertEqual(len(harness.registered), 1)
    self.assertEqual(harness.processor.snapshot().auth_invalid, 1)

  def test_present_claim_mismatch_enforce_rejects(self):
    harness = _Harness(auth_mode="enforce")

    outcome = harness.processor.process(json.dumps(
      _heartbeat(EE_ADDR="0xai_ATTACKER"),
    ))

    self.assertEqual(outcome, "identity_rejected")
    self.assertEqual(harness.registered, [])
    self.assertEqual(harness.processor.snapshot().identity_mismatch, 1)

  def test_decoder_cannot_hide_raw_identity_mismatch(self):
    def overwrite_claim(value):
      value["EE_ADDR"] = "0xai_NODE"
      return value

    harness = _Harness(auth_mode="enforce", decoder=overwrite_claim)

    outcome = harness.processor.process(json.dumps(
      _heartbeat(EE_ADDR="0xai_ATTACKER"),
    ))

    self.assertEqual(outcome, "identity_rejected")
    self.assertEqual(harness.registered, [])

  def test_legacy_prefix_variant_matches_verified_sender(self):
    harness = _Harness(auth_mode="enforce")

    outcome = harness.processor.process(json.dumps(
      _heartbeat(EE_ADDR="aixp_NODE"),
    ))

    self.assertEqual(outcome, "committed")
    self.assertEqual(len(harness.registered), 1)

  def test_missing_legacy_claim_is_counted_but_not_rejected(self):
    harness = _Harness(auth_mode="enforce")
    message = _heartbeat()
    message.pop("EE_ADDR")

    outcome = harness.processor.process(json.dumps(message))

    self.assertEqual(outcome, "committed")
    self.assertEqual(harness.registered[0][0], "0xai_NODE")
    self.assertEqual(harness.processor.snapshot().identity_missing, 1)

  def test_compressed_inner_identity_mismatch_rejects(self):
    harness = _Harness(auth_mode="enforce")
    message = _heartbeat(
      ENCODED_DATA=json.dumps({"EE_ADDR": "0xai_ATTACKER"}),
    )

    outcome = harness.processor.process(json.dumps(message))

    self.assertEqual(outcome, "identity_rejected")
    self.assertEqual(harness.registered, [])

  def test_compressed_identity_is_expanded_once_before_formatter_mutation(self):
    decompressed = []

    def decompress_once(value):
      decompressed.append(value)
      return value

    harness = _Harness(
      auth_mode="enforce",
      decompress_text=decompress_once,
    )
    message = _heartbeat(
      ENCODED_DATA=json.dumps({"EE_ADDR": "0xai_NODE"}),
    )

    outcome = harness.processor.process(json.dumps(message))

    self.assertEqual(outcome, "committed")
    self.assertEqual(decompressed, [message["ENCODED_DATA"]])

  def test_formatter_exposed_compressed_claim_is_validated_once(self):
    for mode in ('enforce', 'shadow'):
      for claim in ('0xai_NODE', 'aixp_NODE', '0xai_OTHER'):
        for original_encoded in (None, json.dumps({'EE_ADDR': '0xai_NODE'})):
          with self.subTest(mode=mode, claim=claim, original=original_encoded):
            decompressed = []

            def decode(value):
              value.update(value.pop('DATA'))
              return value

            def decompress(value):
              decompressed.append(value)
              return value

            harness = _Harness(auth_mode=mode, decoder=decode, decompress_text=decompress)
            inner = json.dumps({'EE_ADDR': claim})
            message = _heartbeat(DATA={'ENCODED_DATA': inner})
            if original_encoded is not None:
              message['ENCODED_DATA'] = original_encoded
            outcome = harness.processor.process(json.dumps(message))
            mismatch = claim == '0xai_OTHER'
            expected = 'identity_rejected' if mismatch and mode == 'enforce' else 'committed'
            self.assertEqual(outcome, expected)
            self.assertEqual(harness.processor.snapshot().identity_mismatch, int(mismatch))
            expected_expansions = [original_encoded] if original_encoded is not None else []
            if inner != original_encoded:
              expected_expansions.append(inner)
            self.assertEqual(decompressed, expected_expansions)
            self.assertEqual(len(harness.registered), int(expected == 'committed'))

  def test_processing_failure_does_not_poison_dedup_retry(self):
    harness = _Harness(auth_mode="enforce")
    attempts = []

    def fail_once(address, value):
      attempts.append((address, value))
      if len(attempts) == 1:
        raise RuntimeError("temporary state-owner failure")
      harness.registered.append((address, dict(value)))

    harness.processor._register_heartbeat = fail_once
    raw = json.dumps(_heartbeat())

    self.assertEqual(harness.processor.process(raw), "processing_failed")
    self.assertEqual(harness.processor.process(raw), "committed")
    self.assertEqual(len(attempts), 2)
    self.assertEqual(len(harness.registered), 1)

  def test_duplicate_is_suppressed_only_after_successful_commit(self):
    harness = _Harness(auth_mode="enforce")
    raw = json.dumps(_heartbeat())

    self.assertEqual(harness.processor.process(raw), "committed")
    self.assertEqual(harness.processor.process(raw), "duplicate")
    self.assertEqual(len(harness.registered), 1)
    self.assertTrue(harness.processor.snapshot().conserved)

  def test_malformed_and_non_heartbeat_inputs_are_terminally_accounted(self):
    harness = _Harness(auth_mode="enforce")

    self.assertEqual(harness.processor.process("not json"), "invalid_json")
    self.assertEqual(
      harness.processor.process(json.dumps(_heartbeat(EE_EVENT_TYPE="PAYLOAD"))),
      "ignored_non_heartbeat",
    )
    snapshot = harness.processor.snapshot()
    self.assertEqual(snapshot.received, 2)
    self.assertTrue(snapshot.conserved)

  def test_parallel_invalid_signature_flood_cannot_mutate_state(self):
    item_count = 2000
    harness = _Harness(
      auth_mode="enforce",
      verify_result=_VerifyResult(valid=False, message="tampered"),
    )
    messages = queue.Queue(maxsize=item_count)
    for value in range(item_count):
      messages.put(json.dumps(_heartbeat(EE_HASH="invalid-{}".format(value))))
    worker = HeartbeatIngressWorker(
      message_buffer=messages,
      prepare_message=harness.processor.authenticate,
      commit_message=harness.processor.commit_authenticated,
      prepare_workers=4,
      max_in_flight=32,
      poll_timeout=0.001,
    )

    worker.start()
    self.assertTrue(worker.wait_until_idle(timeout=10.0))
    worker.stop(drain=True, timeout=2.0)

    snapshot = harness.processor.snapshot()
    self.assertEqual(harness.registered, [])
    self.assertEqual(snapshot.received, item_count)
    self.assertEqual(snapshot.auth_invalid, item_count)
    self.assertEqual(snapshot.auth_rejected, item_count)
    self.assertEqual(snapshot.in_flight, 0)
    self.assertTrue(snapshot.conserved)


class TestHeartbeatIngressWorker(unittest.TestCase):

  def test_worker_drains_synchronized_burst_in_fifo_order(self):
    item_count = 5000
    messages = queue.Queue(maxsize=item_count)
    committed = []
    for value in range(item_count):
      messages.put(value)

    worker = HeartbeatIngressWorker(
      message_buffer=messages,
      process_message=committed.append,
      poll_timeout=0.001,
    )
    worker.start()
    self.assertTrue(worker.wait_until_idle(timeout=10.0))
    worker.stop(drain=True, timeout=2.0)

    self.assertEqual(committed, list(range(item_count)))
    self.assertFalse(worker.is_alive())

  def test_parallel_authentication_keeps_one_fifo_commit_owner(self):
    item_count = 64
    messages = queue.Queue(maxsize=item_count)
    committed = []
    commit_thread_ids = set()
    preparation_thread_ids = set()
    active_preparations = 0
    max_active_preparations = 0
    preparation_lock = threading.Lock()
    for value in range(item_count):
      messages.put(value)

    def prepare(value):
      nonlocal active_preparations, max_active_preparations
      with preparation_lock:
        preparation_thread_ids.add(threading.get_ident())
        active_preparations += 1
        max_active_preparations = max(
          max_active_preparations,
          active_preparations,
        )
      try:
        if value < 4:
          time.sleep(0.02)
        time.sleep((3 - (value % 4)) * 0.0005)
        return value
      finally:
        with preparation_lock:
          active_preparations -= 1

    def commit(value):
      commit_thread_ids.add(threading.get_ident())
      committed.append(value)

    worker = HeartbeatIngressWorker(
      message_buffer=messages,
      prepare_message=prepare,
      commit_message=commit,
      prepare_workers=4,
      max_in_flight=8,
      poll_timeout=0.001,
    )
    worker.start()
    self.assertTrue(worker.wait_until_idle(timeout=10.0))
    worker.stop(drain=True, timeout=2.0)

    self.assertEqual(committed, list(range(item_count)))
    self.assertEqual(len(commit_thread_ids), 1)
    self.assertGreater(len(preparation_thread_ids), 1)
    self.assertGreater(max_active_preparations, 1)
    self.assertEqual(worker.in_flight, 0)
    self.assertEqual(worker.prepare_workers, 4)
    self.assertEqual(worker.max_in_flight, 8)
    self.assertFalse(worker.is_alive())

  def test_wait_until_idle_waits_for_reserved_inflight_work(self):
    dequeued = threading.Event()
    allow_dequeue_return = threading.Event()

    class _GatedQueue(queue.Queue):
      def get(self, block=True, timeout=None):
        message = super().get(block=block, timeout=timeout)
        dequeued.set()
        allow_dequeue_return.wait(timeout=5.0)
        return message

    messages = _GatedQueue(maxsize=1)
    messages.put(1)
    processing_started = threading.Event()
    continue_processing = threading.Event()
    in_flight_seen = []
    worker_ref = []

    def prepare(message):
      processing_started.set()
      in_flight_seen.append(worker_ref[0].in_flight if worker_ref else None)
      continue_processing.wait(timeout=5.0)
      return message

    message_worker = HeartbeatIngressWorker(
      message_buffer=messages,
      prepare_message=prepare,
      commit_message=lambda message: None,
      prepare_workers=1,
      max_in_flight=1,
      poll_timeout=0.001,
    )
    worker_ref.append(message_worker)

    message_worker.start()
    self.addCleanup(message_worker.stop, drain=False, timeout=2.0)
    self.addCleanup(continue_processing.set)
    self.addCleanup(allow_dequeue_return.set)
    self.assertTrue(dequeued.wait(timeout=5.0))
    handoff_lock_acquired = message_worker._state_condition.acquire(
      blocking=False,
    )
    if handoff_lock_acquired:
      message_worker._state_condition.release()
    allow_dequeue_return.set()

    self.assertFalse(
      handoff_lock_acquired,
      "dequeue and in-flight reservation must share the idle-state lock",
    )
    self.assertTrue(processing_started.wait(timeout=5.0))
    self.assertEqual(message_worker.in_flight, 1)
    self.assertFalse(message_worker.wait_until_idle(timeout=0.2))
    continue_processing.set()
    self.assertTrue(message_worker.wait_until_idle(timeout=5.0))
    message_worker.stop(drain=True, timeout=2.0)

    self.assertEqual(message_worker.in_flight, 0)
    self.assertEqual(in_flight_seen, [1])
    self.assertFalse(message_worker.is_alive())

  def test_parallel_worker_drains_admitted_messages_during_stop(self):
    item_count = 100
    messages = queue.Queue(maxsize=item_count)
    committed = []
    for value in range(item_count):
      messages.put(value)

    def prepare(value):
      time.sleep(0.0005)
      return value

    worker = HeartbeatIngressWorker(
      message_buffer=messages,
      prepare_message=prepare,
      commit_message=committed.append,
      prepare_workers=4,
      max_in_flight=16,
      poll_timeout=0.001,
    )

    worker.start()
    worker.stop(drain=True, timeout=5.0)

    self.assertEqual(committed, list(range(item_count)))
    self.assertEqual(worker.in_flight, 0)
    self.assertFalse(worker.is_alive())

  def test_forced_stop_prevents_unadmitted_commit_after_drain_timeout(self):
    messages = queue.Queue(maxsize=1)
    messages.put("heartbeat")
    preparation_started = threading.Event()
    allow_preparation = threading.Event()
    committed = []

    def prepare(message):
      preparation_started.set()
      allow_preparation.wait(timeout=5.0)
      return message

    worker = HeartbeatIngressWorker(
      message_buffer=messages,
      prepare_message=prepare,
      commit_message=committed.append,
      prepare_workers=1,
      max_in_flight=1,
      poll_timeout=0.001,
    )
    worker.start()
    self.assertTrue(preparation_started.wait(timeout=2.0))

    worker.stop(drain=True, timeout=0.01)
    self.assertTrue(worker.is_alive())
    worker.stop(drain=False, timeout=0)
    self.assertFalse(worker.commit_in_progress)
    allow_preparation.set()
    worker.stop(drain=False, timeout=2.0)

    self.assertFalse(worker.is_alive())
    self.assertEqual(committed, [])

  def test_forced_stop_does_not_block_on_commit_in_progress(self):
    messages = queue.Queue(maxsize=1)
    messages.put("heartbeat")
    commit_started = threading.Event()
    allow_commit = threading.Event()
    committed = []

    def commit(message):
      commit_started.set()
      allow_commit.wait(timeout=5.0)
      committed.append(message)

    worker = HeartbeatIngressWorker(
      message_buffer=messages,
      prepare_message=lambda message: message,
      commit_message=commit,
      prepare_workers=1,
      max_in_flight=1,
      poll_timeout=0.001,
    )
    worker.start()
    self.assertTrue(commit_started.wait(timeout=2.0))
    self.assertTrue(worker.commit_in_progress)

    worker.stop(drain=True, timeout=0.01)
    self.assertTrue(worker.is_alive())
    started = time.monotonic()
    worker.stop(drain=False, timeout=0)
    self.assertLess(time.monotonic() - started, 0.1)
    self.assertTrue(worker.is_alive())
    self.assertTrue(worker.commit_in_progress)

    allow_commit.set()
    worker.stop(drain=False, timeout=2.0)

    self.assertFalse(worker.is_alive())
    self.assertFalse(worker.commit_in_progress)
    self.assertEqual(committed, ["heartbeat"])


if __name__ == "__main__":
  unittest.main()
