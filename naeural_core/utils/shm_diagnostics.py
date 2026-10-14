"""Always-on, bounded scalar evidence for the existing serving transport.

Inference callers collect scalar counters and enqueue bounded failure evidence.
Only the daemon output thread serializes records or invokes the runtime logger.
The legacy ``EE_SHM_DIAGNOSTICS`` variable is ignored, including malformed values.
"""

import json
import os
import threading
import time
from collections import OrderedDict, deque


_EVENTS = frozenset(("send", "receive", "result", "consumer", "configuration", "timeout"))
_TIMEOUT_FIELDS = frozenset(("worker_pid", "worker_alive", "worker_exitcode", "occupied_before",
                             "free_slots", "pending_count", "expected_cmd_id", "oldest_pending_ms",
                             "requested_server", "initialized", "capacity"))
_REASONS = frozenset((
  "array_too_large", "rank_too_large", "uninitialized", "not_buffer", "no_free_slot",
  "unadmitted_placeholder", "invalid_slot", "slot_unavailable", "absent_model",
  "wrong_type", "cardinality", "response_timeout", "cmd_id_mismatch",
  "unmatched_response", "observation_error", "transport_exception", "error_marker", "other",
))
_FIELDS = frozenset((
  "arrays", "bytes", "admitted", "read_ok", "images_read", "missing", "first_bad_ordinal", "slot",
  "reason", "rank", "elements", "capacity", "occupied_before",
  "occupied_after", "initialized", "images", "entries", "result_entries",
  "attempted", "completed", "exception_type", "stream", "correlated",
  "shm", "error_marker", "wrong_type", "observed_cmd_id", "observation_error",
  "requested_server", "worker_pid", "worker_alive", "worker_exitcode", "free_slots",
  "pending_count", "expected_cmd_id", "expected_entries", "expected_images", "returned_images",
  "oldest_pending_ms", "request_age_ms", "tracking_evictions", "failures", "missing_total",
  "first_failed_cmd_id", "last_failed_cmd_id", "first_failed_reason",
  "transport_kind", "segment_name", "max_elements", "max_rank", "dtype",
  "tracking_status", "result_contract",
)) | frozenset("fail_" + reason for reason in _REASONS) \
  | frozenset("suppressed_timeout_" + field for field in _TIMEOUT_FIELDS)
_MAX_TARGETS = 64
_MAX_STATES = 128
_MAX_PENDING = 64
_MAX_PENDING_TOTAL = 256
_MAX_ENTRIES = 64
_MAX_IMAGE_ITEMS = 256
_HISTORY_LIMIT = 128
_HISTORY_SECONDS = 10.0
_CONTEXT_LIMIT = 8
_QUEUE_LIMIT = 32
_OUTPUT_RECORDS_PER_SECOND = 32
_OUTPUT_BYTES_PER_SECOND = 65536
_LOCK = threading.Lock()
_STATE = OrderedDict()
_PENDING = OrderedDict()
_EVICTIONS = {}
_CONFIGURED = OrderedDict()
_TRACKING_COMPLETE = OrderedDict()
_LOSS_GENERATION = 0
_APPLIED_LOSS_GENERATION = 0
_IDENTITY_HISTORY_LOST = False
_HISTORY = deque(maxlen=_HISTORY_LIMIT)
_OUTPUT = deque(maxlen=_QUEUE_LIMIT)
_WAKE = threading.Event()
_STARTED = False
_LOG = None
_THREAD = None
_COUNTERS = dict.fromkeys(("history_evictions", "history_expired", "state_evictions",
                          "target_evictions", "tracking_evictions", "contention_drops",
                          "output_drops", "logger_errors", "budget_drops", "startup_errors",
                          "tracking_invalidations"), 0)


