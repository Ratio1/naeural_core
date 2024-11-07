"""

1. Delete Manager
2. Open multiprocessing SharedMemory (serving_id = 'Slave2) & alloc in master 100 images
3. Write to SharedMemory & send to Slave nr of images
4. Slave gets nr of images and retrieves from SharedMemory

"""

import multiprocessing
import numpy as np
import time

from uuid import uuid4

from naeural_core.xperimental.manager_shm.slave import Slave1, Slave2  # Ensure this import matches your file organization
from naeural_core.utils.system_shared_memory import NumpySharedMemory
from naeural_core import Logger
from math import prod


MSG_SHAPE = (24, 3, 720, 1280)
MSG_SIZE = prod(MSG_SHAPE) * np.dtype('uint8').itemsize


class Master:
  """
  Master class to manage slave processes and communication, including sending specific payloads
  and receiving acknowledgments with counters.
  """
  def __init__(self, log, use_shared_memory=True):
    # self.manager = multiprocessing.Manager()
    self.queue1_snd = multiprocessing.Queue()
    self.queue1_rcv = multiprocessing.Queue()
    
    self.queue2_snd = multiprocessing.Queue()
    self.queue2_rcv = multiprocessing.Queue()
    # self.dict2 = self.manager.dict()
    self.shared_mem_kwargs = {
      'mem_name' : 'Slave2',
      'mem_size' : MSG_SIZE,
      'np_shape' : MSG_SHAPE,
      'np_type' : 'uint8',
      'log' : log,
    }
    self.dict2 = NumpySharedMemory(**self.shared_mem_kwargs, create=True)
    self.__counter = 0
    self.use_shared_memory = use_shared_memory

    self.slave1 = Slave1(self.queue1_snd, self.queue1_rcv)
    self.slave2 = Slave2(self.queue2_snd, self.queue2_rcv, {**self.shared_mem_kwargs, 'log': None}, use_shared_memory=use_shared_memory)
    return

  def generate_payload(self, nr_images=24, image_size=(3, 720, 1280)):
    """
    Generates a list of numpy arrays with specified values based on a counter.


    Returns:
    - list: A list of numpy arrays.
    """
    payload = {
      'DATA' : [],
      'COUNTER' : 0,
    }
    for i in range(nr_images):
      self.__counter += 1
      payload['DATA'].append(
        np.ones(image_size, dtype='uint8') * self.__counter
      )
    payload['COUNTER'] = self.__counter
    return payload
    

  def run_slaves_and_send_payload(self, iterations=10):
    """
    Starts slave processes, sends payloads with counters, and measures round-trip time.
    """
    try:
      self.slave1.start()
      self.slave2.start()
    except Exception as e:
      a = 1
      print(f"Error starting slave processes: {e}", flush=True)
      return
    slave1_timers = []
    slave2_timers = []
    slave21 = []
    
    USER_ARRAYS = True
    USE_SINGLE_BUFFER = True
    
    SHM_WORKER_ID = 'Slave2'
    
    if USER_ARRAYS and not self.use_shared_memory:
      self.dict2[SHM_WORKER_ID] = np.zeros((24, 3, 720, 1280), dtype='uint8')

    for counter in range(1, iterations):  # Example: Send 5 payloads
      payload = self.generate_payload()
      counter = payload['COUNTER']
      print(f"Master: Sending payload with last counter: {counter}...", flush=True)
      
      # Send payload to Slave1 and measure round-trip time
      
      start_time1 = time.time()
      self.queue1_snd.put(payload)
      recv1 = self.queue1_rcv.get()  # Receive counter acknowledgment
      elapsed1 = time.time() - start_time1
      slave1_timers.append(elapsed1)
      print(f"Master: Slave1 ack payload: {recv1} in {elapsed1:.4f} seconds.", flush=True)

      # Send payload to Slave2 and measure round-trip time
      start_time2 = time.time()
      if USE_SINGLE_BUFFER:
        pointer = SHM_WORKER_ID
      else:
        pointer = str(uuid4())[:5]
      imgs = np.array(payload['DATA'])
      t21 = time.time()
      if self.use_shared_memory:
        self.dict2.write(np.array(payload['DATA']))
      else:
        if not USER_ARRAYS:
          self.dict2[pointer] = payload['DATA']
        else:
          self.dict2[pointer][:,:,:,:] = imgs[:,:,:,:]
      e21 = time.time() - t21
      self.queue2_snd.put({'COUNTER' : pointer, 'DATA' :  None, 'IS_ARRAY' : USER_ARRAYS})  # Signal to process data
      recv2 = self.queue2_rcv.get()  # Receive counter acknowledgment
      elapsed2 = time.time() - start_time2
      slave2_timers.append(elapsed2)
      slave21.append(e21)
      data = self.dict2[pointer] if not self.use_shared_memory else self.dict2.read()
      print(f"Master: Slave2 ack payload: {recv2} in {elapsed2:.4f} seconds for {id(data)}", flush=True)
      if not USE_SINGLE_BUFFER:
        del self.dict2[pointer]

    # Cleanup
    print(f"Results:\n Slave1 timers: {np.mean(slave1_timers):.4f} seconds\n Slave2 timers: {np.mean(slave2_timers):.4f} seconds  {np.mean(slave21):.4f}", flush=True)
    self.queue1_snd.put({'DATA' : [], 'COUNTER' : None,})  # Signal to Slave1 to terminate
    self.queue2_snd.put({'DATA' : [], 'COUNTER' : None,})  # Signal to Slave1 to terminate
    self.slave1.join()
    self.slave2.join()
    return


