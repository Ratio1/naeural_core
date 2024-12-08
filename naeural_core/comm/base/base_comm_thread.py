import abc
import json
import numpy as np
import uuid

from time import time


from collections import deque
from naeural_core import DecentrAIObject
from naeural_core import Logger
from threading import Thread

from naeural_core.bc import DefaultBlockEngine
from naeural_core.main.net_mon import NetworkMonitor
from naeural_core.utils.shm_manager import SharedMemoryManager
from naeural_core.utils.plugins_base.plugin_base_utils import _UtilsBaseMixin
from naeural_core import constants as ct

from naeural_core.comm.mixins import (
  _CommunicationTelemetryMixin,
  _DefaultCommMixin,
  _CommandControlCommMixin,
  _NotificationsCommMixin,
  _HeartbeatsCommMixin,
)

from naeural_core.local_libraries import _ConfigHandlerMixin


RUN_ON_THREAD = True
MAX_MESSAGE_LEN = 1024 * 1024 * 2  # 2MB

_CONFIG = {

  'LOG_SEND_COMMANDS': True,
  
  'ENCRYPTED_COMMS': False,       # if True, all comms are end-to-end encrypted

  'DEBUG_LOG_PAYLOADS': False,
  'DEBUG_LOG_PAYLOADS_PIPELINES': [],
  'DEBUG_LOG_PAYLOADS_SIGNATURES': [],
  'DEBUG_LOG_PAYLOADS_SAVE_FILTER': None,
  'DEBUG_LOG_PAYLOADS_REVALIDATE' : False,

  'DEBUG_COMM_ERRORS': False,

  'VALIDATION_RULES': {

  }
}


