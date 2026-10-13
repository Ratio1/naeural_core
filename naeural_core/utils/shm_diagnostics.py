"""Opt-in, scalar-only observations of the existing serving transport.

``EE_SHM_DIAGNOSTICS`` is a JSON object with ``targets`` (one or two canonical
serving names) and ``until`` (an absolute Unix timestamp). Configuration is read
once per interpreter. The window is at most ten minutes and never renews on a
worker restart past ``until``. No inference or wire data is modified here.
"""

import json
import os
import re
import threading
import time
from collections import OrderedDict


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
)) | frozenset("fail_" + reason for reason in _REASONS) \
  | frozenset("suppressed_timeout_" + field for field in _TIMEOUT_FIELDS)
_LOCK = threading.Lock()
_STATE = {}
_PENDING = {}
_EVICTIONS = {}
_CONFIGURED = {}
_MAX_PENDING = 64
_MAX_ENTRIES = 64
_BYTES = 0
_LIMIT = 1024 * 1024  # Per interpreter, shared by all selected targets/events.
_TARGETS = ()
_UNTIL = 0.0
_DEADLINE = 0.0
try:
  _config = json.loads(os.environ.get("EE_SHM_DIAGNOSTICS", "{}"))
  _targets = _config.get("targets", [])
  _until = _config.get("until", 0)
  if (isinstance(_targets, list) and 0 < len(_targets) <= 2
      and all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", x)
              for x in _targets)
      and type(_until) in (int, float) and time.time() < _until <= time.time() + 600):
    _TARGETS = tuple(x.upper() for x in _targets)
    _UNTIL = _until
    _DEADLINE = time.monotonic() + max(0, _until - time.time())
except (ValueError, TypeError, AttributeError):
  pass  # Invalid diagnostic configuration must not affect runtime startup.


def _after_fork():
  """Reset child-local accounting and locks while preserving absolute expiry."""
  global _LOCK, _STATE, _BYTES, _PENDING, _EVICTIONS, _CONFIGURED
  _LOCK = threading.Lock()
  _STATE = {}
  _BYTES = 0
  _PENDING, _EVICTIONS, _CONFIGURED = {}, {}, {}


if hasattr(os, "register_at_fork"):
  # A serving child must never inherit a lock held by a parent business thread.
  os.register_at_fork(after_in_child=_after_fork)


def enabled(target=None):
  """Return whether this finite window includes the supplied serving name.

  Parameters
  ----------
  target : str or None
      Canonical serving name; None checks whether any target is enabled.

  Returns
  -------
  bool
      False without allocation or clock access when diagnostics are disabled.
  """
  global _TARGETS
  if not _TARGETS or _BYTES >= _LIMIT:
    return False
  if target is not None and target.upper() not in _TARGETS:
    return False
  if time.monotonic() >= _DEADLINE or time.time() >= _UNTIL:
    # Expiry permanently restores the cheap off path in this interpreter.
    _TARGETS = ()
    with _LOCK:
      _PENDING.clear()
      _EVICTIONS.clear()
      _CONFIGURED.clear()
    return False
  return True


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


