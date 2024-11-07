"""
TODO: 
   - merge with amqp controller implementation  - this is 1-to-1 unusefull separation
   as new plugins will have to implement the comm logic anyway
   - as mqtt is the "base" we can leave full amqp implementation here in user area
"""

from naeural_core.comm import AMQPWrapper
from naeural_core.comm.base import BaseCommThread

_CONFIG = {
  **BaseCommThread.CONFIG,
  'VALIDATION_RULES': {
    **BaseCommThread.CONFIG['VALIDATION_RULES'],
  },
}


class AMQPCommThread(BaseCommThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._controller = None
    super(AMQPCommThread, self).__init__(**kwargs)
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

  @property
  def channel(self):
    if self._controller.channel is not None:
      if self._controller.channel.is_closed:
        return None

    return self._controller.channel

  def _get_server_address(self):
    return "{}:{}".format(self._controller.cfg_vhost, self._controller.cfg_port)

  # base comm abstract implementations

  def _init(self):
    self._controller = AMQPWrapper(
      log=self.log, config=self._config, recv_buff=self._recv_buff,
      send_channel_name=self._send_channel_name, recv_channel_name=self._recv_channel_name,
      comm_type=self._comm_type
    )
    self._maybe_reconnect_to_server()
    return

  def _maybe_reconnect_to_server(self):
    if self.connection is None or self.channel is None or not self.has_server_conn:
      # must reset otherwise we have same bug as in mqtt
      self.has_send_conn = False
      self.has_recv_conn = False
      dct_ret = self._controller.server_connect()
      self.has_server_conn = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      self._create_notification(
        notif=msg_type,
        msg=msg
      )
      if not self.has_server_conn:
        self._total_conn_fails += 1
        self._error_messages.append((msg_type, msg))
        self._error_times.append(self.log.time_to_str())
        self.P(msg, color='r')
      else:
        self.P(msg)
    # endif
    return

  def _maybe_reconnect_recv(self):
    self._maybe_reconnect_to_server()
    if self.has_server_conn and not self.has_recv_conn:
      dct_ret = self._controller.establish_one_way_connection('recv')
      self.has_recv_conn = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      self._create_notification(
        notif=msg_type,
        msg=msg
      )
      if not self.has_recv_conn:
        self._total_conn_fails += 1
        self._error_messages.append((msg_type, msg))
        self._error_times.append(self.log.time_to_str())
        self.P(msg, color='r')
      else:
        self.P(msg)
    # endif
    return

  def _maybe_reconnect_send(self):
    self._maybe_reconnect_to_server()
    if self.has_server_conn and not self.has_send_conn:
      dct_ret = self._controller.establish_one_way_connection('send')
      self.has_send_conn = dct_ret['has_connection']
      msg = dct_ret['msg']
      msg_type = dct_ret['msg_type']
      self._create_notification(
        notif=msg_type,
        msg=msg
      )
      if not self.has_send_conn:
        self._total_conn_fails += 1
        self._error_messages.append((msg_type, msg))
        self._error_times.append(self.log.time_to_str())
        self.P(msg, color='r')
      else:
        self.P(msg)
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
