from time import time
from collections import deque
from datetime import datetime as dt
import numpy as np

class _CommunicationTelemetryMixin(object):
  def __init__(self):
    self._dct_columns = self._empty_telemetry_data()
    self._last_dump_time = time()
    self._deque_time_payloads_trip = deque(maxlen=99)
    super(_CommunicationTelemetryMixin, self).__init__()
    return

  def _empty_telemetry_data(self):
    return {
      'event_type'            : [],
      't1_cap_time'           : [],
      't2_plugin_time'        : [],
      't3_comm_added_in_buff' : [],
      't4_comm_before_send'   : [],
      't5_comm_after_send'    : [],
      'successful_send'       : [],
      'message_uuid'          : [],
      'ee_current_message'    : [],
      'ee_total_messages'     : [],
      'demo_mode'             : [],
      'stream_id'             : [],
      'instance_id'           : [],
      'business_data'         : []
    }

  @property
  def statistics_payloads_trip(self):
    if len(self._deque_time_payloads_trip) == 0:
      return ''

    _avg = np.mean(self._deque_time_payloads_trip)
    _min, _med, _max = np.quantile(self._deque_time_payloads_trip, q=[0,0.5,1])

    s = "Payloads trip (avg: {:.3f}s) -> min:{:.2f}s med:{:.2f}s max:{:.2f}s".format(
      _avg, _min, _med, _max
    )

    return s

  def _telemetry_add_message(self, msg, t3_comm_added_in_buff, t5_comm_after_send, successful_send):
    event_type = msg['type']

    msg_cap_metadata = msg['metadata'].get('captureMetadata', {})
    msg_plg_metadata = msg['metadata'].get('pluginMetadata', {})
    t1_cap_time = msg_cap_metadata.get('cap_time', None)
    t2_plugin_time = msg['data']['time']

    self._dct_columns['event_type'].append(event_type)
    self._dct_columns['t1_cap_time'].append(t1_cap_time)
    self._dct_columns['t2_plugin_time'].append(t2_plugin_time)
    self._dct_columns['t3_comm_added_in_buff'].append(t3_comm_added_in_buff)
    self._dct_columns['t4_comm_before_send'].append(msg['time']['hostTime'])
    self._dct_columns['t5_comm_after_send'].append(t5_comm_after_send)
    self._dct_columns['successful_send'].append(int(successful_send))
    self._dct_columns['message_uuid'].append(msg['messageID'])
    self._dct_columns['ee_current_message'].append(msg['metadata']['sbCurrentMessage'])
    self._dct_columns['ee_total_messages'].append(msg['metadata']['sbTotalMessages'])
    self._dct_columns['demo_mode'].append(int(msg_plg_metadata.get('DEMO_MODE', False)))
    self._dct_columns['stream_id'].append(msg['data']['identifiers'].get('streamId', None))
    self._dct_columns['instance_id'].append(msg['data']['identifiers'].get('instanceId', None))
    self._dct_columns['business_data'].append(self.log.safe_dumps_json(msg['data']['specificValue']))

    if event_type not in ['heartbeat', 'notification'] and t1_cap_time is not None:
      dt_t1 = dt.strptime(t1_cap_time, "%Y-%m-%d %H:%M:%S.%f")
      dt_t5 = dt.strptime(t5_comm_after_send, "%Y-%m-%d %H:%M:%S.%f")
      self._deque_time_payloads_trip.append((dt_t5-dt_t1).total_seconds())
    #endif

    if time() - self._last_dump_time >= 60:
      self._dump_telemetry_data()
    return

  def telemetry_maybe_add_message(self, msg, ts_added_in_buff, successful_send):
    if self._formatter_name == 'cavi2' and self.log.config_data.get('COLLECT_TELEMETRY', False):
      _tmp_time = self.log.now_str(nice_print=True, short=False)
      self._telemetry_add_message(
        msg=msg,
        t3_comm_added_in_buff=ts_added_in_buff,
        t5_comm_after_send=_tmp_time,
        successful_send=successful_send,
      )
    #endif
    return

  def _dump_telemetry_data(self):
    import pandas as pd
    df = pd.DataFrame(self._dct_columns)

    self.log.save_dataframe(
      df,
      fn='{}.csv'.format(self.log.now_str()),
      folder='output',
      subfolder_path='comm_telemetry/{}'.format(self.log.file_prefix),
      verbose=False
    )

    self._last_dump_time = time()
    self._dct_columns = self._empty_telemetry_data()
    return
