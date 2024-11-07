from multiprocessing import Pipe, Queue
from time import time, sleep


COMM_QUEUE_SIZE = 5


def comm_generator(engine='queue'):
  comm_server, comm_client = None, None
  if engine.lower() == 'pipe':
    pipe_server, pipe_client = Pipe(True)
    comm_server = CommEngine(pipe_server)
    comm_client = CommEngine(pipe_client)
  elif engine.lower() == 'queue':
    # raise ValueError("Not implemented")
    queue_server, queue_client = Queue(COMM_QUEUE_SIZE), Queue(COMM_QUEUE_SIZE)
    comm_server = CommEngine((queue_server, queue_client))
    comm_client = CommEngine((queue_client, queue_server))
  elif engine.lower() == 'raw_pipe':
    comm_server, comm_client = Pipe(True)
  # endif engine type
  return comm_server, comm_client


class CommEngine():
  def __init__(self, comm_obj, **kwargs):
    self.__comm = comm_obj
    if isinstance(comm_obj, tuple):
      self.__type = comm_obj[0].__class__.__name__.lower()
    else:
      self.__type = comm_obj.__class__.__name__.lower()
    return
  
  def send(self, data):
    if 'pipe' in self.__type:
      self.__comm.send(data)
    elif 'queue' in self.__type:
      self.__comm[0].put(data)
    return
  
  def recv(self):
    res = None
    if 'pipe' in self.__type:
      res = self.__comm.recv()
    elif 'queue' in self.__type:
      res = self.__comm[1].get()
    return res
  
  def poll(self, timeout):
    res = False
    if 'pipe' in self.__type:
      res = self.__comm.poll(timeout)
    elif 'queue' in self.__type:
      start_time = time()
      while time() - start_time < timeout and not res:
        sleep(0.001)
        res = not self.__comm[1].empty()
      # endwhile queue not empty
    return res

  def close(self):
    if 'pipe' in self.__type:
      self.__comm.close()
    elif 'queue' in self.__type:
      self.__comm[0].close()
      self.__comm[1].close()
    # endif
    return
