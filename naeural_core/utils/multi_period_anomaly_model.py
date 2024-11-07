from datetime import timedelta, datetime
from naeural_core.utils.basic_anomaly_model import BasicAnomalyModel
import numpy as np
DAYS_OF_WEEK = [
  0, #  'MONDAY',
  1, #'TUESDAY',
  2, #'WEDNESDAY',
  3, #'THURSDAY',
  4, #'FRIDAY',
  5, #'SATURDAY',
  6, #'SUNDAY'
]

HOURS = list(range(24))
DAYS_OF_MONTH = list(range(1,32))
EXPECTED_VALUE_INTERVAL_MULTIPLIER = 1.6

class MultiPeriodAnomalyModel:
  def __init__(self, anom_prc, log, timers_section=None, data_validation_callback=None):
    """
    :param anom_prc: float -> prc threshold that an event is considered an anomaly
    :param log: Logger object
    :param timers_section: Needed for logger.P
    :param data_validation_callback: callback used to validate data
    """
    self.anom_prc = anom_prc
    self.models = {
      'HOURLY_DAYS_OF_WEEK': {},
      'DAYS_OF_WEEK': {},
      'DAYS_OF_MONTH': {}
    }
    self.trained = False
    self.data_validation_callback = data_validation_callback
    self.log = log

    self._timers_section = timers_section
    return


  def train(self, np_data_hourly, np_data_daily, dates2id_hourly, dates2ids_daily, filter_start_date=None, new_dates_hourly=None, new_dates_daily=None):
    """
    Trains all the anomaly models where we have new data (eg. if we have only new datapoints for friday, we will only
      train the friday models). Filtering is done by filtering items in the `dates2id` dict

    :param np_data_hourly: Whole hourly data history
    :param np_data_daily: Whole daily data history
    :param dates2id_hourly: Dict mapping datetime to ids - for hourly data
    :param dates2ids_daily: Dict mapping datetime to ids - for daily data
    :param filter_start_date: A start date used to filter the data
    :param new_dates_hourly: Numpy array containing new hourly data (not previously trained on)
    :param new_dates_daily: Numpy array containing new daily data (not previously trained on)
    :return:
    """

    if filter_start_date:
      # Setup the start date filter
      dates2id_hourly = {k:v for k,v in dates2id_hourly.items() if k > filter_start_date}
      dates2ids_daily = {k:v for k,v in dates2ids_daily.items() if k > filter_start_date}
    #endif

    if new_dates_hourly is not None:
      unique_dow_hour = {x: [] for x in np.unique([y.day_of_week for y in new_dates_hourly])}
      for dow in unique_dow_hour.keys():
        unique_dow_hour[dow] = np.unique([x.hour for x in new_dates_hourly if x.day_of_week == dow]).tolist()
      self._train_hourly_days_of_week(np_data_hourly, dates2id_hourly, unique_dow_hour)

    if new_dates_daily is not None:
      unique_dow = np.unique([x.day_of_week for x in new_dates_daily]).tolist()
      self._train_days_of_week(np_data_daily, dates2ids_daily, unique_dow)

      unique_dom = np.unique([x.day for x in new_dates_daily]).tolist()
      self._train_days_of_month(np_data_daily, dates2ids_daily, unique_dom)
    self.trained = True
    return

  def _train_hourly_days_of_week(self, np_data_hourly, dates2id, unique_dow_hour):
    self.log.start_timer('train_hourly_dow', section=self._timers_section)
    for dow in unique_dow_hour.keys():
      filtered_dates2id = {
        k:v for k,v in dates2id.items()
        if k.day_of_week == dow
      }
      if dow not in self.models['HOURLY_DAYS_OF_WEEK']:
        self.models['HOURLY_DAYS_OF_WEEK'][dow] = {}
      # endif
      for hour in unique_dow_hour[dow]:
        self.log.start_timer('train_hourly_dow_single', section=self._timers_section)

        self.log.start_timer('train_hourly_dow_filter', section=self._timers_section)
        ts_indices = [
          v for k,v in filtered_dates2id.items()
          if k.hour == hour
        ]
        ts = np.expand_dims(np_data_hourly[ts_indices], -1)
        self.log.end_timer_no_skip('train_hourly_dow_filter', section=self._timers_section)

        self.log.start_timer('train_hourly_dow_fit', section=self._timers_section)

        self.models['HOURLY_DAYS_OF_WEEK'][dow][hour] = BasicAnomalyModel(
          data_validation_callback=self.data_validation_callback
        )
        self.models['HOURLY_DAYS_OF_WEEK'][dow][hour].fit(
          x_train=ts,
          prc=self.anom_prc
        )
        self.log.end_timer_no_skip('train_hourly_dow_fit', section=self._timers_section)

        self.log.end_timer_no_skip('train_hourly_dow_single', section=self._timers_section)

      #endfor hours
    #endfor dow
    self.log.end_timer_no_skip('train_hourly_dow', section=self._timers_section)
    return

  def _train_days_of_week(self, np_data_daily, dates2id, unique_dow):
    self.log.start_timer('train_dow', section=self._timers_section)

    for dow in unique_dow:
      self.log.start_timer('train_dow_single', section=self._timers_section)

      self.log.start_timer('train_dow_filter', section=self._timers_section)
      ts_indices = [
        v for k, v in dates2id.items()
        if k.day_of_week == dow
      ]
      ts = np.expand_dims(np_data_daily[ts_indices], -1)
      self.log.end_timer_no_skip('train_dow_filter', section=self._timers_section)
      self.log.start_timer('train_dow_fit', section=self._timers_section)

      self.models['DAYS_OF_WEEK'][dow] = BasicAnomalyModel(
        data_validation_callback=self.data_validation_callback
      )
      self.models['DAYS_OF_WEEK'][dow].fit(
          x_train=ts,
          prc=self.anom_prc
      )
      self.log.end_timer_no_skip('train_dow_fit', section=self._timers_section)

      self.log.end_timer_no_skip('train_dow_single', section=self._timers_section)

    #endfor
    self.log.end_timer_no_skip('train_dow', section=self._timers_section)

    return

  def _train_days_of_month(self, np_data_daily, dates2id, unique_dom):
    self.log.start_timer('train_dom', section=self._timers_section)

    for dom in unique_dom:
      self.log.start_timer('train_dom_single', section=self._timers_section)
      self.log.start_timer('train_dom_filter', section=self._timers_section)

      ts_indices = [
        v for k, v in dates2id.items()
        if k.day == (dom + 1)
      ]
      ts = np.expand_dims(np_data_daily[ts_indices], -1)
      self.log.end_timer_no_skip('train_dom_filter', section=self._timers_section)

      self.log.start_timer('train_dom_fit', section=self._timers_section)

      self.models['DAYS_OF_MONTH'][dom] = BasicAnomalyModel(
        data_validation_callback=self.data_validation_callback
      )
      self.models['DAYS_OF_MONTH'][dom].fit(
          x_train=ts,
          prc=self.anom_prc
      )
      self.log.end_timer_no_skip('train_dom_fit', section=self._timers_section)

      self.log.end_timer_no_skip('train_dom_single', section=self._timers_section)

    # endfor
    self.log.end_timer_no_skip('train_dom', section=self._timers_section)

    return

  def predict(self, np_data_hourly=None, np_data_daily=None, dates2ids_hourly=None, dates2ids_daily=None, series_info={}, first_event_day=None, skip_top_prc=None):
    """

    :param np_data_hourly: hourly data to be predicted
    :param np_data_daily: daily data to be predicted
    :param dates2ids_hourly: datetime to ids dict for hourly data
    :param dates2ids_daily: datetime to ids dict for daily data
    :param series_info: metadata to be appended to the anomalies
    :param first_event_day: datetime of the first event in the timeseries (to skip the 0s)
    :param skip_top_prc: remove the first percentage of elements (to skip stuff like configurations etc)

    :return: list(anomalies)
    """
    if not self.trained:
      return None
    anomalies = []

    if first_event_day:
      filter_start_date = first_event_day
      if skip_top_prc is not None:
        no_days = (datetime.now() - filter_start_date).days
        filter_start_date = filter_start_date + timedelta(days=int(no_days * skip_top_prc / 100))
    else:
      filter_start_date = None
    #endif

    if np_data_hourly is not None:
      if filter_start_date:
        dates2ids_hourly = {k: v for k, v in dates2ids_hourly.items() if k >= filter_start_date}
      #endif

      anomalies += self._predict_hourly_days_of_week(np_data_hourly, dates2ids_hourly, first_event_day=first_event_day, series_info=series_info)
    #endif

    if np_data_daily is not None:
      if filter_start_date:
        dates2ids_daily = {k: v for k, v in dates2ids_daily.items() if k >= filter_start_date}
      #endif

      anomalies += self._predict_days_of_week(np_data_daily, dates2ids_daily, first_event_day=first_event_day, series_info=series_info)
      anomalies += self._predict_days_of_month(np_data_daily, dates2ids_daily, first_event_day=first_event_day, series_info=series_info)
    #endif

    return anomalies

  def _predict_hourly_days_of_week(self, np_data_hourly, dates2id, first_event_day, series_info={}):
    anomalies = []

    for dow in DAYS_OF_WEEK:
      filtered_dates2id = {
        k: v for k, v in dates2id.items()
        if k.day_of_week == dow
      }
      if len(filtered_dates2id) == 0:
        continue

      for hour in HOURS:
        ts_indices = [
          v for k, v in filtered_dates2id.items()
          if k.hour == hour
        ]
        if len(ts_indices) == 0:
          continue

        ts = np.expand_dims(np_data_hourly[ts_indices], -1)

        preds = self.models['HOURLY_DAYS_OF_WEEK'][dow][hour].predict(
          x_test=ts
        )

        if any(preds):
          for i in range(preds.sum()):
            anomaly_date = {v: k for k, v in dates2id.items()}[np.array(ts_indices)[preds[:, 0]][i]]

            anomaly = {
              'anomaly_type': 'HOURLY_WEEKDAY_ANOMALY_W{}_H{}'.format(dow, hour),
              'expected_values': (
                max(np.round(self.models['HOURLY_DAYS_OF_WEEK'][dow][hour]._mean[0] - EXPECTED_VALUE_INTERVAL_MULTIPLIER * self.models['HOURLY_DAYS_OF_WEEK'][dow][hour]._std[0], decimals=1), 0),
                np.round(self.models['HOURLY_DAYS_OF_WEEK'][dow][hour]._mean[0] + EXPECTED_VALUE_INTERVAL_MULTIPLIER * self.models['HOURLY_DAYS_OF_WEEK'][dow][hour]._std[0], decimals=1)
              ),
              'anomaly_value': np_data_hourly[dates2id[anomaly_date]],
              'anomaly_date': datetime.strftime(anomaly_date, '%Y-%m-%d %H:%M'),
             'DAYS_FROM_START_DATE': (anomaly_date - first_event_day).days,

              **series_info
            }
            anomalies.append(anomaly)
          # endfor
        #endif
      # endfor hours
    # endfor dow
    return anomalies

  def _predict_days_of_week(self, np_data_daily, dates2id, first_event_day, series_info={}):
    anomalies = []

    for dow in DAYS_OF_WEEK:
      ts_indices = [
        v for k, v in dates2id.items()
        if k.day_of_week == dow
      ]
      ts = np.expand_dims(np_data_daily[ts_indices], -1)

      # Get and save results
      # TODO(S): investigate why does this throw error, key error 0
      preds = self.models['DAYS_OF_WEEK'][dow].predict(
        x_test=ts
      )

      if any(preds):
        for i in range(preds.sum()):
          # Preds is [True, False, False, ...]
          anomaly_date_selector = np.array(ts_indices)[preds[:,0]][i]
          anomaly_date = {
            v:k for k,v in dates2id.items()
          }[anomaly_date_selector]

          anomaly = {
            'anomaly_type': 'WEEKDAY_ANOMALY_{}'.format(dow),
            'expected_values': (
              max(np.round(self.models['DAYS_OF_WEEK'][dow]._mean[0] - EXPECTED_VALUE_INTERVAL_MULTIPLIER * self.models['DAYS_OF_WEEK'][dow]._std[0], decimals=1), 0),
              np.round(self.models['DAYS_OF_WEEK'][dow]._mean[0] + EXPECTED_VALUE_INTERVAL_MULTIPLIER * self.models['DAYS_OF_WEEK'][dow]._std[0], decimals=1)
            ),
            'anomaly_value': np_data_daily[dates2id[anomaly_date]], #ts[np.arange(preds.shape[0])[preds[:,0]][i]][0],
            'anomaly_date': datetime.strftime(anomaly_date, '%Y-%m-%d'),
            'DAYS_FROM_START_DATE': (anomaly_date - first_event_day).days,
            **series_info
          }
          anomalies.append(anomaly)
        #endfor
      #endif
    #endfor
    return anomalies

  def _predict_days_of_month(self, np_data_daily, dates2id, first_event_day, series_info={}):
    anomalies = []

    for dom in DAYS_OF_MONTH:
      ts_indices = [
        v for k, v in dates2id.items()
        if k.day == dom
      ]
      ts = np.expand_dims(np_data_daily[ts_indices], -1)
      # Get and save results
      if ts.shape[0] > 0:
        preds = self.models['DAYS_OF_MONTH'][dom].predict(
          x_test=ts,
        )
        if any(preds):
          for i in range(preds.sum()):
            anomaly_date = {v: k for k, v in dates2id.items()}[np.array(ts_indices)[preds[:, 0]][i]]
            anomaly = {
              'anomaly_type': 'MONTHDAY_ANOMALY_{}'.format(dom),
              'expected_values': (
                max(np.round(self.models['DAYS_OF_MONTH'][dom]._mean[0] - EXPECTED_VALUE_INTERVAL_MULTIPLIER * self.models['DAYS_OF_MONTH'][dom]._std[0], decimals=1), 0),
                np.round(self.models['DAYS_OF_MONTH'][dom]._mean[0] + EXPECTED_VALUE_INTERVAL_MULTIPLIER * self.models['DAYS_OF_MONTH'][dom]._std[0], decimals=1)
              ),
              'anomaly_value': np_data_daily[dates2id[anomaly_date]],
              'anomaly_date': datetime.strftime(anomaly_date, '%Y-%m-%d'),
              'DAYS_FROM_START_DATE': (anomaly_date - first_event_day).days,
              **series_info
            }
            anomalies.append(anomaly)
          #endfor range(preds.sum())
        #endif any(preds)
      #endif ts.shape[0]
    #endfor
    return anomalies



