
class _WorkingHoursMixin(object):

  def __init__(self):
    self.__is_new_shift = False
    self.__shift_started = False
    super(_WorkingHoursMixin, self).__init__()
    return

  def _valid_hour_interval(self, interval):
    """
    Method for validating an interval.
    Parameters
    ----------
    interval : list - list of 2 strings in format HH:MM

    Returns
    -------
    res - bool - True if the interval is valid, False otherwise
    """
    if not isinstance(interval, list) or len(interval) != 2:
      return False
    if not all(isinstance(time, str) for time in interval):
      return False
    for time in interval:
      if len(time.split(':')) < 2:
        return False
      hour, minute = time.split(':')[:2]
      if not (0 <= int(hour) < 24 and 0 <= int(minute) < 60):
        return False
    return True

  def _valid_hour_schedule(self, hour_schedule):
    """
    Method for validating the hour schedule.
    Parameters
    ----------
    hour_schedule : list - list of intervals described as a list of 2 strings in format HH:MM

    Returns
    -------
    res - bool - True if the schedule is valid, False otherwise
    """
    if not isinstance(hour_schedule, list):
      return False
    if not all(self._valid_hour_interval(interval) for interval in hour_schedule):
      return False
    return True

  def validate_working_hours(self):
    """
    Method for validating the working hours configuration.
    Returns
    -------
    res - bool - True if the configuration is valid, False otherwise
    """
    working_hours = self.cfg_working_hours
    if working_hours is None:
      return True
    err_message = (
      f"Invalid working hours configuration for plugin {self}.\nThe configuration "
      f"should be a list of intervals, with each interval described as a list of 2 "
      f"strings in format \"HH:MM\" or a dict with the days as keys and the "
      f"already described list format as values.\n"
    )
    is_valid = True
    if isinstance(working_hours, list):
      # if the schedule is a list of intervals we consider it to be a universal schedule(the weekday is irrelevant)
      if not self._valid_hour_schedule(working_hours):
        is_valid = False
    elif isinstance(working_hours, dict):
      # if the schedule is a dict we consider it to be a schedule based on week days and besides the check made for the
      # intervals we also check if the keys are valid week days.
      if not all(
        key.upper() in self.ct.WEEKDAYS_SHORT and isinstance(value, list) and self._valid_hour_schedule(value)
        for key, value in working_hours.items()
      ):
        is_valid = False
    else:
      # if the schedule is not a list or a dict it is invalid.
      is_valid = False
    # endif working_hours type
    if not is_valid:
      self.add_error(err_message)
    return is_valid

  def get_timezone(self):
    tz = self.cfg_working_hours_timezone
    if tz is None:
      return self.log.timezone
    return tz

  def str_to_datetime(self, str_time, weekday=None):
    """
    Convert a string time to a datetime object
    Parameters
    ----------
    str_time : str - time in format HH:MM
    weekday : int or None - the weekday index starting from 0

    Returns
    -------
    datetime object with the time set to the provided one
    """
    # This will be 1st of January 1900, Monday
    res_dt = self.datetime.strptime(str_time, '%H:%M')
    if weekday is not None:
      res_dt = res_dt.replace(day=weekday + 1)
    # This is done in order to avoid invalid argument errors when converting the datetime to local time
    res_dt = res_dt + self.timedelta(weeks=5000)
    return res_dt

  def interval_to_local(self, interval, weekday=None, timezone=None):
    """
    Method for converting an interval to local time.
    In case the weekday is provided and the interval is crossing the midnight
    this will return a list of 2 intervals, one for each day.
    Parameters
    ----------
    interval : list - list of 2 strings representing the start and end of the interval in format HH:MM
    weekday : int or None - the weekday index starting from 0
    timezone : str or None - the timezone to convert to

    Returns
    -------
    res - list of 1 or 2 tuples representing the weekday, start and end of the interval in local time.
    """
    res = []
    start_dt = self.str_to_datetime(interval[0], weekday)
    end_dt = self.str_to_datetime(interval[1], weekday)
    tz = timezone if timezone is not None else self.get_timezone()
    start_dt_local = self.log.utc_to_local(start_dt, tz)
    end_dt_local = self.log.utc_to_local(end_dt, tz)
    if start_dt_local.weekday() != end_dt_local.weekday():
      res.append((start_dt_local.weekday(), start_dt_local, self.datetime.combine(start_dt_local.date(), self.datetime.max.time())))
      res.append((end_dt_local.weekday(), self.datetime.combine(end_dt_local.date(), self.datetime.min.time()), end_dt_local))
    else:
      res.append((start_dt_local.weekday(), start_dt_local, end_dt_local))
    return res

  def working_hours_to_local(self, working_hours_schedule, timezone=None):
    """
    Method for converting the working hours to local time.
    Parameters
    ----------
    working_hours_schedule : list or dict - the working hours schedule
    timezone : str or None - the timezone to convert to

    Returns
    -------
    res_working_hours - list or dict with the working hours (and weekdays if necessary) converted to local time
    """
    dct_working_hours = {}
    universal_schedule = False
    if isinstance(working_hours_schedule, list):
      # if the schedule is a list of intervals we consider it to be a universal schedule(the weekday is irrelevant)
      universal_schedule = True
      dct_working_hours = {None: working_hours_schedule}
    elif isinstance(working_hours_schedule, dict):
      dct_working_hours = {**working_hours_schedule}
    # endif list
    tz = timezone if timezone is not None else self.get_timezone()
    lst_all_intervals = []
    for key, lst_intervals in dct_working_hours.items():
      if key is None or key.upper() in self.ct.WEEKDAYS_SHORT:
        weekday_idx = None if key is None else self.ct.WEEKDAYS_SHORT.index(key.upper())
        for interval in lst_intervals:
          lst_all_intervals.extend(self.interval_to_local(interval, weekday_idx, timezone=tz))
        # endfor
      # endif key is None or key in WEEKDAYS_SHORT
    # endfor
    if universal_schedule:
      res_working_hours = [
        [start_dt.strftime('%H:%M'), end_dt.strftime('%H:%M')]
        for _, start_dt, end_dt in lst_all_intervals
      ]
    else:
      res_working_hours = {}
      for (key, start_dt, end_dt) in lst_all_intervals:
        key_str = self.ct.WEEKDAYS_SHORT[key]
        if key_str not in res_working_hours:
          res_working_hours[key_str] = []
        res_working_hours[key_str].append([start_dt.strftime('%H:%M'), end_dt.strftime('%H:%M')])
      # endfor
    # endif universal_schedule
    return res_working_hours

  @property
  def working_hours(self):
    schedule = self.cfg_working_hours

    # adapted also in case of having just one interval and mistakenly the operator did not configured list of lists.
    if isinstance(schedule, list) and len(schedule) == 2 and isinstance(schedule[0], str):
      schedule = [schedule]

    if isinstance(schedule, dict):
      schedule = {
        key.upper(): value
        for key, value in schedule.items()
      }
    # endif dict
    try:
      res_working_hours = self.working_hours_to_local(schedule, timezone=self.get_timezone())
    except Exception as e:
      self.P("Exception {} occurent in working_hours_to_local:\ncfg_working_hours:\n{}\n\nschedule:\n{}".format(
        e, self.json_dumps(self.cfg_working_hours), self.json_dumps(schedule),
      ))
      raise
    #endtry
    return res_working_hours
  
  
  def on_shift_start(self, interval_idx=None, weekday_name=None, **kwargs):
    """
    This method should be defined in plugins with specific code that will be
    run when a new working hours shift starts.
    """
    return

  def on_shift_end(self, **kwargs):
    """
    This method should be defined in plugins with specific code that will be
    run when a working hours shift ends.
    """
    return

  def __on_shift_start(self, interval_idx, weekday_name=None):
    # TODO: in future implement dict params for instance config in a specific time interval
    hour_schedule = self.working_hours[weekday_name] if weekday_name is not None else self.working_hours
    hrs = 'NON-STOP' if interval_idx is None else hour_schedule[interval_idx]
    shift_str = f'{hrs}' if weekday_name is None else f'{weekday_name}: {hrs}[{self.get_timezone()}]'
    msg = f"Starting new working hours shift {shift_str}"
    info = f"Plugin {self} starting new shift {shift_str}"
    self.P(msg)
    self._create_notification(
      msg=msg,
      info=info,
      displayed=True,
      notif_code=self.ct.NOTIFICATION_CODES.PLUGIN_WORKING_HOURS_SHIFT_START, 
    )
    self.add_payload_by_fields(
      status="Working hours shift STARTED",
      working_hours=self.cfg_working_hours,
      working_hours_timezone=self.get_timezone(),
      forced_pause=self.cfg_forced_pause,
      ignore_working_hours=self.cfg_ignore_working_hours,
      img=None,
    )
    self.P("Executing on_shift_start method")
    self.on_shift_start(interval_idx=interval_idx, weekday_name=weekday_name)
    return

  def __on_shift_end(self):
    msg = f"Ended current working hours shift for {self}. The full schedule is: {self.working_hours}[{self.get_timezone()}]"
    info = f"Plugin {self} ended its shift shift. The full schedule is: {self.working_hours}[{self.get_timezone()}]"
    self.P(msg, color='r')
    self._create_notification(
      msg=msg,
      info=info,
      working_hours=self.cfg_working_hours,
      working_hours_timezone=self.get_timezone(),
      forced_pause=self.cfg_forced_pause,
      displayed=True,
      notif_code=self.ct.NOTIFICATION_CODES.PLUGIN_WORKING_HOURS_SHIFT_END, 
    )
    self.add_payload_by_fields(
      status="Working hours shift ENDED",
      working_hours=self.cfg_working_hours,
      working_hours_timezone=self.get_timezone(),
      ignore_working_hours=self.cfg_ignore_working_hours,
      forced_pause=self.cfg_forced_pause,
      img=None,
    )
    self.P("Executing on_shift_end method")
    self.on_shift_end()
    return

  def __get_outside_working_hours(self):
    interval_idx = None
    result = True
    weekday_name = None

 # if the plugin is configured to ignore working hours it will always be considered as inside working hours
    if self.cfg_ignore_working_hours:      
      return False, interval_idx, weekday_name
    
    # if the provided working_hours is None the plugin will always be outside working hours
    if self.working_hours is None:
      return True, interval_idx, weekday_name

    ts_now = self.datetime.now()
    # extracting both the weekday and the hour intervals if it's the case
    lst_hour_schedule, weekday_name = self.log.extract_weekday_schedule(
      ts=ts_now,
      schedule=self.working_hours,
      return_day_name=True
    )
    
    # in case we have schedule based on week days and the current day was not specified
    # it means we are outside the working hours
    
    if lst_hour_schedule is not None:
      # if hour_schedule is an empty list we have 2 cases:
      # 1. The plugin will work non-stop on the current day (if schedule is using week days)
      # 2. The plugin will work non-stop regardless of the week day
      if len(lst_hour_schedule) == 0:
        result = False

      interval_idx = self.log.extract_hour_interval_idx(
        ts=ts_now,
        lst_schedule=lst_hour_schedule
      )
    # endif hour_schedule is not None
    return result, interval_idx, weekday_name
  

  @property
  def outside_working_hours(self):
    result, interval_idx, weekday_name = self.__get_outside_working_hours()

    # interval found or non-stop functioning
    if interval_idx is not None or not result:
      self.__is_new_shift = False
      if not self.__shift_started:
        self.__is_new_shift = True      
        self.__on_shift_start(
          weekday_name=weekday_name,
          interval_idx=interval_idx
        )
      # endif mark new shift
      self.__shift_started = True
      result = False
    elif self.__shift_started:
      # shift already started so we close it
      self.__on_shift_end()
      self.__shift_started = False
    # endif current time in valid interval
    return result


  @property
  def working_hours_is_new_shift(self):
    return self.__is_new_shift




