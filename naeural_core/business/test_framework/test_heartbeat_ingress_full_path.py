import base64
import hashlib
import json
import math
import os
import tempfile
import time
import unittest
from uuid import uuid4

from naeural_core import constants as ct
from naeural_core.comm.heartbeat_ingress import (
  HeartbeatIngressProcessor,
  HeartbeatIngressWorker,
)
from naeural_core.comm.message_buffer import ObservableMessageBuffer
from naeural_core.business.test_framework.test_netmon_register_heartbeat_profile import (
  _NetmonHarness,
)
from ratio1.bc import DefaultBlockEngine
from ratio1.comm.mqtt_wrapper import MQTTWrapper
from ratio1.const import COMMS
from ratio1.io_formatter import IOFormatterWrapper


PROFILE_MESSAGES_ENV = "EE_HEARTBEAT_INGRESS_PROFILE_MESSAGES"
PROFILE_SENDERS_ENV = "EE_HEARTBEAT_INGRESS_PROFILE_SENDERS"
PROFILE_AUTH_WORKERS_ENV = "EE_HEARTBEAT_INGRESS_PROFILE_AUTH_WORKERS"
PROFILE_PERSIST_VERIFY_STATS_ENV = (
  "EE_HEARTBEAT_INGRESS_PROFILE_PERSIST_VERIFY_STATS"
)
PROFILE_ASSERT_TARGETS_ENV = "EE_HEARTBEAT_INGRESS_ASSERT_TARGETS"
DEFAULT_PROFILE_MESSAGES = 32
DEFAULT_PROFILE_AUTH_WORKERS = 4
MIN_PROFILE_WIRE_BYTES = 5500
MIN_TARGET_MESSAGES_PER_SECOND = 1000
MAX_TARGET_BURST_SECONDS = 10


def _percentile(values, percentile):
  """Return a nearest-rank percentile for a non-empty numeric sequence.

  Parameters
  ----------
  values : iterable of float
    Observed latency values.
  percentile : float
    Percentile in the inclusive ``0`` to ``100`` range.

  Returns
  -------
  float
    Nearest-rank percentile value.
  """
  ordered = sorted(values)
  rank = max(1, math.ceil((percentile / 100) * len(ordered)))
  return ordered[min(rank - 1, len(ordered) - 1)]


def _env_int(name, default):
  try:
    return int(os.environ.get(name, default))
  except Exception:
    return default


def _env_bool(name, default=False):
  value = os.environ.get(name)
  if value is None:
    return default
  return str(value).strip().lower() in {"1", "true", "yes", "on"}


class _QuietCommLog:
  def P(self, *args, **kwargs):
    return


class _MqttMessage:
  def __init__(self, payload):
    self.payload = payload


def _mqtt_config():
  return {
    COMMS.HOST: "localhost",
    COMMS.PORT: 1883,
    COMMS.USER: "",
    COMMS.PASS: "",
    COMMS.EE_ADDR: "0xSELF",
    COMMS.QOS: 1,
    COMMS.SECURED: 0,
    COMMS.COMMUNICATION_CTRL_CHANNEL: {
      COMMS.TOPIC: "test/ctrl",
      COMMS.QOS: 1,
    },
  }


def _deterministic_padding(message_idx, raw_bytes=3200):
  """Return repeatable, compression-resistant heartbeat telemetry padding."""
  chunks = []
  counter = 0
  while sum(len(chunk) for chunk in chunks) < raw_bytes:
    seed = "{}:{}".format(message_idx, counter).encode("ascii")
    chunks.append(hashlib.sha512(seed).digest())
    counter += 1
  return base64.b85encode(b"".join(chunks)[:raw_bytes]).decode("ascii")


