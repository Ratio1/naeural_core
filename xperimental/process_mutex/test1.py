from time import sleep
import multiprocessing as mp

  
from naeural_core import Logger

if __name__ == '__main__':
  mp.set_start_method('spawn')
  
  l = Logger('PMTX', base_folder='.', app_folder='_cache')
  
  lock = l.lock_process('AID_MOB')
  
  if lock is not None:    
    print("Lock: {}".format(lock))
    for i in range(100):
      print("I'm running",i)
      sleep(2)
    print("I'm done")
  else:
    print("Process already running")

  