def remember_request(target, cmd_id, command, requested, inputs):
  """Retain bounded scalar PREDICT expectations, never arrays or inputs.

  Parameters
  ----------
  target, requested : str
      Actual worker and original routing identities.
  cmd_id : int
      Existing request ID.
  command : str
      Existing command; only PREDICT has a correlated RESULT contract.
  inputs : object
      Existing request, inspected without changing it.

  Notes
  -----
  At most 64 requests per selected target and 64 per-stream image counts per
  request are retained. Larger/non-image requests keep outer counts only.
  """
  if not enabled(target):
    return
  try:
    if (type(cmd_id) is not int or command != "PREDICT" or not isinstance(inputs, list)
        or type(requested) is not str or len(requested) > 96):
      return
    counts = [] if len(inputs) <= _MAX_ENTRIES else None
    images = 0
    if counts is not None:
      for entry in inputs:
        if not isinstance(entry, dict) or not isinstance(entry.get("INPUTS"), list):
          counts = None
          break
        count = 0
        for item in entry["INPUTS"]:
          if not isinstance(item, dict) or type(item.get("TYPE")) is not str or item["TYPE"] != "IMG":
            counts = None
            break
          count += item.get("IMG") is not None
        if counts is None:
          break
        images += count
        counts.append(count)
    summary = {"requested": requested, "entries": len(inputs),
               "images": images if counts is not None else None,
               "counts": tuple(counts) if counts is not None else None,
               "sent": time.monotonic()}
    with _LOCK:
      # A concurrent consumer may expire/cap collection during inspection.
      if target.upper() not in _TARGETS or _BYTES >= _LIMIT:
        return
      pending = _PENDING.setdefault(target.upper(), OrderedDict())
      if len(pending) >= _MAX_PENDING:
        pending.popitem(last=False)
        _EVICTIONS[target.upper()] = _EVICTIONS.get(target.upper(), 0) + 1
      pending[cmd_id] = summary
  except Exception:
    return


def clear_requests(target, cmd_id=None):
  """Discard completed/failed request evidence without touching SHM slots.

  Parameters
  ----------
  target : str
      Actual serving target.
  cmd_id : int or None
      One failed send, or None to discard the target after cleanup.
  """
  with _LOCK:
    if cmd_id is None:
      _PENDING.pop(target.upper(), None)
      _EVICTIONS.pop(target.upper(), None)
    else:
      _PENDING.get(target.upper(), {}).pop(cmd_id, None)


def pending_observation(target, requested=None):
  """Snapshot bounded pending IDs/count/age before cleanup.

  Parameters
  ----------
  target : str
      Actual serving target.
  requested : str or None
      Optional logical handle whose oldest expected ID is reported.

  Returns
  -------
  dict
      Scalar snapshot; occupancy and process status belong to the caller.
  """
  with _LOCK:
    pending = _PENDING.get(target.upper(), {})
    candidates = [(key, value) for key, value in pending.items()
                  if requested is None or value["requested"] == requested]
    first = candidates[0] if candidates else None
    oldest = next(iter(pending.values()), None)
    return {"pending_count": len(pending), "expected_cmd_id": first[0] if first else None,
            "oldest_pending_ms": max(0, int((time.monotonic() - oldest["sent"]) * 1000))
            if oldest else None, "tracking_evictions": _EVICTIONS.get(target.upper(), 0)}


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
            "missing": sum(isinstance(row, list) and not row for row in rows)
            if isinstance(rows, list) else 0,
            "wrong_type": not isinstance(rows, list), "correlated": False}
  meta = data.get("INFERENCES_META") if isinstance(data, dict) else None
  fields["error_marker"] = isinstance(meta, dict) and "ERROR" in meta
  with _LOCK:
    pending = _PENDING.get(target.upper(), {})
    expected = next(((key, value) for key, value in pending.items()
                     if value["requested"] == requested), None)
    if expected is not None:
      key, summary = expected
      fields.update(expected_cmd_id=key, expected_entries=summary["entries"],
                    expected_images=summary["images"],
                    request_age_ms=max(0, int((time.monotonic() - summary["sent"]) * 1000)))
      fields["correlated"] = type(observed) is int and observed == key
      if not fields["correlated"]:
        fields["reason"] = "cmd_id_mismatch"
      elif fields["wrong_type"] or any(not isinstance(row, list) for row in rows):
        fields.update(wrong_type=True, reason="wrong_type")
      elif len(rows) != summary["entries"]:
        fields["reason"] = "cardinality"
      elif summary["counts"] is not None:
        fields["returned_images"] = sum(len(row) for row in rows)
        if any(len(row) != count for row, count in zip(rows, summary["counts"])):
          fields["reason"] = "cardinality"
    else:
      fields["reason"] = "unmatched_response"
    # Arrival consumes only the observed known ID; unexpected messages do not
    # erase the expected request or change which result the runtime returns.
    if type(observed) is int:
      pending.pop(observed, None)
  return fields


