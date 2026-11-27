"""
TODO: 
   - merge with MQTT controller implementation - then move to core
   - here just a simple inherited class with init and _CONFIG should reside
  
"""
from naeural_core.comm import MQTTWrapper
from naeural_core import constants as ct
from naeural_core.comm.base import BaseCommThread

_CONFIG = {
  **BaseCommThread.CONFIG,

  "CONNECTION_FAIL_SLEEP_TIME": 50,
  "BURST_RECONNECTS" : 2,

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

  @property
  def receive_disabled(self):
    return self._recv_channel_name is None

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
    """
    Connect to the broker when there is no connection, and wait out a back-off when the connect burst fails.

    Does nothing while the client exists and the server connection flag is set. Otherwise it clears the send
    and receive flags and makes one connect burst of ``BURST_RECONNECTS`` attempts (the wrapper tries each
    configured broker at least once). A failed burst is counted, logged in red and followed by a wait of
    ``CONNECTION_FAIL_SLEEP_TIME`` seconds (50 by default), so a running node does not retry a broker that
    refuses it in a tight loop. The outcome of the burst is sent as a notification.

    Returns
    -------
    None

    Notes
    -----
    Once ``stop`` was asked the method opens no connection: it marks the server connection as gone and
    returns. The flag is cleared because a disconnect first seen after the stop leaves it set with no client
    behind it, and the loops send and subscribe when it is set. The lap that is running when stop is asked can
    call this again (the heartbeat and command loops reconnect for sending and then for receiving), and a
    burst against a broker that does not answer lasts until the client's connect timeout.

    The back-off waits on the stop event, which ``stop`` sets, so it ends the moment the node closes; while
    the node runs it lasts its whole length, as the plain sleep it replaces did.

    Examples
    --------
    >>> self._maybe_reconnect_to_server()   # broker refuses: one burst, then up to 50 s of back-off
    >>> self.has_server_conn
    False
    """
    if self.connection is None or not self.has_server_conn:
      self.has_send_conn = False
      self.has_recv_conn = False
      if self._stop:
        # Stopping: no new broker connection. The flag must say so: left set by a disconnect seen only now, it
        # would make the loop send to, and subscribe with, a client that no longer exists.
        self.has_server_conn = False
        return
      dct_ret = self._controller.server_connect(
        max_retries=self.cfg_burst_reconnects
      )
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
        # The same back-off as before while the node runs; stop() sets the event, so a closing node leaves it at
        # once instead of holding stop()'s join for the rest of the 50 s.
        self._stop_event.wait(self.cfg_connection_fail_sleep_time)
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
    if self.receive_disabled:
      if not self.has_recv_conn:
        self.has_recv_conn = True
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          msg="MQTT receive disabled for this communicator.",
        )
      return
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
      if self._controller.get_recv_channel_def() is not None:
        topic_name = self._controller.get_recv_channel_def()['TOPIC']

      if self.has_recv_conn:
        self.P("Subscribing succeeded to topic '{}'".format(topic_name), color='g')
      else:
        self.P("Subscribing failed to topic '{}'".format(topic_name), color='r')
    # endif
    return

  def _maybe_fill_recv_buffer(self):
    self._controller.receive()
    return

  def _send(self, data, send_to=None):
    self._controller.send(data, send_to=send_to)
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
