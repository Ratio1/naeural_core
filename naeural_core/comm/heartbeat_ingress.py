"""Authenticated ordered processing for raw heartbeat ingress messages."""

import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import Empty
from threading import Condition, Event, Lock, Thread
from time import monotonic


AUTH_MODES = ("off", "shadow", "enforce")
TERMINAL_OUTCOMES = (
  "invalid_json",
  "auth_rejected",
  "identity_rejected",
  "decode_rejected",
  "duplicate",
  "ignored_non_heartbeat",
  "committed",
  "processing_failed",
)


@dataclass(frozen=True)
class HeartbeatIngressSnapshot:
  received: int
  in_flight: int
  invalid_json: int
  auth_invalid: int
  auth_rejected: int
  identity_missing: int
  identity_mismatch: int
  identity_rejected: int
  decode_rejected: int
  duplicate: int
  ignored_non_heartbeat: int
  committed: int
  processing_failed: int
  conserved: bool


@dataclass(frozen=True)
class AuthenticatedHeartbeat:
  """Result of stateless raw-envelope parsing and signature verification."""

  message: object = None
  verified_sender: object = None
  signature_valid: bool = False
  terminal_outcome: str = None


class HeartbeatIngressProcessor:
  """Verify, canonicalize, deduplicate, and commit one heartbeat at a time.

  The processor has no internal worker pool. One caller owns the ordered commit
  lane, while injected functions keep transport, crypto, formatter, and NetMon
  dependencies testable in isolation.
  """

  def __init__(
      self,
      verify_message,
      decode_message,
      register_heartbeat,
      decompress_text,
      normalize_address,
      auth_mode="shadow",
      max_seen_hashes=10000,
  ):
    auth_mode = str(auth_mode).strip().lower()
    if auth_mode not in AUTH_MODES:
      raise ValueError(
        "Invalid heartbeat auth mode '{}'; expected one of {}".format(
          auth_mode, AUTH_MODES,
        )
      )
    max_seen_hashes = int(max_seen_hashes)
    if max_seen_hashes <= 0:
      raise ValueError("max_seen_hashes must be greater than zero")

    self._verify_message = verify_message
    self._decode_message = decode_message
    self._register_heartbeat = register_heartbeat
    self._decompress_text = decompress_text
    self._normalize_address = normalize_address
    self._auth_mode = auth_mode
    self._max_seen_hashes = max_seen_hashes
    self._seen_hashes = deque()
    self._seen_hash_set = set()
    self._stats_lock = Lock()
    self._stats = {
      "received": 0,
      "in_flight": 0,
      "auth_invalid": 0,
      "identity_missing": 0,
      "identity_mismatch": 0,
      **{outcome: 0 for outcome in TERMINAL_OUTCOMES},
    }

  @property
  def auth_mode(self):
    return self._auth_mode

  def _start_message(self):
    with self._stats_lock:
      self._stats["received"] += 1
      self._stats["in_flight"] += 1

  def _record_diagnostic(self, name):
    with self._stats_lock:
      self._stats[name] += 1

  def _finish_message(self, outcome):
    with self._stats_lock:
      self._stats[outcome] += 1
      self._stats["in_flight"] -= 1
    return outcome

  def snapshot(self):
    """Return exact terminal conservation plus non-terminal auth diagnostics."""
    with self._stats_lock:
      stats = dict(self._stats)
    terminal_total = sum(stats[name] for name in TERMINAL_OUTCOMES)
    stats["conserved"] = stats["received"] == (
      stats["in_flight"] + terminal_total
    )
    return HeartbeatIngressSnapshot(**stats)

  def _extract_compressed_claim(self, value):
    if not isinstance(value, str):
      return None
    decoded = json.loads(self._decompress_text(value))
    if isinstance(decoded, dict):
      return decoded.get("EE_ADDR")
    return None

  def _collect_identity_claims(
      self,
      raw_message,
      decoded_message,
      include_compressed=True,
  ):
    claims = []
    for value in (raw_message, decoded_message):
      if not isinstance(value, dict):
        continue
      claims.append(value.get("EE_ADDR"))
      nested_data = value.get("DATA")
      if isinstance(nested_data, dict):
        claims.append(nested_data.get("EE_ADDR"))
      encoded_data = value.get("ENCODED_DATA")
      if include_compressed and encoded_data is not None:
        claims.append(self._extract_compressed_claim(encoded_data))
    return [claim for claim in claims if claim is not None]

  def _is_duplicate(self, payload_hash):
    return payload_hash is not None and payload_hash in self._seen_hash_set

  def _mark_committed_hash(self, payload_hash):
    if payload_hash is None or payload_hash in self._seen_hash_set:
      return
    if len(self._seen_hashes) >= self._max_seen_hashes:
      expired_hash = self._seen_hashes.popleft()
      self._seen_hash_set.discard(expired_hash)
    self._seen_hashes.append(payload_hash)
    self._seen_hash_set.add(payload_hash)

  def authenticate(self, raw_message):
    """Parse and verify one raw envelope without mutating protocol state."""
    self._start_message()
    try:
      try:
        message = json.loads(raw_message)
      except Exception:
        return AuthenticatedHeartbeat(terminal_outcome="invalid_json")
      if not isinstance(message, dict):
        return AuthenticatedHeartbeat(terminal_outcome="invalid_json")

      verified_sender = message.get("EE_SENDER")
      signature_valid = self._auth_mode == "off"
      if self._auth_mode != "off":
        try:
          verification = self._verify_message(message)
          signature_valid = bool(getattr(verification, "valid", False))
          verified_sender = getattr(verification, "sender", None) or verified_sender
        except Exception:
          signature_valid = False
        if not signature_valid:
          self._record_diagnostic("auth_invalid")
          if self._auth_mode == "enforce":
            return AuthenticatedHeartbeat(terminal_outcome="auth_rejected")

      return AuthenticatedHeartbeat(
        message=message,
        verified_sender=verified_sender,
        signature_valid=signature_valid,
      )
    except Exception:
      return AuthenticatedHeartbeat(terminal_outcome="processing_failed")

  def commit_authenticated(self, authenticated):
    """Canonicalize and commit one authenticated result on the FIFO owner."""
    if authenticated.terminal_outcome is not None:
      return self._finish_message(authenticated.terminal_outcome)

    message = authenticated.message
    verified_sender = authenticated.verified_sender
    signature_valid = authenticated.signature_valid
    try:

      payload_hash = message.get("EE_HASH")
      if self._is_duplicate(payload_hash):
        return self._finish_message("duplicate")

      try:
        # Formatters are allowed to mutate their input. Preserve every identity
        # claim from the verified wire envelope before handing it to the decoder.
        identity_claims = self._collect_identity_claims(message, {})
      except Exception:
        return self._finish_message("processing_failed")

      try:
        decoded_message = self._decode_message(message)
      except Exception:
        return self._finish_message("processing_failed")
      if not isinstance(decoded_message, dict):
        return self._finish_message("decode_rejected")

      event_type = decoded_message.get(
        "EE_EVENT_TYPE", message.get("EE_EVENT_TYPE"),
      )
      if event_type != "HEARTBEAT":
        return self._finish_message("ignored_non_heartbeat")

      try:
        # The signed compressed claim was captured before formatter mutation.
        # Do not expand the same ENCODED_DATA a second time when a formatter
        # returns the original envelope (the normal heartbeat path).
        identity_claims.extend(self._collect_identity_claims(
          {},
          decoded_message,
          include_compressed=False,
        ))
      except Exception:
        return self._finish_message("processing_failed")

      if len(identity_claims) == 0:
        self._record_diagnostic("identity_missing")

      normalized_sender = self._normalize_address(verified_sender)
      has_mismatch = any(
        self._normalize_address(claim) != normalized_sender
        for claim in identity_claims
      )
      if has_mismatch:
        self._record_diagnostic("identity_mismatch")
        if self._auth_mode == "enforce" and signature_valid:
          return self._finish_message("identity_rejected")

      claimed_address = (
        decoded_message.get("EE_ADDR")
        or (identity_claims[0] if identity_claims else None)
        or verified_sender
      )
      commit_address = (
        verified_sender
        if self._auth_mode == "enforce" and signature_valid
        else claimed_address
      )
      if commit_address is None:
        return self._finish_message("identity_rejected")

      try:
        self._register_heartbeat(commit_address, decoded_message)
      except Exception:
        return self._finish_message("processing_failed")

      self._mark_committed_hash(payload_hash)
      return self._finish_message("committed")
    except Exception:
      return self._finish_message("processing_failed")

  def process(self, raw_message):
    """Process one raw heartbeat synchronously through both ordered stages."""
    return self.commit_authenticated(self.authenticate(raw_message))