def _after_fork():
  """Reset child-local locks, bounded state and emitter lifecycle.

  Notes
  -----
  Threads do not survive fork. The child must call ``start`` with its own logger
  rather than retain the parent's logger or a permanently locked wake event.
  """
  global _LOCK, _STATE, _PENDING, _EVICTIONS, _CONFIGURED, _HISTORY, _OUTPUT
  global _WAKE, _STARTED, _LOG, _THREAD, _COUNTERS
  global _TRACKING_COMPLETE, _LOSS_GENERATION, _APPLIED_LOSS_GENERATION, _IDENTITY_HISTORY_LOST
  _LOCK = threading.Lock()
  _STATE, _PENDING, _CONFIGURED = OrderedDict(), OrderedDict(), OrderedDict()
  _EVICTIONS = {}
  _TRACKING_COMPLETE = OrderedDict()
  _LOSS_GENERATION, _APPLIED_LOSS_GENERATION = 0, 0
  _IDENTITY_HISTORY_LOST = False
  _HISTORY = deque(maxlen=_HISTORY_LIMIT)
  _OUTPUT = deque(maxlen=_QUEUE_LIMIT)
  _WAKE = threading.Event()
  _STARTED, _LOG, _THREAD = False, None, None
  _COUNTERS = dict.fromkeys(_COUNTERS, 0)


if hasattr(os, "register_at_fork"):
  os.register_at_fork(after_in_child=_after_fork)


def start(log):
  """Start one asynchronous diagnostic emitter per interpreter.

  Parameters
  ----------
  log : Logger
      Process-local runtime logger, retained until interpreter exit.

  Returns
  -------
  bool
      Whether this call started the emitter. Repeated calls are harmless.

  Notes
  -----
  Call at manager/worker startup, outside inference execution. The status record
  is asynchronous, so a broken logger never delays runtime startup.
  """
  global _STARTED, _LOG, _THREAD
  try:
    with _LOCK:
      if _STARTED:
        return False
      _LOG = log
      _OUTPUT.append({"event": "startup", "pid": os.getpid(), "always_on": True,
                      "history_limit": _HISTORY_LIMIT, "history_seconds": _HISTORY_SECONDS,
                      "context_limit": _CONTEXT_LIMIT, "queue_limit": _QUEUE_LIMIT,
                      "expiry": None, "legacy_env_ignored": "EE_SHM_DIAGNOSTICS" in os.environ})
      try:
        _THREAD = threading.Thread(target=_emit, name="shm-diagnostics", daemon=True)
        _THREAD.start()
      except Exception:
        # A later successful retry must still emit exactly one startup status.
        _OUTPUT.pop()
        _LOG, _THREAD = None, None
        _COUNTERS["startup_errors"] += 1
        return False
      _STARTED = True
    _WAKE.set()
    return True
  except Exception:
    # Failure to create a diagnostics thread must not abort runtime startup.
    _COUNTERS["startup_errors"] += 1
    return False


def _emit():
  """Serialize and log admitted records away from inference threads.

  Notes
  -----
  A blocked logger can stall this daemon only. Pending records remain bounded;
  output budgets renew every second instead of disabling diagnostics forever.
  """
  window, records, size = time.monotonic(), 0, 0
  while True:
    _WAKE.wait(0.1)
    with _LOCK:
      if not _OUTPUT:
        _WAKE.clear()
        continue
      record = _OUTPUT.popleft()
    now = time.monotonic()
    if now - window >= 1:
      window, records, size = now, 0, 0
    try:
      if "observed_at" in record:
        record["queue_delay_ms"] = max(0, int((now - record["observed_at"]) * 1000))
      line = "[SHM_DIAG] " + json.dumps(record, separators=(",", ":"), allow_nan=False)
      encoded = len(line.encode("utf-8"))
      if records >= _OUTPUT_RECORDS_PER_SECOND or size + encoded > _OUTPUT_BYTES_PER_SECOND:
        _COUNTERS["budget_drops"] += 1
        # Renew failed summary eligibility after budget loss. Merge only scalar
        # aggregate evidence, never the history snapshot or logger references.
        if record.get("anomaly") and record.get("failures"):
          with _LOCK:
            state = _STATE.get((record["target"], record["event"]))
            if state is not None:
              if not state["failures"]:
                state["first_failed_cmd_id"] = record.get("first_failed_cmd_id")
                state["first_failed_reason"] = record.get("first_failed_reason")
              state["failures"] += record["failures"]
              state["missing_total"] += record.get("missing_total", 0)
              state["last_failed_cmd_id"] = record.get("last_failed_cmd_id")
              state["suppressed"] += record.get("suppressed", 0)
              for reason in _REASONS:
                count = record.get("fail_" + reason, 0)
                if count:
                  state["reasons"][reason] = state["reasons"].get(reason, 0) + count
              # Budget loss returns unresolved image failures to the episode,
              # including when a later record already reset its aggregate.
              state["image_failure_pending"] |= bool(
                record.get("fail_cardinality") or record.get("fail_wrong_type"))
              state["bad"] = False
        continue
      records += 1
      size += encoded
      _LOG.P(line)
    except Exception:
      _COUNTERS["logger_errors"] += 1


