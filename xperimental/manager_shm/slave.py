import multiprocessing
import numpy as np
from naeural_core.utils.system_shared_memory import NumpySharedMemory
from naeural_core import Logger


class Slave(multiprocessing.Process):
  """
  Base class for Slave processes, handling acknowledgment with counters.
  """
  def __init__(self, queue_rcv, queue_snd, show_prints=True):
    super().__init__()
    self.queue_rcv = queue_rcv
    self.queue_snd = queue_snd
    self.show_prints = show_prints
    return

  def maybe_print(self, *args, flush=False):
    if self.show_prints:
      print(*args, flush=flush)
    return

class Slave1(Slave):
  """
  Slave1 processes data from a Queue and sends back a counter acknowledgment.
  """
  def run(self):
    while True:
      payload = self.queue_rcv.get()
      data = payload['DATA']
      counter = payload['COUNTER']
      if counter is None:
        self.maybe_print("Slave1: received termination signal.", flush=True)
        break  # Termination signal
      # Assume data[0] contains the counter value used for the entire payload
      # some processing
      counters = [x[0,0,0] for x in data]
      self.maybe_print(f"Slave1: processing payload with counter {counter}...")
      response = {
        'RESULT' : counters,
      }
      self.queue_snd.put(response)  # Send counter back as acknowledgment
    return

class Slave2(Slave):
  """
  Slave2 processes data from a Manager dict and sends back a counter acknowledgment through a Queue.
  """
  def __init__(self, queue_rcv, queue_snd, data_dict, show_prints=True, use_shared_memory=False, log: Logger = None):
    super().__init__(queue_rcv, queue_snd, show_prints=show_prints)
    self.data_dict = data_dict
    self.use_shared_memory = use_shared_memory
    return

  def run(self):
    if self.use_shared_memory:
      self.maybe_print("Slave2: using shared memory...")
      self.data_dict['log'] = Logger(
        'SHMT', base_folder='.', app_folder='_cache'
      )
      local_buffer = NumpySharedMemory(**self.data_dict, create=False)
    else:
      local_buffer = np.zeros((24, 3, 720, 1280), dtype='uint8')
    while True:
      payload = self.queue_rcv.get()
      pointer = payload['COUNTER']
      is_array = payload.get('IS_ARRAY')
      if pointer is None: 
        self.maybe_print("Slave2: received termination signal.", flush=True)
        break  # Termination signal
      if self.use_shared_memory:
        data = local_buffer.read()
      else:
        if not is_array:
          data = self.data_dict[pointer]
        else:
          local_buffer[:,:,:,:] = self.data_dict[pointer][:,:,:,:]  # Copy data to local buffer
          data = local_buffer
      if data is not None:
        # Assume data[0] contains the counter value used for the entire payload
        # some processing
        counters = [x[0,0,0] for x in data]
        counter = max(counters)
        self.maybe_print(f"Slave2: processing payload with counter {counter} id: {id(data)}...", flush=True)
        response = {
          'RESULT' : counters,
        }
        self.queue_snd.put(response)  # Send counter back as acknowledgment
      #endif
    #endwhile
    return

if __name__ == "__main__":
    print("This script is not intended to be run directly.")
