

class _AquisitionOnIntervalsMixin(object):

  def __init__(self):
    super(_AquisitionOnIntervalsMixin, self).__init__()
    return

  @property
  def cfg_intervals(self):
    dct_intervals = self.cfg_stream_config_metadata.get('INTERVALS', {})
    return dct_intervals

  def acquisition_on_intervals_current_interval(self):
    if len(self.cfg_intervals) == 0:
      return

    for name, [start, end] in self.cfg_intervals.items():
      if self.log.now_in_interval_hours(start=start, end=end):
        return name

    return ""
