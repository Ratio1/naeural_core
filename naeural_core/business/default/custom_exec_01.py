from time import time
from naeural_core.business.base.cv_plugin_executor import CVPluginExecutor

__VER__ = '2.1.0'

_CONFIG = {
  **CVPluginExecutor.CONFIG,
  
  'PROCESS_DELAY'           : 0.05,
  'ALLOW_EMPTY_INPUTS'      : True,
  'MAX_INPUTS_QUEUE_SIZE'   : 1, # default queue set to 1 due to imposed delay 
  
  'RESULT_KEY'              : 'exec_result',
  'ERROR_KEY'               : 'exec_errors',
  'WARNING_KEY'             : 'exec_warning',
    
  'CODE'        : "",
  'DEBUG_EXEC'  : False,
  
  'VALIDATION_RULES' : {
    **CVPluginExecutor.CONFIG['VALIDATION_RULES'],
    
    'CODE' : {
      'TYPE' : 'str',
    }
  },

}

SLEEP_ON_ERRORS = 10

class CustomExec01Plugin(CVPluginExecutor):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self.__first_run_executed = False
    self.__last_run = None
    self.__iteration = 0
    self.__null_payload_time = time()
    self.__debug_time = time()
    self.__debug_count = 0
    super().__init__(**kwargs)
    return
  
  
  @property
  def iteration(self):
    return self.__iteration
  
  def get_base64code(self):
    return self.cfg_code  

  def _process(self):
    self.__iteration += 1
    

    if not self.__first_run_executed:
      self.P("* * * * Custom executor up and running * * * * ", color='g')
      self.__first_run_executed = True      
    else:
      if self.cfg_debug_exec and (time() - self.__debug_time) > 10:
        self.P("DEBUG EXEC: * * * * Custom executor last exec (of {} execs) was ago {:.1f}s * * * *".format(
          self.__debug_count,
          time() - self.__last_run,
          ), color='y'
        )
        self.__debug_time = time()
        self.__debug_count = 0
      
    result, errors, payload, warnings = None, None, None, []
    
    self.__last_run = time()
    self.__debug_count += 1
    str_b64code = self.get_base64code()
    if len(str_b64code) > 0: # no need to test for `str` as is imposed by validation
      res = self.exec_code(
        str_b64code=str_b64code,
        debug=self.cfg_debug_exec,
        self_var='plugin', # we set plugin local for exec code
        modify=True,
      )
      if isinstance(res, tuple):
        result, errors, warnings = res
    
    if len(warnings) > 0:
      self.P("Execution warnings: {}".format(warnings), color='r')
      
    if errors is not None or result is not None:
      payload = self._create_payload(
        **{
          self.cfg_result_key  : result,
          self.cfg_error_key   : errors,
          self.cfg_warning_key : warnings,
          'exec_iter'          : self.__iteration,
        }
      )
      self.__null_payload_time = None
      if errors is not None:
        _show_err = True
        if isinstance(errors, list) and len(errors[-1]) > 100:
          trace = errors[-1]
          errors = errors[:-1]
        else:
          trace = None
        self.add_payload(payload)
        payload = None # reset as there is no need to send it again
        sep = '*' * 100
        msg = "Exception in {}".format(self)
        info = "Reset/sleep plugin for {}s. Error in custom code execution: {}\n{}\n{}\n{}\n{}".format(
          SLEEP_ON_ERRORS, errors, 
          sep, self.base64_to_code(str_b64code),
          sep if trace is not None else '', trace if trace is not None else ''
        )
        if _show_err:
          self.P(msg +' ' + info, color='r')
        self._create_error_notification(msg=msg, info=info, displayed=_show_err)
        self.reset_plugin_instance()
        self.sleep(SLEEP_ON_ERRORS)
      # endif we have errors
    # end if some data needs to be send
    else:
      if self.__null_payload_time is None:
        self.__null_payload_time = time()
      else:
        null_elapsed = time() - self.__null_payload_time
        if null_elapsed > self.cfg_send_manifest_each:
          self.__null_payload_time = time()
          msg = "Custom plugin {}:{}:{} was idle past {:.1f}s {}".format(
            self._stream_id,
            self._signature,
            self.cfg_instance_id,
            null_elapsed,
            '(IDLE DUE TO NO CODE PROVIDED)' if len(self.cfg_code) == 0 else '(code available)'
          )
          self.P(msg)
          payload = self._create_payload(
            **{
              self.cfg_result_key  : None,
              self.cfg_error_key   : msg,
              self.cfg_warning_key : None,
              'exec_iter'          : self.__iteration,
            }
          )
        #endif send idle message
      #endif check null payload time
    #endif something or nothing returned
    return payload
    