def test_npy_copy(n_iter=1000):
  """
  Test the time it takes to copy a numpy array with different methods.
  """
  import math

  shape = (100, 720, 1280, 3)
  data = np.random.randint(low=0, high=255, size=shape, dtype='uint8')
  timings = []
  ok = True
  x = np.zeros(shape, dtype='uint8')
  print(f'Method 1: plain : + copy')
  for _ in range(n_iter):
    start_time = time.time()
    x[:] = data[:]
    y = x.copy()
    elapsed = time.time() - start_time
    timings.append(elapsed)
    ok = ok and np.all(data == y)
  print(f"[{ok}]Copy time: {np.mean(timings):.4f} seconds", flush=True)


  timings1 = []
  ok1 = True
  x = np.zeros(math.prod(shape), dtype='uint8')
  print(f'Method 2: ravel + : + reshape + copy')
  for _ in range(n_iter):
    start_time = time.time()
    x[:] = data.ravel()[:]
    y = x.reshape(shape)
    z = y.copy()
    elapsed = time.time() - start_time
    timings1.append(elapsed)
    ok1 = ok1 and np.all(data == z)
  print(f"[{ok1}]Copy time: {np.mean(timings1):.4f} seconds", flush=True)


  timings2 = []
  ok2 = True
  x = np.zeros(math.prod(shape), dtype='uint8')
  print(f'Method 3: reshape(-1) + : + reshape + copy')
  for _ in range(n_iter):
    start_time = time.time()
    x[:] = data.reshape(-1)[:]
    y = x.reshape(shape)
    z = y.copy()
    elapsed = time.time() - start_time
    timings2.append(elapsed)
    ok2 = ok2 and np.all(data == z)
  print(f"[{ok2}]Copy time: {np.mean(timings2):.4f} seconds", flush=True)

  timings3 = []
  ok3 = True
  x = np.zeros(math.prod(shape), dtype='uint8')
  print(f'Method 4: reshape(-1) + np.contigousarray + : + reshape + copy')
  for _ in range(n_iter):
    start_time = time.time()
    x[:] = np.ascontiguousarray(data.reshape(-1))[:]
    y = x.reshape(shape)
    z = y.copy()
    elapsed = time.time() - start_time
    timings3.append(elapsed)
    ok3 = ok3 and np.all(data == z)
  print(f"[{ok3}]Copy time: {np.mean(timings3):.4f} seconds", flush=True)

  timings4 = []
  ok4 = True
  x = np.zeros(math.prod(shape), dtype='uint8')
  print(f'Method 5: reshape(-1) + : + reshape + copy')
  for _ in range(n_iter):
    start_time = time.time()
    x[:] = data.reshape(-1)[:]
    y = x.reshape(shape)
    z = y.copy()
    elapsed = time.time() - start_time
    timings4.append(elapsed)
    ok4 = ok4 and np.all(data == z)
  print(f"[{ok4}]Copy time: {np.mean(timings4):.4f} seconds", flush=True)
  return


if __name__ == "__main__":
  test_npy_copy(100)
  exit(-1)
  log = Logger(
    'SHMT', base_folder='.', app_folder='_cache'
  )
  master = Master(log=log)
  master.run_slaves_and_send_payload()
