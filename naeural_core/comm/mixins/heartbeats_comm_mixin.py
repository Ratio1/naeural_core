import numpy as np

from collections import deque
from time import time, sleep
from naeural_core import constants as ct



class _HeartbeatsCommMixin(object):

  def __init__(self):
    super(_HeartbeatsCommMixin, self).__init__()
    return

  def _run_thread_heartbeats(self):
    self._init()
    bytes_delivered = 1 # force to 1 to trigger the first send
    last_delivered = 0
    send_times = deque(maxlen=10_000)
    loop_times = deque(maxlen=10_000)
    while True:
      try:
        # check stop
        start_it = time()
        if self._stop:
          # stop command received from outside. stop imediatly
          self.P('`stop` command received. Exiting from `{}._run_thread_heartbeats`'.format(
            self.__class__.__name__))
          break

        # handle send
        self._maybe_reconnect_send()
        if bytes_delivered > 0:
          to_send = None
          if len(self._send_buff) > 0:
            to_send = self._send_buff.popleft()
            bytes_delivered = 0 if to_send is not None else 1

        if to_send is not None and self.has_send_conn:
          msg_id, msg, ts_added_in_buff = to_send
          msg = self._prepare_message(msg=msg, msg_id=msg_id)
          bytes_delivered = self.send_wrapper(msg)
          time_from_last = 0
          if last_delivered != 0:
            time_from_last = time() - last_delivered
            send_times.append(time_from_last)
          last_delivered = time()
          average = np.mean(send_times) if len(send_times) > 0 else 0
          min_time = np.min(send_times) if len(send_times) > 0 else 0
          max_time = np.max(send_times) if len(send_times) > 0 else 0
          self.P("Delivered {:.0f}KB after {:.1f}s from last hb. Time between hb: {:.1f}s, min: {:.1f}s, max: {:.1f}s, loop freq: {:.1f}Hz".format(
            bytes_delivered / 1024, time_from_last, average,
            min_time, max_time,
            1 / np.mean(loop_times) if len(loop_times) > 0 else -1
          ))
          self.telemetry_maybe_add_message(msg=msg, ts_added_in_buff=ts_added_in_buff, successful_send=bytes_delivered > 0)
        # endif
        now = time()
        # the actual loop resolution is high (minimum 10 per second) and this branch is meant to reduce the reading operations to 2 per second.
        if now - self._last_read >= 0.5:
          self._maybe_reconnect_recv()
          if self.has_recv_conn:
            # this is actually useless as for most comms we have receive callback
            self.maybe_fill_recv_buffer_wrapper()
          self._last_read = now
        # endif

        end_it = time()
        loop_time = end_it - start_it
        loop_resolution = self.loop_resolution
        sleep_time = max(1 / loop_resolution - loop_time, 0.00001)
        sleep(sleep_time)
        loop_times.append(time() - start_it)
      except Exception as e:
        msg = "Exception in heartbeats comm thread loop. Forcing loop delay extension until the exception does not occur."
        self.P(msg, color='r')
        sleep(5)
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          autocomplete_info=True
        )
      # end try-except
    # endwhile

    self._release()
    self.P('`run_thread` finished')
    self._thread_stopped = True
    return
