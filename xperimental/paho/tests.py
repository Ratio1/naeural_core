from tqdm import tqdm
from time import sleep
from naeural_core import Logger
from datetime import datetime
from comm.comm_plugins.paho_comm import PahoCommThread

CONFIG = {
  'DEVICE_ID'             : '12345',
  'HOST'                  : 'HOSTNAME',
  'PORT'                  : 1883,
  'USER'                  : 'USERNAME',
  'PASS'                  : 'PASSWORD',
  'PATH'                  : 'PATH',
  'TOPIC_SEND'            : 'config_send_recv',
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

def test_send_receive(comm, n_messages=200):  
  log.p('Running `test_send_receive`')  
  t_sleep = 0.1
  l_recv = []
  l_send = [{'ID': i, 'TEXT': 'TEST_{}'.format(i)} for i in range(n_messages)]
  l_send_test = l_send + 10 * [None] #some empty messages
  for msg in tqdm(l_send_test):
    if msg is not None:
      comm.send(msg)
    sleep(t_sleep)
    l_recv+= comm.get_messages()
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
  comm = PahoCommThread(
    log=log,
    config_sender=CONFIG,
    config_receiver=None
    )
  comm.start()
  
  #allow some time to setup communication with the server
  sec_sleep = 5
  start = datetime.now()
  while (datetime.now() - start).seconds <= sec_sleep:
    comm.dump_log()
  
  is_ok = test_connection(comm)
  if is_ok:    
    l_send, l_recv = test_send_receive(comm)
    