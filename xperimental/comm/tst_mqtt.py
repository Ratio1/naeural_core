from naeural_core.comm import MQTTWrapper
from naeural_core.core_logging import SBLogger
from collections import deque
from time import sleep

if __name__ == '__main__':

  log = SBLogger()
  url_config_app = 'https://www.dropbox.com/s/bcoawurb1tyq7ma/config_app_paho_staging.txt?dl=1'
  # url_config_app = 'https://www.dropbox.com/s/is5b15d27kv9hi9/config_app_paho_gts_bmw.txt?dl=1'
  # url_config_app = 'https://www.dropbox.com/s/qco560yiyi1rrzv/config_app_paho_249_gts_qa.txt?dl=1'
  USE_MULTIPLE_RECEIVERS = False
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
  recv_buffer_2 = deque(maxlen=1000)
  sender = MQTTWrapper(log=log, config=config_communication['PARAMS'],
                       recv_buff=None, send_channel_name='PAYLOADS_CHANNEL', DEBUG=True)
  receiver1 = MQTTWrapper(
    log=log, config=config_communication['PARAMS'], recv_buff=recv_buffer_1, recv_channel_name='PAYLOADS_CHANNEL')
  if USE_MULTIPLE_RECEIVERS:
    receiver2 = MQTTWrapper(
      log=log, config=config_communication['PARAMS'], recv_buff=recv_buffer_1, recv_channel_name='PAYLOADS_CHANNEL')

  _dct_conn_sender = sender.server_connect()
  log.P("Send  conn: {}".format(_dct_conn_sender))
  _dct_conn_receiver1 = receiver1.server_connect()
  log.P("Recv1 conn: {}".format(_dct_conn_receiver1))
  if USE_MULTIPLE_RECEIVERS:
    _dct_conn_receiver2 = receiver2.server_connect()
    log.P("Recv2 conn: {}".format(_dct_conn_receiver2))

  _dct_subscribe_receiver1 = receiver1.subscribe()
  log.P("Recv1 subs: {}".format(_dct_subscribe_receiver1))
  if USE_MULTIPLE_RECEIVERS:
    _dct_subscribe_receiver2 = receiver2.subscribe()
    log.P("Recv2 subs: {}".format(_dct_subscribe_receiver2))

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
      log.P("  {}".format(str(x)[:80]))
  # enddef

  read_all(receiver1, '1')
  if USE_MULTIPLE_RECEIVERS:
    read_all(receiver2, '2')

  for i in range(30):
    iteration = i + 1
    log.P("\n====Iteration {:03d}====".format(iteration))
    sender.send(log.safe_dumps_json({'msg_id': i + 1, 'payload': 'test'}, ensure_ascii=False))
    read_all(receiver1, '1')
    if USE_MULTIPLE_RECEIVERS:
      read_all(receiver2, '2')
  # endfor
