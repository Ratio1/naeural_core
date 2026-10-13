"""Resident, bounded load shared by opt-in isolated and full-node tests."""

import os
from pathlib import Path
import time


MIB = 1024 ** 2
GIB = 1024 ** 3
BATCH_BYTES = 128 * MIB
BUFFER_BYTES = 16 * MIB
RECORDS_PER_BATCH = 2048
RECORD_BYTES = 512
MIN_OWNED_BYTES = 1536 * MIB
MAX_BUFFER_BYTES = 2304 * MIB
SHARED_BYTES = 256 * MIB
PRESSURE_FRACTION = 0.80
SCAN_HEADROOM_BYTES = 192 * MIB
MAX_SCAN_SECONDS = 2


def resident_buffer(size):
  """Allocate and touch every byte rather than reserving lazy zero pages.

  Parameters
  ----------
  size : int
    Number of nonzero bytes to retain.

  Returns
  -------
  bytearray
    Independently allocated resident payload.
  """
  return bytearray(b"\x5a") * size


def memory_snapshot():
  """Read kernel container accounting and this process's resident footprint.

  Returns
  -------
  dict
    Timestamp, finite cgroup-v2 limit/usage, anonymous usage, RSS, swap and OOM
    counters. Refuses an unlimited or differently configured test environment.
  """
  cgroup = Path("/sys/fs/cgroup")
  limit = int((cgroup / "memory.max").read_text().strip())
  expected_limit = int(os.environ.get("R1_MEMORY_LIMIT_BYTES", 3 * GIB))
  assert expected_limit in (3 * GIB, 4 * GIB), "Use a reviewed, bounded test limit"
  assert limit == expected_limit, "Hard cgroup limit does not match the test configuration"
  stats = dict(line.split() for line in (cgroup / "memory.stat").read_text().splitlines())
  events = dict(line.split() for line in (cgroup / "memory.events").read_text().splitlines())
  current = int((cgroup / "memory.current").read_text())
  swap = int((cgroup / "memory.swap.current").read_text())
  assert int((cgroup / "memory.swap.max").read_text()) == 0, "Disable swap for pressure tests"
  assert swap == 0, "Payload must remain resident"
  rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
  return {
    "timestamp": time.time(), "current_bytes": current, "limit_bytes": limit,
    "utilization": current / limit, "anonymous_bytes": int(stats["anon"]),
    "rss_bytes": rss, "swap_bytes": swap,
    "oom": int(events["oom"]), "oom_kill": int(events["oom_kill"]),
  }


class RetainedLoad:
  """Grow large resident buffers and a realistically nested cache together.

  Notes
  -----
  Large buffers consume RAM; per-batch nested records also load graph traversal.
  The buffers use large allocations so releasing them returns resident pages
  without injecting allocator trimming into the production process.
  """

  def __init__(self):
    self.buffers = []
    self.records = []

  @property
  def payload_bytes(self):
    """Return retained payload bytes, excluding Python container overhead."""
    return len(self.buffers) * BUFFER_BYTES + len(self.records) * RECORD_BYTES

  def grow(self):
    """Add one bounded batch after reserving space for scanner scratch memory.

    Returns
    -------
    int
      Total retained payload bytes; raises before unsafe or excessive growth.
    """
    snapshot = memory_snapshot()
    assert snapshot["utilization"] < 0.92, "Stop before container exhaustion"
    assert len(self.buffers) * BUFFER_BYTES + BATCH_BYTES <= MAX_BUFFER_BYTES, "Load cap reached"
    reserve = BATCH_BYTES + 8 * MIB + SCAN_HEADROOM_BYTES
    assert snapshot["current_bytes"] + reserve < 0.95 * snapshot["limit_bytes"], "Insufficient scan headroom"
    for _ in range(BATCH_BYTES // BUFFER_BYTES):
      self.buffers.append(resident_buffer(BUFFER_BYTES))
    start = len(self.records)
    for offset in range(RECORDS_PER_BATCH):
      self.records.append({
        "sequence": start + offset, "cache": {"payload": resident_buffer(RECORD_BYTES)},
      })
    return self.payload_bytes

  def clear(self):
    """Release all test-owned payloads and records without allocator tricks."""
    self.buffers.clear()
    self.records.clear()
