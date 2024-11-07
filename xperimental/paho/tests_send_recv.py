import json
from tqdm import tqdm
from time import sleep
from naeural_core import Logger
from datetime import datetime
from comm.comm_plugins.paho_comm import PahoCommThread

CONFIG_SENDER = {
  'DEVICE_ID'             : '12345',
  'HOST'                  : 'HOSTNAME',
  'PORT'                  : 1883,
  'USER'                  : 'USERNAME',
  'PASS'                  : 'PASSWORD',
  'PATH'                  : 'PATH',
  'TOPIC_SEND'            : 'config_send_recv',
  'TOPIC_RECV'            : 'not_used',
  'TOPIC_DEVICE_SPECIFIC' : False,
  'QOS'                   : 1
}
CONFIG_RECEIVER = {
  'DEVICE_ID'             : '54321',
  'HOST'                  : 'HOSTNAME',
  'PORT'                  : 1883,
  'USER'                  : 'USERNAME',
  'PASS'                  : 'PASSWORD',
  'PATH'                  : 'PATH',
  'TOPIC_SEND'            : 'not_used',
  'TOPIC_RECV'            : 'config_send_recv',
  'TOPIC_DEVICE_SPECIFIC' : False,
  'QOS'                   : 1
}

def test_connection(comm):
  log.p('Running `test_connection`')
  is_ok = False
  t_sleep = 0.1
  nr_retry = 5
  has_conn = False
  i = 0
  while i < nr_retry:    
    has_conn = comm.has_send_conn and comm.has_recv_conn
    if has_conn:
      break
    i+= 1
    comm.dump_log()
    sleep(t_sleep)
  if not has_conn: 
    log.p('Test failed!', color='r')
  else:
    is_ok = True
    log.p('Communication passed!', color='g')
  return is_ok

def test_send_receive(comm_send, comm_recv, n_messages=200):
  log.p('Running `test_send_receive`')
  t_sleep = 0.1
  l_recv = []
  l_send = [{'ID': i, 'TEXT': 'TEST_{}'.format(i)} for i in range(n_messages)]
  l_send_test= l_send + 10 * [None] #some dummy messages that will not be send
  for msg in tqdm(l_send_test):
    if msg:
      comm_send.send(msg)
    sleep(t_sleep)
    l_recv+= comm_recv.get_messages()
  #endfor
  
  if not l_send == l_recv:
    log.p('Test failed! {} sent vs {} received'.format(len(l_send), len(l_recv)), color='r')
  else:
    log.p('Test passed!', color='g')
  return l_send, l_recv


if __name__ == '__main__':
  cfg_file = 'main_config.txt'
  log = Logger(
    lib_name='PAHO_TEST', 
    config_file=cfg_file, 
    max_lines=1000, 
    TF_KERAS=False
    )
  
  #start comm  
  comm_send = PahoCommThread(
    log=log,
    config_sender=CONFIG_SENDER,
    config_receiver=None
    )
  comm_send.start()
  
  comm_recv = PahoCommThread(
    log=log,
    config_sender=CONFIG_RECEIVER,
    config_receiver=None
    )
  comm_recv.start()
  
  #allow some time to setup communication with the server
  sec_sleep = 5
  start = datetime.now()
  while (datetime.now() - start).seconds <= sec_sleep:
    comm_send.dump_log()
    comm_recv.dump_log()
  
  send_ok = test_connection(comm_send)
  recv_ok = test_connection(comm_recv)
  if send_ok and recv_ok:
    l_send, l_recv = test_send_receive(comm_send, comm_recv)
  # test_send_receive_2(comm)
    