if __name__ == '__main__':
  from naeural_core import Logger
  from datetime import datetime

  log = Logger(
    'gigi',
    base_folder='.',
    app_folder='_local_cache'
  )
  class Base(object):
    def __init__(self, **kwargs):
      return

  def str_to_datetime(str_time, weekday=None):
    """
    Convert a string time to a datetime object
    Parameters
    ----------
    str_time : str - time in format HH:MM
    weekday : int or None - the weekday index starting from 0

    Returns
    -------
    datetime object with the time set to the provided one
    """
    # This will be 1st of January 1900, Monday
    res_dt = datetime.strptime(str_time, '%H:%M')
    if weekday is not None:
      res_dt = res_dt.replace(day=weekday + 1)
    return res_dt


  def interval_to_datetime(interval):
    return [
      log.str_to_datetime(interval[0]),
      log.str_to_datetime(interval[1])
    ]

  class P(_WorkingHoursMixin, Base):
    def __init__(self, **kwargs):
      super(P, self).__init__(**kwargs)
      return


  working_hours_tests = [
    [
      ['21:10', '10:10'],
      ['11:10', '11:25']
    ],
    [['10:10', '21:10']]
  ]

  for hours in working_hours_tests:
    log.P(hours)
    p = P()
    p.cfg_working_hours = hours
    p.log = log
    log.P(f'`p.outside_working_hours`={p.outside_working_hours}')
    log.P(f'`p.working_hours_is_new_shift`={p.working_hours_is_new_shift}')
    # print(p.outside_working_hours)
    # print(p.working_hours_is_new_shift)

