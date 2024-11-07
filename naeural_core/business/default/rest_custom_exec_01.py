from naeural_core.business.base import SimpleRestExecutor as BasePlugin



__VER__ = '0.1.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  
  'RESULT_KEY'              : 'exec_result',
  'ERROR_KEY'               : 'exec_errors',  
  'WARNING_KEY'             : 'exec_warning',
  
  'ROOT_PAYLOAD_KEY'        : 'ROOT_PAYLOAD_DATA',
  
  'DEBUG_EXEC'              : False,

  'MAX_INPUTS_QUEUE_SIZE'   : 1, # default queue set to 1 due to imposed delay 
  
  # the following two flags have been defaulted in such a way to force the plugin
  # execution only when IMG is available - this way any custom exec request will 
  # be executed only on "loaded" iterations of the plugin process
  'ALLOW_EMPTY_INPUTS'      : False, #
  'RUN_WITHOUT_IMAGE'       : False,
  
 
  "PROCESS_DELAY" : 1,
  
  "INFO_MANIFEST" : {
    "NAME" : "REST-like custom execution",

    "REQUEST" :{ 
      "DATA" : {
        "CODE" : "Base64 encoded script",
        },
      "TIMESTAMP" : "timestamp float (optional)"
      }
  },

  'VALIDATION_RULES' : {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },

}

_DEFAULT_INSTANCE = {
    "SIGNATURE": "REST_CUSTOM_EXEC_01",
    "INSTANCES": [
        {
            "INSTANCE_ID": "REST_EXEC",
        }
    ],
}


SLEEP_ON_ERRORS = 10

class RestCustomExec01Plugin(BasePlugin):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    super(RestCustomExec01Plugin, self).__init__(**kwargs)
    return
  
  

  def _on_request(self, request):
    result, errors, warnings = None, None, []
    
    if not isinstance(request, dict):
      errors = "Request does not comply with the given manifest"
    else:
      str_b64code = request.get('CODE')
      if str_b64code is not None and len(str_b64code) > 0:
        res = self.exec_code(
          str_b64code=str_b64code,
          debug=self.cfg_debug_exec,
          self_var='plugin', # we set plugin local for exec code
          modify=True,
        )
        if isinstance(res, tuple):
          result, errors, warnings = res
      else:
        errors = "Received empty CODE"

    if self.cfg_allow_empty_inputs:
      warnings.append('Result might be erroneous due to plugin being allowed to run without input')
      
    dct_root_payload_data = {}
    if isinstance(result, dict) and len(result) > 0:
      _rkey = self.cfg_root_payload_key
      rkey1 = _rkey.lower()
      rkey2 = _rkey.upper()
      dct_root = result.get(rkey1, result.get(rkey2))
      if dct_root is not None and isinstance(dct_root, dict) and len(dct_root) > 0:
        dct_root_payload_data = dct_root
      #endif root payload in result
    #endif result is dict
    kwargs = {
      self.cfg_result_key : result,
      self.cfg_error_key : errors,
      self.cfg_warning_key : warnings,
    }            
    payload = self._create_payload(
      **kwargs,
      **dct_root_payload_data,
    )    
    
    if len(warnings) > 0:
      self.P("Execution warnings: {}".format(warnings), color='r')

    if errors is not None:
      if isinstance(errors, list) and len(errors[-1]) > 100:
        trace = errors[-1]
        errors = errors[:-1]
      else:
        trace = None
      _show_err = True
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
      self.add_payload(payload)
      payload = None # after forced send set to None so no sending again...
      self.reset_plugin_instance()
      self.sleep(SLEEP_ON_ERRORS)
    return payload
    