def _acquire():
  """Acquire diagnostic state without waiting for another producer.

  Returns
  -------
  bool
      False means evidence was dropped, never that inference should retry.

  Notes
  -----
  Contention counters are best effort under simultaneous increments. They are
  evidence of lost observation completeness, not transport correctness metrics.
  """
  if _LOCK.acquire(blocking=False):
    return True
  _COUNTERS["contention_drops"] += 1
  return False


def _tracking_loss():
  """Mark a lost tracking mutation without acquiring the diagnostic lock.

  Notes
  -----
  A scalar generation is enough to force conservative invalidation at the next
  successful admission. It cannot grow an identity or request tombstone set.
  """
  global _LOSS_GENERATION
  _LOSS_GENERATION += 1


def _sync_tracking():
  """Invalidate stale expectations after a dropped mutation, under the lock.

  Notes
  -----
  Loss can hide a response or a failed-send cleanup. Existing identities stay
  incomplete until an explicit worker lifecycle clear; new requests alone do
  not prove that delayed old responses are impossible.
  """
  global _APPLIED_LOSS_GENERATION, _IDENTITY_HISTORY_LOST
  if _APPLIED_LOSS_GENERATION != _LOSS_GENERATION:
    for target in _TRACKING_COMPLETE:
      _TRACKING_COMPLETE[target] = False
    _PENDING.clear()
    # A dropped admission may involve an identity never entered in the map.
    _IDENTITY_HISTORY_LOST = True
    _APPLIED_LOSS_GENERATION = _LOSS_GENERATION
    _COUNTERS["tracking_invalidations"] += 1


def _tracking_target(target, reset=False):
  """Keep bounded identity completeness, under the diagnostic lock.

  Parameters
  ----------
  target : str
      Canonical physical worker identity.
  reset : bool
      Explicit worker lifecycle cleanup, which establishes a fresh boundary.

  Notes
  -----
  Forgotten identities cannot retain individual loss markers indefinitely.
  Once that bounded map evicts an identity, unseen identities default to
  incomplete; explicit lifecycle clears can still restore their confidence.
  """
  global _IDENTITY_HISTORY_LOST
  if target not in _TRACKING_COMPLETE:
    if len(_TRACKING_COMPLETE) >= _MAX_TARGETS:
      removed, _ = _TRACKING_COMPLETE.popitem(last=False)
      _PENDING.pop(removed, None)
      _EVICTIONS.pop(removed, None)
      _IDENTITY_HISTORY_LOST = True
      _COUNTERS["target_evictions"] += 1
    _TRACKING_COMPLETE[target] = not _IDENTITY_HISTORY_LOST
  _TRACKING_COMPLETE.move_to_end(target)
  if reset:
    _TRACKING_COMPLETE[target] = True


