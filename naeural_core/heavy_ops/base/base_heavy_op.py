from naeural_core import DecentrAIObject
from naeural_core import Logger

import abc
from naeural_core import constants as ct
from time import sleep
from collections import deque
from threading import Thread

class BaseHeavyOp(DecentrAIObject):
  """Base class for asynchronous or synchronous heavy operations.

  The class converts payloads into heavy-operation dictionaries via
  `_register_payload_operation()` and then either queues or executes the
  resulting work item. `None` is reserved for the intentional no-work
  sentinel. Internal registration failures use a distinct sentinel so the
  no-work contract remains separable from error handling.
  """

  _REGISTER_FAILED_SENTINEL = object()

  def __init__(self, log: Logger, shmem, config, comm_async=True, **kwargs):
    self._thread = None
    self.shmem = shmem
    self.comm_async = comm_async
    # number large enough to keep all the ops, but also to ensure that memory overflow is not allowed
    self._ops = deque(maxlen=10000)
    self._config = config or {}
    self.heavy_op_count = 0
    super(BaseHeavyOp, self).__init__(log=log, **kwargs)
    return
  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.COMM
    super().P(s, prefix=True, color=color, **kwargs)
    return 

  def startup(self):
    super().startup()
    self.P("Initiating Heavy Ops process `{}` ".format(
      self.__class__.__name__), color='m'
    )
    self.config_data = self._config
    if self.comm_async:
      self.P("  Starting thread for `{}`".format(
        self.__class__.__name__), color='m'
      )
      self._thread = Thread(
        target=self._run_thread, 
        args=(), 
        name=ct.THREADS_PREFIX + 'hvy_' + self.__class__.__name__, 
        daemon=True,
      )
      self._thread.daemon = True
      self._thread.start()
    #endif
    return

  @property
  def cfg_idle_thread_sleep_time(self):
    return self._config.get('IDLE_THREAD_SLEEP_TIME', 1)

  @property
  def _eeid(self):
    return self.log.config_data[ct.CONFIG_STARTUP_v2.K_EE_ID][:ct.EE_ALIAS_MAX_SIZE]

  @abc.abstractmethod
  def _process_dct_operation(self, dct):
    raise NotImplementedError()

  @abc.abstractmethod
  def _register_payload_operation(self, payload):
    raise NotImplementedError()

  @staticmethod
  def __err_dict(payload, err_type, err_file, err_func, err_line, err_msg):
    return {
      'ERR_TYPE'    : err_type,
      'ERR_MSG'     : err_msg,
      'ERR_FILE'    : err_file,
      'ERR_FUNC'    : err_func,
      'ERR_LINE'    : err_line,
      'STREAM'      : payload.get('STREAM', None),
      'SIGNATURE'   : payload.get('SIGNATURE', None),
      'INSTANCE_ID' : payload.get('INSTANCE_ID', None)
    }

  def _run_thread(self):
    # thread loop for async heavy ops
    while True:
      processed = False
      if len(self._ops) > 0:
        dct = self._ops.popleft()
        self.process_dct_operation(dct)
        processed = True
      #endif

      if processed:
        sleep(0.01)
      else:
        sleep(self.cfg_idle_thread_sleep_time)
    #endwhile

  def process_dct_operation(self, dct):
    try:
      self._process_dct_operation(dct)
    except Exception as e:
      err_dict = self.__err_dict(dct, *self.log.get_error_info(return_err_val=True))
      self._create_notification(
        notif='EXCEPTION',
        msg='Error in heavy payload operation {}\n{}'.format(self.__class__.__name__, err_dict)
      )
    return

  def register_payload_operation(self, payload):
    """Register a payload and isolate registration failures.

    Parameters
    ----------
    payload : dict
      Incoming payload dictionary to transform into a heavy-op work item.

    Returns
    -------
    object or None
      The registered work dictionary when work should proceed, ``None`` when
      subclasses intentionally signal no work, or an internal failure
      sentinel when registration raised and notification handling already ran.
    """
    dct = None
    try:
      dct = self._register_payload_operation(payload)
    except Exception as e:
      err_dict = self.__err_dict(payload, *self.log.get_error_info(return_err_val=True))
      self._create_notification(
        notif='EXCEPTION',
        msg='Error in heavy payload operation {}\n{}'.format(self.__class__.__name__, err_dict)
      )
      # Keep the failure path distinguishable from the intentional no-work
      # sentinel. `process_payload()` treats both as non-work, but callers can
      # still reason about the outcome without overloading `None`.
      return self._REGISTER_FAILED_SENTINEL
    return dct

  def process_payload(self, payload):
    """Register and execute one heavy-op payload.

    Parameters
    ----------
    payload : dict
      Incoming payload dictionary that subclasses may inspect or normalize
      before deciding whether any heavy-op work is required.

    Returns
    -------
    None
      The method performs side effects only. When registration returns
      `None`, the payload is treated as an explicit no-work sentinel and is
      neither queued nor counted. When registration fails, the internal
      failure sentinel is also returned early without queueing or counting.
    """
    dct = self.register_payload_operation(payload)
    if dct is None:
      # Subclasses use `None` to mean that the payload should be ignored
      # entirely. Keep that sentinel out of the async queue and out of the
      # execution counter so no-op registrations stay invisible to metrics.
      return
    if dct is self._REGISTER_FAILED_SENTINEL:
      # Registration already emitted the error notification and returned a
      # distinct sentinel. Keep that failure path separate from intentional
      # no-work so the code remains readable and future-safe.
      return

    if self.comm_async:
      # only add to ops queue of the async thread
      self._ops.append(dct)
    else:
      # directly process the payload and eventually modify it inplace if it was 
      # correctly returned by the above `register_payload_operation`
      self.process_dct_operation(dct)
    #endif
    self.heavy_op_count += 1
    return
