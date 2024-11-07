from naeural_core.comm import MQTTWrapper
from naeural_core.core_logging import SBLogger
from collections import deque
from time import sleep, time

if __name__ == '__main__':

  log = SBLogger()
  url_config_app = 'https://www.dropbox.com/s/bcoawurb1tyq7ma/config_app_paho_staging.txt?dl=1'

  saved_files, _ = log.maybe_download(
    url=url_config_app,
    fn='tmp/config_app_paho.txt',
    force_download=True,
    target='output'
  )

  config_app = log.load_json(saved_files[0])
  config_communication = config_app['COMMUNICATION']

  log.P("Server params:", color='b')
  for k, v in config_communication['PARAMS'].items():
    log.P(" * {}: {}".format(k, v), color='b')

  config_communication['PARAMS']['EE_ID'] = 'icsulescu'

  recv_buffer_1 = deque(maxlen=1000)
  receiver1 = MQTTWrapper(
    log=log, config=config_communication['PARAMS'], recv_buff=recv_buffer_1, recv_channel_name='NOTIF_CHANNEL')

  _dct_conn_receiver1 = receiver1.server_connect()
  log.P("Recv1 conn: {}".format(_dct_conn_receiver1))

  _dct_subscribe_receiver1 = receiver1.subscribe()
  log.P("Recv1 subs: {}".format(_dct_subscribe_receiver1))

  all_notifications = []

  def read_all(receiver, receiver_name):
    log.P("Flushing the queue for receiver {} ...".format(receiver_name))
    prev_len = len(receiver._recv_buff)
    while True:
      receiver.receive()
      crt_len = len(receiver._recv_buff)
      if crt_len == prev_len:
        break

      prev_len = crt_len
      sleep(0.1)
    # endwhile

    log.P("  Flush done, there were {} messages in the queue:".format(crt_len))
    while len(receiver._recv_buff) > 0:
      x = receiver._recv_buff.popleft()
      all_notifications.append(x)
      log.P("  {}".format(str(x)[:80]))
  # enddef

  start_loop = time()
  while True:
    read_all(receiver1, '1')
    sleep(5)

    if time() - start_loop >= 3 * 60:
      break
  # endwhile
