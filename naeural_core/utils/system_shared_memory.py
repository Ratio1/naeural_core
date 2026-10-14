"""
TODO:
  - must find locking methods for free position searching
  - change additional to np.int64 to allow for PID of child process instead of 1 when marking a position as occupied
  - use pid in read_buffer_data to check if the data is requested by the right process
"""

import numpy as np
import traceback

from naeural_core import DecentrAIObject
from naeural_core.utils.shm_diagnostics import array_observation

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
    Exclusively create or attach the assigned kernel-level shared-memory names.
    A failed acquisition leaves initialized=False and exposes error_message and
    error_type; it never changes the requested role or allocates private storage.
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
    self.error_type = None
    self.cleanup_errors = []
    self.cleanup_errors_dropped = 0
    self.initialized = False
    self.version = __VER__
    self._mem_name = mem_name
    self._np_shape = np_shape
    self._init_np_shape = np_shape
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
    # Ownership follows individual successful creations, never the requested role
    # alone: a control collision must not unlink someone else's allocation.
    self._mmap_obj = None
    self._mmap_obj_additional = None
    self._shm = None
    self._shm_additional = None
    self._owns_shm = False
    self._owns_shm_additional = False
    self._fd_open = False
    self._fd_open_additional = False
    self._np_mem = None
    self._np_mem_additional = None
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

  def _set_error(self, err, error_type=None):
    """Expose an initialization/use failure without changing the requested role.

    Parameters
    ----------
    err : str
      Existing human-readable failure contract.
    error_type : str or None
      Exception class name for allocation retry classification.
    """
    self.error_message = err
    self.error_type = error_type
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
      try:
        self.P(f"Shared memory '{self._mem_name}'[{self.instance_type}] successfully initialized")
      except BaseException:
        # The constructor has not yet returned ownership to its caller. A log
        # failure at this point still has to roll back the acquired allocation.
        try:
          self.shutdown()
        except BaseException:
          pass
        raise
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
    """Acquire the exact assigned POSIX names without changing roles.

    Returns
    -------
    str or None
      Initialization error, or None after successful acquisition.
    """
    return self._open_memory(use_mp=False)

  def _mp_open(self):
    """Acquire the exact assigned multiprocessing names without role fallback.

    Returns
    -------
    str or None
      Initialization error, or None after successful acquisition.
    """
    return self._open_memory(use_mp=True)

  def _open_memory(self, use_mp):
    """Acquire data/control as one transaction, then expose NumPy views.

    Parameters
    ----------
    use_mp : bool
      Select multiprocessing.SharedMemory or the retained posix_ipc backend.

    Returns
    -------
    str or None
      Failure text after owned-resource rollback, or None on success.

    Notes
    -----
    Attach accepts larger externally produced segments, but never smaller ones.
    Only newly created control metadata is initialized; image pages are untouched.
    """
    segments = [("", self._mem_name, int(self._np_bytes), self._np_shape, self._np_type)]
    if self.is_buffer:
      shape = (self.maxlen, 1 + self.shape_offset)
      size = int(self.maxlen * (1 + self.shape_offset) * np.dtype(np.int32).itemsize)
      segments.append(("_additional", f"{self._mem_name}_additional", size, shape, np.int32))
    try:
      if use_mp:
        from multiprocessing import shared_memory
      else:
        import posix_ipc
        import mmap
      # Save every returned handle immediately. Subsequent acquisition, mapping,
      # size validation or ndarray failures can then clean the whole transaction.
      for suffix, name, size, shape, dtype in segments:
        if use_mp:
          handle = shared_memory.SharedMemory(name=name, create=self.initiator, size=size)
        else:
          flags = posix_ipc.O_CREX if self.initiator else 0
          try:
            handle = posix_ipc.SharedMemory(name=name, flags=flags, size=size if self.initiator else 0)
          except posix_ipc.ExistentialError as exc:
            error_class = FileExistsError if self.initiator else FileNotFoundError
            raise error_class(str(exc)) from exc
        setattr(self, "_shm" + suffix, handle)
        setattr(self, "_owns_shm" + suffix, bool(self.initiator))
        if not use_mp:
          setattr(self, "_fd_open" + suffix, True)
        if handle.size < size:
          raise ValueError(f"Shared memory '{name}' has {handle.size} bytes; requires at least {size}")
      for suffix, name, size, shape, dtype in segments:
        handle = getattr(self, "_shm" + suffix)
        if use_mp:
          buffer = handle.buf
        else:
          buffer = mmap.mmap(handle.fd, handle.size)
          setattr(self, "_mmap_obj" + suffix, buffer)
          handle.close_fd()
          setattr(self, "_fd_open" + suffix, False)
        setattr(self, "_np_mem" + suffix, np.ndarray(shape, dtype=dtype, buffer=buffer))
      if self.is_buffer and self._owns_shm_additional:
        self._np_mem_additional[:] = 0
      return None
    except BaseException as exc:
      error_type = type(exc).__name__
      error_msg = "ERROR in {} ShareMemory('{}') open: {}\n{}".format(
        "multiprocessing" if use_mp else "posix_ipc", self._mem_name,
        error_type, traceback.format_exc(),
      )
      # Drop any local exported buffer before closing its backing mapping.
      buffer = None
      # Publish the original failure before cleanup/reporting can itself fail.
      self.error_message = error_msg
      self.error_type = error_type
      self.initialized = False
      try:
        self.shutdown()
      except BaseException:
        # Cleanup has already attempted both resources and retained its scalar
        # errors. A reporting exception must not replace acquisition failure.
        pass
      if not isinstance(exc, Exception):
        # Interrupts still roll back owned objects, but keep process-control
        # semantics instead of becoming an ordinary failed-initialization object.
        raise
      # Ordinary reporting failures must preserve failed initialization and its
      # original classification, so the caller can select its transport fallback.
      try:
        self._set_error(error_msg, error_type=error_type)
      except Exception:
        pass
      try:
        self._create_notification(notif='EXCEPTION', msg=error_msg)
      except Exception:
        pass
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
      self._add_warning("Data of shape {} is larger than the maximum size {}. init_np_shape: {}".format(
        data.shape, self._max_element_size, self._init_np_shape, 
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
    """Close both resources independently and unlink only owned creations.

    Returns
    -------
    None
      Cleanup failures remain in ``cleanup_errors`` as bounded scalar records.

    Raises
    ------
    BaseException
      The first process-control exception is re-raised after all cleanup attempts.

    Notes
    -----
    Failed closes remain retryable. Successful unlink clears ownership so repeat
    shutdown cannot delete a later foreign allocation with the same fixed name.
    """
    self.initialized = False
    self._np_mem = None
    self._np_mem_additional = None
    new_errors = []
    control_error = None
    for suffix in ("", "_additional"):
      name = self._mem_name + suffix
      handle = getattr(self, "_shm" + suffix)
      mapping = getattr(self, "_mmap_obj" + suffix)
      operations = []
      if mapping is not None:
        operations.append(("close_mapping", mapping.close, "_mmap_obj" + suffix, None))
      if handle is not None:
        if self.uses_mp:
          operations.append(("close", handle.close, None, None))
        elif getattr(self, "_fd_open" + suffix):
          operations.append(("close_fd", handle.close_fd, "_fd_open" + suffix, False))
        if getattr(self, "_owns_shm" + suffix):
          operations.append(("unlink", handle.unlink, "_owns_shm" + suffix, False))
      close_failed = False
      for operation, action, attr, value in operations:
        try:
          action()
        except BaseException as exc:
          if not isinstance(exc, Exception) and control_error is None:
            control_error = exc
          missing = isinstance(exc, FileNotFoundError)
          if operation == "unlink" and not self.uses_mp:
            import posix_ipc
            missing = missing or isinstance(exc, posix_ipc.ExistentialError)
          if operation == "unlink" and missing:
            # Another owner/tracker may already have removed the object. Do not
            # retain permission to unlink a new allocation reusing this name.
            setattr(self, "_owns_shm" + suffix, False)
            continue
          if operation != "unlink":
            close_failed = True
          new_errors.append({"segment": name, "operation": operation, "exception_type": type(exc).__name__})
        else:
          if attr is not None:
            setattr(self, attr, value)
      if not close_failed and not getattr(self, "_owns_shm" + suffix):
        setattr(self, "_shm" + suffix, None)
    # Do not let a reporting failure prevent either segment's cleanup attempt.
    for error in new_errors:
      if len(self.cleanup_errors) == 32:
        self.cleanup_errors.pop(0)
        self.cleanup_errors_dropped += 1
      self.cleanup_errors.append(error)
    try:
      if new_errors:
        self.P(f"Shared memory cleanup failures: {new_errors}", color='error')
    finally:
      # A second interrupt or reporting failure must not replace the first
      # process-control exception or skip cleanup of the sibling resource.
      if control_error is not None:
        raise control_error
    return


"""SHM CLASS END"""


class NumpySharedMemoryPlaceholder:
  def __init__(self, shm_idx):
    self.__shm_idx = shm_idx
    return

  def get_shm_idx(self):
    return self.__shm_idx


def ndarray_to_shm(data: np.ndarray, shm: NumpySharedMemory, debug: bool = False, trace=None) -> NumpySharedMemoryPlaceholder:
  """
  Helper function to replace a numpy ndarray with a shared memory object.
  Parameters
  ----------
  data - np.ndarray - the numpy array to be replaced
  shm - NumpySharedMemory - the shared memory object to be used
  debug - bool - if True, the function will print debug information
  trace : dict or None
      Optional request-local scalar counters; no data or slot mutation.

  Returns
  -------
  res - NumpySharedMemoryPlaceholder - the placeholder object
  """
  if debug:
    shm.log.P(f"Writing data of shape {data.shape if isinstance(data, np.ndarray) else None} to shared memory.")
  idx = shm.add_data_to_buffer(data)
  if trace is not None:
    try:
      reason = None
      if idx is None:
        if data.size > shm._max_element_size:
          reason = "array_too_large"
        elif data.ndim > shm.shape_offset:
          reason = "rank_too_large"
        elif not shm.initialized:
          reason = "uninitialized"
        elif not shm.is_buffer:
          reason = "not_buffer"
        else:
          reason = "no_free_slot"
      array_observation(trace, data, idx, reason)
    except Exception:
      trace["observation_error"] = True
  if debug:
    shm.log.P(f"Data written to shared memory index {idx}")
  return NumpySharedMemoryPlaceholder(idx)


def replace_ndarray_with_shm(data: any, shm: NumpySharedMemory, debug: bool = False, trace=None) -> any:
  """
  Replaces all numpy ndarrays from data with shared memory placeholders.
  Parameters
  ----------
  data - any - the data to be processed
  shm - NumpySharedMemory or None - the shared memory object to be used or None if no replacement is needed
  debug - bool - if True, the function will print debug information
  trace : dict or None
      Optional request-local scalar counters; no data or slot mutation.

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
        res[i] = ndarray_to_shm(data[i], shm, debug, trace=trace)
      else:
        res[i] = replace_ndarray_with_shm(data[i], shm, debug, trace=trace)
    # endfor data
  elif isinstance(data, dict):
    res = {}
    for k in data:
      if isinstance(data[k], np.ndarray):
        res[k] = ndarray_to_shm(data[k], shm, debug, trace=trace)
      else:
        res[k] = replace_ndarray_with_shm(data[k], shm, debug, trace=trace)
    # endfor data
  elif isinstance(data, np.ndarray):
    res = ndarray_to_shm(data, shm, debug, trace=trace)
  else:
    res = data
  return res


def shm_to_ndarray(data: NumpySharedMemoryPlaceholder, shm: NumpySharedMemory, debug: bool = False, trace=None) -> np.ndarray:
  """
  Helper function to replace a shared memory object with a numpy ndarray.
  Parameters
  ----------
  data - NumpySharedMemoryPlaceholder - the placeholder object to be replaced
  shm - NumpySharedMemory - the shared memory object to be used
  debug - bool - if True, the function will print debug information
  trace : dict or None
      Optional request-local scalar counters; no data or slot mutation.

  Returns
  -------
  res - np.ndarray - the numpy array
  """
  shm_idx = data.get_shm_idx()
  if debug:
    shm.log.P(f"Reading data from shared memory index {shm_idx}")
  res = shm.read_buffer_data(shm_idx) if shm_idx is not None else None
  if trace is not None:
    try:
      # read_buffer_data already coerces built-in strings/floats. Classify that
      # same index, without invoking a custom object's __int__ a second time.
      observed_idx = int(shm_idx) if type(shm_idx) in (str, float) else shm_idx
      reason = None
      if res is None:
        if shm_idx is None:
          reason = "unadmitted_placeholder"
        elif not shm.initialized:
          reason = "uninitialized"
        elif isinstance(observed_idx, (int, np.integer)) and not 0 <= observed_idx < shm.maxlen:
          reason = "invalid_slot"
        else:
          reason = "slot_unavailable"
      array_observation(trace, res, observed_idx, reason, reading=True)
    except Exception:
      trace["observation_error"] = True
  if debug:
    shm.log.P(f"Read data of shape {res.shape if isinstance(res, np.ndarray) else None} from shared memory index {shm_idx}.")
  return res


def replace_shm_with_ndarray(data: any, shm: NumpySharedMemory, debug: bool = False, trace=None) -> any:
  """
  Replaces all shared memory placeholders from data with numpy ndarrays.
  Parameters
  ----------
  data - any - the data to be processed
  shm - NumpySharedMemory or None - the shared memory object to be used or None if no replacement is needed
  debug - bool - if True, the function will print debug information
  trace : dict or None
      Optional request-local scalar counters; no data or slot mutation.

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
        res[i] = shm_to_ndarray(data[i], shm, debug, trace=trace)
      else:
        res[i] = replace_shm_with_ndarray(data[i], shm, debug, trace=trace)
    # endfor data
  elif isinstance(data, dict):
    res = {}
    for k in data:
      if isinstance(data[k], NumpySharedMemoryPlaceholder):
        res[k] = shm_to_ndarray(data[k], shm, debug, trace=trace)
      else:
        res[k] = replace_shm_with_ndarray(data[k], shm, debug, trace=trace)
    # endfor data
  elif isinstance(data, NumpySharedMemoryPlaceholder):
    res = shm_to_ndarray(data, shm, debug, trace=trace)
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
    
  
  

    
    