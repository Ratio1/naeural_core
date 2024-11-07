"""
Example pipeline configuration:

{
    "NAME": "on-demand-stream",
    "TYPE": "OnDemandTextInput",

    "PLUGINS": [
        {
            "SIGNATURE": "code_assist_01"
            "INSTANCES": [
                {
                    "INSTANCE_ID": "default"
                }
            ],
        }
    ],
    "STREAM_CONFIG_METADATA" : {
      "SERVER_SIDE_PARAMS_HERE" : "1234"
    }
}

then:

{ 
  "ACTION" : "PIPELINE_COMMAND",  
  "PAYLOAD" : {
    "NAME": "on-demand-stream",
    "PIPELINE_COMMAND" : {
      "STRUCT_DATA" : ["hello world"]
    }
  }
}
"""

from naeural_core.data.base import DataCaptureThread

__VER__ = '0.1.0'

_CONFIG = {
  **DataCaptureThread.CONFIG,
  
  'CAP_RESOLUTION'    : 10, # default run at 10Hz not that it really matters :)
  
  'LIVE_FEED'         : False, # it should run buffered
  
  'VALIDATION_RULES'  : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

class OnDemandTextInputDataCapture(
  DataCaptureThread,

  ):

  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(OnDemandTextInputDataCapture, self).__init__(**kwargs)
    return

  
  def startup(self):
    # NON-threaded code in startup
    super().startup()
    self._metadata.update(
      pipeline_version=__VER__,
    )
    return 
  
  
  def _init(self):
    self._iter = 0
    # not much here
    self._maybe_reconnect()    
    return
  
  @property
  def is_idle_alert(self):
    # we are never idle :)
    return False
  
  
  def _maybe_reconnect(self):
    # not much here
    if self.has_connection:
      return    
    self.has_connection = True
    return  
  
  
  def _on_pipeline_command(self, cmd_data, payload_context=None, **kwargs):
    _obs = cmd_data.get('STRUCT_DATA', [])
    _train = None
    if not isinstance(_obs, list):
      _obs = [_obs]
      
    inputs = [
        self._new_input(img=None, struct_data=x, metadata=self._metadata.__dict__.copy(), init_data=_train)
        for x in _obs
    ]
    self._add_inputs(inputs)
    return  
    
    
  def _run_data_aquisition_step(self):
    self._iter += 1
    # not much here either as stuff happens in _on_pipeline_command
    return 
    
    
  def _release(self):    
    return  
  
  