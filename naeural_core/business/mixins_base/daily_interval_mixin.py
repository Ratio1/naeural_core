import datetime
from naeural_core import constants as ct


class _DailyIntervalMixin(object):

  def __init__(self):
    super(_DailyIntervalMixin, self).__init__()
    return

  # @property
  # def cfg_daily_intervals(self):
  #   return self._instance_config.get(ct.DAILY_INTERVALS, 24)

  def _calculate_daily_intervals(self, *, intervals=None):
    intervals = intervals or self.cfg_daily_intervals
    time_delta = datetime.timedelta(minutes=24 * 60 / intervals)
    start = datetime.datetime(1900, 1, 1, 0, 0)
    lst = [start.strftime('%H:%M')]
    for _ in range(1, intervals):
      step = start + time_delta
      start = step
      lst.append(step.strftime('%H:%M'))
    # endfor
    return lst

  def _check_daily_interval(self, *, intervals=None, now_time=None):
    # TODO: should receive a now_time
    intervals = intervals or self.cfg_daily_intervals
    time_delta = datetime.timedelta(minutes=24 * 60 / intervals)
    lst = self._calculate_daily_intervals(intervals=intervals)

    _interval = None
    now = now_time or datetime.datetime.now()
    weekday = now.weekday()
    mod_now = datetime.datetime(year=1900, day=1, month=1, hour=now.hour, minute=now.minute)

    for idx, start_interval in enumerate(lst):
      start_date = datetime.datetime.strptime(start_interval, '%H:%M')
      stop_date = start_date + time_delta
      if mod_now >= start_date and mod_now < stop_date:
        _interval = idx  # if coding with time _interval = start_date
        break
    # endfor
    return _interval, weekday

  def _get_previous_interval(self, *, intervals=None, now_time=None):
    i, w = self._check_daily_interval(intervals=intervals, now_time=now_time)

    intervals = self._calculate_daily_intervals(intervals=intervals)
    index = i
    prev_index = (index - 1) % len(intervals)
    prev_w = w
    if prev_index > index:
      prev_w = (w - 1) % 7

    return prev_index, prev_w

  def _interval_to_now_datetime(self, interval):
    # should be compatible with 24h working
    h, m = interval.split(':')
    now = datetime.datetime.now()

    return datetime.datetime(year=now.year, day=now.day, month=now.month, hour=int(h), minute=int(m))
