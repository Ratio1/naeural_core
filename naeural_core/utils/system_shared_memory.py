"""
TODO:
  - must find locking methods for free position searching
  - change additional to np.int64 to allow for PID of child process instead of 1 when marking a position as occupied
  - use pid in read_buffer_data to check if the data is requested by the right process
"""

import numpy as np
import sys
import traceback

from naeural_core import DecentrAIObject

__VER__ = '0.2.1.3'
MAXLEN = 50
SHAPE_OFFSET = 10  # on how many elements the shape will be stored


class NumpySharedMemory(DecentrAIObject):
  def __init__(
      self, mem_name, np_shape, np_type,
      create=False, is_buffer=False,
      shape_offset=SHAPE_OFFSET,
      maxlen=MAXLEN, **kwargs
  ):
    """
    Opens an already existing kernel level shared memory depending of python version.
    In case this is used as a buffer there will be an additional memory zone used
    to store the availability of the memory and the shape of the numpy array.
    It will have the shape (maxlen, 1 + shape_offset) and each line will be structured as follows:
    - the first element will dictate if the line is available or not
    - the next elements will store the shape of the numpy array
  
    Parameters
    ----------
    mem_name : str
      shared memory name as created by its originator.

    np_shape : tuple
      shape of the target numpy ndarray.
      
    np_type : str
      type of the target numpy ndarray.

    create : bool
      if True, the shared memory is created, otherwise it is opened.

    is_buffer : bool
      if True, the shared memory is treated as a buffer, otherwise it is treated as a single zone of memory.

    shape_offset : int
      Will be used when the shared memory is treated as a buffer.
      This will dictate how many elements will be used to store the shape of the numpy array.

    maxlen : int
      Will be used then the shared memory is treated as a buffer.
      
    log : Logger
      parent instance of the Logger
      
  
    Returns
    -------
    Nothing.  
    """
    self.initiator = create
    self.error_message = None
    self.initialized = False
    self.version = __VER__
    self._mem_name = mem_name
    self._np_shape = np_shape
    self._np_type = np_type
    prod_shape = np.prod(np_shape, dtype='uint64')
    self._np_bytes = prod_shape * np.dtype(np_type).itemsize
    self.is_buffer = is_buffer
    self.maxlen = maxlen
    self.shape_offset = shape_offset
    self.buffer_position = 0
    if self.is_buffer:
      self._np_shape = (maxlen, prod_shape)
      self._np_bytes = maxlen * self._np_bytes
      self._max_element_size = prod_shape
    self._mmap_obj = None
    self._shm = None
    super(NumpySharedMemory, self).__init__(**kwargs)
    return

  @property
  def uses_mp(self):
    return self.log.python_minor >= 8

  @property
  def shm_type(self):
    if self.uses_mp:
      return "multiprocessing.SharedMemory"
    else:
      return "posix_ipc"      

  def get_instance_type(self, initiator=False):
    return "Creator" if initiator else "User"

  @property
  def instance_type(self):
    return self.get_instance_type(self.initiator)

  def _set_error(self, err):
    self.error_message = err
    self.P(err, color='error')
    self.initialized = False
    return

  def _add_warning(self, warn):
    self.P(warn, color='warning')
    return

  def __repr__(self):
    return f"NumpySharedMemory({self._mem_name}, {self._np_bytes}, {self._np_shape}, {self._np_type})"
  
  def startup(self):
    super().startup()
    self.error_message = self._init_mem()
    if self.error_message is None:
      self.initialized = True
      self.P(f"Shared memory '{self._mem_name}'[{self.instance_type}] successfully initialized")
    # endif initialized
    return
  
  
  def _init_mem(self):
    self.P("Initializing shmem '{}' using {}".format(self._mem_name, self.shm_type))
    if self.uses_mp:
      msg = self._mp_open()
    else:
      msg = self._posix_ipc_open()
    return msg
      
  
  def P(self, s, **kwargs):
    super().P(s, prefix=True, **kwargs)
    return

  def _posix_ipc_open(self):
    try:
      import posix_ipc 
      import mmap

      def initialize_posix_shared_memory(
          flags, mode, mem_name, mem_size, np_shape, np_type
      ):
        self.P("Initializing ({} - {}) posix_ipc SharedMemory {}".format(
          flags, mode, mem_name))

        shm = posix_ipc.SharedMemory(
          name=mem_name,
          # posix_ipc.O_RDWR is not available in 1.0.4 version O_RDWR=2 O_CREAT=512, CRRW=514
          flags=flags,
          size=int(mem_size),
        )
        # mmap
        self.P(f"Initializing mmap on fd: {shm.fd}, size: {shm.size}")
        mmap_obj = mmap.mmap(shm.fd, shm.size)
        # memoryview of mmap
        mmap_ptr = memoryview(mmap_obj)
        # after mmap we are done with the fd
        shm.close_fd()
        # finally the numpy
        self.P("Initializing shmem ndarray")
        np_mem = np.ndarray(
          shape=np_shape,
          dtype=np_type,
          buffer=mmap_ptr,
        )
        self.P("Done initializing shmem.")

        return shm, mmap_obj, np_mem

      # open shm
      _O_RDWR = 2
      _O_CREAT = 64
      flags = _O_RDWR if not self.initiator else _O_CREAT | _O_RDWR
      mode = "creating" if self.initiator else "open r/w"
      mem_init_kwargs = {
        'flags': flags,
        'mode': mode,
        'mem_name': self._mem_name,
        'mem_size': self._np_bytes,
        'np_shape': self._np_shape,
        'np_type': self._np_type,
      }
      self._shm, self._mmap_obj, self._np_mem = initialize_posix_shared_memory(**mem_init_kwargs)
      if self.is_buffer:
        # additional memory for buffer mode
        # This will be used to store the availability of memory and the shape of the numpy array
        mem_init_kwargs['mem_name'] = f'{self._mem_name}_additional'
        mem_init_kwargs['np_shape'] = (self.maxlen, 1 + self.shape_offset)
        mem_init_kwargs['np_type'] = np.int32
        mem_init_kwargs['mem_size'] = self.maxlen * (1 + self.shape_offset) * np.dtype(np.int32).itemsize
        self._shm_additional, self._mmap_obj_additional, self._np_mem_additional = initialize_posix_shared_memory(**mem_init_kwargs)
      # endif buffer mode
      error_msg = None
    except:
      full_err = traceback.format_exc()
      error_msg =  "ERROR in posix_ipc ShareMemory('{}') open: {}\n{}".format(
        self._mem_name,
        sys.exc_info()[0], full_err,
      )
      self._set_error(error_msg)
      self._create_notification(
         notif='EXCEPTION',
         msg=error_msg,
      )
      # fake numpy buffer
      self._np_mem = np.zeros(
        shape=self._np_shape,
        dtype=self._np_type,
      )
      if self.is_buffer:
        self._np_mem_additional = np.zeros(
          shape=(self.maxlen, 1 + self.shape_offset),
          dtype=np.int32,
        )
    return error_msg
  
  def _mp_open(self):
    try:
      from multiprocessing import shared_memory

      def initialize_mp_shared_memory(
          create, mem_name, mem_size, np_shape, np_type
      ):
        try:
          # Attempt to create the shared memory object.
          shm = shared_memory.SharedMemory(
            create=create,
            name=mem_name,
            size=int(mem_size),
          )
        except Exception as exc:
          # If the shared memory object already exists, attempt to open it.
          # If the shared memory object does not exist, create it.
          initial_instance_type = self.get_instance_type(create)
          current_instance_type = self.get_instance_type(not create)
          self.P(f"Failed to create shared memory '{mem_name}'[{initial_instance_type}]: {exc}\n"
                 f"Attempting to open shared memory '{mem_name}'[{current_instance_type}]")
          create = not create
          shm = shared_memory.SharedMemory(
            create=create,
            name=mem_name,
            size=int(mem_size),
          )
        # endtry create shared memory
        # Create a numpy array from the shared memory object.
        # The numpy array will be used to read and write data to the shared memory object.
        np_mem = np.ndarray(
          np_shape,
          dtype=np_type,
          buffer=shm.buf,
        )
        return shm, np_mem, create

      mem_init_kwargs = {
        'create': self.initiator,
        'mem_name': self._mem_name,
        'mem_size': self._np_bytes,
        'np_shape': self._np_shape,
        'np_type': self._np_type,
      }
      self._shm, self._np_mem, self.initiator = initialize_mp_shared_memory(**mem_init_kwargs)
      if self.is_buffer:
        # additional memory for buffer mode
        # This will be used to store the availability of memory and the shape of the numpy array
        mem_init_kwargs['mem_name'] = f'{self._mem_name}_additional'
        mem_init_kwargs['np_shape'] = (self.maxlen, 1 + self.shape_offset)
        mem_init_kwargs['np_type'] = np.int32
        mem_init_kwargs['mem_size'] = int(self.maxlen * (1 + self.shape_offset) * np.dtype(np.int32).itemsize)
        mem_init_kwargs['create'] = self.initiator
        self._shm_additional, self._np_mem_additional, _ = initialize_mp_shared_memory(**mem_init_kwargs)
      # endif buffer mode
      error_msg = None
    except:
      full_err = traceback.format_exc()
      error_msg =  "ERROR in multiprocessing ShareMemory('{}') open: {}\n{}".format(
        self._mem_name,
        sys.exc_info()[0], full_err,
      )
      self._set_error(error_msg)
      self._create_notification(
         notif='EXCEPTION',
         msg=error_msg,
      )
      # fake numpy buffer
      self._np_mem = np.zeros(
        shape=self._np_shape,
        dtype=self._np_type,        
      )
      if self.is_buffer:
        self._np_mem_additional = np.zeros(
          shape=(self.maxlen, 1 + self.shape_offset),
          dtype=np.int32,
        )
      # endif buffer mode
      
    return error_msg

  def get_position(self, position):
    """
    Returns a valid position of the shared memory buffer.
    Parameters
    ----------
    position : int or list or tuple or None

    Returns
    -------
    list - array of integers with the position
    """
    if position is None:
      return []
    if isinstance(position, (int, np.integer)):
      position = [position]
    if len(position) > len(self._np_shape):
      position = position[:len(self._np_shape)]
    nr_pos = min(len(position), len(self._np_shape))
    for i in range(nr_pos):
      if position[i] >= self._np_shape[i]:
        position[i] = self._np_shape[i] - 1
    return position

  def read(self, position=None):
    pos = self.get_position(position)
    if self.initialized:
      return self._np_mem[tuple(pos)].copy()
    return None
  
  def write(self, val, position=None):
    if not self.initialized:
      return False
    pos = self.get_position(position)
    curr_mem = self._np_mem[tuple(pos)]
    if isinstance(val, np.ndarray):
      if curr_mem.shape != val.shape:
        section_str = f" [{pos}]" if len(pos) > 0 else ""
        err_msg = f"Shape mismatch for input {val.shape} vs numpy shmem{section_str} {curr_mem.shape}"
        self._set_error(err_msg)
        return False
      curr_mem[:] = val[:]
    elif isinstance(val, (int, float, np.integer)):
      curr_mem[:] = val
    else:
      self._set_error("Uknown data type {} for NumpySharedMemory".format(
        type(val)
      ))
      return False
    return True

  def first_buffer_position(self):
    if not self.initialized:
      return None
    if self.is_buffer:
      for i in range(self.maxlen):
        if self._np_mem_additional[i, 0] == 0:
          self._np_mem_additional[i, 0] = 1
          return i
    return None

  def check_data(self, data):
    """
    Checks if the data is a valid for the shared memory buffer.
    This is used only in buffer mode.
    Parameters
    ----------
    data : any

    Returns
    -------
    True if the data is valid, False otherwise.
    """
    valid = True
    if not isinstance(data, np.ndarray):
      self._add_warning("Data is not a numpy array")
      valid = False
    if np.prod(data.shape) > self._max_element_size:
      self._add_warning("Data of shape {} is larger than the maximum size {}".format(
        data.shape, self._max_element_size
      ))
      valid = False
    if len(data.shape) > self.shape_offset:
      self._add_warning("Data shape {} has more than {} dimensions".format(
        data.shape, self.shape_offset
      ))
      valid = False
    return valid

  def add_data_to_buffer(self, data: np.ndarray):
    """
    Adds data to the buffer memory zone.
    Parameters
    ----------
    data : np.ndarray of maximum self.shape_offset dimensions

    Returns
    -------
    res : int or None - the position in the buffer or None if:
     - the buffer is full
     - the buffer is not initialized
     - the data is not valid
    """
    if not self.check_data(data):
      return None
    insert_pos = self.first_buffer_position()
    if insert_pos is not None:
      self._np_mem_additional[insert_pos, 1:1 + len(data.shape)][:] = data.shape
      reshaped_data = data.reshape(-1)
      self._np_mem[insert_pos, :len(reshaped_data)][:] = reshaped_data[:]
      return insert_pos
    return None

  def read_buffer_data(self, position, pid=None):
    """
    Reads data from the buffer memory zone.
    Parameters
    ----------
    position : int - the position in the buffer
    pid : int - the process id of the process requesting the data

    Returns
    -------
    data : np.ndarray or None - the data from the buffer or None if:
      - the buffer is not initialized
      - the position is invalid
      - the data is not available
    """
    position = int(position)
    if -1 < position < self.maxlen and self.initialized:
      if self._np_mem_additional[position, 0] == 1:
        shape = self._np_mem_additional[position, 1:1 + self.shape_offset]
        shape = shape[shape > 0]
        data = self._np_mem[position, :np.prod(shape)].reshape(shape)
        self._np_mem_additional[position, :] = 0
        return data
      # endif data is available
    # endif position is valid and memory initialized
    return None

  def shutdown(self):
    self.P(f"Shutdown NumpySharedMemory {self._mem_name}[{self.instance_type}]...")
    if self._mmap_obj is not None:
      self._mmap_obj.close()
      if self.is_buffer:
        self._mmap_obj_additional.close()
    if self._shm is not None:
      del self._np_mem
      self._shm.close()
      if self.initiator:
        self._shm.unlink()
      if self.is_buffer:
        del self._np_mem_additional
        self._shm_additional.close()
        if self.initiator:
          self._shm_additional.unlink()
    return