def _scalars(fields):
  """Copy allowlisted built-in scalars without retaining caller objects.

  Parameters
  ----------
  fields : dict
      Caller observations, which may include rejected arbitrary objects.

  Returns
  -------
  dict
      Scalar-only observations with bounded strings and integer magnitudes.
  """
  scalars = {}
  for key, value in fields.items():
    if key not in _FIELDS:
      continue
    value_type = type(value)
    if (value is None or value_type is bool
        or (value_type is int and value.bit_length() <= 64)
        or (value_type is str and len(value) <= 96)):
      scalars[key] = value
  return scalars


def serving_target(requested, worker=None, covered_by=None):
  """Resolve diagnostic identity without changing the transport handle.

  Parameters
  ----------
  requested : str
      Existing canonical requested handle.
  worker : object or None
      Existing registered worker; only its serving name is inspected.
  covered_by : str or None
      Existing registry alias fallback.

  Returns
  -------
  str
      Actual worker name when available, otherwise the original handle.
  """
  try:
    actual = getattr(worker, "server_name", None)
    if type(actual) is str:
      return actual.upper()
    if type(covered_by) is str:
      return covered_by.upper()
  except Exception:
    pass
  return requested


def remember_request(target, cmd_id, command, requested, inputs, observation=None):
  """Retain bounded scalar PREDICT expectations without retaining inputs.

  Parameters
  ----------
  target, requested : str
      Actual worker and original routing identities.
  cmd_id : int
      Existing request ID.
  command : str
      Only PREDICT has a correlated RESULT contract.
  inputs : object
      Existing request; never retained or modified.
  observation : dict or None
      Locally generated ``request_observation`` result, avoiding a second image
      scan. This internal argument must never be supplied from a wire payload;
      its private tuple already contains validated scalar counts.

  Notes
  -----
  At most 64 requests per worker and 256 globally are retained. Per-stream
  counts inspect at most 64 entries/256 items; larger requests retain only the
  outer cardinality, explicitly leaving image expectations unavailable.
  """
  if (type(target) is not str or not 0 < len(target) <= 96
      or type(requested) is not str or len(requested) > 96
      or type(cmd_id) is not int or cmd_id.bit_length() > 64
      or command != "PREDICT" or not isinstance(inputs, list)):
    return
  try:
    observation = observation if observation is not None else request_observation(inputs)
    counts = observation.get("_image_counts")
    # Private count tuples are produced locally, never passed to observe/logged.
    if type(counts) is not tuple or len(counts) > _MAX_ENTRIES:
      counts = None
    summary = {"requested": requested, "entries": len(inputs),
               "images": observation["images"] if counts is not None else None,
               "counts": counts, "sent": time.monotonic()}
    if not _acquire():
      _tracking_loss()
      return
    try:
      _sync_tracking()
      target = target.upper()
      _tracking_target(target)
      pending = _PENDING.setdefault(target, OrderedDict())
      _PENDING.move_to_end(target)
      if cmd_id not in pending and len(pending) >= _MAX_PENDING:
        pending.popitem(last=False)
        _EVICTIONS[target] = _EVICTIONS.get(target, 0) + 1
        _TRACKING_COMPLETE[target] = False
        _COUNTERS["tracking_evictions"] += 1
      pending[cmd_id] = summary
      # Find the oldest scalar record only when the global bound is exceeded.
      if sum(len(value) for value in _PENDING.values()) > _MAX_PENDING_TOTAL:
        owner = min((name for name in _PENDING if _PENDING[name]),
                    key=lambda name: next(iter(_PENDING[name].values()))["sent"])
        _PENDING[owner].popitem(last=False)
        _EVICTIONS[owner] = _EVICTIONS.get(owner, 0) + 1
        _TRACKING_COMPLETE[owner] = False
        _COUNTERS["tracking_evictions"] += 1
    finally:
      _LOCK.release()
  except Exception:
    return


