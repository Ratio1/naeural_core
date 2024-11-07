
"""
Example pipeline configuration:

{
    "CAP_RESOLUTION": 20,
    "NAME": "on-demand-stream",
    "PLUGINS": [
        {
            "INSTANCES": [
                {
                    "AI_ENGINE"   : "a_sum_model",
                    "INSTANCE_ID": "default"
                }
            ],
            "SIGNATURE": "a_simple_plugin"
        }
    ],
    "TYPE": "OnDemandInput",
    "STREAM_CONFIG_METADATA" : {
      "SERVER_SIDE_PARAMS_HERE" : "1234"
    },
    "URL": 0
}

then:

{ 
  "ACTION" : "PIPELINE_COMMAND",  
  "PAYLOAD" : {
    "NAME": "on-demand-stream",
    "PIPELINE_COMMAND" : {
      "STRUCT_DATA" : [1,2,3,4]
    }
  }
}
"""

from naeural_core.data.base import DataCaptureThread

__VER__ = '0.1.0'

_CONFIG = {
  **DataCaptureThread.CONFIG,
  
  'CAP_RESOLUTION'    : 10, # default run at 10Hz
  
  'LIVE_FEED'         : False, # it should run buffered
  
  'VALIDATION_RULES'  : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

class OnDemandInputDataCapture(
  DataCaptureThread,

  ):

  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(OnDemandInputDataCapture, self).__init__(**kwargs)
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
    _obs = cmd_data.get('STRUCT_DATA', None)
    _base64_img = cmd_data.get('IMG', None)
    _train = None
    if _base64_img is not None:
      _img = self.base64_to_img(_base64_img)
      self._add_inputs(
        [
          self._new_input(img=_img, struct_data=None, metadata=self._metadata.__dict__.copy(), init_data=None),
        ]
      )
      # This is done in order to not have the image received saved in the plugin config
      if 'LAST_PIPELINE_COMMAND' in self.config_data:
        obj_size = self.log.get_obj_size(self.config_data['LAST_PIPELINE_COMMAND'])
        self.P(f"Last pipeline command had size {obj_size} bytes")
        self.config_data['LAST_PIPELINE_COMMAND'] = None
        self.archive_config_keys(keys=['PIPELINE_COMMAND'], defaults=[{}])
      # endif reset last pipeline command
    else:
      self._add_inputs(
        [
          self._new_input(img=None, struct_data=_obs, metadata=self._metadata.__dict__.copy(), init_data=_train),
        ]
      )
    return  
    
    
  def _run_data_aquisition_step(self):
    self._iter += 1
    # not much here either as stuff happens in _on_pipeline_command
    return 
    
    
  def _release(self):
    return  
  
  