"""SHM CLASS END"""


class NumpySharedMemoryPlaceholder:
  def __init__(self, shm_idx):
    self.__shm_idx = shm_idx
    return

  def get_shm_idx(self):
    return self.__shm_idx


def ndarray_to_shm(data: np.ndarray, shm: NumpySharedMemory, debug: bool = False) -> NumpySharedMemoryPlaceholder:
  """
  Helper function to replace a numpy ndarray with a shared memory object.
  Parameters
  ----------
  data - np.ndarray - the numpy array to be replaced
  shm - NumpySharedMemory - the shared memory object to be used
  debug - bool - if True, the function will print debug information

  Returns
  -------
  res - NumpySharedMemoryPlaceholder - the placeholder object
  """
  if debug:
    shm.log.P(f"Writing data of shape {data.shape if isinstance(data, np.ndarray) else None} to shared memory.")
  idx = shm.add_data_to_buffer(data)
  if debug:
    shm.log.P(f"Data written to shared memory index {idx}")
  return NumpySharedMemoryPlaceholder(idx)


def replace_ndarray_with_shm(data: any, shm: NumpySharedMemory, debug: bool = False) -> any:
  """
  Replaces all numpy ndarrays from data with shared memory placeholders.
  Parameters
  ----------
  data - any - the data to be processed
  shm - NumpySharedMemory or None - the shared memory object to be used or None if no replacement is needed
  debug - bool - if True, the function will print debug information

  Returns
  -------
  res - any - the data with all numpy ndarrays replaced with shared memory placeholders or left unchanged
  """
  if shm is None:
    return data
  if isinstance(data, list):
    res = [None] * len(data)
    for i in range(len(data)):
      if isinstance(data[i], np.ndarray):
        res[i] = ndarray_to_shm(data[i], shm, debug)
      else:
        res[i] = replace_ndarray_with_shm(data[i], shm, debug)
    # endfor data
  elif isinstance(data, dict):
    res = {}
    for k in data:
      if isinstance(data[k], np.ndarray):
        res[k] = ndarray_to_shm(data[k], shm, debug)
      else:
        res[k] = replace_ndarray_with_shm(data[k], shm, debug)
    # endfor data
  elif isinstance(data, np.ndarray):
    res = ndarray_to_shm(data, shm, debug)
  else:
    res = data
  return res


