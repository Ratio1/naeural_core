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
  )
  mon.start()

  done_flags = {
    1 : False,
    2 : False,
    }

  def stop_thread(thr):
    done_flags[thr] = True
    return
  
  data = ['start']
  data2 = ['start_bin']
  l.save_data_json(data, 'test.txt')
  l.save_pickle_to_data(
    data=data2,
    fn='test.pkl'
    )
  l.P(l._lock_table)  
  
  l.update_pickle_from_data(fn='test.pkl', update_callback=lambda x:x + ['update'])
  
    
  
  def t1():
    cnt = 0
    def t1_appender(lst):
      lst.append('t1_' + str(cnt))
      return lst
    while True:
      # l.P("T1 " + str(i))
      cnt += 1
      l.update_data_json(fname='test.txt', update_callback=t1_appender)
      l.update_pickle_from_data(fn='test.pkl', update_callback=t1_appender)
      if done_flags[1]:
        l.P("Forcing quit T1")
        break
      else:
        sleep(0.001)
    return  


  def t2():
    cnt = 0
    def t2_appender(lst):
      lst.append('t2_' + str(cnt))
      return lst
    i = 0
    while True:
      # l.P("T2 " + str(i))
      cnt += 1
      i += 1
      l.update_data_json(fname='test.txt', update_callback=t2_appender)
      l.update_pickle_from_data(fn='test.pkl', update_callback=t2_appender)
      if done_flags[2]:
        l.P("Forcing quit T2")
        break
      else:
        sleep(0.001)
    return  
  
  thr1 = threading.Thread(target=t1, name='S_T1000', daemon=True)
  thr2 = threading.Thread(target=t2, name='S_TLoop', daemon=True)
  thr1.start()
  thr2.start()
  
  sleep(1)
  
  pts = psutil.Process().threads()
  tts = threading.enumerate()
  l.P("psutil threads: {}".format(pts), color='y')
  l.P("threading threads: {}".format(tts), color='m')
  try:
    l.P("threading native: {}".format(', '.join(['{}:{}'.format(x.name, x.native_id) for x in tts])), color='b')
  except:
    l.P("ERROR: native pids not support on this system", color='error')
                                       
  
  
  thr1.join(timeout=3)
  l.P("Thread thr1 join exited and thread is_alive = {}.".format(thr1.is_alive()))
  thr2.join(timeout=3)
  l.P("Thread thr2 join exited and thread is_alive = {}.".format(thr2.is_alive()))
  
  l.P("Stopping thr1")
  stop_thread(1)
  l.P("Stopping thr2")
  stop_thread(2)
  l.P("Thread thr2 is_alive={}".format(thr2.is_alive()))
  
  l.P(l._lock_table)
  
  d1 = l.load_data_json(fname='test.txt')
  d2 = l.load_pickle_from_data(fn='test.pkl')
  # l.P(d1)
  # l.P(d2)
  
  mon.log_status(full_log=True)
  
  mon.stop()
  
    
  # l.dict_pretty_format(mon.threads_data, display=True)