def configuration_observation(log, target, requested, worker, shm, comm):
  """Emit scalar capacity/identity once per selected worker/segment lifecycle.

  Parameters
  ----------
  log : Logger
      Existing runtime logger.
  target, requested : str
      Actual worker and original handle.
  worker, shm, comm : object or None
      Existing objects; diagnostics never attach, allocate or change them.
  """
  if not enabled(target):
    return
  try:
    target = target.upper()
    pid = getattr(worker, "pid", None)
    identity = (pid, id(shm))
    with _LOCK:
      if _CONFIGURED.get(target) == identity:
        return
    fields = {"requested_server": requested, "worker_pid": pid,
              "transport_kind": getattr(worker, "comm_method", type(comm).__name__),
              "shm": shm is not None}
    if shm is not None:
      fields.update(initialized=bool(shm.initialized), capacity=int(shm.maxlen),
                    segment_name=getattr(shm, "_mem_name", None),
                    max_elements=int(shm._max_element_size), max_rank=int(shm.shape_offset),
                    dtype=str(shm._np_mem.dtype))
    if observe(log, target, "configuration", correlated=False, **fields):
      with _LOCK:
        if target in _TARGETS and _BYTES < _LIMIT:
          _CONFIGURED[target] = identity
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
      trace["bytes"] = trace.get("bytes", 0) + int(data.nbytes)
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
  """Return scalar request counts without walking/copying image contents.

  Parameters
  ----------
  inputs : object
      Existing serving request; only standard list/dict image entries count.
  shm : NumpySharedMemory or None
      Existing segment, observed without attaching or changing slot flags.

  Returns
  -------
  dict
      Counts and optional non-atomic occupancy observation.
  """
  trace = {"arrays": 0, "bytes": 0, "admitted": 0, "missing": 0,
           "shm": shm is not None, "images": 0}
  try:
    if isinstance(inputs, list):
      trace["entries"] = len(inputs)
      for entry in inputs:
        if isinstance(entry, dict) and isinstance(entry.get("INPUTS"), list):
          trace["images"] += sum(
            isinstance(item, dict) and type(item.get("TYPE")) is str and item["TYPE"] == "IMG"
            and item.get("IMG") is not None for item in entry["INPUTS"]
          )
    if shm is not None:
      trace.update(initialized=bool(shm.initialized), capacity=int(shm.maxlen))
      trace["occupied_before"] = occupancy_observation(shm)
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
  """Emit bounded scalar evidence; logging failures never alter execution.

  Parameters
  ----------
  log : Logger
      Existing runtime logger.
  target : str
      Canonical serving name, matched against the explicit target selection.
  event : str
      One of send, receive, result, consumer, configuration or timeout.
  cmd_id : int or None
      Existing IPC command ID; None means the boundary is uncorrelated.
  **fields : scalar
      Allowlisted observations. Arbitrary values and unknown keys are omitted.

  Notes
  -----
  Each target/event emits at most once per second; repeats are summarized every
  ten seconds. A change between healthy/error emits at the next eligible call.
  Suppressed failures survive in fixed reason counters and first/last failed
  IDs; a healthy summary with pending failure evidence is eligible after one
  second. Healthy/error repeats otherwise keep the ten-second cadence.
  Timeout events have an independent one-second gate and retain the first
  suppressed snapshot until the next eligible timeout. State has at most twelve
  keys. Output stops at one MiB per interpreter, with a
  final cap record. This is a local cap, not a cross-process global guarantee.
  """
  if not enabled(target) or event not in _EVENTS:
    return
  global _BYTES
  try:
    now = time.monotonic()
    target = target.upper()
    anomaly = bool(fields.get("missing") or fields.get("wrong_type") or fields.get("error_marker")
                   or fields.get("exception_type") or fields.get("reason")
                   or fields.get("observation_error"))
    with _LOCK:
      if _BYTES >= _LIMIT:
        return
      state = _STATE.setdefault((target, event), {
        "last": -float("inf"), "bad": None, "count": 0, "suppressed": 0,
        "failures": 0, "missing_total": 0, "reasons": {}, "first_failed_cmd_id": None,
        "last_failed_cmd_id": None, "first_failed_reason": None,
        "timeout_snapshot": None,
      })
      state["count"] += 1
      if anomaly:
        reason = fields.get("reason")
        if type(reason) is not str or reason not in _REASONS:
          reason = "transport_exception" if fields.get("exception_type") else \
            "observation_error" if fields.get("observation_error") else \
            "error_marker" if fields.get("error_marker") else "other"
        if not state["failures"]:
          state.update(first_failed_cmd_id=cmd_id if type(cmd_id) is int else None,
                       first_failed_reason=reason)
        state["failures"] += 1
        state["last_failed_cmd_id"] = cmd_id if type(cmd_id) is int else None
        state["reasons"][reason] = state["reasons"].get(reason, 0) + 1
        if type(fields.get("missing")) is int:
          state["missing_total"] += fields["missing"]
      elapsed = now - state["last"]
      if elapsed < 1 or (event not in ("configuration", "timeout") and elapsed < 10
                         and state["bad"] == anomaly and not (state["failures"] and not anomaly)):
        if event == "timeout" and state["timeout_snapshot"] is None:
          state["timeout_snapshot"] = {
            "suppressed_timeout_" + key: value for key, value in fields.items()
            if key in _TIMEOUT_FIELDS and (value is None or type(value) in (int, bool)
                                          or (type(value) is str and len(value) <= 96))
          }
        state["suppressed"] += 1
        return
      record = {
        "event": event, "target": target, "pid": os.getpid(),
        "window_until": _UNTIL, "cmd_id": cmd_id if type(cmd_id) is int else None,
        "observations": state["count"], "suppressed": state["suppressed"],
        "anomaly": anomaly, "recovery": state["bad"] is True and not anomaly,
        "failures": state["failures"], "missing_total": state["missing_total"],
      }
      if state["failures"]:
        record.update(first_failed_cmd_id=state["first_failed_cmd_id"],
                      last_failed_cmd_id=state["last_failed_cmd_id"],
                      first_failed_reason=state["first_failed_reason"])
        record.update(("fail_" + reason, count) for reason, count in state["reasons"].items())
      if state["timeout_snapshot"] is not None:
        record.update(state["timeout_snapshot"])
      for key, value in fields.items():
        if key in _FIELDS and (value is None or type(value) in (int, bool)
                              or (type(value) is str and len(value) <= 96)):
          record[key] = value
      line = "[SHM_DIAG] " + json.dumps(record, separators=(",", ":"))
      size = len(line.encode("utf-8"))
      if size > 2048 or _BYTES + size + 128 > _LIMIT:
        line = '[SHM_DIAG] {"event":"output_cap","collection_stopped":true}'
        _BYTES = _LIMIT
        _PENDING.clear()
        _EVICTIONS.clear()
        _CONFIGURED.clear()
      else:
        _BYTES += size
      state.update(last=now, bad=anomaly, count=0, suppressed=0)
      state.update(failures=0, missing_total=0, reasons={}, first_failed_cmd_id=None,
                   last_failed_cmd_id=None, first_failed_reason=None, timeout_snapshot=None)
    # Reserve budget under the lock, but never hold it through logger I/O.
    log.P(line)
    return True
  except Exception:
    # Only the new observation is best effort. Callers retain their original
    # inference/transport exceptions and must never catch them through here.
    return
