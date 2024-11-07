#global dependencies
import pandas as pd

from datetime import datetime as dt
from dateutil.relativedelta import relativedelta

class _IntervalAggregationMixin(object):

  def __init__(self):
    self._aggregation_buffer = []
    self._aggregation_times = []
    super(_IntervalAggregationMixin, self).__init__()
    return

  @property
  def cfg_interval_aggregation_seconds(self):
    return self._instance_config.get('INTERVAL_AGGREGATION_SECONDS', 0)

  def aggregation_add_to_buffer(self, obj):
    self._aggregation_buffer.append(obj)
    full_date_fmt = '%Y-%m-%d %H:%M:%S'
    relative_time_fmt = '%H:%M:%S'

    t = None
    is_last_data = False
    fmt = full_date_fmt
    if hasattr(self, 'is_data_limited'):
      if self.is_data_limited_and_has_frame:
        if self.is_last_data:
          is_last_data = True
        t = dt.strptime(self.limited_data_crt_time, '{}.%f'.format(relative_time_fmt))
        fmt = relative_time_fmt

    if t is None:
      t = dt.now()

    self._aggregation_times.append(t)
    intervals = []

    df = pd.DataFrame(
      {
        'ts'  : self._aggregation_times,
        'idx' : list(range(len(self._aggregation_times)))
      }
    )

    interval_seconds = self.cfg_interval_aggregation_seconds
    if interval_seconds == 0:
      freq = '1ms'
    else:
      freq = '{}s'.format(interval_seconds)

    groups = df.groupby(pd.Grouper(key='ts', freq=freq))

    last_idx = -1
    for i, (_time, df_grp) in enumerate(groups):
      start_time = _time
      end_time = _time + relativedelta(seconds=interval_seconds)
      idx = df_grp['idx'].values

      # at tick, the interest is for the last added object, whereas for specific intervals
      # the interest is for all the past intervals except the current one which is not finished
      if i == len(groups)-1 and interval_seconds > 0 and not is_last_data:
        break

      objects = []
      for j in idx:
        objects.append(self._aggregation_buffer[j])
        last_idx = j

      intervals.append({
        'START' : start_time.strftime(fmt),
        'END'   : end_time.strftime(fmt),
        'OBJECTS' : objects
      })
    #endfor

    self._aggregation_buffer = self._aggregation_buffer[last_idx+1:]
    self._aggregation_times = self._aggregation_times[last_idx+1:]

    if len(intervals) == 0:
      last_interval = None
      null_intervals = []
    else:
      last_interval = intervals[0]
      null_intervals = intervals[1:]

    ### sanity check that null intervals are really null :)
    for interval in null_intervals:
      objects = interval.pop('OBJECTS')
      if len(objects) > 0:
        self.P("WARNING! There is a coding problem in `aggregation_add_to_buffer`", color='e')
    #endfor

    return last_interval, null_intervals

# if __name__ == '__main__':
#
#   from time import sleep
#
#   class Base(object):
#     def __init__(self, **kwargs):
#       self._instance_config = {
#         'INTERVAL_AGGREGATION_SECONDS' : 0
#       }
#       return
#
#
#   class P(_IntervalAggregationMixin, Base):
#     def __init__(self, **kwargs):
#       super(P, self).__init__(**kwargs)
#       return
#
#
#   p = P()
#   for step in range(10):
#     _last_interval, _null_intervals = p.aggregation_add_to_buffer({'INFERENCE' : step+1})
#     print(_last_interval)
#     print(len(_null_intervals))
#     sleep(1)
#     # if step == 10:
#     #   sleep(20)
#