def clear_requests(target, cmd_id=None):
  """Discard diagnostic request expectations without touching transport state.

  Parameters
  ----------
  target : str
      Actual worker identity.
  cmd_id : int or None
      One failed send, or None for target cleanup.
  """
  if not _acquire():
    _tracking_loss()
    return
  try:
    _sync_tracking()
    if cmd_id is None:
      _tracking_target(target.upper(), reset=True)
      _PENDING.pop(target.upper(), None)
      _EVICTIONS.pop(target.upper(), None)
      _CONFIGURED.pop(target.upper(), None)
    else:
      _PENDING.get(target.upper(), {}).pop(cmd_id, None)
  finally:
    _LOCK.release()


def pending_observation(target, requested=None):
  """Snapshot bounded pending IDs/count/age before transport cleanup.

  Parameters
  ----------
  target : str
      Actual worker identity.
  requested : str or None
      Optional alias whose expected ID is reported.

  Returns
  -------
  dict
      Scalar snapshot, or observation-error evidence on contention.
  """
  if not _acquire():
    return {"observation_error": True}
  try:
    _sync_tracking()
    pending = _PENDING.get(target.upper(), {})
    first = next(((key, value) for key, value in pending.items()
                  if requested is None or value["requested"] == requested), None)
    oldest = next(iter(pending.values()), None)
    return {"pending_count": len(pending), "expected_cmd_id": first[0] if first else None,
            "oldest_pending_ms": max(0, int((time.monotonic() - oldest["sent"]) * 1000))
            if oldest else None, "tracking_evictions": _EVICTIONS.get(target.upper(), 0)}
  finally:
    _LOCK.release()


def result_observation(target, payload, requested):
  """Compare a received RESULT with scalar request expectations.

  Parameters
  ----------
  target, requested : str
      Actual worker and handle currently collecting its response.
  payload : dict
      Original response envelope; never modified or validated for execution.

  Returns
  -------
  dict
      Correlation and cardinality evidence. Transport acceptance is unchanged.
  """
  observed = payload.get("CMD_ID")
  data = payload.get("DATA")
  rows = data.get("INFERENCES") if isinstance(data, dict) else None
  fields = {"observed_cmd_id": observed if type(observed) is int else None,
            "entries": len(rows) if isinstance(rows, list) else None,
            "missing": 0, "wrong_type": False, "correlated": False,
            "tracking_status": "missing_expectation", "result_contract": "unverified"}
  meta = data.get("INFERENCES_META") if isinstance(data, dict) else None
  fields["error_marker"] = isinstance(meta, dict) and "ERROR" in meta
  if not _acquire():
    _tracking_loss()
    fields["tracking_status"] = "contended"
    return fields
  try:
    _sync_tracking()
    target = target.upper()
    pending = _PENDING.get(target, {})
    complete = _TRACKING_COMPLETE.get(target, not _IDENTITY_HISTORY_LOST)
    expected = next(((key, value) for key, value in pending.items()
                     if value["requested"] == requested), None)
    if not complete:
      # A directly known request still establishes its own scalar cardinality.
      # Without complete history, FIFO ordering cannot establish a mismatch.
      summary = pending.get(observed) if type(observed) is int else None
      expected = (observed, summary) if summary is not None and summary["requested"] == requested else None
      fields["tracking_status"] = "incomplete"
    if expected is not None:
      key, summary = expected
      fields.update(expected_cmd_id=key, expected_entries=summary["entries"],
                    expected_images=summary["images"],
                    tracking_status="known" if complete else "incomplete_known",
                    request_age_ms=max(0, int((time.monotonic() - summary["sent"]) * 1000)))
      fields["correlated"] = type(observed) is int and observed == key
      counts = summary["counts"]
      if counts is not None:
        fields["result_contract"] = "structured_images"
      if complete and not fields["correlated"]:
        fields["reason"] = "cmd_id_mismatch"
      elif counts is not None:
        # Arbitrary non-image servings may return dicts/arrays. List-of-lists
        # cardinality is established only by a known structured image request.
        if not isinstance(rows, list):
          fields.update(wrong_type=True, reason="wrong_type")
        elif len(rows) != summary["entries"]:
          fields["reason"] = "cardinality"
        else:
          returned_images, missing = 0, 0
          wrong_type, cardinality = False, False
          for row, count in zip(rows, counts):
            if not isinstance(row, list):
              wrong_type = True
              continue
            size = len(row)
            returned_images += size
            missing += count > 0 and size == 0
            cardinality |= size != count
          fields["missing"] = missing
          if wrong_type:
            fields.update(wrong_type=True, reason="wrong_type")
          else:
            fields["returned_images"] = returned_images
            if cardinality:
              fields["reason"] = "cardinality"
    # Arrival consumes only the observed known ID; unexpected messages do not
    # erase the expected request or change which result the runtime returns.
    if type(observed) is int:
      pending.pop(observed, None)
  finally:
    _LOCK.release()
  return fields


