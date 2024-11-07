from naeural_core.business.base import BasePluginExecutor as BasePlugin



_CONFIG = {
  **BasePlugin.CONFIG,
  
  "PROCESS_DELAY"           : 5,
  "ALLOW_EMPTY_INPUTS"      : True,
  
  "SEND_MANIFEST_EACH"      : 60,
  
  'RESULT_KEY'              : 'exec_result',
  'ERROR_KEY'               : 'exec_errors',  
  'WARNING_KEY'             : 'exec_warning',
  

  'VALIDATION_RULES' : {
    **BasePlugin.CONFIG['VALIDATION_RULES'],

  },  
}


class CodeExecutorPlugin(BasePlugin):
  CONFIG = _CONFIG

  def on_init(self):
    self.__last_ready_timestamp = 0
    self.P(f"{self.__class__.__init__} initialized and sending manifest each {self.cfg_send_manifest_each} s.")
    return
  
  def __exec_code(self, base64code, **kwargs):
    result, errors, warnings = None, None, None
    if base64code is not None and len(base64code) > 0:
      res = self.exec_code(
        str_b64code=base64code,
        debug=self.cfg_debug_exec,
        self_var='plugin', # we set plugin local for exec code
        modify=True,
      )
      if isinstance(res, tuple):
        result, errors, warnings = res
    else:
      errors = "Received empty CODE"

    kwargs = {
      self.cfg_result_key : result,
      self.cfg_error_key : errors,
      self.cfg_warning_key : warnings,
    }            
    payload = self._create_payload(
      **kwargs,
    )    
    
    if len(warnings) > 0:
      self.P("Execution warnings: {}".format(warnings), color='r')    
      
    if errors is not None:
      self.P("Execution errors: {}".format(errors), color='r')    
    return
  
  def on_command(self, data, **kwargs):
    str_b64code = data.get('CODE')
    self.__exec_code(b64code=str_b64code, **kwargs)
    return
  
  def process(self):
    payload = None
    if (self.time() - self.__last_ready_timestamp) > self.cfg_send_manifest_each:
      self.P("Sending ready status...")
      payload = self._create_payload(
        status="Ready to receive commands."
      )
      self.__last_ready_timestamp = self.time()
    #endif send manifest
    return payload
    
      
