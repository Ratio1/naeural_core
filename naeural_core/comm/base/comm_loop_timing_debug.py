import os

from time import perf_counter


class _CommLoopTimingDebugMixin:
  """Aggregate command/control loop timing without per-message log pressure.

  Elapsed timings use ``perf_counter`` rather than wall-clock time so NTP/clock
  adjustments cannot produce negative phase durations in long-running nodes.
  """

  def _config_bool(self, key, env_key=None, default=False):
    value = default
    if self._config is not None:
      value = self._config.get(key, value)
    if env_key is not None:
      env_value = os.environ.get(env_key, None)
      if env_value not in [None, ""]:
        value = env_value
    if isinstance(value, str):
      return value.strip().upper() in ["1", "TRUE", "YES", "Y", "ON"]
    return bool(value)

  def _config_float(self, key, env_key=None, default=0.0):
    value = default
    if self._config is not None:
      value = self._config.get(key, value)
    if env_key is not None:
      env_value = os.environ.get(env_key, None)
      if env_value not in [None, ""]:
        value = env_value
    try:
      return float(value)
    except Exception:
      return float(default)

  def _configure_comm_loop_timing_debug(self):
    self._debug_comm_loop_timings_enabled = self._config_bool(
      key="DEBUG_COMM_LOOP_TIMINGS",
      env_key="EE_DEBUG_COMM_LOOP_TIMINGS",
      default=False,
    )
    self._debug_comm_loop_timings_interval = max(
      1.0,
      self._config_float(
        key="DEBUG_COMM_LOOP_TIMINGS_INTERVAL",
        env_key="EE_DEBUG_COMM_LOOP_TIMINGS_INTERVAL",
        default=30.0,
      ),
    )
    self._debug_comm_loop_timings_slow_seconds = max(
      0.0,
      self._config_float(
        key="DEBUG_COMM_LOOP_TIMINGS_SLOW_SECONDS",
        env_key="EE_DEBUG_COMM_LOOP_TIMINGS_SLOW_SECONDS",
        default=0.25,
      ),
    )
    self._debug_comm_loop_timings_since = perf_counter()
    self._debug_comm_loop_timing_stats = {}
    self._debug_comm_loop_timing_counters = {}
    comm_type = str(getattr(self, "_comm_type", "")).upper()
    should_log_startup = "COMMAND" in comm_type and "CONTROL" in comm_type
    if self._debug_comm_loop_timings_enabled and should_log_startup:
      self.P(
        "Command/control loop timing debug enabled: interval={}s, slow_threshold={}s".format(
          self._debug_comm_loop_timings_interval,
          self._debug_comm_loop_timings_slow_seconds,
        ),
        color='y',
      )
    return

  def _comm_loop_timing_count(self, name, value=1):
    if not self._debug_comm_loop_timings_enabled:
      return
    self._debug_comm_loop_timing_counters[name] = (
      self._debug_comm_loop_timing_counters.get(name, 0) + value
    )
    return

  def _comm_loop_timing_add(self, name, elapsed):
    if not self._debug_comm_loop_timings_enabled:
      return
    count, total, max_elapsed, slow_count = self._debug_comm_loop_timing_stats.get(
      name, (0, 0.0, 0.0, 0)
    )
    self._debug_comm_loop_timing_stats[name] = (
      count + 1,
      total + elapsed,
      max(max_elapsed, elapsed),
      slow_count + int(elapsed >= self._debug_comm_loop_timings_slow_seconds),
    )
    return

  def _maybe_report_comm_loop_timings(self, now=None):
    if not self._debug_comm_loop_timings_enabled:
      return
    now = perf_counter() if now is None else now
    elapsed_window = now - self._debug_comm_loop_timings_since
    if elapsed_window < self._debug_comm_loop_timings_interval:
      return

    # Aggregate timing keeps the receive path observable without adding
    # per-message logs, which would create exactly the pressure we are measuring.
    phase_lines = []
    for name in sorted(self._debug_comm_loop_timing_stats):
      count, total, max_elapsed, slow_count = self._debug_comm_loop_timing_stats[name]
      avg_ms = 1000 * total / max(count, 1)
      max_ms = 1000 * max_elapsed
      phase_lines.append(
        "{} avg={:.3f}ms max={:.3f}ms n={} slow={}".format(
          name, avg_ms, max_ms, count, slow_count
        )
      )

    counter_lines = [
      "{}={}".format(name, self._debug_comm_loop_timing_counters[name])
      for name in sorted(self._debug_comm_loop_timing_counters)
    ]
    self.P(
      "C&C loop timings over {:.1f}s | counters: {} | phases: {}".format(
        elapsed_window,
        ", ".join(counter_lines) if len(counter_lines) > 0 else "none",
        "; ".join(phase_lines) if len(phase_lines) > 0 else "none",
      ),
      color='y',
    )
    self._debug_comm_loop_timings_since = now
    self._debug_comm_loop_timing_stats = {}
    self._debug_comm_loop_timing_counters = {}
    return