def configuration_observation(log, target, requested, worker, shm, comm):
  """Queue scalar capacity once per worker/segment lifecycle.

  Parameters
  ----------
  log : Logger
      Runtime logger; actual output uses the logger passed to ``start``.
  target, requested : str
      Actual worker and requested handle.
  worker, shm, comm : object or None
      Existing runtime objects, observed without changing them.
  """
  try:
    target = target.upper()
    pid = getattr(worker, "pid", None)
    identity = (pid, id(shm))
    if not _acquire():
      return
    try:
      if _CONFIGURED.get(target) == identity:
        return
    finally:
      _LOCK.release()
    fields = {"requested_server": requested, "worker_pid": pid,
              "transport_kind": getattr(worker, "comm_method", type(comm).__name__),
              "shm": shm is not None}
    if shm is not None:
      fields.update(initialized=bool(shm.initialized), capacity=int(shm.maxlen),
                    segment_name=getattr(shm, "_mem_name", None),
                    max_elements=int(shm._max_element_size), max_rank=int(shm.shape_offset),
                    dtype=str(shm._np_mem.dtype), occupied_before=occupancy_observation(shm))
    if observe(log, target, "configuration", correlated=False, **fields) and _acquire():
      try:
        if target not in _CONFIGURED and len(_CONFIGURED) >= _MAX_TARGETS:
          _CONFIGURED.popitem(last=False)
          _COUNTERS["target_evictions"] += 1
        _CONFIGURED[target] = identity
      finally:
        _LOCK.release()
  except Exception:
    return


def array_observation(trace, data, slot, reason, reading=False):
  """Count one existing conversion without retaining/copying its array.

  Parameters
  ----------
  trace : dict
      Request-local scalar counters, never transmitted to another process.
  data : ndarray or None
      Already available array, or None for an unsuccessful read.
  slot : int or None
      Existing slot index; this function never reserves or releases slots.
  reason : str or None
      Fixed rejection category; None denotes a successful conversion.
  reading : bool
      Distinguish an existing receiver read from sender admission.
  """
  try:
    ordinal = trace.get("arrays", 0)
    trace["arrays"] = ordinal + 1
    if data is not None:
      trace["bytes"] = trace.get("bytes", 0) + data.nbytes
    if reason is None:
      key = "read_ok" if reading else "admitted"
      trace[key] = trace.get(key, 0) + 1
    else:
      trace["missing"] = trace.get("missing", 0) + 1
      if "first_bad_ordinal" not in trace:
        trace.update(first_bad_ordinal=ordinal, slot=slot, reason=reason)
        if data is not None:
          trace.update(rank=int(data.ndim), elements=int(data.size))
  except Exception:
    trace["observation_error"] = True