def shm_to_ndarray(data: NumpySharedMemoryPlaceholder, shm: NumpySharedMemory, debug: bool = False) -> np.ndarray:
  """
  Helper function to replace a shared memory object with a numpy ndarray.
  Parameters
  ----------
  data - NumpySharedMemoryPlaceholder - the placeholder object to be replaced
  shm - NumpySharedMemory - the shared memory object to be used
  debug - bool - if True, the function will print debug information

  Returns
  -------
  res - np.ndarray - the numpy array
  """
  shm_idx = data.get_shm_idx()
  if debug:
    shm.log.P(f"Reading data from shared memory index {shm_idx}")
  res = shm.read_buffer_data(shm_idx) if shm_idx is not None else None
  if debug:
    shm.log.P(f"Read data of shape {res.shape if isinstance(res, np.ndarray) else None} from shared memory index {shm_idx}.")
  return res


def replace_shm_with_ndarray(data: any, shm: NumpySharedMemory, debug: bool = False) -> any:
  """
  Replaces all shared memory placeholders from data with numpy ndarrays.
  Parameters
  ----------
  data - any - the data to be processed
  shm - NumpySharedMemory or None - the shared memory object to be used or None if no replacement is needed
  debug - bool - if True, the function will print debug information

  Returns
  -------
  res - any - the data with all shared memory placeholders replaced with numpy ndarrays or left unchanged
  """
  if shm is None:
    return data
  if isinstance(data, list):
    res = [None] * len(data)
    for i in range(len(data)):
      if isinstance(data[i], NumpySharedMemoryPlaceholder):
        res[i] = shm_to_ndarray(data[i], shm, debug)
      else:
        res[i] = replace_shm_with_ndarray(data[i], shm, debug)
    # endfor data
  elif isinstance(data, dict):
    res = {}
    for k in data:
      if isinstance(data[k], NumpySharedMemoryPlaceholder):
        res[k] = shm_to_ndarray(data[k], shm, debug)
      else:
        res[k] = replace_shm_with_ndarray(data[k], shm, debug)
    # endfor data
  elif isinstance(data, NumpySharedMemoryPlaceholder):
    res = shm_to_ndarray(data, shm, debug)
  else:
    res = data
  return res


