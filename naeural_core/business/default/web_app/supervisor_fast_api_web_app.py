from naeural_core.business.default.web_app.fast_api_web_app import FastApiWebAppPlugin as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  
  "RUNS_ONLY_ON_SUPERVISOR_NODE" : True,

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
    res = not self.cfg_disabled and self.cfg_ngrok_edge_label is not None and self.is_supervisor_node
    if res != self.__supervisor_fastapi_plugin_running:
      self.__supervisor_fastapi_plugin_running = res
      if res:
        self.P(f"{self.__class__.__name__} is enabled")
      else:
        disabled_cause = 'disabled by config parameter' if self.cfg_disabled \
          else 'no ngrok edge label provided' if self.cfg_ngrok_edge_label is None \
          else 'not a supervisor node'
        msg = f"{self.__class__.__name__} is disabled. (cause: {disabled_cause})"
        self.P(msg, color='r', boxed=True)
    # endif changed state
    return res

  def _process(self):
    if self.cfg_runs_only_on_supervisor_node and not self.__is_enabled:
      return None
    return super(SupervisorFastApiWebApp, self)._process()
