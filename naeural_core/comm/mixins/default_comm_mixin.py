from time import time, sleep
from naeural_core import constants as ct


class _DefaultCommMixin(object):

  def __init__(self):
    self._last_read = time()
    super(_DefaultCommMixin, self).__init__()
    return
  
  def _post_on_message(self):
    self.P("Received config request: {}...".format(str(self._recv_buff[-1])[:250]))
    return

  def _run_thread_default(self):
    self._init()
    bytes_delivered = 1 # force to 1 to trigger the first send
    while True:
      try:
        # check stop
        start_it = time()
        if self._stop:
          # stop command received from outside. stop imediatly
          self.P('`stop` command received. Exiting from `{}._run_thread_default`'.format(
            self.__class__.__name__))
          break

        # handle send
        self._maybe_reconnect_send()
        if bytes_delivered > 0:
          to_send = None
          if len(self._send_buff) > 0:
            to_send = self._send_buff.popleft()
            # if to_send is None then set bytes_delivered to 1 (1 byte bytes_delivered means error)
            bytes_delivered = 0 if to_send is not None else 1 

        if to_send is not None and self.has_send_conn:
          # msg is always a dict!
          msg_id, msg, ts_added_in_buff = to_send
          self._heavy_ops_manager.run_all_on_comm_thread(msg)
          # now add whatever you need in the message
          msg = self._prepare_message(msg=msg, msg_id=msg_id)
          # now convert and send the dict as json
          bytes_delivered = self.send_wrapper(msg)
          self.telemetry_maybe_add_message(msg=msg, ts_added_in_buff=ts_added_in_buff, successful_send=bytes_delivered > 0)
          if bytes_delivered <= 0:
            self.P("Failed to send message: {}...".format(
              msg.get(ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, "<Unknown path>")
              ), color='r'
            )
          elif bytes_delivered == 1:
            self.P("Failed to send message: {} most likely due to large size...".format(
              msg.get(ct.PAYLOAD_DATA.EE_PAYLOAD_PATH, "<Unknown path>")
              ), color='r'
            )
          #endif
            
        #endif
        end_it = time()
        loop_time = end_it - start_it
        # loop_resolution = max(2 * len(self._send_buff), 10)
        loop_resolution = self.loop_resolution
        sleep_time = max(1 / loop_resolution - loop_time, 0.00001)
        sleep(sleep_time)
        self.loop_timings.append(loop_time)
      except Exception as e:
        msg = "Exception in default comm thread loop. Forcing loop delay extension until the exception does not occur."
        self.P(msg, color='r')
        sleep(5)
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          autocomplete_info=True
        )
      #end try-except
    #endwhile

    self._release()
    self.P('`run_thread` finished')
    self._thread_stopped = True
    return