if __name__ == '__main__':
  RECREATE = False

  from time import sleep
  def test_shm_release(release_method='del'):
    from time import time
    from multiprocessing import shared_memory
    curr_ts = time()
    shm_kwargs = {
      'name': f'test_shm_{str(curr_ts)}',
      'size': 100,
    }
    shm1 = shared_memory.SharedMemory(**shm_kwargs, create=True)
    print(f'{shm1} created')

    kwargs2 = {**shm_kwargs, 'size': 0}
    shm2 = shared_memory.SharedMemory(**kwargs2, create=False)
    print(f'{shm2} created')

    print('Attempting to release the memory...')
    if release_method == 'del':
      del shm1
    elif release_method == 'unlink':
      shm1.unlink()
      shm1.close()
    elif release_method == 'close':
      shm1.close()
    elif release_method == 'buffrelease':
      shm1.buf.release()
    else:
      print(f'Unknown release method {release_method}')
      return

    print(f'Shared memory deleted using {release_method}')
    # print('Attempting freeing the memory...')

    if RECREATE:
      try:
        shm3 = shared_memory.SharedMemory(**shm_kwargs, create=True)
        print(f'{shm3} created')
      except:
        print('Failed to recreate the memory')
    else:
      shm2.close()
      # if release_method == 'del':
      #   del shm2
      # elif release_method == 'unlink':
      #   shm2.unlink()
      # elif release_method == 'close':
      #   shm2.close()
      # elif release_method == 'buffrelease':
      #   shm2.buf.release()
      # else:
      #   print(f'Unknown release method {release_method}')
      #   return
      print('Shared memory reference deleted')
      print('Attempting recreating the memory...')
      try:
        shm3 = shared_memory.SharedMemory(**shm_kwargs, create=True)
        print(f'{shm3} created')
        del shm3
      except Exception as exc:
        print(f'Failed to recreate the memory:\n\t{exc}')
        sleep(30)
    return

  def full_test_shm_release():
    for close_method in ['del', 'unlink', 'close', 'buffrelease']:
      print(f'###Starting test for {close_method}...####')
      test_shm_release(close_method)
      print(f'###Test for {close_method} done###########')
    while True:
      sleep(1000)
    return

  full_test_shm_release()
  exit(0)

  from naeural_core import Logger
  l = Logger('SHMT', base_folder='.', app_folder='_cache')
  mem_name = 'test1'
  shm1 = NumpySharedMemory(
    mem_name=mem_name, 
    mem_size=100, 
    np_shape=(10,10), 
    np_type=np.uint8, 
    log=l,
    create=True,
  )
  
  if shm1.initialized:
    shm1.write(2)
    
    shm2 = NumpySharedMemory(
      mem_name=mem_name, 
      mem_size=100, 
      np_shape=(10,10), 
      np_type=np.uint8, 
      log=l,
      create=False,
    )
    
    res = shm2.read()
    
    print(res)
    
    shm1.shutdown()
    shm2.shutdown()
    
  
  

    
    