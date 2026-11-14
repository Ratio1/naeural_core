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
    """Run the default comm send loop.

    The loop sends the payloads of the send queue one at a time, until the thread is told to stop.

    Notes
    -----
    Messages are prepared only when they reach the head of the send queue. When
    a fanout send partially succeeds, the loop keeps the original payload and
    retries only the failed destinations recorded by ``send_wrapper``.

    A payload whose preparation raises (``_prepare_message``: for example text that is not UTF-8 encodable,
    which its encryption cannot encode) is dropped with one red line naming its path (node, stream, signature,
    instance) and the error, and the next payload is taken; before, the same payload was prepared again every
    5 s for good and no later payload left this thread. An error elsewhere in the loop is handled as before.

    Returns
    -------
    None

    Examples
    --------
    >>> self._run_thread_default()   # on the comm thread, until ``self._stop`` is set
    """
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
          msg_id, msg, ts_added_in_buff, retry_send_to = to_send
          raw_msg = msg
          self._heavy_ops_manager.run_all_on_comm_thread(msg)
          # now add whatever you need in the message
          try:
            # The final payload (encrypted when it has a destination).
            msg = self._prepare_message(msg=msg, msg_id=msg_id)
          except Exception as exc:
            # A payload that cannot be prepared (text that is not UTF-8 encodable, for example) fails the same way
            # at every retry, as each one prepares a copy of it: dropped with one red line naming its path, so the
            # payloads queued behind it still leave. Before, it was retried every 5 s for good.
            path = [raw_msg.get(key) for key in (
              ct.EE_ID, ct.PAYLOAD_DATA.STREAM_NAME, ct.PAYLOAD_DATA.SIGNATURE, ct.PAYLOAD_DATA.INSTANCE_ID)]
            self.P("Dropped payload {} that cannot be prepared: {}".format(path, exc), color='r')
            # The next payload is taken at the next turn.
            bytes_delivered = 1
            continue
          # now convert and send the dict as json
          bytes_delivered = self.send_wrapper(msg, send_to=retry_send_to)
          if bytes_delivered <= 0 and self._last_send_retry_targets is not None:
            to_send = (msg_id, raw_msg, ts_added_in_buff, self._last_send_retry_targets)
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
