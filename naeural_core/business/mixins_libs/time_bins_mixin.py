import numpy as np
from collections import deque
from naeural_core.business.templates.counting_template_mixin import _CountingTemplateMixin

from naeural_core.utils.basic_anomaly_model import BasicAnomalyModel


class _TimeBinsMixin(object):
  # TODO: change name to api calls (tbinapi_{})
  def __init__(self, **kwargs):
    # self.__time_bins = {} TODO: This doesn't work, don't know why
    self._time_bins = {}
    self._accepted_functions = {
      "mean": self.__custom_mean,
      "median": self.__custom_median,
      "count": self.__custom_count,
      "min": self.__custom_min,
      "max": self.__custom_max,
      "std": self.__custom_std,
      "sum": self.__custom_sum,
    }
    super(_TimeBinsMixin, self).__init__(**kwargs)
    return

  def __custom_mean(self, data):
    if len(data) == 0:
      return self.cfg_report_default_empty_value
    return np.mean(data)

  def __custom_median(self, data):
    if len(data) == 0:
      return self.cfg_report_default_empty_value
    return np.median(data)

  def __custom_count(self, data):
    return len(data)

  def __custom_min(self, data):
    if len(data) == 0:
      return self.cfg_report_default_empty_value
    return min(data)

  def __custom_max(self, data):
    if len(data) == 0:
      return self.cfg_report_default_empty_value
    return max(data)

  def __custom_std(self, data):
    if len(data) == 0:
      return self.cfg_report_default_empty_value
    return np.std(data)

  def __custom_sum(self, data):
    if len(data) == 0:
      return self.cfg_report_default_empty_value
    return sum(data)

  def timebins_create_bin(self, key="default", weekday_names=None, report_default_empty_value=None, per_day_of_week_timeslot=None, warmup_anomaly_models=None):
    if key not in self._time_bins:
      self._time_bins[key] = _TimeBins(
        self,
        weekday_names if weekday_names is not None else self.cfg_weekday_names,
        report_default_empty_value if report_default_empty_value is not None else self.cfg_report_default_empty_value,
        per_day_of_week_timeslot if per_day_of_week_timeslot is not None else self.cfg_per_day_of_week_timeslot,
        warmup_anomaly_models if warmup_anomaly_models is not None else self.cfg_warmup_anomaly_models
      )
    return

  def _save_bins(self):
    # self.persistence_serialization_save(self._deques)
    return

  def _maybe_load_bins(self):
    # try:
    #   deq = self.persistence_serialization_load()
    # except:
    #   return
    # if deq is None:
    #   return
    # self._get_bin()

    # def _merge(source, destination):
    #   """ Deep merge two dicts.

    #   Args:
    #       source (dict):
    #       destination (dict):

    #   Returns:
    #       dict: the resulting dict after merge
    #   """
    #   for key, value in source.items():
    #       if isinstance(value, dict):
    #           # get node or create one
    #           node = destination.setdefault(key, {})
    #           _merge(value, node)
    #       else:
    #           destination[key] = value

    #   return destination

    # self._deques = _merge(deq, self._deques)
    return

  def timebins_append(self, value, key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    self._time_bins[key]._append_bin(value)

  def timebins_get_bin(self, key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    return self._time_bins[key]._get_bin()

  def timebins_get_bin_mean(self, key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    return self._time_bins[key]._get_bin_mean()

  def timebins_is_anomaly(self, key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    return self._time_bins[key]._is_binned_anomaly()

  def timebins_get_bin_size(self, key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    return self._time_bins[key]._get_bin_size()

  def timebins_get_total_size(self, key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    return self._time_bins[key]._get_total_size()

  def timebins_get_bin_report(self, aggr_func="mean", key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    # TODO: base on the string, compute mean, median, count, min, max, std, sum
    assert aggr_func in self._accepted_functions
    return self._time_bins[key]._get_bin_report(aggr_func, self._accepted_functions[aggr_func])

  def timebins_get_bin_statistic(self, aggr_func="mean", key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    assert aggr_func in self._accepted_functions

    return self._time_bins[key]._get_bin_aggr_func(self._accepted_functions[aggr_func])

  def timebins_get_previous_bin_statistic(self, aggr_func="mean", key='default'):
    assert key in self._time_bins, "Unknown time_bins key {}".format(key)
    assert aggr_func in self._accepted_functions

    return self._time_bins[key]._get_previous_bin_aggr_func(self._accepted_functions[aggr_func])


class _TimeBins(object):
  # TODO: add to base?
  def __init__(self, owner, weekday_names, report_default_empty_value, per_day_of_week_timeslot, warmup_anomaly_models, **kwargs):
    self._deques = {}
    self._anomaly_detectors = {}
    self._owner = owner

    self.weekday_names = weekday_names
    self.report_default_empty_value = report_default_empty_value
    self.per_day_of_week_timeslot = per_day_of_week_timeslot
    self.warmup_anomaly_models = warmup_anomaly_models

    return

  def startup(self):
    super().startup()

  # @property
  # def cfg_weekday_names(self):
  #   return self._instance_config.get("WEEKDAY_NAMES", ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])

  # @property
  # def cfg_report_default_empty_value(self):
  #   return self._instance_config.get("REPORT_DEFAULT_EMPTY_VALUE", -1)

  # @property
  # def cfg_per_day_of_week_timeslot(self):
  #   return self._instance_config.get("PER_DAY_OF_WEEK_TIMESLOT", True)

  # @property
  # def cfg_warmup_anomaly_models(self):
  #   return self._instance_config.get("WARMUP_ANOMALY_MODELS", 10)

  def _maybe_init_bin(self, weekday, interval):
    intervals = self._owner._calculate_daily_intervals()
    index = interval
    next_index = (index + 1) % len(intervals)
    i = intervals[index]
    next_i = intervals[next_index]

    if self.per_day_of_week_timeslot:
      if weekday not in self._deques or weekday not in self._anomaly_detectors:
        self._deques[weekday] = {}
        self._anomaly_detectors[weekday] = {}
      # endif

      if interval not in self._deques[weekday] or interval not in self._anomaly_detectors[weekday]:
        self._deques[weekday][interval] = _TimeBinDeque(
          maxlen=20000, interval=f"{self.weekday_names[weekday][:3]}_{i}-{next_i}")
        self._anomaly_detectors[weekday][interval] = BasicAnomalyModel()
      # endif
    else:
      if interval not in self._deques or interval not in self._anomaly_detectors:
        self._deques[interval] = _TimeBinDeque(
          maxlen=20000, interval=f"{i}-{next_i}")
        self._anomaly_detectors[interval] = BasicAnomalyModel()
      # endif
    # endif

  def _get_bin(self, interval=None, weekday=None):
    # TODO: should receive a now_time
    i, w = self._owner._check_daily_interval()
    interval = interval if interval is not None else i
    weekday = weekday if weekday is not None else w

    self._maybe_init_bin(
      interval=interval,
      weekday=weekday
    )
    if self.per_day_of_week_timeslot:
      return self._deques[weekday][interval]
    else:
      return self._deques[interval]
    # endif
    return

  def _get_bin_mean(self, interval=None, weekday=None):
    dq = self._get_bin(interval, weekday)
    if dq is not None:
      return np.mean(dq)
    return None

  def _init_all_bins(self):
    daily_intervals = self._owner._calculate_daily_intervals()
    for interval in range(len(daily_intervals)):
      if self.per_day_of_week_timeslot:
        for weekday in range(7):
          self._maybe_init_bin(
            interval=interval,
            weekday=weekday
          )
      else:
        _, weekday = self._owner._check_daily_interval()
        self._maybe_init_bin(
          interval=interval,
          weekday=weekday
        )

  def __format_report(self, report, aggr_func_name=None):
    formatted_report = {}

    if self.per_day_of_week_timeslot:
      for weekday, intervals in report.items():
        intervals_names = list(intervals.keys())
        weekday_name = self.weekday_names[weekday]
        formatted_report[weekday_name] = {}
        for i in range(len(intervals) - 1):
          value = intervals[intervals_names[i]]

          formatted_report[weekday_name][intervals_names[i]] = {
            'START': intervals_names[i],
            'END': intervals_names[i + 1],
            'VALUE': value,
            'AGGR_FUNC': aggr_func_name
          }
    else:
      formatted_report = {}
      intervals_names = list(report.keys())
      for i in range(len(report) - 1):
        value = report[intervals_names[i]]

        formatted_report[intervals_names[i]] = {
          'START': intervals_names[i],
          'END': intervals_names[i + 1],
          'VALUE': value,
          'AGGR_FUNC': aggr_func_name
        }
    return formatted_report

  def __format_result(self, value, i):
    intervals = self._owner._calculate_daily_intervals()
    index = i
    next_index = (index + 1) % len(intervals)
    i = intervals[index]
    next_i = intervals[next_index]

    start_interval = self._owner._interval_to_now_datetime(i)
    end_interval = self._owner._interval_to_now_datetime(next_i)

    start_interval = self._owner.log.time_to_str(start_interval.timestamp())
    end_interval = self._owner.log.time_to_str(end_interval.timestamp())

    return {'START': start_interval, 'END': end_interval, 'VALUE': value}

  def _get_bin_size(self, interval=None, weekday=None):
    dq = self._get_bin(interval, weekday)
    if dq is not None:
      return len(dq)
    return None

  def _get_total_size(self):
    total_size = 0
    for w in self._deques.values():
      for h in w.values():
        total_size += len(h)
      # endfor
    # endof
    return total_size

  def _get_previous_bin_aggr_func(self, aggr_func):
    i, w = self._owner._get_previous_interval()
    result = self._get_bin_aggr_func(aggr_func, interval=i, weekday=w)
    return self.__format_result(result, i)

  def _get_bin_aggr_func(self, aggr_func, interval=None, weekday=None):
    dq = self._get_bin(interval, weekday)
    if dq is not None:
      return aggr_func(dq)
    return None

  def _get_bin_report(self, aggr_func_name, aggr_func):
    report = {}
    daily_intervals = self._owner._calculate_daily_intervals()
    self._init_all_bins()
    if self.per_day_of_week_timeslot:
      for weekday in range(7):
        report[weekday] = {}
        for i, interval in enumerate(daily_intervals):
          report[weekday][interval] = self._get_bin_aggr_func(aggr_func, i, weekday)
        # end for intervals
      # end for weekdays
    else:
      for i, interval in enumerate(daily_intervals):
        report[interval] = self._get_bin_aggr_func(aggr_func, i)
      # end for intervals
    return self.__format_report(report, aggr_func_name)

  def _get_model(self):
    interval, weekday = self._owner._check_daily_interval()
    self._maybe_init_bin(interval=interval, weekday=weekday)
    if self.per_day_of_week_timeslot:
      return self._anomaly_detectors[weekday][interval]
    else:
      return self._anomaly_detectors[interval]
    # endif
    return

  def _append_bin(self, value):
    # TODO: should receive a now_time
    dq = self._get_bin()
    dq.append(value)

  def _is_binned_anomaly(self):
    dq = self._get_bin()
    if len(dq) > self.warmup_anomaly_models:
      model = self._get_model()
      model.fit(np.expand_dims(np.array(dq)[:-1], -1), prc=0.05)
      res = model.predict(np.expand_dims(np.array(dq)[-1], -1))
      return bool(res[0][0])
    return False


class _TimeBinDeque(deque):
  def __init__(self, *args, interval, **kwargs):
    self.interval = interval
    super(_TimeBinDeque, self).__init__(*args, **kwargs)

  def __str__(self):
    min_v = f"{np.min(self):.02f}" if len(self) > 0 else 'n'
    max_v = f"{np.max(self):.02f}" if len(self) > 0 else 'n'
    mean_v = f"{np.mean(self):.02f}" if len(self) > 0 else 'n'
    median_v = f"{np.median(self):.02f}" if len(self) > 0 else 'n'
    header = f"{self.interval} m/M/a/med={min_v}/{max_v}/{mean_v}/{median_v} |"
    return header + " ".join([f"{x}" if isinstance(x, int) else f"{x:.02f}" for x in list(self)[-10:]])


"""
no weekdays
[
  {
    'start': 'timp',
    'end': 'timp',
    'value': ___
  }
]

{
  'timp-timp': ____
}

with weekdays
{
  'monday':[
    {
      'start': 'timp',
      'end': 'timp',
      'value': ___
    }
  ]
}


"""


"""
report = {
  'start': {
    'start': __
    'end': ___
    'value': __
    'aggr_func': ___
  }
}

report = {
  'monday':{
    'start': {
      'start': __
      'end': ___
      'value': __
      'aggr_func': ___
    }
  }
}



"""
