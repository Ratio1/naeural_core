import threading
import psutil
from time import sleep
from naeural_core import Logger

from naeural_core.utils.sys_mon import SystemMonitor
                  

if __name__ == '__main__':
  l = Logger('THR', base_folder='.', app_folder='_cache')
  mon = SystemMonitor(
    log=l, 
    monitored_prefix='S_', 
    name='S_SysMon',
    max_size=20,
    tick=0.1,
    DEBUG=True # shows debug status dict
  )
  mon.start()

  mon.log_status()

  thread_data = {
    1 : {
      'flag' : False,
      'thread' : None,
    },
    2 : {
      'flag' : False,
      'thread' : None,
    },
  }

  def stop_thread(thr):
    thread_data[thr]['flag'] = True
    thread_data[thr]['thread'].join()
    return
  
  def start_thread(thr):
    thread_data[thr]['thread'].start()
    return
    
      
  
  def t1():
    cnt = 0
    while True:
      cnt += 1
      if thread_data[1]['flag']:
        l.P("Forcing quit {}".format(thread_data[1]['thread']), color='d')
        break
      else:
        sleep(0.5)
        l.P("Tick {} from {}".format(cnt, thread_data[1]['thread']), color='d')
    return  


  def t2():
    cnt = 0
    while True:
      cnt += 1
      if thread_data[2]['flag']:
        l.P("Forcing quit {}".format(thread_data[2]['thread']), color='d')
        break
      else:
        sleep(1)
        l.P("Tick {} from {}".format(cnt, thread_data[2]['thread']), color='d')
    return  
  
  thr1 = threading.Thread(target=t1, name='S_T1', daemon=True)
  thr2 = threading.Thread(target=t2, name='S_T2', daemon=True)
  thread_data[1]['thread'] = thr1
  thread_data[2]['thread'] = thr2
  
  l.P("Starting threads...")
  start_thread(1)
  start_thread(2)
  
  sleep(5)
                                         
  mon.log_status()
  
  
  l.P("Stopping thr1")
  stop_thread(1)
  l.P("Thread thr1 is_alive={}".format(thr1.is_alive()))
  l.P("Stopping thr2")
  stop_thread(2)
  l.P("Thread thr2 is_alive={}".format(thr2.is_alive()))
  
  mon.log_status()
  
  l.P("Sleeping another 2 sec", color='g')
  sleep(2)

  mon.log_status()
    
  # l.dict_pretty_format(mon.threads_data, display=True)

  # mon.display_process_tree_info()