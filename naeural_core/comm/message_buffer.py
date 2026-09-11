from collections import deque
from dataclasses import dataclass
from threading import Condition
from time import monotonic


@dataclass(frozen=True)
class MessageBufferSnapshot:
  """Constant-time snapshot of one bounded process-local FIFO."""

  capacity: int
  depth: int
  admitted: int
  rejected_full: int
  rejected_closed: int
  dequeued: int
  discarded_on_close: int
  high_water_mark: int
  oldest_age_seconds: float
  closed: bool
  degraded: bool
  conserved: bool


@dataclass(frozen=True)
class _MessageEntry:
  payload: object
  admitted_at: float


class ObservableMessageBuffer:
  """Thread-safe bounded FIFO that rejects newest instead of evicting oldest."""

  def __init__(self, capacity, clock=monotonic):
    capacity = int(capacity)
    if capacity <= 0:
      raise ValueError("Message buffer capacity must be greater than zero")
    if not callable(clock):
      raise TypeError("Message buffer clock must be callable")

    self._capacity = capacity
    self._clock = clock
    self._entries = deque()
    self._condition = Condition()
    self._admitted = 0
    self._rejected_full = 0
    self._rejected_closed = 0
    self._dequeued = 0
    self._discarded_on_close = 0
    self._high_water_mark = 0
    self._closed = False
    self._degraded = False

  @property
  def maxlen(self):
    return self._capacity

  @property
  def capacity(self):
    return self._capacity

  def __len__(self):
    with self._condition:
      return len(self._entries)

  def __bool__(self):
    return len(self) > 0

  def try_append(self, payload):
    with self._condition:
      if self._closed:
        self._rejected_closed += 1
        self._degraded = True
        return False
      if len(self._entries) >= self._capacity:
        self._rejected_full += 1
        self._degraded = True
        return False

      self._entries.append(_MessageEntry(
        payload=payload,
        admitted_at=self._clock(),
      ))
      self._admitted += 1
      self._high_water_mark = max(self._high_water_mark, len(self._entries))
      self._condition.notify()
      return True

  def append(self, payload):
    return self.try_append(payload)

  def __getitem__(self, index):
    with self._condition:
      return self._entries[index].payload

  def popleft(self):
    with self._condition:
      if len(self._entries) == 0:
        raise IndexError("pop from an empty message buffer")
      entry = self._entries.popleft()
      self._dequeued += 1
      return entry.payload

  def get(self, timeout=None):
    with self._condition:
      if timeout is None:
        while len(self._entries) == 0 and not self._closed:
          self._condition.wait()
      else:
        deadline = monotonic() + max(float(timeout), 0.0)
        while len(self._entries) == 0 and not self._closed:
          remaining = deadline - monotonic()
          if remaining <= 0:
            return None
          self._condition.wait(remaining)

      if len(self._entries) == 0:
        return None
      entry = self._entries.popleft()
      self._dequeued += 1
      return entry.payload

  def clear(self):
    with self._condition:
      discarded = len(self._entries)
      self._entries.clear()
      self._discarded_on_close += discarded
      return discarded

  def close(self, discard=False):
    with self._condition:
      self._closed = True
      if discard:
        self._discarded_on_close += len(self._entries)
        self._entries.clear()
      self._condition.notify_all()

  def snapshot(self, now=None):
    with self._condition:
      depth = len(self._entries)
      if now is None:
        now = self._clock()
      oldest_age_seconds = 0.0
      if depth > 0:
        oldest_age_seconds = max(0.0, now - self._entries[0].admitted_at)
      conserved = self._admitted == (
        self._dequeued + self._discarded_on_close + depth
      )
      return MessageBufferSnapshot(
        capacity=self._capacity,
        depth=depth,
        admitted=self._admitted,
        rejected_full=self._rejected_full,
        rejected_closed=self._rejected_closed,
        dequeued=self._dequeued,
        discarded_on_close=self._discarded_on_close,
        high_water_mark=self._high_water_mark,
        oldest_age_seconds=oldest_age_seconds,
        closed=self._closed,
        degraded=self._degraded,
        conserved=conserved,
      )
