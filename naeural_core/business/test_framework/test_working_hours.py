"""Test actual shared scheduling logic without starting an edge or loading models.

The production module is loaded directly, with no rewritten method bodies or
alternate evaluator. The harness supplies only the executor clock and event sink.
Run: python3 -m unittest discover -s naeural_core/business/test_framework -p test_working_hours.py
"""

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
spec = importlib.util.spec_from_file_location(
  "working_hours_under_test", ROOT / "business/mixins_base/working_hours_mixin.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
WorkingHoursMixin = module._WorkingHoursMixin


class FakePluginBase:
  """Recording plugin surface for the real working-hours transition code."""

  def __init__(self):
    self.log = SimpleNamespace(timezone=self.edge_timezone)
    self.ct = SimpleNamespace(
      WEEKDAYS_SHORT=list(WEEKDAYS),
      NOTIFICATION_CODES=SimpleNamespace(
        PLUGIN_WORKING_HOURS_SHIFT_START=112,
        PLUGIN_WORKING_HOURS_SHIFT_END=113,
      ),
    )
    self.events = []

  def P(self, message, **kwargs):
    self.events.append(("log", message, kwargs))

  def _create_notification(self, **kwargs):
    self.events.append(("notification", kwargs))

  def add_payload_by_fields(self, **kwargs):
    self.events.append(("payload", kwargs))

  def json_dumps(self, value):
    return repr(value)

  def add_error(self, message):
    self.events.append(("error", message))

  def time(self):
    """Match BasePluginExecutor.time() using the harness-controlled instant."""
    return self.now_utc.timestamp()


class ScheduleHarness(WorkingHoursMixin, FakePluginBase):
  """Actual working-hours mixin with a fixed clock and recording hooks."""

  def __init__(self, schedule, schedule_timezone, now_utc, ignore=False):
    self.cfg_working_hours = schedule
    self.cfg_working_hours_timezone = schedule_timezone
    self.cfg_ignore_working_hours = ignore
    self.cfg_forced_pause = False
    self.edge_timezone = "Europe/Bucharest"
    self.now_utc = datetime.fromisoformat(now_utc)
    super().__init__()

  def on_shift_start(self, interval_idx=None, weekday_name=None, **kwargs):
    self.events.append(("start_hook", interval_idx, weekday_name, kwargs))

  def on_shift_end(self, **kwargs):
    self.events.append(("end_hook", kwargs))


def is_outside(schedule, schedule_timezone, now_utc, ignore=False):
  """Evaluate one isolated instant without carrying transition state."""
  return ScheduleHarness(schedule, schedule_timezone, now_utc, ignore).outside_working_hours


class WorkingHoursTests(unittest.TestCase):
  """Behavioral regressions for the shared production working-hours mixin."""

  def test_shift_start_repeat_and_end_use_exact_upstream_path_once(self):
    plugin = ScheduleHarness(
      {"THU": [["06:00", "07:00"]]},
      "Europe/Bucharest",
      "2026-09-24T03:00:00+00:00",
    )
    self.assertFalse(plugin.outside_working_hours)
    self.assertTrue(plugin.working_hours_is_new_shift)
    self.assertEqual([event[0] for event in plugin.events].count("notification"), 1)
    self.assertEqual([event[0] for event in plugin.events].count("payload"), 1)
    self.assertEqual([event[0] for event in plugin.events].count("start_hook"), 1)
    start_notification = next(event[1] for event in plugin.events if event[0] == "notification")
    self.assertEqual(start_notification["notif_code"], 112)
    self.assertTrue(start_notification["displayed"])
    start_payload = next(event[1] for event in plugin.events if event[0] == "payload")
    self.assertEqual(
      set(start_payload),
      {
        "status",
        "working_hours",
        "working_hours_timezone",
        "forced_pause",
        "ignore_working_hours",
        "img",
      },
    )
    self.assertEqual(start_payload["working_hours"], {"THU": [["06:00", "07:00"]]})
    self.assertEqual(start_payload["working_hours_timezone"], "Europe/Bucharest")
    start_hook = next(event for event in plugin.events if event[0] == "start_hook")
    self.assertEqual(start_hook[1:3], (0, "THU"))

    self.assertFalse(plugin.outside_working_hours)
    self.assertFalse(plugin.working_hours_is_new_shift)
    self.assertEqual([event[0] for event in plugin.events].count("notification"), 1)
    self.assertEqual([event[0] for event in plugin.events].count("start_hook"), 1)

    plugin.now_utc = datetime.fromisoformat("2026-09-24T04:01:00+00:00")
    self.assertTrue(plugin.outside_working_hours)
    self.assertEqual([event[0] for event in plugin.events].count("notification"), 2)
    self.assertEqual([event[0] for event in plugin.events].count("payload"), 2)
    self.assertEqual([event[0] for event in plugin.events].count("end_hook"), 1)
    end_notification = [event[1] for event in plugin.events if event[0] == "notification"][-1]
    self.assertEqual(end_notification["notif_code"], 113)
    self.assertTrue(end_notification["displayed"])
    self.assertTrue(plugin.outside_working_hours)
    self.assertEqual([event[0] for event in plugin.events].count("end_hook"), 1)

  def test_inclusive_end_minute_and_midnight(self):
    schedule = {"THU": [["06:00", "07:00"]]}
    samples = {
      "2026-09-24T02:59:59+00:00": True,
      "2026-09-24T03:00:00+00:00": False,
      "2026-09-24T04:00:00+00:00": False,
      "2026-09-24T04:00:59+00:00": False,
      "2026-09-24T04:01:00+00:00": True,
    }
    for instant, expected_outside in samples.items():
      with self.subTest(instant=instant):
        self.assertEqual(is_outside(schedule, "Europe/Bucharest", instant), expected_outside)

    midnight = {"MON": [["23:00", "23:59"]]}
    self.assertFalse(is_outside(midnight, "Europe/Bucharest", "2026-09-21T20:59:59+00:00"))
    self.assertTrue(is_outside(midnight, "Europe/Bucharest", "2026-09-21T21:00:00+00:00"))

  def test_summer_winter_and_source_weekday_selection(self):
    summer = {"WED": [["09:00", "10:00"]]}
    self.assertFalse(is_outside(summer, "Asia/Dubai", "2026-07-01T05:00:00+00:00"))
    self.assertFalse(is_outside(summer, "Asia/Dubai", "2026-07-01T06:00:59+00:00"))
    self.assertTrue(is_outside(summer, "Asia/Dubai", "2026-07-01T06:01:00+00:00"))

    winter = {"THU": [["09:00", "10:00"]]}
    self.assertFalse(is_outside(winter, "Asia/Dubai", "2026-01-01T05:00:00+00:00"))

    source_monday = {"MON": [["00:00", "00:30"]]}
    self.assertFalse(is_outside(source_monday, "Asia/Dubai", "2026-01-04T20:00:00+00:00"))
    self.assertFalse(is_outside(source_monday, "Asia/Dubai", "2026-01-04T20:30:59+00:00"))
    self.assertTrue(is_outside(source_monday, "Asia/Dubai", "2026-01-04T20:31:00+00:00"))

  def test_dst_spring_gap_and_both_fall_folds(self):
    spring = {"SUN": [["02:55", "04:05"]]}
    self.assertFalse(is_outside(spring, "Europe/Bucharest", "2026-03-29T00:59:59+00:00"))
    self.assertFalse(is_outside(spring, "Europe/Bucharest", "2026-03-29T01:00:00+00:00"))
    self.assertFalse(is_outside(spring, "Europe/Bucharest", "2026-03-29T01:05:59+00:00"))
    self.assertTrue(is_outside(spring, "Europe/Bucharest", "2026-03-29T01:06:00+00:00"))

    repeated = {"SUN": [["03:15", "03:30"]]}
    self.assertFalse(is_outside(repeated, "Europe/Bucharest", "2026-10-25T00:20:00+00:00"))
    self.assertFalse(is_outside(repeated, "Europe/Bucharest", "2026-10-25T01:20:00+00:00"))

    dubai = {"SUN": [["04:00", "04:30"]]}
    self.assertFalse(is_outside(dubai, "Asia/Dubai", "2026-10-25T00:20:00+00:00"))
    self.assertTrue(is_outside(dubai, "Asia/Dubai", "2026-10-25T01:20:00+00:00"))

  def test_empty_and_override_semantics(self):
    self.assertTrue(is_outside(None, "Europe/Bucharest", "2026-09-24T03:00:00+00:00"))
    self.assertFalse(is_outside([], "Europe/Bucharest", "2026-09-24T03:00:00+00:00"))
    self.assertTrue(is_outside({}, "Europe/Bucharest", "2026-09-24T03:00:00+00:00"))
    self.assertTrue(
      is_outside({"THU": []}, "Europe/Bucharest", "2026-09-24T03:00:00+00:00")
    )
    self.assertTrue(
      is_outside({"FRI": [["06:00", "07:00"]]}, "Europe/Bucharest", "2026-09-24T03:00:00+00:00")
    )
    self.assertFalse(
      is_outside({"FRI": [["06:00", "07:00"]]}, "Europe/Bucharest", "2026-09-24T03:00:00+00:00", True)
    )

  def test_fixed_offset_compatibility_and_invalid_zone_failure(self):
    schedule = {"THU": [["06:00", "07:00"]]}
    self.assertFalse(is_outside(schedule, "UTC+2", "2026-09-24T04:00:59+00:00"))
    self.assertFalse(is_outside(schedule, None, "2026-09-24T03:00:00+00:00"))
    with self.assertRaisesRegex(ValueError, "Unsupported WORKING_HOURS_TIMEZONE"):
      is_outside(schedule, "Europe/NotAZone", "2026-09-24T04:00:59+00:00")

    fallback = ScheduleHarness(schedule, None, "2026-09-24T04:00:59+00:00")
    fallback.log.timezone = "local timezone lookup failed"
    with self.assertRaisesRegex(ValueError, "Unsupported WORKING_HOURS_TIMEZONE"):
      _ = fallback.outside_working_hours

  def test_daily_overnight_and_equal_endpoint_minutes(self):
    """Daily schedules wrap midnight; equal endpoints select one full minute."""
    hours = [["22:00", "02:00"]]
    for clock, outside in [("21:59:59", True), ("22:00:00", False),
                           ("23:59:59", False), ("00:00:00", False),
                           ("02:00:59", False), ("02:01:00", True)]:
      with self.subTest(clock=clock):
        self.assertEqual(is_outside(hours, "UTC", "2026-09-24T" + clock + "+00:00"), outside)
    for clock, outside in [("05:59:59", True), ("06:00:00", False),
                           ("06:00:59", False), ("06:01:00", True)]:
      with self.subTest(equal_endpoint=clock):
        self.assertEqual(is_outside([["06:00", "06:00"]], "UTC",
                                    "2026-09-24T" + clock + "+00:00"), outside)

  def test_weekly_overnight_start_day_and_week_wrap(self):
    """Only the interval's starting weekday owns its before-midnight part."""
    hours = {"SUN": [["22:00", "02:00"]]}
    for instant, outside in [
      ("2026-09-27T01:00:00+00:00", True),
      ("2026-09-27T21:59:59+00:00", True),
      ("2026-09-27T22:00:00+00:00", False),
      ("2026-09-28T00:00:00+00:00", False),
      ("2026-09-28T02:00:59+00:00", False),
      ("2026-09-28T02:01:00+00:00", True),
    ]:
      with self.subTest(instant=instant):
        self.assertEqual(is_outside(hours, "UTC", instant), outside)

  def test_overnight_cold_start_reports_original_day_and_interval(self):
    """A carried shift wins overlapping current-day hours in hook metadata."""
    hours = {"MON": [["10:00", "11:00"], ["22:00", "02:00"]],
             "TUE": [["00:00", "03:00"]]}
    plugin = ScheduleHarness(hours, "UTC", "2026-09-22T01:00:00+00:00")
    self.assertFalse(plugin.outside_working_hours)
    hook = next(event for event in plugin.events if event[0] == "start_hook")
    self.assertEqual(hook[1:3], (1, "MON"))
    notification = next(event[1] for event in plugin.events if event[0] == "notification")
    self.assertIn("MON: ['22:00', '02:00'][UTC]", notification["msg"])

  def test_overnight_shift_does_not_restart_at_midnight(self):
    """Crossing the calendar day must preserve one active shift lifecycle."""
    plugin = ScheduleHarness({"MON": [["22:00", "02:00"]]}, "UTC",
                             "2026-09-21T22:00:00+00:00")
    self.assertFalse(plugin.outside_working_hours)
    plugin.now_utc = datetime.fromisoformat("2026-09-22T00:00:00+00:00")
    self.assertFalse(plugin.outside_working_hours)
    self.assertFalse(plugin.working_hours_is_new_shift)
    plugin.now_utc = datetime.fromisoformat("2026-09-22T02:00:59+00:00")
    self.assertFalse(plugin.outside_working_hours)
    plugin.now_utc = datetime.fromisoformat("2026-09-22T02:01:00+00:00")
    self.assertTrue(plugin.outside_working_hours)
    kinds = [event[0] for event in plugin.events]
    self.assertEqual(kinds.count("start_hook"), 1)
    self.assertEqual(kinds.count("end_hook"), 1)
    self.assertEqual(kinds.count("payload"), 2)

  def test_timezone_parser_retains_logger_forms(self):
    """Integer, tzinfo and legacy string offsets retain the same instant."""
    hours = [["06:00", "07:00"]]
    for zone in [2, timezone(timedelta(hours=2)), "UTC+2", "UTC+02:00"]:
      with self.subTest(zone=zone):
        self.assertFalse(is_outside(hours, zone, "2026-09-24T04:00:00+00:00"))
        self.assertTrue(is_outside(hours, zone, "2026-09-24T05:01:00+00:00"))
    self.assertFalse(is_outside(hours, "UTC-02:30", "2026-09-24T08:30:00+00:00"))

  def test_validation_reports_bad_zone_before_execution(self):
    """Invalid nonempty schedule zones produce the existing validation error."""
    for zone in ["Europe/NotAZone", "", 24, object()]:
      with self.subTest(zone=zone):
        plugin = ScheduleHarness([["06:00", "07:00"]], zone,
                                 "2026-09-24T04:00:00+00:00")
        self.assertFalse(plugin.validate_working_hours())
        self.assertEqual(sum(event[0] == "error" for event in plugin.events), 1)
    for hours in [[['22:00', '02:00']], [['06:00', '06:00']], {}, []]:
      self.assertTrue(ScheduleHarness(hours, "UTC", "2026-09-24T04:00:00+00:00")
                      .validate_working_hours())

  def test_normalization_and_config_update_do_not_reuse_stale_values(self):
    """Live hours/timezone/ignore updates are read on the next evaluation."""
    plugin = ScheduleHarness({"thu": [["06:00", "07:00"]]}, "UTC+2",
                             "2026-09-24T04:00:00+00:00")
    self.assertFalse(plugin.outside_working_hours)
    plugin.cfg_working_hours_timezone = "UTC"
    self.assertTrue(plugin.outside_working_hours)
    plugin.cfg_working_hours = ["04:00", "05:00"]
    self.assertFalse(plugin.outside_working_hours)
    plugin.cfg_working_hours = None
    self.assertTrue(plugin.outside_working_hours)
    plugin.cfg_ignore_working_hours = True
    self.assertFalse(plugin.outside_working_hours)

  def test_public_legacy_view_remains_separate_from_runtime_decision(self):
    """Keep the old conversion API while execution bypasses its clock loss."""
    plugin = ScheduleHarness({"thu": [["06:00", "07:00"]]}, "UTC",
                             "2026-09-24T06:30:00+00:00")
    calls = []
    def legacy_view(schedule, timezone=None):
      calls.append((schedule, timezone))
      return {"THU": [["08:00", "09:00"]]}
    plugin.working_hours_to_local = legacy_view
    self.assertEqual(plugin.working_hours, {"THU": [["08:00", "09:00"]]})
    self.assertEqual(calls, [({"THU": [["06:00", "07:00"]]}, "UTC")])
    calls.clear()
    self.assertFalse(plugin.outside_working_hours)
    self.assertEqual(calls, [])

  def test_ignore_hours_never_requires_an_interval_or_starts_twice(self):
    """Ignore-hours bypasses absent schedules through the existing lifecycle."""
    for hours in [None, {}, {"THU": []}, []]:
      with self.subTest(hours=hours):
        plugin = ScheduleHarness(hours, "UTC", "2026-09-24T04:00:00+00:00", True)
        self.assertFalse(plugin.outside_working_hours)
        self.assertFalse(plugin.outside_working_hours)
        self.assertEqual(sum(event[0] == "start_hook" for event in plugin.events), 1)

  def test_unrestricted_and_ignored_schedules_need_no_host_timezone(self):
    """Default/admin plugins must start even if host zone discovery failed."""
    for fallback in [None, "local timezone lookup failed"]:
      for hours, ignore in [([], False), ({}, True), ([["06:00", "07:00"]], True)]:
        with self.subTest(fallback=fallback, hours=hours, ignore=ignore):
          plugin = ScheduleHarness(hours, None, "2026-09-24T04:00:00+00:00", ignore)
          plugin.log.timezone = fallback
          self.assertTrue(plugin.validate_working_hours())
          self.assertFalse(any(event[0] == "error" for event in plugin.events))
          self.assertFalse(plugin.outside_working_hours)

  def test_overnight_overlap_handoff_does_not_restart_shift(self):
    """The carried interval and today's overlapping interval form one shift."""
    hours = {"MON": [["22:00", "02:00"]], "TUE": [["01:00", "03:00"]]}
    plugin = ScheduleHarness(hours, "UTC", "2026-09-22T01:00:00+00:00")
    self.assertFalse(plugin.outside_working_hours)
    plugin.now_utc = datetime.fromisoformat("2026-09-22T02:01:00+00:00")
    self.assertFalse(plugin.outside_working_hours)
    self.assertFalse(plugin.working_hours_is_new_shift)
    plugin.now_utc = datetime.fromisoformat("2026-09-22T03:01:00+00:00")
    self.assertTrue(plugin.outside_working_hours)
    kinds = [event[0] for event in plugin.events]
    self.assertEqual(kinds.count("start_hook"), 1)
    self.assertEqual(kinds.count("end_hook"), 1)


if __name__ == "__main__":
  unittest.main(verbosity=2)
