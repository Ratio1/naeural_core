# -*- coding: utf-8 -*-
import abc
import json
from collections import deque

from naeural_core import constants as ct
from naeural_core.data.base.base_plugin_dct import DataCaptureThread
from naeural_core.io_formatters.io_formatter_manager import IOFormatterManager
from naeural_core.comm import AMQPWrapper, MQTTWrapper

_CONFIG = {
  **DataCaptureThread.CONFIG,

  'CAP_RESOLUTION'  : 50, # overwrite default cap resolution - we should iterate faster on IoT data
  'LIVE_FEED'       : False,  
  
  'MAX_IDLE_TIME'   : 60,  
  
  'DEBUG_IOT_PAYLOADS' : False,

  'RECONNECTABLE': True,
  'ONE_AT_A_TIME': False,
  'ADAPTIVE_STREAM_WINDOW': False,
  'MIN_STREAM_WINDOW': 1,
  'MAX_STREAM_WINDOW': 1,
  'STREAM_WINDOW_STEP': 1,


  "HOST": '#DEFAULT',
  "PORT": '#DEFAULT',
  "USER": '#DEFAULT',
  "PASS": '#DEFAULT',
  "QOS": '#DEFAULT',
  "TOPIC": "#DEFAULT",
  "SECURED": "#DEFAULT",
  "PROTOCOL": "#DEFAULT",
  
  "MESSAGE_FILTER": {},
  "PATH_FILTER": [None, None, None, None],
  "FILTER_BY_DESTINATION": False,
  "DISABLE_ADDRESSED_PAYLOAD_SUBS": False,

  "URL": None,
  "STREAM_CONFIG_METADATA": {
    "HOST": '#DEFAULT',
    "PORT": '#DEFAULT',
    "USER": '#DEFAULT',
    "PASS": '#DEFAULT',
    "QOS": '#DEFAULT',
    "TOPIC": "#DEFAULT",
    "SECURED": "#DEFAULT",
    "PROTOCOL": "#DEFAULT",
  },

  'VALIDATION_RULES': {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
    "FILTER_BY_DESTINATION": {
      "DESCRIPTION": "When true, drop payloads not addressed to this node (missing destination is treated as broadcast).",
      "TYPE": "bool",
    },
    "ADAPTIVE_STREAM_WINDOW": {
      "DESCRIPTION": "When true, dynamically adjust the per-loop message batch within the configured min/max bounds.",
      "TYPE": "bool",
    },
    "MIN_STREAM_WINDOW": {
      "DESCRIPTION": "Lower bound used when ADAPTIVE_STREAM_WINDOW is enabled.",
      "TYPE": "int",
      "MIN_VAL": 1,
      "MAX_VAL": 2048,
    },
    "MAX_STREAM_WINDOW": {
      "DESCRIPTION": "Upper bound used when ADAPTIVE_STREAM_WINDOW is enabled.",
      "TYPE": "int",
      "MIN_VAL": 1,
      "MAX_VAL": 2048,
    },
    "STREAM_WINDOW_STEP": {
      "DESCRIPTION": "Step used when the adaptive stream window grows or shrinks.",
      "TYPE": "int",
      "MIN_VAL": 1,
      "MAX_VAL": 64,
    },
    "DISABLE_ADDRESSED_PAYLOAD_SUBS": {
      "DESCRIPTION": "When true, subscribe only to the broadcast payload topic and skip addressed payload topics.",
      "TYPE": "bool",
    },
  },
}


class BaseIoTQueueListenerDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(BaseIoTQueueListenerDataCapture, self).__init__(**kwargs)
    self.message_queue = deque(maxlen=1000)
    self.connected = False
    self.subscribed = False
    self._adaptive_stream_window = None
    self._io_formatter_manager: IOFormatterManager = None
    return

  @property
  def _conn_type(self):
    """Property that defines the type of connection to create. Possible types: mqtt, amqp

    Returns:
        str: one of the following: 'mqtt', 'amqp'
    """
    ret = self.shmem['config_communication']['TYPE']
    if 'PROTOCOL' in self.cfg_stream_config_metadata and self.cfg_stream_config_metadata['PROTOCOL'] != '#DEFAULT':
      ret = self.cfg_stream_config_metadata['PROTOCOL'].lower()
    return ret

  def check_debug_logging_enabled(self):
    return super(BaseIoTQueueListenerDataCapture, self).check_debug_logging_enabled() or self.cfg_debug_iot_payloads


  def __get_stream_config_metadata_property(self, property):
    """Get the specific connection property from STREAM_CONFIG_METADATA. If it is not explicitly defined, consider the default value.
    The default value is the one specified in the communication layer.

    Args:
        property (str): the field representing a communication parameter

    Returns:
        str/int: the specified value defined by the user or the default value
    """
    params = self.shmem['config_communication']['PARAMS']
    ret = params.get(property)
    if self.config.get(property) != '#DEFAULT':
      ret = self.config.get(property)
    elif property in self.cfg_stream_config_metadata and self.cfg_stream_config_metadata.get(property) != '#DEFAULT':
      ret = self.cfg_stream_config_metadata.get(property)

    return ret

  def __disable_addressed_payload_subs(self):
    """
    Determine whether addressed payload subscriptions should be disabled.

    Returns
    -------
    bool
      `True` when addressed payload topics should be skipped and only the
      broadcast payload topic should be subscribed.
    """
    value = self.cfg_disable_addressed_payload_subs
    if not value:
      value = self.os_environ.get("EE_DISABLE_ADDRESSED_PAYLOAD_SUBS", "")
    if isinstance(value, str):
      return value.strip().upper() in ["1", "TRUE", "YES"]
    return bool(value)

  def __get_custom_channel_config(self):
    """
    Build the payload channel configuration used by the wrapper instance.

    Returns
    -------
    dict
      Payload channel configuration after applying stream-level overrides and
      the addressed-subscription rollout flag.
    """
    params = self.shmem['config_communication']['PARAMS']
    channel_cfg = dict(params['PAYLOADS_CHANNEL'])
    if 'TOPIC' in self.cfg_stream_config_metadata and self.cfg_stream_config_metadata.get('TOPIC') != '#DEFAULT':
      channel_cfg['TOPIC'] = self.cfg_stream_config_metadata.get('TOPIC')
    if 'TARGETED_TOPIC' in self.cfg_stream_config_metadata and self.cfg_stream_config_metadata.get('TARGETED_TOPIC') != '#DEFAULT':
      channel_cfg['TARGETED_TOPIC'] = self.cfg_stream_config_metadata.get('TARGETED_TOPIC')
    if self.__disable_addressed_payload_subs():
      channel_cfg.pop('TARGETED_TOPIC', None)
    return channel_cfg

  def _init(self):
    """
    Initialize the wrapper used by the IoT queue listener.

    Notes
    -----
    The listener reuses the shared communication parameters, injects both
    `EE_ID` and `EE_ADDR`, and can optionally disable addressed payload topic
    subscriptions during rollout or rollback.
    """
    # use the parameters from the comm layer as default for this connection, if unspecified
    params = self.shmem['config_communication']['PARAMS']
    self._io_formatter_manager = self.shmem['io_formatter_manager']

    # build the config dict with all connection paramteres required by wrapper server
    self._comm_config = {
      ct.COMMS.EE_ID: params.get(ct.COMMS.EE_ID, None),
      ct.COMMS.EE_ADDR: self.bc.address,
      "DISABLE_ADDRESSED_PAYLOAD_SUBS": self.__disable_addressed_payload_subs(),
      "CUSTOM_CHANNEL": self.__get_custom_channel_config(),
      ct.COMMS.HOST: self.__get_stream_config_metadata_property(ct.COMMS.HOST),
      ct.COMMS.PORT: self.__get_stream_config_metadata_property(ct.COMMS.PORT),
      ct.COMMS.USER: self.__get_stream_config_metadata_property(ct.COMMS.USER),
      ct.COMMS.PASS: self.__get_stream_config_metadata_property(ct.COMMS.PASS),
      ct.COMMS.QOS: self.__get_stream_config_metadata_property(ct.COMMS.QOS),
      ct.COMMS.SECURED: self.__get_stream_config_metadata_property(ct.COMMS.SECURED),
    }
    
    self.P("IoT DCT connection config:\n{}".format(self.json_dumps(self._comm_config, indent=2)))

    # build the kwargs of the wrapper server
    # TODO: maybe add low-level filtering of messages in the pub-sub wrapper
    #       this would allow for a more efficient message handling and no unwanted messages
    #       in the DCT message queue
    wrapper_kwargs = dict(
      log=self.log,
      config=self._comm_config,
      recv_channel_name="CUSTOM_CHANNEL",
      recv_buff=self.message_queue,
      connection_name=''.join([self._device_id, '_IoT_Listener_', self.cfg_name]),
    )

    # define the wrapper server
    if self._conn_type == 'mqtt':
      self.wrapper_server = MQTTWrapper(**wrapper_kwargs)
    elif self._conn_type == 'amqp':
      self.wrapper_server = AMQPWrapper(**wrapper_kwargs)
    else:
      raise ValueError("Cannot understand reduce controller type: {}".format(self.wrapper_server))

    self._maybe_reconnect_to_controller_server()
    return

  def _release(self):
    dct_ret = self.wrapper_server.release()
    for msg in dct_ret['msgs']:
      self.P(msg)
    self.connected = False
    self.subscribed = False
    del self.wrapper_server
    return

  def _maybe_reconnect_to_controller_server(self):
    """Connect to the server and send a notification with the result of the attempt.
    """
    if self.wrapper_server.connection is None or not self.connected:
      self.P("Trying to connect to the pub-sub server...")
      self.subscribed = False
      dct_ret = self.wrapper_server.server_connect()
      self.connected = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      self.P("IoT DCT status post reconnect:\n{}".format(self.json_dumps(msg, indent=2)))
      self._create_notification(
        notif=msg_type,
        msg=msg
      )
    return

  def _maybe_reconnect(self):
    self._maybe_reconnect_to_controller_server()
    if not self.subscribed:
      self.P("Trying to subscribe to the pub-sub server...")
      if self._conn_type == 'amqp':
        dct_ret = self.wrapper_server.establish_one_way_connection('recv')
      elif self._conn_type == 'mqtt':
        dct_ret = self.wrapper_server.subscribe()
      else:
        dct_ret = None
      # endif

      self.P("IoT DCT status post subscribe:\n{}".format(self.json_dumps(dct_ret, indent=2)))
      self.subscribed = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      self._create_notification(
        notif=msg_type,
        msg="IoTDCT Status:" + msg
      )
    # endif

    return

  def _maybe_fill_message_queue(self):
    """Call the receive method associated with the controller server, which can fill the buffer with messages

    Raises:
        e: Exception from receiving, induces by a connection issue
    """
    try:
      self.wrapper_server.receive()
    except Exception as e:
      self.connected = False
      self.subscribed = False
      self.P(str(e), color='r')
      raise e
    # end try-except
    return


  def __extract_and_process_one_message(self):
    """
    This method extracts one message from the message queue and processes it via __process_iot_message
    """
    msg = self.message_queue.popleft()
    dict_msg = json.loads(msg)
    processed_message, message_type = self.__process_iot_message(dict_msg)

    if processed_message is None:
      return
    
    _path = processed_message.get(self.ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, [None, None, None, None])
    self.Pd(f"Accepted message of type {message_type} from {_path}")
    
    if message_type == "struct_data":
      self._add_struct_data_input(processed_message)
    elif message_type == "image":
      self._add_img_input(processed_message)
    else:
      self.P("Unknown message type: {}".format(message_type), color='r')
      self.P("Full message: {}".format(processed_message), color='r')
    return


  def _extract_and_process_messages(self, nr_messages=1):
    """ This method extracts and processes a number of messages from the message queue"""
    for _ in range(nr_messages):
      self.__extract_and_process_one_message()
    return


  def _run_data_aquisition_step(self):
    """
    Drain one batch of queued pub-sub messages into the DCT output deque.

    Notes
    -----
    The batch size is either fixed by ``STREAM_WINDOW`` or adjusted dynamically
    through the adaptive stream-window controls. This keeps the MQTT ingress
    loop independent from the node main-loop cadence while still bounding the
    amount of work done in each capture-thread iteration.
    """
    if len(self.message_queue) == 0:
      return

    nr_messages = min(len(self.message_queue), self.__get_effective_stream_window())
    self._extract_and_process_messages(nr_messages)
    return


  def __get_stream_window_bounds(self):
    """
    Return the bounded stream-window configuration used for queue draining.

    Returns
    -------
    tuple[int, int, int, int]
      Base stream window, minimum adaptive window, maximum adaptive window,
      and adjustment step.
    """
    base_window = max(int(self.cfg_stream_window), 1)
    min_window = max(int(self.cfg_min_stream_window), 1)
    max_window = max(int(self.cfg_max_stream_window), 1)
    step = max(int(self.cfg_stream_window_step), 1)
    min_window = min(min_window, max_window)
    if base_window < min_window:
      base_window = min_window
    if base_window > max_window:
      base_window = max_window
    return base_window, min_window, max_window, step

  def __get_effective_stream_window(self):
    """
    Determine the current per-loop batch size used to drain ``message_queue``.

    Returns
    -------
    int
      Number of messages to process in the current capture iteration.

    Notes
    -----
    When adaptive batching is enabled, backlog on the ingress queue grows the
    window quickly while quiet periods shrink it gradually back toward the
    configured minimum. Output deque headroom is also considered so the listener
    does not scale up aggressively when downstream backpressure is already high.
    The scale-up check uses the configured step size as a minimum free-capacity
    requirement, so one adaptive increase only happens when the downstream DCT
    queue has enough room to absorb that larger batch cleanly.
    """
    if self.cfg_one_at_a_time:
      return 1

    base_window, min_window, max_window, step = self.__get_stream_window_bounds()
    if not self.cfg_adaptive_stream_window:
      self._adaptive_stream_window = base_window
      return base_window

    current_window = self._adaptive_stream_window or base_window
    message_queue_len = len(self.message_queue)
    out_queue_len = len(self._deque) if self._deque is not None else 0
    out_queue_maxlen = self._deque.maxlen if self._deque is not None else 0
    remaining_output_capacity = max(out_queue_maxlen - out_queue_len, 0)
    # Require enough downstream room for one adaptive growth step so we do not
    # increase the ingress batch when the DCT queue is already close to full.
    has_output_headroom = out_queue_maxlen <= 1 or remaining_output_capacity >= step

    if message_queue_len >= max(current_window * 2, min_window) and has_output_headroom:
      current_window = min(current_window + step, max_window)
    elif message_queue_len <= max(min_window // 2, 1) and out_queue_len <= 1:
      current_window = max(current_window - step, min_window)

    if current_window != self._adaptive_stream_window:
      self.Pd(
        f"Adaptive stream window set to {current_window} "
        f"(message_queue={message_queue_len}, deque={out_queue_len}/{out_queue_maxlen})"
      )
    self._adaptive_stream_window = current_window
    return current_window

  def __process_iot_message(self, msg):
    """Decode the message if it is in a format supported by an Execution Engine, and then parse and filter it to support custom logic.
    The former is useful when there are multiple Execution Engines in a network and all send messages with different formatters.

    Parameters:
    ----------
        msg (dict): The raw message received by the listener

    Returns:
    ----------    
        dict/img: The message that will be sent downstream, maybe formatted, parsed and filtered
    """
    result = msg
    message_type = "unknown"

    formatter = self._io_formatter_manager.get_required_formatter_from_payload(result)
    if formatter is not None:
      result = formatter.decode_output(result)
    else:
      # we kind of treat this case already, because the default formatter is considered the identity function
      result = msg
    # endif formatter is not None

    result = self.__filter_message(result)
    if result is not None:
      result = self._parse_message(result)
    # endif result is not None

    # TODO: maybe add support for numpy arrays as struct data
    if result is None:
      message_type = "ignored_message"
    elif isinstance(result, self.np.ndarray) or isinstance(result, self.PIL.Image.Image):
      message_type = "image"
    elif isinstance(result, dict) or isinstance(result, list) or isinstance(result, tuple) or isinstance(result, str):
      message_type = "struct_data"
    # endif decide message type

    return result, message_type
  
  
  def __filter_message_by_path(self, unfiltered_message):
    """Filter messages that get passed forward using the path filter

    Parameters:
    ----------
        unfiltered_message (dict): message received from the queue server, possibly formatted if it was in a format supported by the Execution Engine

    Returns:
    ----------
        dict: the message that satisfies certain conditions or None if it does not satisfy them
    """
    path_filter = self.cfg_path_filter
    result = unfiltered_message
    if isinstance(unfiltered_message, dict):
      path = unfiltered_message.get(self.ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, [None, None, None, None])
      path = [x.upper() if isinstance(x, str) else x for x in path]
      for i in range(4):
        _path_filter =  path_filter[i]
        if _path_filter is not None:
          if not isinstance(_path_filter, list):
            _path_filter = [_path_filter]
          _path_filter = [x.upper() if isinstance(x, str) else x for x in _path_filter]
          if path[i] not in _path_filter:
            self.Pd(f"Path filter {path_filter} dropped {path}")
            result = None
            break
    return result



  def __filter_message(self, unfiltered_message):
    """
    Filter messages that get passed forward.

    Parameters
    ----------
    unfiltered_message : dict
        Message received from the queue server, possibly formatted if it was in a
        format supported by the Execution Engine.

    Returns
    -------
    dict or None
        The message that satisfies certain conditions, or None if it does not.
    """
    result = unfiltered_message
    if result is not None and self.cfg_filter_by_destination:
      destination = None
      if isinstance(result, dict):
        destination = result.get(self.ct.PAYLOAD_DATA.EE_DESTINATION, None)
      # Empty lists will also be considered for everyone
      if destination:
        if not isinstance(destination, list):
          destination = [destination]
        destination = [x.lower() for x in destination if isinstance(x, str)]
        if self.bc.address.lower() not in destination:
          result = None
      # endif destination specified
    # endif filter by destination
    if result is not None:
      # then filter by path
      result = self.__filter_message_by_path(result)
    if result is not None:
      # then filter by message filter dict
      dct_filter = self.cfg_message_filter
      is_valid = True
      if dct_filter is not None:
        is_valid = self.dict_in_dict(dct_filter, result)
      if is_valid:
        result = self._filter_message(result)
    return result


  @abc.abstractmethod
  def _filter_message(self, unfiltered_message):
    """
    Overwrite this method to filter messages that get passed forward

    Parameters
    ----------
    unfiltered_message : dict
        message received from the queue server, possibly formatted if it was in a format supported by the Execution Engine

    Returns
    -------
    filtered_message : dict | None
        the message that satisfies certain conditions or none if it does not satisfy them
    """
    raise NotImplementedError()

  @abc.abstractmethod
  def _parse_message(self, filtered_message):
    """
    Overwrite this method to parse messages that get passed forward

    Parameters
    ----------
    filtered_message : dict
        message received from the queue server, possibly formatted if it was in a format supported by the Execution Engine

    Returns
    -------
    parsed_message : dict | tuple | str | np.ndarray | PIL.Image.Image
        The message that will be sent downstream, either as an image or as a struct data
    """
    raise NotImplementedError()

  @abc.abstractmethod
  def _parse_and_filter_message(self, message):
    """Overwrite this method to parse and filter messages that get passed forward

    Args:
        messages (dict): message received from the queue server, possibly formatted if it was in a format suported by the Execution Engine

    Returns:
        dict/img: the message that satisfies certain conditions
    """
    raise NotImplementedError()
