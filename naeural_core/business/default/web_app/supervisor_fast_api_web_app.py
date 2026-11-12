from naeural_core.business.default.web_app.fast_api_web_app import FastApiWebAppPlugin as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  
  "RUNS_ONLY_ON_SUPERVISOR_NODE" : True,
  # Request-driven APIs: a queued request wakes the loop, so a lap with nothing arriving waits up to 0.25 s
  # for one instead of lapping at the full loop rate with nothing to do. Every input also wakes the loop, and
  # in the admin pipeline each batch of admin network inputs is one, so on a node with network traffic the
  # idle lap rate follows that traffic (up to the 50 laps a second of the loop's floor).
  "IDLE_LOOP_WAIT" : 0.25,

  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },
}


class SupervisorFastApiWebApp(BasePlugin):
  CONFIG = _CONFIG

  def on_init(self):
    self.__supervisor_fastapi_plugin_running = None
    super(SupervisorFastApiWebApp, self).on_init()
    return
  
  def __get_current_epoch(self):
    """
    Get the current epoch of the node.

    Returns
    -------
    int
        The current epoch of the node.
    """    
    return self.netmon.epoch_manager.get_current_epoch()
    
  
  def __sign(self, data):
    """
    Sign the given data using the blockchain engine.
    Returns the signature. 
    Use the data param as it will be modified in place.
    """
    signature = self.bc.sign(data, add_data=True, use_digest=True)
    return signature

  def _get_response(self, dct_data: dict):
    """
    
    Create a response dictionary with the given data.

    Parameters
    ----------
    dct_data : dict
        The data to include in the response - data already prepared 

    Returns
    -------
    dict

    """
    try:
      str_utc_date = self.datetime.now(self.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
      dct_data['server_info'] = {
        'alias': self.node_id,
        'version': self.ee_ver,
        'time': str_utc_date,
        'current_epoch': self.__get_current_epoch(),
        'uptime': str(self.timedelta(seconds=int(self.time_alive))),
      }
      self.__sign(dct_data) # add the signature over full data
    except Exception as e:
      self.P("Error in `get_response`: {}".format(e), color='r')
      preexisting_error = dct_data.get('error', "")
      dct_data['error'] = f"{preexisting_error} - {e}"
    return dct_data  
  

  @property
  def __is_enabled(self):
    is_valid, err_msg = self.check_valid_tunnel_engine_config()
    res = not self.cfg_disabled and is_valid and self.is_supervisor_node
    if res != self.__supervisor_fastapi_plugin_running:
      self.__supervisor_fastapi_plugin_running = res
      if res:
        self.P(f"{self.__class__.__name__} is enabled")
      else:
        disabled_cause = 'disabled by config parameter' if self.cfg_disabled \
          else 'not a supervisor node' if not self.is_supervisor_node \
          else err_msg  # if not is_valid; no need to specify sice it s the only
        # way the last else will be reached
        msg = f"{self.__class__.__name__} is disabled. (cause: {disabled_cause})"
        self.P(msg, color='r', boxed=True)
    # endif changed state
    return res

  def _process(self):
    if self.cfg_runs_only_on_supervisor_node and not self.__is_enabled:
      return None
    return super(SupervisorFastApiWebApp, self)._process()

  def _idle_loop_wait(self):
    """
    Idle wait for a supervisor API, also where the API is disabled on this node.

    An enabled API follows the web-app rule: no wait while uvicorn starts or requests are pending. With
    ``RUNS_ONLY_ON_SUPERVISOR_NODE`` set (the default) the business manager starts the API only on a
    supervisor node, and a plugin disabled by config does not reach this hook, so the API is seen
    disabled here only on a supervisor node whose tunnel configuration is invalid (for example a missing
    tunnel token). It then skips the web-app ``_process``, so while it stays disabled no start-up is
    polled and no request is served. Where it was never enabled its server never comes up, and the
    web-app rule would keep it at the fixed loop rate forever, lapping only to re-check that it is
    disabled. A disabled API therefore waits like any idle plugin.

    Returns
    -------
    float or int
      Seconds the next lap may wait for a wake-up: the configured ``IDLE_LOOP_WAIT``, or 0 to keep the
      normal loop rate.

    Notes
    -----
    The enable state is ``None`` until ``_process`` has checked it (it checks it only while
    ``RUNS_ONLY_ON_SUPERVISOR_NODE`` is set); that case takes the web-app rule, so the API does not wait
    before it has been seen disabled. While that flag is set, ``_process`` re-checks the state on each
    lap before this hook is called, so an API that becomes enabled waits at most one more
    ``IDLE_LOOP_WAIT`` before the web-app rule takes over. If the flag is turned off after the API was
    seen disabled, the state stays ``False`` and the API keeps the plain idle wait.

    Examples
    --------
    >>> api._idle_loop_wait()   # disabled on this node, IDLE_LOOP_WAIT=0.25
    0.25
    >>> api._idle_loop_wait()   # enabled, uvicorn still starting
    0
    """
    if self.__supervisor_fastapi_plugin_running is False:
      # Disabled here (a supervisor node whose tunnel configuration is invalid): _process returns at once
      # without serving anything, so wait like any idle plugin; _process re-checks the state on each lap.
      # super(BasePlugin, ...) skips the web-app override, which returns 0 while the server is not up.
      return super(BasePlugin, self)._idle_loop_wait()
    # Enabled, or not checked yet (None): the web-app rule keeps the normal rate during start-up and
    # while requests are queued or postponed.
    return super(SupervisorFastApiWebApp, self)._idle_loop_wait()