class HeartbeatIngressWorker:
  """Bounded parallel preparation with one ordered FIFO commit owner."""

  def __init__(
      self,
      message_buffer,
      process_message=None,
      poll_timeout=0.1,
      prepare_message=None,
      commit_message=None,
      prepare_workers=1,
      max_in_flight=None,
  ):
    uses_preparation = prepare_message is not None or commit_message is not None
    if uses_preparation and (
        not callable(prepare_message) or not callable(commit_message)
    ):
      raise ValueError(
        "prepare_message and commit_message must both be callable"
      )
    if not uses_preparation and not callable(process_message):
      raise ValueError("process_message must be callable in serial mode")

    prepare_workers = int(prepare_workers)
    if prepare_workers <= 0:
      raise ValueError("prepare_workers must be greater than zero")
    if max_in_flight is None:
      max_in_flight = prepare_workers * 4
    max_in_flight = int(max_in_flight)
    if max_in_flight < prepare_workers:
      raise ValueError("max_in_flight must be at least prepare_workers")

    self._message_buffer = message_buffer
    self._process_message = process_message
    self._prepare_message = prepare_message
    self._commit_message = commit_message
    self._prepare_workers = prepare_workers
    self._max_in_flight = max_in_flight
    self._uses_preparation = uses_preparation
    self._poll_timeout = max(float(poll_timeout), 0.001)
    self._stop_event = Event()
    self._state_condition = Condition()
    self._thread = None
    self._drain_on_stop = True
    self._in_flight = 0
    self._last_error = None

  def _buffer_is_empty(self):
    empty = getattr(self._message_buffer, "empty", None)
    if callable(empty):
      return empty()
    return len(self._message_buffer) == 0

  def _next_message(self, timeout=None):
    if timeout is None:
      timeout = self._poll_timeout
    try:
      return self._message_buffer.get(timeout=timeout)
    except Empty:
      return None

  def _mark_in_flight(self, delta):
    with self._state_condition:
      self._in_flight += delta
      if delta < 0:
        self._state_condition.notify_all()

  def _finish_buffer_message(self):
    task_done = getattr(self._message_buffer, "task_done", None)
    if callable(task_done):
      task_done()
    self._mark_in_flight(-1)

  def _run_serial(self):
    while True:
      if self._stop_event.is_set():
        if not self._drain_on_stop or self._buffer_is_empty():
          break

      message = self._next_message()
      if message is None:
        continue

      self._mark_in_flight(1)
      try:
        self._process_message(message)
      except Exception as exc:
        self._last_error = exc
      finally:
        self._finish_buffer_message()

  def _run_prepared(self):
    pending = deque()
    with ThreadPoolExecutor(
        max_workers=self._prepare_workers,
        thread_name_prefix="heartbeat-auth",
    ) as executor:
      while True:
        while len(pending) < self._max_in_flight:
          if self._stop_event.is_set() and not self._drain_on_stop:
            break
          timeout = self._poll_timeout if len(pending) == 0 else 0
          message = self._next_message(timeout=timeout)
          if message is None:
            break
          self._mark_in_flight(1)
          try:
            future = executor.submit(self._prepare_message, message)
          except Exception as exc:
            self._last_error = exc
            self._finish_buffer_message()
            continue
          pending.append(future)

        if len(pending) > 0:
          # Futures are consumed in submission order even when later auth work
          # completes first. Only this coordinator calls the stateful commit.
          future = pending.popleft()
          try:
            self._commit_message(future.result())
          except Exception as exc:
            self._last_error = exc
          finally:
            self._finish_buffer_message()
          continue

        if self._stop_event.is_set():
          if not self._drain_on_stop or self._buffer_is_empty():
            break

  def _run(self):
    if self._uses_preparation:
      self._run_prepared()
    else:
      self._run_serial()

    with self._state_condition:
      self._state_condition.notify_all()

  def start(self):
    if self._thread is not None and self._thread.is_alive():
      return
    self._stop_event.clear()
    self._thread = Thread(
      target=self._run,
      name="heartbeat-ingress",
      daemon=True,
    )
    self._thread.start()

  def wait_until_idle(self, timeout):
    deadline = monotonic() + max(float(timeout), 0.0)
    with self._state_condition:
      while not self._buffer_is_empty() or self._in_flight > 0:
        remaining = deadline - monotonic()
        if remaining <= 0:
          return False
        self._state_condition.wait(min(remaining, self._poll_timeout))
      return True

  def stop(self, drain=True, timeout=None):
    self._drain_on_stop = bool(drain)
    self._stop_event.set()
    if self._thread is not None:
      self._thread.join(timeout=timeout)

  def is_alive(self):
    return self._thread is not None and self._thread.is_alive()

  @property
  def last_error(self):
    return self._last_error

  @property
  def prepare_workers(self):
    return self._prepare_workers if self._uses_preparation else 0

  @property
  def max_in_flight(self):
    return self._max_in_flight if self._uses_preparation else 1

  @property
  def in_flight(self):
    with self._state_condition:
      return self._in_flight