if __name__ == '__main__':
  from naeural_core import Logger
  # from datetime import datetime
  from naeural_core.utils.predictive_analytics.timeseries.numpyize_time_series import TimeseriesDataMaster

  log = Logger(lib_name='TST', base_folder='dropbox', app_folder='_local_data/_product_dynamics/_goc_experiments', TF_KERAS=False)
  data = log.load_dataframe(fn='goc_data.csv', timestamps=['DT_hour']).drop(columns=['Unnamed: 0'])

  initial_data = data[data.DT_hour < datetime(year=2022, month=2, day=1)]
  delta_data = data[data.DT_hour > datetime(year=2022, month=2, day=1)]

  eng = TimeseriesDataMaster(
    log=log, prefix='to_be_changed',
    aggregation_fields=['ID_SubEchipament', 'CheieEveniment'],
    time_field='DT_hour',
    target_field='event_count',
  )  ### TODO: parametrize

  # TODO: if is not None
  ds_master = eng.create_dataset()
  ds_master.setup(
    column_names=['event_count'],
    freq='d',  # set to 'h'
    df=initial_data
  )
  dct_datasets_master, _, _ = ds_master()
  np_master = dct_datasets_master['event_count']

  anomaly_model = MultiPeriodAnomalyModel(
    anom_prc=0.02
  )

  anomaly_model.train(
    np_master[3368], ds_master.dates2id
  )

  ds_delta = eng.create_dataset()
  ds_delta.setup(
    column_names=['event_count'],
    freq='h',  # set to 'h'
    df=delta_data
  )
  dct_datasets_delta, _, _ = ds_delta()
  np_delta = dct_datasets_delta['event_count']

  anomalies = anomaly_model.predict(
    np_delta[3368], ds_delta.dates2id
  )