def request_observation(inputs, shm=None):
  """Return bounded scalar image counts and cheap segment metadata.

  Parameters
  ----------
  inputs : object
      Standard list/dict image request, inspected without reading pixels.
  shm : NumpySharedMemory or None
      Existing segment; occupancy is sampled separately on failures only.

  Returns
  -------
  dict
      Counters plus a private scalar ``_image_counts`` tuple when the bounded
      scan covers every image entry. Unknown counts are explicitly None.
  """
  trace = {"arrays": 0, "bytes": 0, "admitted": 0, "missing": 0,
           "shm": shm is not None, "images": None}
  try:
    if isinstance(inputs, list):
      trace["entries"] = len(inputs)
      counts = [] if len(inputs) <= _MAX_ENTRIES else None
      inspected, images = 0, 0
      if counts is not None:
        for entry in inputs:
          items = entry.get("INPUTS") if isinstance(entry, dict) else None
          if not isinstance(items, list):
            counts = None
            break
          inspected += len(items)
          if inspected > _MAX_IMAGE_ITEMS:
            counts = None
            break
          count = 0
          for item in items:
            item_type = item.get("TYPE") if isinstance(item, dict) else None
            if type(item_type) is not str or item_type != "IMG":
              counts = None
              break
            count += item.get("IMG") is not None
          if counts is None:
            break
          counts.append(count)
          images += count
      if counts is not None:
        trace["images"] = images
        trace["_image_counts"] = tuple(counts)
    if shm is not None:
      trace.update(initialized=bool(shm.initialized), capacity=int(shm.maxlen))
  except Exception:
    trace["observation_error"] = True
  return trace


def occupancy_observation(shm):
  """Return a non-atomic count of existing flags, or None if unavailable.

  Parameters
  ----------
  shm : NumpySharedMemory or None
      Existing buffer only; never opens or repairs a segment.

  Returns
  -------
  int or None
      Number of nonzero flags. Failure of observation is not a transport error.
  """
  try:
    if shm is not None and shm.initialized and shm.is_buffer:
      return int((shm._np_mem_additional[:, 0] != 0).sum())
  except Exception:
    pass
  return None