class BaseCommThread(
  DecentrAIObject,
  _CommunicationTelemetryMixin,
  _DefaultCommMixin,
  _CommandControlCommMixin,
  _NotificationsCommMixin,
  _HeartbeatsCommMixin,
  _ConfigHandlerMixin,
  _UtilsBaseMixin,
):

  __metaclass__ = abc.ABCMeta
  CONFIG = _CONFIG

  def __init__(self,
               log: Logger, shmem, signature,
               comm_type,
               default_config,
               upstream_config,
               environment_variables=None,
               send_channel_name=None,
               recv_channel_name=None,
               timers_section='comms',
               loop_resolution=50,
               **kwargs
               ):
    self._signature = signature
    self._comm_type = comm_type
    self._config = None
    self._default_config = default_config
    self._upstream_config = upstream_config
    self._timers_section = timers_section
    self._environment_variables = environment_variables or {}
    self.__name__ = Logger.name_abbreviation(self._signature.lower()) + \
        '][' + Logger.name_abbreviation(self._comm_type.lower())

    self.shmem = shmem
    self.shm_manager = SharedMemoryManager(
      dct_shared=shmem,
      stream='DEFAULT',
      plugin=comm_type,
      instance=signature,
      category=ct.SHMEM.COMM,
      linked_instances=None,
      log=log,
    )

    self.has_server_conn = False  # public flag for SERVER connection
    self.has_send_conn = False  # public flag for SEND connection
    self.has_recv_conn = False  # public flag for RECV connection
    self._send_to = None
    self._send_buff = deque(maxlen=ct.COMM_SEND_BUFFER)
    self._send_channel_name = send_channel_name
    self._recv_buff = deque(maxlen=ct.COMM_RECV_BUFFER)
    self._recv_channel_name = recv_channel_name
    
    self.__deque_received_hashes = deque(maxlen=1000)
    
    self._nr_conn_retry_iters = 0
    self._total_conn_fails = 0
    self._error_messages = []
    self._error_times = []
    self._last_activity = None

    self._formatter = None
    self._formatter_name = None
    self._heavy_ops_manager = None
    self._network_monitor: NetworkMonitor = None

    self._msg_id = 0
    self.loop_timings = deque(maxlen=10)

    self._incoming_lens = deque(maxlen=100)
    self._incoming_times = deque(maxlen=100)
    self._outgoing_lens = deque(maxlen=100)
    self._outgoing_times = deque(maxlen=100)
    self._bandwidth_mutex = False

    self._stop = False
    self._thread_stopped = False
    self._thread_logs = deque()
    self._thread = None

    self.loop_resolution = loop_resolution
    super(BaseCommThread, self).__init__(log=log, **kwargs)
    return

  def startup(self):
    super().startup()
    self.P("{} Starting comm '{}' {}".format('=' * 10, self._comm_type, '=' * 10))
    if self._environment_variables is not None and len(self._environment_variables) > 0:
      self.P("Updating config with node environment config....", color='m')
      self._config = self._merge_prepare_config(
        delta_config=self._environment_variables
      )
    self._config = self._merge_prepare_config(
      default_config=self._config
    )

    self.setup_config_and_validate(self._config)

    self._heavy_ops_manager = self.shmem['heavy_ops_manager']
    self._network_monitor: NetworkMonitor = self.shmem['network_monitor']
    self._io_formatter_manager = self.shmem['io_formatter_manager']
    self._formatter, self._formatter_name = self._io_formatter_manager.get_formatter()
    if self._formatter_name is not None:
      self._formatter_name = self._formatter_name.lower()
    return

  @property
  def bc_engine(self) -> DefaultBlockEngine: 
    return self.shmem[ct.BLOCKCHAIN_MANAGER]

  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color, str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.COMM
    super().P(s, prefix=True, color=color, **kwargs)
    return

  @property
  def last_activity_time(self):
    return self._last_activity

  @property
  def server_address(self):
    return self._get_server_address()

  @abc.abstractmethod
  def _init(self):
    # do stuff at thread start
    raise NotImplementedError()

  @abc.abstractmethod
  def _maybe_reconnect_send(self):
    # reconnect SEND connection if required
    raise NotImplementedError()

  @abc.abstractmethod
  def _maybe_reconnect_recv(self):
    # reconnect RECV connection if required
    raise NotImplementedError()

  @abc.abstractmethod
  def _send(self, data):
    # use SEND connection to send data
    raise NotImplementedError()

  @abc.abstractmethod
  def _maybe_fill_recv_buffer(self):
    # get message, decode it and append to self._recv_buffer
    raise NotImplementedError()

  @abc.abstractmethod
  def _release(self):
    # do thread cleanup stuff
    raise NotImplementedError()

  @property
  def cfg_max_retry_iters(self):
    return self._config.get(ct.CONN_MAX_RETRY_ITERS, 3)

  @property
  def comm_failed_after_retries(self):
    if self._nr_conn_retry_iters > self.cfg_max_retry_iters:
      return True
    return False

  def _process_signature(self, signature, payload):
    # add some signature processing if needed.
    # BC related stuff should happen in the BC engine
    return

  def __sign(self, data):
    if self.bc_engine is not None:
      self.start_timer('bc_sign')
      # if inplace=True, it will modify the original data and SAVE deepcopy time
      prepared_data = self.log.replace_nan(data, inplace=True)
      # TODO: should serialize sets
      # prepared_data = self.log.serialize_sets(prepared_data)
      signature = self.bc_engine.sign(prepared_data, add_data=True, use_digest=True)
      self._process_signature(signature=signature, payload=prepared_data)
      self.end_timer('bc_sign')      
      # self.P("DEBUG:\n{}".format(self.log.safe_dumps_json(data, indent=4)))            
    else:
      prepared_data = data
    return prepared_data

  def add_incoming(self, size):
    if self._bandwidth_mutex:
      return
    self._incoming_lens.append(size)
    self._incoming_times.append(time())
    return

  def add_outgoing(self, size):
    if self._bandwidth_mutex:
      return
    self._outgoing_lens.append(size)
    self._outgoing_times.append(time())
    return

  def get_incoming_bandwidth(self):
    result = 0
    self._bandwidth_mutex = True
    if len(self._incoming_lens) > 2:
      interval = self._incoming_times[-1] - self._incoming_times[0]
      result = round(np.sum(self._incoming_lens) / interval / 1024, 2)
    self._bandwidth_mutex = False
    return result

  def get_outgoing_bandwidth(self):
    result = 0
    self._bandwidth_mutex = True
    if len(self._outgoing_lens) > 2:
      interval = self._outgoing_times[-1] - self._outgoing_times[0]
      result = round(np.sum(self._outgoing_lens) / interval / 1024, 2)
    self._bandwidth_mutex = False
    return result

  def _get_server_address(self):
    return

  def _get_errors(self):
    return [], []

  def get_error_report(self):
    errors, times = self._get_errors()
    errors = errors + self._error_messages
    times = times + self._error_times
    return errors, times

  def payload_debugger(self, jsonpayload):
    """This method works in conjunction with the `DEBUG_LOG_PAYLOADS` config flag as follows:
      - if `DEBUG_LOG_PAYLOADS` is False, this method is not called
      - if `DEBUG_LOG_PAYLOADS_PIPELINES` has values, we only show payloads from those pipelines
      - if `DEBUG_LOG_PAYLOADS_SIGNATURES` has values, we only show payloads from those signatures


    Parameters
    ----------
    jsonpayload : dict
        the payload that leaves the execution engine.
    """
    pipelines = [x.upper() for x in self.cfg_debug_log_payloads_pipelines]  # constraint pipelines to upper
    signatures = [x.upper() for x in self.cfg_debug_log_payloads_signatures]  # constrain signatures to upper
    
    # payload filter save section
    str_filter = self.cfg_debug_log_payloads_save_filter            
    data = json.loads(jsonpayload)
    if isinstance(str_filter, str) and str_filter in jsonpayload:
      self._save_raw_payload(jsonpayload, prefix='')
    #endif save filter
    # end payload filter save section
    
    show = True
    try:    
      has_action = data.get(ct.COMMS.COMM_SEND_MESSAGE.K_ACTION, None) is not None
      if has_action:
        # this is a SEND command message
        pass
      else:
        pipe = data[ct.PAYLOAD_DATA.EE_PAYLOAD_PATH][1]
        sign = data[ct.PAYLOAD_DATA.EE_PAYLOAD_PATH][2]
        pipe = pipe or ""
        sign = sign or ""
        if len(pipelines) > 0:  # if we have constraint pipelines, we only show those
          show = pipe.upper() in pipelines
        if len(signatures) > 0:  # if we have constraint signatures, we only show those
          show = sign.upper() in signatures
        if show:
          self.P("{}: {}".format(
            len(jsonpayload),
            data[ct.PAYLOAD_DATA.EE_PAYLOAD_PATH])
          )
        #endif show
      #endif send command vs payload
    except:
      self.P("Failed debug: {}".format(data), color='error')
    return
  
  def _check_send_message(self, message):
    """
    This method is called before sending the message to the network.

    Parameters
    ----------
    message : str
        message to be validated.
    """
    
    if len(message) > MAX_MESSAGE_LEN:
      return False
    return True
    
    

  def send_wrapper(self, data):
    """
    This is the "final exit" function that gets called when the only thing left is to
    json-ify the message and send it to the network with the custom `_send`

    Parameters
    ----------
    data : dict (usually) although could be a list
      the data dictionary.

    Returns
    -------
    is_ok : int
      below 0 if error or the len of the message otherwise.

    """
    is_ok = 0
    try:
      # next step will add the signature, hash, addr and also cleanup the payload
      signed_data = self.__sign(data)
      # transform to json
      message = self._jsonify(signed_data)
      
      # now use custom send
      if self.cfg_debug_log_payloads_revalidate:
        # debug area for revalidation
        msg_id = signed_data.get(ct.PAYLOAD_DATA.EE_MESSAGE_ID)
        if msg_id is None:
          str_init = signed_data.get(ct.PAYLOAD_DATA.INITIATOR_ID, "UnkSender")
          str_id = signed_data[ct.PAYLOAD_DATA.EE_ID]
          str_action = signed_data.get("ACTION", "Unknown")
          msg_id = f"{str_init}:{str_action}:{str_id}"
        result = self.bc_engine.verify(signed_data)
        if not result.valid:
          self.P("Failed revalidation on dict for {}: {}".format(msg_id, result.message), color='error')
          self._save_raw_payload(signed_data, prefix='_err-d-', pickle=True)
        #endif valid 
        json_data = json.loads(message)
        result = self.bc_engine.verify(json_data)
        if not result.valid:
          self.P("Failed revalidation on json for {}: {}".format(msg_id, result.message), color='error')
          self._save_raw_payload(message, prefix='_err-j-')
        #endif not valid         
      #endif debug area for revalidate
      
      # now check the message size
      if self._check_send_message(message): 
        self._send(message)
        is_ok = len(message)
        if is_ok > 0 and self.cfg_debug_log_payloads:
          self.payload_debugger(message)
        self.add_outgoing(is_ok)
        self._last_activity = time()
      else:
        self.P("Message size {:,.1f} KB was dropped for pipeline {}".format(
          len(message) / 1024, signed_data.get(ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, "<Unknown path>")
          ), color='r'
        )
        is_ok = 1 # we return 1 to signal that the message was dropped and should not be preserved
    except Exception as exc:
      self.has_send_conn = False
      msg = "`send_wrapper` error on payload {} -> {}".format(
        data.get(ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, "<Unknown path>"), exc
      )
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        displayed=True,
        autocomplete_info=True
      )
    return is_ok

  def maybe_fill_recv_buffer_wrapper(self):
    try:
      self._maybe_fill_recv_buffer()
    except Exception as e:
      self.has_recv_conn = False
      msg = "maybe_fill_recv_buffer_wrapper err: {}".format(e)
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
    return

  def _jsonify(self, data):
    """
    From data to json. 
    """
    # no need to `replace_nan=True` as the payload is supposed to be already cleaned up
    message = self.log.safe_dumps_json(data, replace_nan=False, ensure_ascii=False)
    return message

  def start(self):
    run_thread_func = None
    if self._comm_type in [ct.COMMS.COMMUNICATION_DEFAULT, "L_" + ct.COMMS.COMMUNICATION_DEFAULT]:
      run_thread_func = self._run_thread_default
    elif self._comm_type in [ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL, "L_" + ct.COMMS.COMMUNICATION_COMMAND_AND_CONTROL]:
      run_thread_func = self._run_thread_command_and_control
    elif self._comm_type in [ct.COMMS.COMMUNICATION_HEARTBEATS, "L_" + ct.COMMS.COMMUNICATION_HEARTBEATS]:
      run_thread_func = self._run_thread_heartbeats
    elif self._comm_type in [ct.COMMS.COMMUNICATION_NOTIFICATIONS, "L_" + ct.COMMS.COMMUNICATION_NOTIFICATIONS]:
      run_thread_func = self._run_thread_notifications

    if RUN_ON_THREAD:
      self._thread = Thread(
        target=run_thread_func,
        args=(),
        name=ct.THREADS_PREFIX + 'comm' + run_thread_func.__name__.replace('_run_thread', ''),
        daemon=True,
      )
      self._thread.daemon = True
      self._thread.start()
    else:
      self.P("Warning! Communication Thread does not run on thread", color='e')
      run_thread_func()
    return

  def stop(self):
    self._stop = True
    self._thread.join()
    self._send_buff.clear()
    self._recv_buff.clear()
    self.P('Received `stop` command. Cleaned everything.')
    return

  def get_logs(self):
    logs = []
    while len(self._thread_logs) > 0:
      msg, color = self._thread_logs.popleft()
      logs.append((msg, color))
    return logs
  
  def already_received(self, data):
    payload_hash = data.get(ct.PAYLOAD_DATA.EE_HASH, None)
    initiator_id = data.get(ct.PAYLOAD_DATA.INITIATOR_ID, None)
    initiator_addr = data.get(ct.PAYLOAD_DATA.EE_SENDER, None)
    action = data.get(ct.COMMS.COMM_SEND_MESSAGE.K_ACTION, None)
    already_received = False    
    if payload_hash is not None:
      already_received = payload_hash in self.__deque_received_hashes
      if not already_received:
        self.__deque_received_hashes.append(payload_hash)
      else:
        self.P("Received from {}:{} duplicate '{}' payload with hash: {}".format(
          initiator_id, initiator_addr, action, payload_hash), color='error'
        )
    return already_received
    

  def get_message(self):
    json_msg = None
    if len(self._recv_buff) > 0:
      str_msg = self._recv_buff.popleft()
      incoming_len = len(str_msg)
      self.add_incoming(incoming_len)
      try:
        json_msg = json.loads(str_msg)
        self._last_activity = time()
      except:
        self.P("Cannot decode received message '{}'".format(str_msg), color='r')
        json_msg = None
      #endif json_msg
    #endif len
    if json_msg is not None and self.already_received(json_msg):
      json_msg = None
    return json_msg

  def get_messages(self):
    l = []
    while True:
      msg = self.get_message()
      if msg is None:
        break
      l.append(msg)
    return l

  def get_message_count(self):
    return self._msg_id

  def send(self, data):
    self._msg_id += 1
    msg_id = self._msg_id
    now = self.log.now_str(nice_print=True, short=False)
    self._send_buff.append((msg_id, data, now))
    return

  def _save_raw_payload(self, msg, prefix='', pickle=False):
    try:
      if pickle:
        data = msg
      else:
        data = json.loads(msg)
      msg_id = data[ct.PAYLOAD_DATA.EE_MESSAGE_ID]
      folder = self.log.get_data_subfolder('payloads', force_create=True)
      ext = 'pkl' if pickle else 'txt'
      fn = self.os_path.join(folder, f'{prefix}{msg_id}.{ext}')
      if pickle:
        import pickle
        with open(fn, 'wb') as fhandle:
          pickle.dump(msg, fhandle, protocol=pickle.HIGHEST_PROTOCOL)
      else:
        with open(fn, 'w') as f:
          f.write(msg)
      self.P(f"  Saved payload {msg_id} to {fn}")
    except Exception as exc:
      self.P("Failed raw payload save {}: {}".format(exc, msg), color='r')
    return

  def _save_formatted_payload(self, event_type, signature, formatted_msg):
    fn = '{}_{}_{}.txt'.format(self.log.now_str(), event_type, signature)
    self.log.save_output_json(
      data_json=formatted_msg,
      fname=fn,
      subfolder_path='payloads',
      verbose=False
    )
    return

  def _prepare_message(self, msg, msg_id):
    """
    Final dictionary preparation. This is called in `_run_thread_xxx` together with `send_wrapper`

    Parameters
    ----------
    msg : dict
      the upstream payload dict.
    msg_id : TBD
      TBD.

    Returns
    -------
    msg : dict
      final 'ready-to-json-ify' dict.
      
      
    TODO: review & optimize this method

    """
    WARNING_TIME = 0.1
    dct_original = self.deepcopy(msg) # just to cleanup any possible references still forgotten in the payload
    
    ee_encrypted_payload = dct_original.get(
      ct.PAYLOAD_DATA.EE_IS_ENCRYPTED.lower(), 
      dct_original.get(ct.PAYLOAD_DATA.EE_IS_ENCRYPTED.upper())
    )
    
    ee_encrypted_payload = bool(ee_encrypted_payload) or self.cfg_encrypted_comms
    destination_addr = dct_original.get(ct.PAYLOAD_DATA.EE_DESTINATION)
    destination_id = dct_original.get(ct.PAYLOAD_DATA.EE_DESTINATION_ID) # only for logging atm
    
    ee_id = dct_original.get(ct.EE_ID, None)
    session_id = dct_original.get(ct.PAYLOAD_DATA.SESSION_ID, None)
    
    initiator_id = dct_original.get(ct.PAYLOAD_DATA.INITIATOR_ID, None)
    initiator_addr = dct_original.get(ct.PAYLOAD_DATA.INITIATOR_ADDR, None)
    
    modified_by_id = dct_original.get(ct.PAYLOAD_DATA.MODIFIED_BY_ID, None)
    modified_by_addr = dct_original.get(ct.PAYLOAD_DATA.MODIFIED_BY_ADDR, None)
    
    stream_name = dct_original.get(ct.PAYLOAD_DATA.STREAM_NAME, None)
    signature = dct_original.get(ct.PAYLOAD_DATA.SIGNATURE, None)
    instance_id = dct_original.get(ct.PAYLOAD_DATA.INSTANCE_ID, None)
    payload_path = [ee_id, stream_name, signature, instance_id]
    sequence_number = msg_id  # sequence number for each communicator
    
    # TODO: delete below
    message_id = str(uuid.uuid4())  # unieuq payload ID
    
    ee_timestamp = self.log.now_str(nice_print=True, short=False)
    ee_timezone = self.log.utc_offset
    ee_tz = self.log.timezone
    ee_version = dct_original.get(ct.PAYLOAD_DATA.EE_VERSION, None)
    ee_event_type = dct_original.get(ct.PAYLOAD_DATA.EE_EVENT_TYPE, None)

    # now lets re-create the payload
    dct_outgoing = {
      ct.PAYLOAD_DATA.EE_TIMESTAMP: ee_timestamp,
      ct.PAYLOAD_DATA.EE_TIMEZONE: ee_timezone,
      ct.PAYLOAD_DATA.EE_TZ: ee_tz,
      ct.PAYLOAD_DATA.EE_MESSAGE_SEQ: sequence_number, # TODO: delete
      ct.PAYLOAD_DATA.EE_MESSAGE_ID: message_id,  # TODO: delete (left here for backward compatibility)
      ct.PAYLOAD_DATA.EE_TOTAL_MESSAGES: self.get_message_count(),
      **dct_original
    }

    formatter_name = None
    formatter = None

    if ct.PAYLOAD_DATA.EE_FORMATTER in dct_outgoing:
      # the user defined a custom formatter
      formatter_name = dct_outgoing[ct.PAYLOAD_DATA.EE_FORMATTER]
      formatter = self._io_formatter_manager.get_formatter_by_name(formatter_name)

    elif self._formatter is not None:
      formatter_name = self._formatter_name
      formatter = self._formatter

    if formatter is not None:
      max_elapsed = WARNING_TIME
      dct_outgoing, elapsed = formatter.encode_output(dct_outgoing)
      if elapsed >= max_elapsed:
        self.P(
          "Warning! Formatter time above {} for {}: {:.3f}s".format(
            max_elapsed,
            payload_path, elapsed),
          color='r'
        )
      # endif log too-much-time in formatter
    # endif we have formatter

    dct_outgoing[ct.PAYLOAD_DATA.EE_FORMATTER] = formatter_name    
    dct_output = {}
    
    if destination_addr is None:
      destination_addr, destination_id = None, None
      if initiator_addr is not None:
        destination_addr = initiator_addr
        destination_id = initiator_id
      else:
        destination_addr = modified_by_addr
        destination_id = modified_by_id
    #endif destination    
    if ee_encrypted_payload and destination_addr is not None:
      # encrypt the payload
      str_data = self.log.safe_json_dumps(dct_outgoing, ensure_ascii=False)
      str_enc_data = self.bc_engine.encrypt(
        plaintext=str_data,
        receiver_address=destination_addr, # this can be a address or a list of addresses
      )
      dct_output[ct.PAYLOAD_DATA.EE_ENCRYPTED_DATA] = str_enc_data
      dct_output[ct.PAYLOAD_DATA.EE_IS_ENCRYPTED] = True
      dct_output[ct.PAYLOAD_DATA.EE_DESTINATION] = destination_addr
      if self.cfg_debug_log_payloads:
        self.P("Encrypted {} for '{}' <{}>".format(
          payload_path, destination_id, destination_addr))
    else:
      # just copy data
      dct_output = {
        **dct_outgoing,
        **dct_output,
      }
      dct_output[ct.PAYLOAD_DATA.EE_IS_ENCRYPTED] = False
      if ee_encrypted_payload:
        dct_output[ct.PAYLOAD_DATA.EE_ENCRYPTED_DATA] = "ERROR: No receiver address found!"
    # endif encrypted payload

           
    dct_output[ct.PAYLOAD_DATA.EE_PAYLOAD_PATH] = payload_path    
    
    dct_output[ct.PAYLOAD_DATA.EE_VERSION] = ee_version
    dct_output[ct.PAYLOAD_DATA.EE_EVENT_TYPE] = ee_event_type
    dct_output[ct.PAYLOAD_DATA.SESSION_ID] = session_id
    
    dct_output[ct.PAYLOAD_DATA.INITIATOR_ID] = initiator_id
    dct_output[ct.PAYLOAD_DATA.INITIATOR_ADDR] = initiator_addr
    
    dct_output[ct.PAYLOAD_DATA.MODIFIED_BY_ID] = modified_by_id
    dct_output[ct.PAYLOAD_DATA.MODIFIED_BY_ADDR] = modified_by_addr
    
    dct_output[ct.PAYLOAD_DATA.EE_TIMESTAMP] = ee_timestamp
    dct_output[ct.PAYLOAD_DATA.EE_TIMEZONE] = ee_timezone
    dct_output[ct.PAYLOAD_DATA.EE_TZ] = ee_tz

    ## TODO: DELETE BELOW !
    ### START BACKWARD COMPATIBLE ###
    dct_output[ct.PAYLOAD_DATA.SB_IMPLEMENTATION] = formatter_name
    dct_output[ct.PAYLOAD_DATA.EE_MESSAGE_ID] = message_id # this must be changed to hash after BC is fully operational
    ### END BACKWARD COMPATIBLE ###
    
    # next the EE_HASH, EE_SIGN and EE_SENDER will be added by the BC engine
    
    return dct_output

  def _prepare_command(self, command, receiver_address=None):
    try:
      critical_data = {
        ct.COMMS.COMM_SEND_MESSAGE.K_ACTION: command.pop(ct.COMMS.COMM_SEND_MESSAGE.K_ACTION),
        ct.COMMS.COMM_SEND_MESSAGE.K_PAYLOAD: command.pop(ct.COMMS.COMM_SEND_MESSAGE.K_PAYLOAD),
      }
    except Exception as exc:
      self.P("Failed to prepare command {}:\n{}".format(exc, self.json_dumps(command, indent=3)), color='r')
      return None

    encrypt_payload = self.cfg_encrypted_comms

    if encrypt_payload and receiver_address is not None:
      # encrypt the payload
      str_data = self.log.safe_json_dumps(critical_data, ensure_ascii=False)
      str_enc_data = self.bc_engine.encrypt(
        plaintext=str_data,
        receiver_address=receiver_address,
        #compressed=True, # default is True
        #embed_compressed=True, # default is True - data will be 13:end instead of 12:end due to compression flag
      )
      critical_data = {
        ct.COMMS.COMM_SEND_MESSAGE.K_EE_IS_ENCRYPTED: True,
        ct.COMMS.COMM_SEND_MESSAGE.K_EE_ENCRYPTED_DATA: str_enc_data,
      }
    else:
      critical_data[ct.COMMS.COMM_SEND_MESSAGE.K_EE_IS_ENCRYPTED] = False
      if encrypt_payload:
        critical_data[ct.COMMS.COMM_SEND_MESSAGE.K_EE_ENCRYPTED_DATA] = "ERROR: No receiver address found!"
    # endif encrypted payload

    dct_outgoing = {
      **command,

      ct.COMMS.COMM_SEND_MESSAGE.K_SENDER_ADDR: self.bc_engine.address,

      **critical_data,
    }

    return dct_outgoing
