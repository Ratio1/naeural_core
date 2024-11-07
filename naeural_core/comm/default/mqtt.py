"""
TODO: 
   - merge with MQTT controller implementation - then move to core
   - here just a simple inherited class with init and _CONFIG should reside
  
"""
from time import sleep

from naeural_core.comm import MQTTWrapper
from naeural_core import constants as ct
from naeural_core.comm.base import BaseCommThread

_CONFIG = {
  **BaseCommThread.CONFIG,

  "CONNECTION_FAIL_SLEEP_TIME": 10,

  'VALIDATION_RULES': {
    **BaseCommThread.CONFIG['VALIDATION_RULES'],
  },

  "QOS": 2
}


class MQTTCommThread(BaseCommThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._controller = None
    super(MQTTCommThread, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    return

  @property
  def send_to(self):
    return self._send_to

  @send_to.setter
  def send_to(self, x):
    self._send_to = x
    self._controller.send_channel_name = (self._send_channel_name, self._send_to)
    return

  @property
  def connection(self):
    return self._controller.connection

  def _get_errors(self):
    dct_errs = self._controller.get_connection_issues()
    err_msgs, err_times = [], []
    for err_time in dct_errs:
      err_msgs.append("Disconnected: " + dct_errs[err_time])
      err_times.append(err_time)
    return err_msgs, err_times

  def _get_server_address(self):
    return "{}:{}".format(self._controller.cfg_host, self._controller.cfg_port)

  # base comm abstract implementations
  def _init(self):
    post_on_message = None
    if hasattr(self, '_post_on_message') and self._comm_type == ct.COMMS.COMMUNICATION_DEFAULT:
      # only if type is default we add "on_message" post-processor
      self.P('Using defined `_post_on_message` for incoming commands')
      post_on_message = self._post_on_message

    self._controller = MQTTWrapper(
      log=self.log, config=self._config, recv_buff=self._recv_buff,
      send_channel_name=self._send_channel_name, recv_channel_name=self._recv_channel_name,
      comm_type=self._comm_type,
      post_default_on_message=post_on_message,
      debug_errors=self.cfg_debug_comm_errors,
      connection_name=self.cfg_ee_id,
    )
    self._maybe_reconnect_to_server()
    return

  def _maybe_reconnect_to_server(self):
    if self.connection is None or not self.has_server_conn:
      self.has_send_conn = False
      self.has_recv_conn = False
      dct_ret = self._controller.server_connect()
      self.has_server_conn = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      if not self.has_server_conn:
        self._nr_conn_retry_iters += 1
        self._total_conn_fails += 1
        self._error_messages.append((msg_type, msg))
        self._error_times.append(self.log.time_to_str())
        ##
        self.P("Multiple connection retry fails occured. Sleeping comms thread for {} sec".format(
          self.cfg_connection_fail_sleep_time), color='r'
        )
        sleep(self.cfg_connection_fail_sleep_time)
        ##
      else:
        self._nr_conn_retry_iters = 0
      self._create_notification(
        notif=msg_type,
        msg=msg
      )
      # self.P(msg)
    # endif
    return

  def _maybe_reconnect_send(self):
    self._maybe_reconnect_to_server()
    if self.has_server_conn:
      self.has_send_conn = True
    else:
      self.has_send_conn = False
    return

  def _maybe_reconnect_recv(self):
    self._maybe_reconnect_to_server()
    if self.has_server_conn and not self.has_recv_conn:
      dct_ret = self._controller.subscribe()
      self.has_recv_conn = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      if not self.has_recv_conn:
        self._total_conn_fails += 1
        self._error_messages.append((msg_type, msg))
        self._error_times.append(self.log.time_to_str())

      self._create_notification(
        notif=msg_type,
        msg=msg
      )

      topic_name = None
      if self._controller.recv_channel_def is not None:
        topic_name = self._controller.recv_channel_def['TOPIC']

      if self.has_recv_conn:
        self.P("Subscribing succeeded to topic '{}'".format(topic_name), color='g')
      else:
        self.P("Subscribing failed to topic '{}'".format(topic_name), color='r')
    # endif
    return

  def _maybe_fill_recv_buffer(self):
    self._controller.receive()
    return

  def _send(self, data):
    self._controller.send(data)
    return

  def _release(self):
    dct_ret = self._controller.release()
    for msg in dct_ret['msgs']:
      self.P(msg)

    self.has_send_conn = False
    self.has_recv_conn = False
    self.has_send_conn = False

    del self._controller
    return