def observe(log, target, event, cmd_id=None, **fields):
  """Record bounded history or enqueue rate-limited failure evidence.

  Parameters
  ----------
  log : Logger
      Kept for caller compatibility; output uses the process startup logger.
  target : str
      Actual serving identity, at most 96 characters.
  event : str
      Existing send/receive/result/consumer/configuration/timeout boundary.
  cmd_id : int or None
      Existing IPC identifier; None explicitly means uncorrelated.
  **fields : scalar
      Only allowlisted built-in scalars are copied into retained evidence.

  Returns
  -------
  bool
      Whether output was queued; successful transport history needs no output.

  Notes
  -----
  First failures emit immediately. Repeats aggregate at ten seconds; recovery
  emits after a one-second floor with suppressed failures preserved. Healthy
  send/receive/result events enter a 128-record, ten-second deque. Failure
  context includes at most eight events. Every state collection is bounded;
  contention/output pressure drops observations rather than delaying inference.
  """
  if (event not in _EVENTS or type(target) is not str or not 0 < len(target) <= 96):
    return False
  try:
    now = time.monotonic()
    target = target.upper()
    fields = _scalars(fields)
    if fields.get("reason") is not None and fields["reason"] not in _REASONS:
      fields["reason"] = "other"
    # Internal count tuples never cross this boundary or survive in history.
    cmd_id = cmd_id if type(cmd_id) is int and cmd_id.bit_length() <= 64 else None
    anomaly = bool(fields.get("missing") or fields.get("wrong_type") or fields.get("error_marker")
                   or fields.get("exception_type") or fields.get("reason")
                   or fields.get("observation_error"))
    if not _acquire():
      return False
    try:
      while _HISTORY and now - _HISTORY[0]["time"] > _HISTORY_SECONDS:
        _HISTORY.popleft()
        _COUNTERS["history_expired"] += 1
      if not anomaly and event in ("send", "receive", "result"):
        if len(_HISTORY) == _HISTORY_LIMIT:
          _COUNTERS["history_evictions"] += 1
        _HISTORY.append(dict(time=now, target=target, event=event, cmd_id=cmd_id, **fields))
      key = (target, event)
      if key not in _STATE:
        # Healthy traffic needs history only until this boundary has failed.
        if not anomaly and event != "configuration":
          return False
        if len(_STATE) >= _MAX_STATES:
          _STATE.popitem(last=False)
          _COUNTERS["state_evictions"] += 1
        _STATE[key] = {"last": -float("inf"), "bad": False, "image_failure_pending": False, "count": 0,
                       "suppressed": 0, "failures": 0, "missing_total": 0, "reasons": {},
                       "first_failed_cmd_id": None, "last_failed_cmd_id": None,
                       "first_failed_reason": None, "timeout_snapshot": None}
      _STATE.move_to_end(key)
      state = _STATE[key]
      if event == "result" and not anomaly:
        verified = fields.get("tracking_status") in ("known", "incomplete_known")
        if not verified or (state["image_failure_pending"]
                            and fields.get("result_contract") != "structured_images"):
          # Receipt remains in recent history, but missing diagnostic evidence
          # cannot prove recovery from a previously verified result failure.
          return False
      state["count"] += 1
      if anomaly:
        reason = fields.get("reason")
        if reason not in _REASONS:
          reason = "transport_exception" if fields.get("exception_type") else \
            "observation_error" if fields.get("observation_error") else \
            "error_marker" if fields.get("error_marker") else "other"
        if not state["failures"]:
          state.update(first_failed_cmd_id=cmd_id, first_failed_reason=reason)
        state["failures"] += 1
        state["last_failed_cmd_id"] = cmd_id
        state["reasons"][reason] = state["reasons"].get(reason, 0) + 1
        # Keep this across suppressed and emitted summaries: a later transport
        # failure must not hide an earlier unresolved image-contract failure.
        state["image_failure_pending"] |= reason in ("cardinality", "wrong_type")
        if type(fields.get("missing")) is int:
          state["missing_total"] += fields["missing"]
      elapsed = now - state["last"]
      recovery = not anomaly and (state["bad"] or state["failures"])
      eligible = event == "configuration" or (anomaly and (not state["bad"] or elapsed >= 10)) \
        or recovery
      if not eligible or elapsed < 1:
        if anomaly:
          state["suppressed"] += 1
          if event == "timeout" and state["timeout_snapshot"] is None:
            state["timeout_snapshot"] = {"suppressed_timeout_" + field: fields[field]
                                         for field in _TIMEOUT_FIELDS if field in fields}
        return False
      record = {"event": event, "target": target, "pid": os.getpid(), "cmd_id": cmd_id,
                "observations": state["count"], "suppressed": state["suppressed"],
                "anomaly": anomaly, "recovery": recovery, "failures": state["failures"],
                "missing_total": state["missing_total"], "observed_at": now}
      if state["failures"]:
        record.update(first_failed_cmd_id=state["first_failed_cmd_id"],
                      last_failed_cmd_id=state["last_failed_cmd_id"],
                      first_failed_reason=state["first_failed_reason"])
        record.update(("fail_" + reason, count) for reason, count in state["reasons"].items())
      if state["timeout_snapshot"]:
        record.update(state["timeout_snapshot"])
      record.update(fields)
      if anomaly or recovery:
        recent = []
        for item in reversed(_HISTORY):
          if item["target"] == target:
            recent.append(dict(item))
            if len(recent) == _CONTEXT_LIMIT:
              break
        record["recent"] = list(reversed(recent))
      record["diagnostic_counters"] = dict(_COUNTERS)
      if not _STARTED or len(_OUTPUT) >= _QUEUE_LIMIT:
        _COUNTERS["output_drops"] += 1
        return False
      _OUTPUT.append(record)
      state.update(last=now, bad=anomaly,
                   image_failure_pending=state["image_failure_pending"] if anomaly else False,
                   count=0, suppressed=0, failures=0,
                   missing_total=0, reasons={}, first_failed_cmd_id=None,
                   last_failed_cmd_id=None, first_failed_reason=None, timeout_snapshot=None)
    finally:
      _LOCK.release()
    # The daemon polls its wake event; producers never acquire its event lock.
    return True
  except Exception:
    # Only diagnostic evidence is best effort. Original runtime exceptions and
    # transport decisions stay entirely with the existing caller.
    return False
