__VER__ = '1.2.1'


class _NetworkProcessorMixin:
  
  @staticmethod
  def payload_handler(signature="DEFAULT"):
    if not isinstance(signature, str):
      signature = "DEFAULT"
    signature = signature.upper()
    def decorator(f):
      f.__payload_signature__ = signature
      return f
    return decorator

  def get_default_stats_dict(self):
    return {
      # Total number of messages processed for this signature
      'total': 0,
      # Number of messages processed for this signature per sender address
      'senders': self.defaultdict(int)
    }

  def __register_message_to_stats(self, signature, sender):
    if signature not in self.__stats:
      self.__stats[signature] = self.get_default_stats_dict()
    # endif first time we see this signature
    # Increment total counts
    self.__stats[signature]['total'] += 1
    self.__stats['total']['total'] += 1
    # Increment per sender counts
    self.__stats[signature]['senders'][sender] += 1
    self.__stats['total']['senders'][sender] += 1
    return

  def network_processor_init(self):
    self.__non_dicts = 0
    self.__handlers = {}
    # Maybe have this persisted?
    self.__stats = {'total': self.get_default_stats_dict()}
    self.__last_logged_stats_time = 0
    # we get all the functions that start with on_payload_
    for name in dir(self):
      if callable(getattr(self, name)):
        func = getattr(self, name)
        if name.startswith("on_payload_"):
          signature = name.replace("on_payload_", "").upper()
          self.__handlers[signature] = getattr(self, name)
        # end if we have a on_payload_<signature>
        if hasattr(func, "__payload_signature__"):
          signature = func.__payload_signature__.upper()
          if signature == "DEFAULT":
            signature = self._signature.upper()
          self.__handlers[signature] = getattr(self, name)
        # end if we have a signature
      # end if callable
    # end for each name in dir
        
    if len(self.__handlers) == 0:
      self.P("No payload handlers found", color="red")
    else:
      self.P("Payload handlers found for: {}".format(list(self.__handlers.keys())), color="green")
    self._network_processor_initialized = True
    return
  
  def __network_processor_maybe_check_initialized(self):
    if not hasattr(self, "_network_processor_initialized") or not self._network_processor_initialized:
      msg = "NetworkProcessorPlugin not initialized probably due to missing super().on_init() in child class"
      self.P(msg, color="red")
      raise ValueError(msg)
      return False
    return True


  def get_instance_path(self):
    return [self.ee_addr, self._stream_id, self._signature, self.cfg_instance_id]

  def get_registered_payload_signatures(self):
    """
    Return the set of registered payload handler signatures.

    Returns
    -------
    set of str
        Uppercase signatures registered by `on_payload_...` methods or
        `payload_handler` decorators.
    """
    if not hasattr(self, "_NetworkProcessorMixin__handlers"):
      return set()
    return set(self.__handlers.keys())
  
  
  def __network_processor_maybe_process_received(self):
    datas = self.dataapi_struct_datas(full=False, as_list=True)    
    assert isinstance(datas, list), f"Expected list but got {type(datas)}"
    if len(datas) > 0:
      for data in datas:
        if not isinstance(data, dict):
          self.__non_dicts += 1
          if self.cfg_full_debug_payloads:
            self.P(f"Received non dict payload: {data} from {datas}", color="red")           
          continue
        # In case the verification is not needed (maybe it was already done in the DCT)
        if not self.cfg_skip_message_verify:
          verified = False
          verify_msg = None
          try:
            verify_results = self.bc.verify(
              dct_data=data,
              str_signature=None, sender_address=None,
              return_full_info=True,
            )
            verified = verify_results.valid
            verify_msg = verify_results.message
          except Exception as e:
            self.P(f"{e}: {data}", color="red")
            continue
          if not verified:
            self.P(
              f"Payload signature verification FAILED with '{verify_msg}': {data}",
              color="red"
            )
            continue
        # endif skip message verification
        payload_path = data.get(self.const.PAYLOAD_DATA.EE_PAYLOAD_PATH, [None, None, None, None])        
        is_self = payload_path == self.get_instance_path()
        if is_self and not self.cfg_accept_self:
          continue
        signature = payload_path[2]
        sender = data.get(self.const.PAYLOAD_DATA.EE_SENDER, None)
        signature = signature.upper()
        self.__register_message_to_stats(signature, sender)
        if signature in self.__handlers:
          if self.cfg_full_debug_payloads:
            self.P(f"RECV-{signature} <{sender}>: {payload_path}")
          self.__handlers[signature](data)
        else:
          if self.cfg_full_debug_payloads:
            self.P(f"RECV-UNKNOWN <{sender}>: {payload_path}")
        # end if we have handlers
      # for each data observation in dct datas     
    # end if we have payloads
    return


  def network_processor_maybe_log_stats(self):
    log_period = getattr(self, 'cfg_log_stats_period', None)
    if not log_period:
      return
    if self.time() - self.__last_logged_stats_time > log_period:
      stats_str = "\n".join([
        f"{k}: {v['total']}({len(v['senders'])})" for k, v in self.__stats.items()
      ])
      self.P(f"Processed messages stats:\n{stats_str}")
      self.__last_logged_stats_time = self.time()
    # endif log time
    return


  def network_processor_loop(self):
    """
    Run one network-processor lap: handle the current input, then drain more queued inputs.

    This method must be protected while the child plugins should have normal
    `process`: ``NetworkProcessorPlugin._process`` calls it right before the
    child's ``process()``. It checks that ``network_processor_init`` ran,
    dispatches the payloads of the input the base loop already took
    (``self.inputs``) to their ``on_payload_<signature>`` handlers and, when
    ``MAX_INPUTS_PER_ITERATION`` is above 1, takes further queued inputs in the
    same lap, up to that many in total and for at most 0.5 s.

    With one input per lap, every input paid a whole lap (``process()``, the
    save check and a sleep of at least 20 ms). On a starved oracle the chain
    store served 1.27 inputs/s against 1.34 arriving, so write confirmations
    queued for about 27 s and hsync timed out.

    Returns
    -------
    None
      The handlers act on the received payloads; the child's ``process()``
      produces the plugin payload.

    Raises
    ------
    ValueError
      When ``network_processor_init`` has not run for this plugin; the logged
      message points to a child ``on_init`` that does not call
      ``super().on_init()``.

    Notes
    -----
    - ``MAX_INPUTS_PER_ITERATION`` is read directly from the config. Its default
      of 1 lives in the base plugin config, so every plugin that uses this mixin
      keeps one input per lap unless it opts in; the chain store sets 32.
    - No further input is taken once the lap limit is reached, the queue is
      empty, or 0.5 s have passed since the first input was handled, so a deep
      queue cannot hold off the child's ``process()`` for long.
    - Inputs are handled in queue order. An exception from a handler
      propagates to the base loop and the inputs not yet taken stay queued for
      the next lap.
    - After a drain, ``self.inputs`` holds the last input taken; that is the
      input the child's ``process()`` then sees.

    Examples
    --------
    >>> # MAX_INPUTS_PER_ITERATION = 32; 40 inputs arrived and the base loop took one
    >>> plugin.input_queue_size
    39
    >>> plugin.network_processor_loop()  # handles 32 inputs if they fit in 0.5 s
    >>> plugin.input_queue_size
    8
    """
    self.__network_processor_maybe_check_initialized()
    self.__network_processor_maybe_process_received()
    # MAX_INPUTS_PER_ITERATION > 1: drain more queued inputs in this lap instead of paying a whole loop lap
    # (process(), save check, sleep of at least 20 ms) for each; the input already handled counts as the first.
    extra = self.cfg_max_inputs_per_iteration - 1
    # Bound the drain to 0.5 s so a deep queue cannot hold off the child's process() for long.
    deadline = self.time() + 0.5
    # Take another input only while the per-lap limit, the queue and the time bound all allow it.
    while extra > 0 and self.input_queue_size > 0 and self.time() < deadline:
      # Use up one place of the per-lap limit, so at most MAX_INPUTS_PER_ITERATION inputs are handled in this lap.
      extra -= 1
      # Dequeue as the base loop does (same copy rule) and expose it as self.inputs, where the handlers read it.
      self.inputs = self.get_next_avail_input()
      # Dispatch its payloads to the on_payload_ handlers now, in queue order, instead of waiting for the next lap.
      self.__network_processor_maybe_process_received()
    self.network_processor_maybe_log_stats()
    return
