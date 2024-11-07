from naeural_core.data.base import BaseStructuredDataCapture


_CONFIG = {
  **BaseStructuredDataCapture.CONFIG,
  
  'DESCRIPTION' : 'This pipeline generates a simple artificial counter at each capture iteration ',
  
  'VALIDATION_RULES' : {
    **BaseStructuredDataCapture.CONFIG['VALIDATION_RULES'],
  },
}

class CounterStreamDataCapture(BaseStructuredDataCapture):
  CONFIG = _CONFIG
      
  def data_step(self):
    data = {'counter' : self._metadata.cap_struct_data_count}
    return data