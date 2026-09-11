from time import perf_counter, sleep, time
from collections import deque
from naeural_core import constants as ct
import traceback


class _CommandControlCommMixin(object):

  def __init__(self):
    self._deque_invalid_messages = deque(maxlen=1000)
    self._last_check_invalid_messages = time()
    super(_CommandControlCommMixin, self).__init__()
    return

  def _run_thread_command_and_control(self):
    """Run the command-and-control comm loop.

    Notes
    -----
    The loop multiplexes command send, command receive, and heartbeat
    registration. Command retries preserve the original command payload and only
    narrow the transport targets when a multi-target publish partially fails.
    """
    bytes_delivered = 1 # force to 1 to trigger the first send
    data = None
    self._init()
    self._start_heartbeat_ingress_worker()
    while True:
      try:
        start_it = perf_counter()
        if self._stop:
          # stop command received from outside. stop imediatly
          self.P('`stop` command received. Exiting from `{}._run_thread_command_and_control`'.format(
            self.__class__.__name__))
          break

        debug_timings = self._debug_comm_loop_timings_enabled
        if debug_timings:
          self._comm_loop_timing_count("loop_iterations")

        # handle send
        if bytes_delivered > 0:
          t_section = perf_counter() if debug_timings else None
          data = None
          if len(self._send_buff) > 0:
            snapshot = getattr(self._send_buff, 'snapshot', None)
            age_at_dequeue = (
              snapshot().oldest_age_seconds if callable(snapshot) else 0.0
            )
            data = self._send_buff.popleft()
            if data is not None:
              self._mark_outbound_dequeued(
                msg_id=data[0],
                age_at_dequeue_seconds=age_at_dequeue,
              )
            bytes_delivered = 0 if data is not None else 1 # if data is None then set bytes_delivered to 1
            if debug_timings:
              self._comm_loop_timing_count("send_buffer_dequeued")
          if debug_timings:
            self._comm_loop_timing_add("send_buffer_poll", perf_counter() - t_section)

        t_section = perf_counter() if debug_timings else None
        self._maybe_reconnect_send()
        if debug_timings:
          self._comm_loop_timing_add("maybe_reconnect_send", perf_counter() - t_section)
        t_section = perf_counter() if debug_timings else None
        self._maybe_reconnect_recv()
        if debug_timings:
          self._comm_loop_timing_add("maybe_reconnect_recv", perf_counter() - t_section)

        if data is not None:
          msg_id, (receiver_id, receiver_addr, command), ts_added_in_buff, retry_send_to = data
          raw_command = command
          self._set_outbound_current_state('preparing')
          t_section = perf_counter() if debug_timings else None
          command = self._prepare_command(command, receiver_addr)
          if debug_timings:
            self._comm_loop_timing_add("prepare_command", perf_counter() - t_section)
          self.P("Sending '{}'  <{}> (LOG_SEND_COMMANDS={}){}".format(
              receiver_id, receiver_addr,
              self.cfg_log_send_commands,
              ":\n{}".format(self.log.dict_pretty_format(command)) if self.cfg_log_send_commands else ''
            ), color='g'
          )
          bytes_delivered = 0
          if self.has_send_conn:
            # the "command" contains also received_addr...
            # TODO: code review and refactor as there are too many unused messages!
            # `received_addr` is being used, since any node will always listen to the address subtopic,
            # even if it also listens to the alias subtopic.
            send_target = retry_send_to if retry_send_to is not None else receiver_addr
            self._set_outbound_current_state('paho_handoff_pending')
            t_section = perf_counter() if debug_timings else None
            bytes_delivered = self.send_wrapper(command, send_to=send_target)
            if debug_timings:
              self._comm_loop_timing_add("send_wrapper", perf_counter() - t_section)
              self._comm_loop_timing_count("send_attempts")
              if bytes_delivered > 0:
                self._comm_loop_timing_count("send_success")
            if bytes_delivered <= 0 and self._last_send_retry_targets is not None:
              data = (msg_id, (receiver_id, receiver_addr, raw_command), ts_added_in_buff, self._last_send_retry_targets)
            if bytes_delivered <= 0:
              self._set_outbound_current_state(
                'retry_pending',
                retry_targets=self._last_send_retry_targets,
                increment_retry=True,
              )
            elif self._last_send_outcome == 'terminal_dropped_oversize':
              self._finish_outbound_current(terminal_discard=True)
            else:
              self._finish_outbound_current(transport_handoff=True)
          else:
            self._set_outbound_current_state('waiting_for_connection')
        # endif

        if self.has_recv_conn:
          t_section = perf_counter() if debug_timings else None
          self.maybe_fill_recv_buffer_wrapper()
          if debug_timings:
            self._comm_loop_timing_add("fill_recv_buffer", perf_counter() - t_section)

        # In legacy topology this instance owns CTRL; in regrouped topology it
        # owns CONFIG and this helper is a no-op. The synchronous path is the
        # worker-off rollback and retains the same auth/identity boundary.
        t_section = perf_counter() if debug_timings else None
        heartbeat_outcome = self._process_next_heartbeat_synchronously()
        if debug_timings:
          self._comm_loop_timing_add(
            "synchronous_heartbeat", perf_counter() - t_section,
          )
          if heartbeat_outcome is not None:
            self._comm_loop_timing_count("messages_received")
          if heartbeat_outcome == 'committed':
            self._comm_loop_timing_count("heartbeats_received")

        now = time()
        nr_minutes = 5
        if now - self._last_check_invalid_messages >= nr_minutes * 60:
          t_section = perf_counter() if debug_timings else None
          self._last_check_invalid_messages = now
          nr_invalid = len(self._deque_invalid_messages)
          if nr_invalid > 0:
            fn = self.log.save_data_json(list(self._deque_invalid_messages), "invalid_payloads.txt")
            self.P("In the last {} minutes received {} wrong messages. Check: {}".format(
              nr_minutes, nr_invalid, fn), color='r')
            self._deque_invalid_messages.clear()
          # endif
          if debug_timings:
            self._comm_loop_timing_add("invalid_message_flush", perf_counter() - t_section)
        # endif

        end_it = perf_counter()
        loop_time = end_it - start_it
        if debug_timings:
          self._comm_loop_timing_add("loop_body", loop_time)
          self._maybe_report_comm_loop_timings(now=end_it)
        loop_resolution = max(self.loop_resolution, 100)
        sleep_time = max(1 / loop_resolution - loop_time, 0.00001)
        sleep(sleep_time)  # sleep(1/25)
        self.loop_timings.append(loop_time)
      except Exception as e:
        err_info = self.log.get_error_info(return_err_val=False)
        info = traceback.format_exc()
        msg = "Exception in C&C comm thread. Forcing loop delay {}s: {}".format(
          ct.FORCED_DELAY, err_info
        )
        self.P(msg + '\n' + info, color='r')
        sleep(ct.FORCED_DELAY)
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          info=info,
          displayed=True,
        )
      # end try-except
    # endwhile

    self._finish_outbound_current(terminal_discard=True)
    self._stop_heartbeat_ingress_worker(drain=True, timeout=10.0)
    self._release()
    self.P('`run_thread` finished')
    self._thread_stopped = True
    return
