

class _TunnelEngineMixin(object):
  """
  Base mixin class for tunnel engine functionality.
  Both _NgrokMixinPlugin and _CloudflareMixinPlugin should inherit from this class.
  """
  def __normalize_dict(self, x):
    """
    Normalize a dictionary by converting all keys to uppercase strings.
    In case the input is not a dictionary, an empty dictionary is returned.
    Parameters
    ----------
    x : any
      The input to normalize.

    Returns
    -------
    res : dict
      A dictionary with all keys as uppercase strings.
      If the input is not a dictionary, an empty dictionary is returned.
    """
    if not isinstance(x, dict):
      return {}
    return {
      (k.upper() if isinstance(k, str) else k): v
      for k, v in x.items()
    }

  def get_default_tunnel_engine_parameters(self):
    """
    Get the default parameters for the tunnel engine.
    Returns
    -------
    res : dict
      A dictionary with default parameters for the tunnel engine.
    """
    return {}

  def maybe_fill_tunnel_engine_parameters(self, tunnel_engine_parameters, default_tunnel_engine_parameters=None):
    """
    Fill the tunnel engine parameters with the ngrok specific parameters.
    """
    # Fill the ngrok parameters
    if default_tunnel_engine_parameters is None:
      default_tunnel_engine_parameters = self.get_default_tunnel_engine_parameters()
      default_tunnel_engine_parameters = self.__normalize_dict(default_tunnel_engine_parameters)
    # endif default_tunnel_engine_parameters not provided
    if len(default_tunnel_engine_parameters) == 0:
      return tunnel_engine_parameters

    parameter_mapping = [
      (k, f"cfg_{k.lower()}", v)
      for k, v in default_tunnel_engine_parameters.items()
    ]
    for _key, method_name, default_value in parameter_mapping:
      value = getattr(self, method_name, default_value)
      if value != default_value:
        tunnel_engine_parameters[_key] = value
      # endif value
    # endfor each parameter
    return tunnel_engine_parameters

  def get_tunnel_engine_parameters(self):
    result = self.cfg_tunnel_engine_parameters or {}
    result = self.__normalize_dict(result)
    default_parameters = self.get_default_tunnel_engine_parameters()
    default_parameters = self.__normalize_dict(default_parameters)

    # The priority will be:
    # particular cfg_<PARAM_NAME> > cfg_tunnel_engine_parameters[<PARAM_NAME>] > default_parameters[<PARAM_NAME>]
    result = {
      **default_parameters,
      **result
    }
    result = self.maybe_fill_tunnel_engine_parameters(
      tunnel_engine_parameters=result,
      default_tunnel_engine_parameters=default_parameters
    )

    return result

  def reset_tunnel_engine(self):
    """
    Reset the tunnel engine state.
    """
    self.tunnel_engine_initiated = False
    self.tunnel_engine_started = False
    self._tunnel_engine_ping_last_ts = 0
    self._tunnel_engine_ping_cnt = 0
    return

  @property
  def app_url(self):
    return None

  def maybe_init_tunnel_engine(self):
    """
    Initialize the tunnel engine if it is not already initialized.
    """
    self.tunnel_engine_initiated = True
    return

  def maybe_start_tunnel_engine(self):
    """
    Start the tunnel engine if it is not already running.
    """
    self.tunnel_engine_started = True
    return

  def maybe_stop_tunnel_engine(self):
    """
    Stop the tunnel engine if it is running.
    """
    self.tunnel_engine_started = False
    return

  def get_setup_commands(self):
    """
    Get the setup commands for the tunnel engine.
    """
    setup_commands = []
    try:
      setup_commands = super(_TunnelEngineMixin, self).get_setup_commands()
    except Exception as e:
      pass
    return setup_commands

  def get_start_commands(self):
    """
    Get the start commands for the tunnel engine.
    """
    start_commands = []
    try:
      start_commands = super(_TunnelEngineMixin, self).get_start_commands()
    except Exception as e:
      pass
    return start_commands

  def get_tunnel_engine_ping_interval(self):
    """
    Get the interval in seconds for emitting pings with details about the tunnel engine.
    If 0(or any number smaller than 0) is returned, a ping will be emitted at every call of `maybe_tunnel_engine_ping`.
    If None(or any non-numeric type) is returned, no pings will be emitted.
    If a positive integer is returned, pings will be emitted every that many seconds.
    If the method is not implemented, it will return 0 by default.
    Returns
    -------
    res : int or float
      Every how many seconds the ping should be emitted.
    """
    return self.cfg_tunnel_engine_ping_interval or 0

  def get_tunnel_engine_ping_data(self):
    """
    Get the data to be included in the tunnel engine ping.
    If None is returned, no data will be included in the ping.
    Returns
    -------
    res : dict
      A dictionary with data to be included in the ping.
      If empty dictionary is returned, no data will be included in the ping.
      If None is returned, no ping will be emitted.
    """
    result = {}
    if self.app_url is not None:
      result['app_url'] = self.app_url
    return result

  def maybe_tunnel_engine_ping(self):
    """
    Define method for emitting a ping with details about the tunnel engine.
    This is optional.
    """
    ping_interval = self.get_tunnel_engine_ping_interval()
    if ping_interval is None or not isinstance(ping_interval, (int, float)):
      return  # No ping will be emitted
    ping_interval = max(ping_interval, 0)
    if self._tunnel_engine_ping_last_ts is None or self.time() - self._tunnel_engine_ping_last_ts >= ping_interval:
      ping_data = self.get_tunnel_engine_ping_data()
      if not isinstance(ping_data, dict):
        ping_data = {}
      if len(ping_data) > 0:
        self.add_payload_by_fields(
          **ping_data
        )
        self._tunnel_engine_ping_cnt += 1
        self._tunnel_engine_ping_last_ts = self.time()
      # endif ping data to send
    # endif time to send ping
    return

  def check_valid_tunnel_engine_config(self):
    """
    Check if the tunnel engine configuration is valid.
    If the configuration is valid, it returns True and None.
    If the configuration is not valid, it returns False and a message describing the issue.
    This can be overridden in child classes to implement specific checks.
    Returns
    -------
    is_valid : bool
      True if the tunnel engine configuration is valid, False otherwise.
    msg : str
      A message describing the result of the validation.
    """
    return True, None

  def on_log_handler(self, text, key=None):
    return
