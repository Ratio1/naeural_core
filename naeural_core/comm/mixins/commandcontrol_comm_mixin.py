from time import sleep, time
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
    bytes_delivered = 1 # force to 1 to trigger the first send
    self._init()
    while True:
      try:
        start_it = time()
        if self._stop:
          # stop command received from outside. stop imediatly
          self.P('`stop` command received. Exiting from `{}._run_thread_command_and_control`'.format(
            self.__class__.__name__))
          break

        # handle send
        if bytes_delivered > 0:
          data = None
          if len(self._send_buff) > 0:
            data = self._send_buff.popleft()
            bytes_delivered = 0 if data is not None else 1 # if data is None then set bytes_delivered to 1

        self._maybe_reconnect_send()
        self._maybe_reconnect_recv()

        if data is not None:
          msg_id, (receiver_id, receiver_addr, command), _ = data
          command = self._prepare_command(command, receiver_addr)
          self.P("Sending new command to '{}' (LOG_SEND_COMMANDS={}){}".format(
              receiver_id,
              self.cfg_log_send_commands,
              ":\n{}".format(self.log.dict_pretty_format(command)) if self.cfg_log_send_commands else ''
            ), color='g'
          )
          self.send_to = receiver_id
          bytes_delivered = 0
          if self.has_send_conn:
            # the "command" contains also received_id...
            # TODO: code review and refactor as there are too many unused messages!
            bytes_delivered = self.send_wrapper(command)
        # endif

        if self.has_recv_conn:
          self.maybe_fill_recv_buffer_wrapper()

        json_msg = self.get_message()
        if json_msg is not None:
          # below code is incorrect: DO NOT assume messages come with local formatter
          # so the format should be decided based on inputs
          # if self._formatter is not None:
          #   json_msg = self._formatter.decode_output(json_msg)

          formatter = self._io_formatter_manager.get_required_formatter_from_payload(json_msg)
          if formatter is not None:
            json_msg = formatter.decode_output(json_msg)

            # TODO: @Stefan - explain why this was moved in if in comment
            # also why register heartbeat dependant on formatter
            device_addr = json_msg.get(ct.EE_ADDR, json_msg.get(ct.PAYLOAD_DATA.EE_SENDER))
            event_type = json_msg.get(ct.PAYLOAD_DATA.EE_EVENT_TYPE, None)
            
              
            if device_addr is None or event_type is None:
              self._deque_invalid_messages.append(json_msg)

            is_heartbeat = (event_type == ct.HEARTBEAT)
            if is_heartbeat:
              self._network_monitor.register_heartbeat(addr=device_addr, data=json_msg)
            # endif
        # endif

        now = time()
        nr_minutes = 5
        if now - self._last_check_invalid_messages >= nr_minutes * 60:
          self._last_check_invalid_messages = now
          nr_invalid = len(self._deque_invalid_messages)
          if nr_invalid > 0:
            fn = self.log.save_data_json(list(self._deque_invalid_messages), "invalid_payloads.txt")
            self.P("In the last {} minutes received {} wrong messages. Check: {}".format(
              nr_minutes, nr_invalid, fn), color='r')
            self._deque_invalid_messages.clear()
          # endif
        # endif

        end_it = time()
        loop_time = end_it - start_it
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

    self._release()
    self.P('`run_thread` finished')
    self._thread_stopped = True
    return
