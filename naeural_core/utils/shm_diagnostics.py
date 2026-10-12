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


_EVENTS = frozenset(("send", "receive", "result", "consumer"))
_FIELDS = frozenset((
  "arrays", "bytes", "admitted", "read_ok", "images_read", "missing", "first_bad_ordinal", "slot",
  "reason", "rank", "elements", "capacity", "occupied_before",
  "occupied_after", "initialized", "images", "entries", "result_entries",
  "attempted", "completed", "exception_type", "stream", "correlated",
  "shm", "error_marker", "wrong_type", "observed_cmd_id", "observation_error",
))
_LOCK = threading.Lock()
_STATE = {}
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
  global _LOCK, _STATE, _BYTES
  _LOCK = threading.Lock()
  _STATE = {}
  _BYTES = 0


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
    return False
  return True


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
      One of send, receive, result or consumer.
  cmd_id : int or None
      Existing IPC command ID; None means the boundary is uncorrelated.
  **fields : scalar
      Allowlisted observations. Arbitrary values and unknown keys are omitted.

  Notes
  -----
  Each target/event emits at most once per second; repeats are summarized every
  ten seconds. A change between healthy/error emits at the next eligible call.
  State has at most eight keys. Output stops at one MiB per interpreter, with a
  final cap record. This is a local cap, not a cross-process global guarantee.
  """
  if not enabled(target) or event not in _EVENTS:
    return
  global _BYTES
  try:
    now = time.monotonic()
    target = target.upper()
    anomaly = bool(fields.get("missing") or fields.get("wrong_type")
                   or fields.get("exception_type") or fields.get("reason")
                   or fields.get("observation_error"))
    with _LOCK:
      if _BYTES >= _LIMIT:
        return
      state = _STATE.setdefault((target, event), {
        "last": -float("inf"), "bad": None, "count": 0, "suppressed": 0,
      })
      state["count"] += 1
      elapsed = now - state["last"]
      if elapsed < 1 or (elapsed < 10 and state["bad"] == anomaly):
        state["suppressed"] += 1
        return
      record = {
        "event": event, "target": target, "pid": os.getpid(),
        "window_until": _UNTIL, "cmd_id": cmd_id if type(cmd_id) is int else None,
        "observations": state["count"], "suppressed": state["suppressed"],
        "anomaly": anomaly, "recovery": state["bad"] is True and not anomaly,
      }
      for key, value in fields.items():
        if key in _FIELDS and (value is None or type(value) in (int, bool)
                              or (type(value) is str and len(value) <= 96)):
          record[key] = value
      line = "[SHM_DIAG] " + json.dumps(record, separators=(",", ":"))
      size = len(line.encode("utf-8"))
      if size > 2048 or _BYTES + size + 128 > _LIMIT:
        line = '[SHM_DIAG] {"event":"output_cap","collection_stopped":true}'
        _BYTES = _LIMIT
      else:
        _BYTES += size
      state.update(last=now, bad=anomaly, count=0, suppressed=0)
    # Reserve budget under the lock, but never hold it through logger I/O.
    log.P(line)
  except Exception:
    # Only the new observation is best effort. Callers retain their original
    # inference/transport exceptions and must never catch them through here.
    return