class _FullPathHarness:
  def __init__(
      self,
      message_count,
      sender_count=1,
      auth_workers=4,
      persist_verify_stats=False,
  ):
    self.netmon_harness = _NetmonHarness()
    self.log = self.netmon_harness.log
    self.persist_verify_stats = persist_verify_stats
    self._allowed_file_path = os.path.join(
      self.log.base_folder,
      "authorized_addrs",
    )
    self._allowed_file_existed = os.path.exists(self._allowed_file_path)
    self._key_directory = tempfile.TemporaryDirectory(
      prefix="heartbeat-ingress-profile-",
    )
    self.block_engines = []
    for sender_idx in range(sender_count):
      engine_name = "hb-ingress-profile-{}-{}".format(
        uuid4().hex,
        sender_idx,
      )
      self.block_engines.append(DefaultBlockEngine(
        name=engine_name,
        log=self.log,
        config={
          "PEM_FILE": os.path.join(
            self._key_directory.name,
            engine_name + ".pem",
          ),
        },
        eth_enabled=False,
        verbosity=0,
      ))
    if persist_verify_stats:
      self.block_engines[0]._verify_canon_stats_path = os.path.join(
        self._key_directory.name,
        "verify_canon_stats.json",
      )
    self.formatters = IOFormatterWrapper(self.log)
    self.buffer = ObservableMessageBuffer(capacity=max(message_count, 1))
    self.wrapper = MQTTWrapper(
      log=_QuietCommLog(),
      config=_mqtt_config(),
      recv_buff=self.buffer,
      recv_channel_name=COMMS.COMMUNICATION_CTRL_CHANNEL,
      verbosity=99,
    )
    self.client = object()
    self.wrapper._mqttc = self.client
    self.commit_order = []
    self.commit_addresses = []
    self.admitted_at = {}
    self.commit_latencies = []
    self.processor = HeartbeatIngressProcessor(
      verify_message=self._verify,
      decode_message=self._decode,
      register_heartbeat=self._register,
      decompress_text=self.log.decompress_text,
      normalize_address=self._normalize_address,
      auth_mode="enforce",
      max_seen_hashes=max(message_count, 1),
    )
    self.worker = HeartbeatIngressWorker(
      message_buffer=self.buffer,
      prepare_message=self.processor.authenticate,
      commit_message=self.processor.commit_authenticated,
      prepare_workers=auth_workers,
      max_in_flight=auth_workers * 8,
      poll_timeout=0.001,
    )

  def _verify(self, message):
    return self.block_engines[0].verify(
      message,
      return_full_info=True,
      verify_allowed=False,
      log_hash_sign_fails=False,
      persist_canon_stats=False,
    )

  def _decode(self, message):
    formatter = self.formatters.get_required_formatter_from_payload(message)
    return formatter.decode_output(message)

  def _register(self, address, message):
    timestamp = message[ct.PAYLOAD_DATA.EE_TIMESTAMP]
    self.commit_order.append(timestamp)
    self.commit_addresses.append(address)
    self.commit_latencies.append(
      time.perf_counter() - self.admitted_at[timestamp]
    )
    self.netmon_harness.netmon.register_heartbeat(address, message)

  def _normalize_address(self, address):
    if not isinstance(address, str):
      return None
    lowered = address.lower()
    for prefix in ("0xai_", "aixp_"):
      if lowered.startswith(prefix):
        return address[len(prefix):]
    return address

  def build_message(self, message_idx):
    sender_idx = message_idx % len(self.block_engines)
    block_engine = self.block_engines[sender_idx]
    address = block_engine.address
    body = self.netmon_harness.make_heartbeat_body(
      addr=address,
      node_idx=sender_idx,
      hb_idx=message_idx,
      payload_scale=4,
    )
    body["PROFILE_PADDING"] = _deterministic_padding(message_idx)
    envelope = {
      ct.HB.ENCODED_DATA: self.log.compress_text(json.dumps(body)),
      ct.EE_ID: body[ct.EE_ID],
      ct.HB.EE_ADDR: address,
      ct.PAYLOAD_DATA.EE_TIMESTAMP: body[ct.PAYLOAD_DATA.EE_TIMESTAMP],
      ct.PAYLOAD_DATA.EE_TIMEZONE: body[ct.PAYLOAD_DATA.EE_TIMEZONE],
      ct.PAYLOAD_DATA.EE_EVENT_TYPE: ct.HEARTBEAT,
    }
    block_engine.sign(envelope, add_data=True, use_digest=True)
    return (
      json.dumps(envelope),
      envelope[ct.PAYLOAD_DATA.EE_TIMESTAMP],
      address,
    )

  def deliver(self, raw_message):
    self.wrapper._callback_on_message(
      self.client,
      None,
      _MqttMessage(raw_message.encode("utf-8")),
    )

  def close(self):
    """Remove temporary signer material created for this isolated profile."""
    self.block_engines[0].wait_for_verify_canon_stats_flush(timeout=2.0)
    self._key_directory.cleanup()
    if not self._allowed_file_existed and os.path.isfile(self._allowed_file_path):
      os.remove(self._allowed_file_path)


