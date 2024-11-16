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

  'RECONNECTABLE': True,
  'ONE_AT_A_TIME': False,


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
  },
}


class BaseIoTQueueListenerDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(BaseIoTQueueListenerDataCapture, self).__init__(**kwargs)
    self.message_queue = deque(maxlen=1000)
    self.connected = False
    self.subscribed = False
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

  def __get_stream_config_metadata_property(self, property):
    """Get the specific connection property from STREAM_CONFIG_METADATA. If it is not explicitly defined, consider the default value.
    The default value is the one specified in the communication layer.

    Args:
        property (str): the field representing a communication parameter

    Returns:
        str/int: the specified value defined by the user or the default value
    """
    params = self.shmem['config_communication']['PARAMS']
    ret = params[property]
    if self.config.get(property) != '#DEFAULT':
      ret = self.config.get(property)
    elif property in self.cfg_stream_config_metadata and self.cfg_stream_config_metadata.get(property) != '#DEFAULT':
      ret = self.cfg_stream_config_metadata.get(property)

    return ret

  def __get_topic(self):
    params = self.shmem['config_communication']['PARAMS']
    ret = params['PAYLOADS_CHANNEL']['TOPIC']
    if 'TOPIC' in self.cfg_stream_config_metadata and self.cfg_stream_config_metadata.get('TOPIC') != '#DEFAULT':
      ret = self.cfg_stream_config_metadata.get('TOPIC')
    return ret

  def _init(self):
    # use the parameters from the comm layer as default for this connection, if unspecified
    params = self.shmem['config_communication']['PARAMS']
    self._io_formatter_manager = self.shmem['io_formatter_manager']

    # build the config dict with all connection paramteres required by wrapper server
    self._comm_config = {
      ct.COMMS.EE_ID: params.get(ct.COMMS.EE_ID, None),
      "CUSTOM_CHANNEL": {
        "TOPIC": self.__get_topic()
      },
      ct.COMMS.HOST: self.__get_stream_config_metadata_property(ct.COMMS.HOST),
      ct.COMMS.PORT: self.__get_stream_config_metadata_property(ct.COMMS.PORT),
      ct.COMMS.USER: self.__get_stream_config_metadata_property(ct.COMMS.USER),
      ct.COMMS.PASS: self.__get_stream_config_metadata_property(ct.COMMS.PASS),
      ct.COMMS.QOS: self.__get_stream_config_metadata_property(ct.COMMS.QOS),
      ct.COMMS.SECURED: self.__get_stream_config_metadata_property(ct.COMMS.SECURED),
    }

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
    if self._conn_type == 'amqp':
      self.wrapper_server = AMQPWrapper(**wrapper_kwargs)
    elif self._conn_type == 'mqtt':
      self.wrapper_server = MQTTWrapper(**wrapper_kwargs)
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
      dct_ret = self.wrapper_server.server_connect()
      self.connected = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      self._create_notification(
        notif=msg_type,
        msg=msg
      )
    return

  def _maybe_reconnect(self):
    self._maybe_reconnect_to_controller_server()
    if not self.subscribed:
      if self._conn_type == 'amqp':
        dct_ret = self.wrapper_server.establish_one_way_connection('recv')
      elif self._conn_type == 'mqtt':
        dct_ret = self.wrapper_server.subscribe()
      else:
        dct_ret = None
      # endif

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
    if len(self.message_queue) == 0:
      return
    
    if self.cfg_one_at_a_time:
      nr_messages = 1
    else:
      nr_messages = min(len(self.message_queue), self.cfg_stream_window)

    self._extract_and_process_messages(nr_messages)
    return


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
      for i in range(4):
        _path_filter =  path_filter[i]
        if _path_filter is not None:
          if not isinstance(_path_filter, list):
            _path_filter = [_path_filter]
          if path[i] not in _path_filter:
            result = None
            break
    return result



  def __filter_message(self, unfiltered_message):
    """Filter messages that get passed forward

    Args:
        unfiltered_message (dict): message received from the queue server, possibly formatted if it was in a format supported by the Execution Engine

    Returns:
        dict: the message that satisfies certain conditions or None if it does not satisfy them
    """
    # first filter by path
    result = self.__filter_message_by_path(unfiltered_message)
    if result is not None:
      # then filter by message filter dict
      dct_filter = self.cfg_message_filter
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
