from datetime import datetime
from copy import copy

class _SqlQueryAcquisitionMixin(object):
  def __init__(self):
    self.stored_procedures = None
    self.queries = None
    super(_SqlQueryAcquisitionMixin, self).__init__()
    return

  def _get_sql_queries(self, current_datetime):
    """
    Returns queries to be run at the current timestep - based on the current timestep and the past
      ran queries.

    :param current_datetime: datetime
    :return: dict(DATASOURCE_NAME: query string)
    """
    sql_queries = {}

    if self.stored_procedures is not None:
      for i in range(len(self.stored_procedures)):
        if self._has_new_data(self.stored_procedures[i], current_datetime):
          last_query_datetime = self.stored_procedures[i].get(
            'LAST_QUERY_DATETIME',
            self._backlog_start_time
          )

          sql_queries[self.stored_procedures[i]['DATASOURCE_NAME']] = self._parse_sp(
              self.stored_procedures[i], current_datetime, last_query_datetime
          )
          self.stored_procedures[i]['LAST_QUERY_DATETIME'] = datetime.strftime(
            current_datetime,
            "%Y-%m-%d %H:%M:%S"
          )
        # endif
      # endfor

    if self.queries is not None:
      for i in range(len(self.queries)):
        if self._has_new_data(self.queries[i], current_datetime):
          last_query_datetime = self.queries[i].get(
            'LAST_QUERY_DATETIME',
            self._backlog_start_time
          )

          sql_queries[self.queries[i]['DATASOURCE_NAME']] = self._parse_query(
            self.queries[i],
            current_datetime,
            last_query_datetime
          )

          self.queries[i]['LAST_QUERY_DATETIME'] = datetime.strftime(
            current_datetime,
            "%Y-%m-%d %H:%M:%S"
          )
        # endif
      # endfor
    # endif
    return sql_queries

  def _save_state(self):
    """
    Saves last ran datetime for each query and stored procedure

    :return:
    """
    if not self.cfg_save_last_state:
      return

    obj = {
      'stored_procedures': {},
      'queries': {}
    }

    if self.stored_procedures is not None:
      obj['stored_procedures'] = {
        sp['DATASOURCE_NAME']: sp for sp in self.stored_procedures
      }

    if self.queries is not None:
      obj['queries'] = {
          q['DATASOURCE_NAME']: q for q in self.queries
      }

    self.persistence_serialization_save(obj)
    return

  def _load_state(self):
    """
    Loads last ran datetime for each query and stored procedure

    :return:
    """
    obj = self.persistence_serialization_load()
    if obj is None:
      return

    for name, details in obj['stored_procedures'].items():
      for i in range(len(self.stored_procedures)):
        if self.stored_procedures[i]['DATASOURCE_NAME'] == name:
            self.stored_procedures[i]['LAST_QUERY_DATETIME'] = details['LAST_QUERY_DATETIME']
        # endif
      # endfor
    # endfor

    for name, details in obj['queries'].items():
      for i in range(len(self.stored_procedures)):
        if self.queries[i]['DATASOURCE_NAME'] == name:
          self.queries[i]['LAST_QUERY_DATETIME'] = details['LAST_QUERY_DATETIME']
        # endif
      # endfor
    # endfor
    return

  def _has_new_data(self, dct_config, current_datetime):
    """
    Checks if a query needs to be ran at the current timestamp - based on its config,
      current datetime and last run time

    :param dct_config:
    :param current_datetime:
    :return:
    """
    period = dct_config.get("PERIOD", "DAILY")  ### Default period is daily
    last_query_time = datetime.strptime(
      dct_config.get("LAST_QUERY_DATETIME", self._backlog_start_time),
      "%Y-%m-%d %H:%M:%S")

    if last_query_time == datetime(1900, 1, 1, 0, 0):
      return True

    if period == 'DAILY':
      current_date = copy(current_datetime).replace(minute=0, hour=0)
      query_hour = dct_config.get("SCHEDULE_HOUR", None)
      if (current_date - last_query_time).days >= 1:
        if query_hour is not None:
          if query_hour == current_datetime.hour:
            return True
        else:
          return True
      return False
    elif period == 'HOURLY':
      if (current_datetime - last_query_time).seconds >= 3600 or \
              (current_datetime - last_query_time).days > 1:
        return True
      return False
    elif period == 'WEEKLY':
      current_date = copy(current_datetime).replace(minute=0, hour=0)
      query_hour = dct_config.get("SCHEDULE_HOUR", None)
      if (current_date - last_query_time).days >= 7:
        if query_hour is not None:
          if query_hour == current_datetime.hour:
            return True
        else:
          return True
      return False
    elif period == 'MINUTE':
      if (current_datetime - last_query_time).seconds >= 60 or \
              (current_datetime - last_query_time).days > 1:
        return True
      return False

    else:
      raise ValueError("Invalid configured query period `{}`".format(period))
    return


  def _parse_query(self, dct_query, current_datetime, last_query_datetime):
    """
    Method used to convert a query dict to the string required to be run for the current time window

    :param dct_query:
    :param current_datetime:
    :param last_query_datetime:
    :return:
    """
    query_string = "SELECT {} FROM {} WHERE {}".format(', '.join(
      dct_query['SELECT']),
      dct_query["TABLE"],
      self._get_filter_string(dct_query, current_datetime, last_query_datetime)
    )

    if dct_query.get("GROUP_BY", None) not in ["NONE", "None", None]:
      query_string += " GROUP BY {}".format(', '.join(dct_query['GROUP_BY']))

    return query_string

  def _get_filter_string(self, dct_query, current_datetime, last_query_datetime):
    """
    Method used to parse the 'WHERE' part of the query.

    :param dct_query:
    :param current_datetime:
    :param last_query_datetime:
    :return:
    """
    filter_string = "{} < CONVERT(DATETIME, '{}') ".format(
      dct_query['DATETIME_COLUMN'],
      current_datetime
    )
    filter_string += " AND {} > CONVERT(DATETIME, '{}') ".format(
      dct_query['DATETIME_COLUMN'],
      last_query_datetime,
    )

    if dct_query.get("FILTER", None) not in ["NONE", 'None', None]:
      filter_string += " AND {}".format(dct_query['FILTER'])

    return filter_string

  def _parse_sp(self, dct_stored_procedure, current_datetime, last_query_datetime):
    """
    Method used to create the query string needed to execute a stored procedure on the current
      time windows

    :param dct_stored_procedure: stored procedure config
    :param current_datetime: current datetime
    :param last_query_datetime: datetime of the last run (used for the start point of the window)

    :return: query string
    """
    query_string = "EXEC {} @{} = '{}', @{} = '{}'".format(
        dct_stored_procedure["SP_NAME"],
        dct_stored_procedure["START_DATE_PARAMETER_NAME"],
        last_query_datetime,
        dct_stored_procedure["END_DATE_PARAMETER_NAME"],
        current_datetime
      )
    return query_string


