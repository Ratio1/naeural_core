"""


"""

from naeural_core.business.base import BasePluginExecutor as BaseClass

_CONFIG = {
  **BaseClass.CONFIG,
  
  'ALLOW_EMPTY_INPUTS' : False,
  
  'ACCEPT_SELF' : False,
  
  'FULL_DEBUG_PAYLOADS' : False,

  'VALIDATION_RULES' : {
    **BaseClass.CONFIG['VALIDATION_RULES'],
  },  
}

__VER__ = '1.2.1'

class NetworkProcessorPlugin(BaseClass):
  CONFIG = _CONFIG
  

  def _on_init(self):
    self.__non_dicts = 0
    self.__handlers = {}
    # we get all the functions that start with on_payload_
    for name in dir(self):
      if name.startswith("on_payload_") and callable(getattr(self, name)):
        signature = name.replace("on_payload_", "").upper()
        self.__handlers[signature] = getattr(self, name)
        
    if len(self.__handlers) == 0:
      self.P("No payload handlers found", color="red")
    else:
      self.P("Payload handlers found for: {}".format(list(self.__handlers.keys())), color="green")
    self._network_processor_initialized = True
    
    self.P("NetworkProcessorPlugin v{} base initialization completed. Proceeding to custom init...".format(__VER__), color="green")
    self.on_init()
    return
  
  def maybe_check_initialized(self):
    if not hasattr(self, "_network_processor_initialized") or not self._network_processor_initialized:
      msg = "NetworkProcessorPlugin not initialized probably due to missing super().on_init() in child class"
      self.P(msg, color="red")
      raise ValueError(msg)
      return False
    return True


  def get_instance_path(self):
    return [self.ee_id, self._stream_id, self._signature, self.cfg_instance_id]
  
  
  def __maybe_process_received(self):
    datas = self.dataapi_struct_datas(full=False, as_list=True)    
    assert isinstance(datas, list), f"Expected list but got {type(datas)}"
    if len(datas) > 0:
      for data in datas:
        if not isinstance(data, dict):
          self.__non_dicts += 1
          if self.cfg_full_debug_payloads:
            self.P(f"Received non dict payload: {data} from {datas}", color="red")           
          continue
        try:
          verified = self.bc.verify(data, str_signature=None, sender_address=None)
        except Exception as e:
          self.P(f"{e}: {data}", color="red")
          continue
        if not verified:
          self.P(f"Payload signature verification FAILED: {data}", color="red")
          continue
        payload_path = data.get(self.const.PAYLOAD_DATA.EE_PAYLOAD_PATH, [None, None, None, None])        
        is_self = payload_path == self.get_instance_path()
        if is_self and not self.cfg_accept_self:
          continue
        signature = payload_path[2]
        sender = data.get(self.const.PAYLOAD_DATA.EE_SENDER, None)
        if signature in self.__handlers:
          if self.cfg_full_debug_payloads:
            self.P(f"RECV-{signature} <{sender}>: {payload_path}")
          self.__handlers[signature](data)
        else:
          if self.cfg_full_debug_payloads:
            self.P(f"RECV-UNKNOWN <{sender}>: {payload_path}")
        # end if we have handlers
      # for each data observation in dct datas     
    # end if we have payloads
    return


  def _process(self):
    """
    This method must be protected while the child plugins should have normal `process`
    """
    self.maybe_check_initialized()
    self.__maybe_process_received()  
    return self.process()
