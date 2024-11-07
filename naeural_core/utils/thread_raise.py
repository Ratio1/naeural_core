from time import sleep
import threading
import ctypes

NULL = 0
dct_shmem = {
  }
        

def ctype_async_raise(target_tid, exception):
    ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(target_tid), ctypes.py_object(exception))
    # ref: http://docs.python.org/c-api/init.html#PyThreadState_SetAsyncExc
    if ret == 0:
      raise ValueError("Invalid thread ID")
    elif ret > 1:
      # Huh? Why would we notify more than one threads?
      # Because we punch a hole into C level interpreter.
      # So it is better to clean up the mess.
      ctypes.pythonapi.PyThreadState_SetAsyncExc(target_tid, NULL)
      raise SystemError("PyThreadState_SetAsyncExc failed")
    print("Successfully set asynchronized exception for", target_tid)

def main_stopper():
  sleep(dct_shmem['wait'])
  ctype_async_raise(dct_shmem['pid'], ValueError)
  return


if __name__ == '__main__':
  dct_shmem['pid'] = threading.get_ident()
  dct_shmem['wait'] = 10
  
  thr = threading.Thread(target=main_stopper, daemon=True)
  thr.start()
  try:
    while True:
      sleep(0.1)
  except Exception as e:
    print('Exited from forever loop: {}'.format(e))
  
  print('Done.')
  
  
  