from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,
  'VALIDATION_RULES' : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

class BaseStructuredDataCapture(DataCaptureThread):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    super(BaseStructuredDataCapture, self).__init__(**kwargs)
    return


  def startup(self):
    super().startup()
    self._metadata.update(
      cap_stream_type='STRUCT_DATA',
      cap_struct_data_count=0,
    )
    return

  def _get_data(self):
    return self.get_data()          
  
  def __get_data_step(self):
    return self._get_data()        
  
  def data_step(self):    
    if self.has_connection:
      self._metadata.cap_struct_data_count = self._metadata.cap_struct_data_count + 1
      _obs = self.__get_data_step()
      self._add_struct_data_input(obs=_obs)
    return 
    
  
  def _release(self):
    return  
  
  