class TestHeartbeatIngressFullPath(unittest.TestCase):

  def test_signed_compressed_callback_worker_netmon_epoch_profile(self):
    message_count = _env_int(PROFILE_MESSAGES_ENV, DEFAULT_PROFILE_MESSAGES)
    sender_count = _env_int(PROFILE_SENDERS_ENV, 1)
    auth_workers = _env_int(
      PROFILE_AUTH_WORKERS_ENV,
      DEFAULT_PROFILE_AUTH_WORKERS,
    )
    persist_verify_stats = _env_bool(PROFILE_PERSIST_VERIFY_STATS_ENV)
    self.assertGreater(message_count, 0)
    self.assertGreater(sender_count, 0)
    self.assertGreater(auth_workers, 0)
    self.assertLessEqual(sender_count, message_count)
    harness = _FullPathHarness(
      message_count,
      sender_count=sender_count,
      auth_workers=auth_workers,
      persist_verify_stats=persist_verify_stats,
    )
    self.addCleanup(harness.close)
    built = [harness.build_message(idx) for idx in range(message_count)]
    raw_messages = [item[0] for item in built]
    expected_order = [item[1] for item in built]
    wire_sizes = [len(message.encode("utf-8")) for message in raw_messages]

    self.assertGreaterEqual(min(wire_sizes), MIN_PROFILE_WIRE_BYTES)

    harness.worker.start()
    started = time.perf_counter()
    admission_started = started
    for raw_message, expected_timestamp, _sender in built:
      harness.admitted_at[expected_timestamp] = time.perf_counter()
      harness.deliver(raw_message)
    admission_elapsed = time.perf_counter() - admission_started
    drained = harness.worker.wait_until_idle(timeout=MAX_TARGET_BURST_SECONDS * 2)
    elapsed = time.perf_counter() - started
    harness.worker.stop(drain=True, timeout=2.0)

    stats_flush_elapsed = 0.0
    stats_flushed = True
    persisted_verify_count = None
    if persist_verify_stats:
      stats_flush_started = time.perf_counter()
      stats_flushed = harness.block_engines[0].flush_verify_canon_stats(
        wait=True,
        timeout=5.0,
      )
      stats_flush_elapsed = time.perf_counter() - stats_flush_started
      with open(
          harness.block_engines[0]._verify_canon_stats_path,
          "rt",
        ) as stats_file:
        persisted_stats = json.load(stats_file)
      persisted_verify_count = sum(persisted_stats["total"].values())
    total_elapsed = time.perf_counter() - started

    buffer_snapshot = harness.buffer.snapshot()
    processor_snapshot = harness.processor.snapshot()
    messages_per_second = message_count / elapsed if elapsed else float("inf")
    admission_per_second = (
      message_count / admission_elapsed if admission_elapsed else float("inf")
    )
    profile = {
      "admission_messages_per_second": round(admission_per_second, 2),
      "auth_workers": auth_workers,
      "committed": processor_snapshot.committed,
      "elapsed_seconds": round(elapsed, 6),
      "epoch_register_calls": harness.netmon_harness.epoch_manager.calls,
      "high_water_mark": buffer_snapshot.high_water_mark,
      "max_wire_bytes": max(wire_sizes),
      "messages": message_count,
      "messages_per_second": round(messages_per_second, 2),
      "min_wire_bytes": min(wire_sizes),
      "commit_latency_p50_seconds": round(
        _percentile(harness.commit_latencies, 50),
        6,
      ),
      "commit_latency_p95_seconds": round(
        _percentile(harness.commit_latencies, 95),
        6,
      ),
      "commit_latency_p99_seconds": round(
        _percentile(harness.commit_latencies, 99),
        6,
      ),
      "queue_rejected_full": buffer_snapshot.rejected_full,
      "persist_verify_stats": persist_verify_stats,
      "persisted_verify_count": persisted_verify_count,
      "senders": sender_count,
      "total_elapsed_seconds": round(total_elapsed, 6),
      "verify_stats_flush_seconds_after_commit": round(
        stats_flush_elapsed,
        6,
      ),
      "verify_stats_persisted_on_hot_path": False,
    }
    print("HEARTBEAT_INGRESS_FULL_PATH_PROFILE " + json.dumps(profile, sort_keys=True))

    self.assertTrue(drained)
    self.assertIsNone(harness.worker.last_error)
    self.assertEqual(harness.wrapper.nr_dropped_messages, 0)
    self.assertEqual(buffer_snapshot.admitted, message_count)
    self.assertEqual(buffer_snapshot.dequeued, message_count)
    self.assertEqual(buffer_snapshot.depth, 0)
    self.assertEqual(buffer_snapshot.rejected_full, 0)
    self.assertTrue(buffer_snapshot.conserved)
    self.assertEqual(processor_snapshot.received, message_count)
    self.assertEqual(processor_snapshot.committed, message_count)
    self.assertEqual(processor_snapshot.auth_invalid, 0)
    self.assertEqual(processor_snapshot.identity_mismatch, 0)
    self.assertTrue(processor_snapshot.conserved)
    self.assertEqual(harness.commit_order, expected_order)
    self.assertEqual(
      set(harness.commit_addresses),
      {engine.address for engine in harness.block_engines},
    )
    self.assertEqual(harness.netmon_harness.epoch_manager.calls, message_count)
    self.assertTrue(stats_flushed)
    if persist_verify_stats:
      self.assertEqual(persisted_verify_count, message_count)

    latest = harness.netmon_harness.netmon.network_node_last_heartbeat(
      built[-1][2],
    )
    self.assertEqual(latest[ct.PAYLOAD_DATA.EE_TIMESTAMP], expected_order[-1])

    if _env_bool(PROFILE_ASSERT_TARGETS_ENV):
      self.assertGreaterEqual(
        messages_per_second,
        MIN_TARGET_MESSAGES_PER_SECOND,
      )
      if message_count >= 5000:
        self.assertLess(elapsed, MAX_TARGET_BURST_SECONDS)


if __name__ == "__main__":
  unittest.main()
