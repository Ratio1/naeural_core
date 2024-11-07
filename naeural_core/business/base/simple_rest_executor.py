from time import time
from naeural_core.business.base import CVPluginExecutor as BasePlugin

_CONFIG = {
  **BasePlugin.CONFIG,
  
  
  'RESTART_ALERTERS_ON_CONFIG': False,
  'RESTART_SHMEM_ON_CONFIG'   : False,
  'ALLOW_EMPTY_INPUTS'        : True,
  
  'ALLOW_ORIGINAL_IMAGE'      : False,
  
  
  'DEBUG_REST'                : False,
    
  'REQUEST' : None,
  
  'INFO_MANIFEST' : {
    'REQUEST' : {
      'DATA' : {
          'PARAM1' : 'Set value of params',
        },
      'TIMESTAMP' : 'Float value of timestamp',
    }
  },

  'VALIDATION_RULES' : {
    **BasePlugin.CONFIG['VALIDATION_RULES'],

  },  

}

__VER__ = '0.1.0'

class SimpleRestExecutor(BasePlugin):
  CONFIG = _CONFIG
  
  def __init__(self, **kwargs):
    self.__loop_counter = 0
    self.__request_counter = 0
    self.__last_request_timestamp = None
    self.__last_request = None
    self.__time_of_last_request = time()
    self.__time_of_last_manifest = time()
    self.__request_served = False
    self.__first_run_executed = False
    
    super(SimpleRestExecutor, self).__init__(**kwargs)
    return

  
  @property
  def request_count(self):
    return self.__request_counter
  
  def _get_request(self):
    request = self.cfg_request
    data = None    
    _timestamp = None
    if request is not None:
      if isinstance(request, dict) and len(request) > 0:
        # timestamp can be any kind of "nonce" message not necesarelly a actual timestamp
        _timestamp = request.get('TIMESTAMP')
        # we dont actually need repeated_timestamp
        repeated_timestamp = _timestamp is not None and _timestamp == self.__last_request_timestamp
        repeated_request = self.__last_request == request
        maybe_data = request.get('DATA')
        
        if not repeated_request:
          if maybe_data is None:
            self.P("Received request with no 'DATA': {}".format(request), color='r')          
          data = request if maybe_data is None else maybe_data
      else:
        # non dict request ... pass it forward as it is
        if self.__last_request != request:
          self.P("Received non-dict request: {}".format(request), color='r')
          data = request
      # dict or non-dict request
    if data is not None:
      self.__request_counter += 1
      self.__time_of_last_request = time()
      self.__time_of_last_manifest = time()      
      self.__request_served = True
      self.__last_request = request
      self.__last_request_timestamp = _timestamp
    return data
  
  def _on_request(self, data):
    raise NotImplementedError()
    
  def _maybe_cleanup_instance(self):
    ### cleanup any residues from previous runs !
    # clean default image - as it may remain from a previous run and will be continuously delivered
    self.default_image = None
    return
  
  def _needs_manifest(self):
    last_req_time = time() - self.__time_of_last_request
    last_manifest_time = time() - self.__time_of_last_manifest
    result = False
    if self.__request_served:
      if last_req_time > (self.cfg_send_manifest_each * 3):
        result = True
    else:
      if last_manifest_time > self.cfg_send_manifest_each:
        result = True
    return result
  
  
  def _maybe_cancel_original_image(self):
    if self.cfg_add_original_image and not self.cfg_allow_original_image:
      self.P("Found ADD_ORIGINAL_IMAGE=True but ALLOW_ORIGINAL_IMAGE=False. Setting ADD_ORIGINAL_IMAGE=False", color='r')
      self.config_data['ADD_ORIGINAL_IMAGE'] = False
      self.P("  ADD_ORIGINAL_IMAGE={}".format(self.cfg_add_original_image))
    #endif
    return
  
  def _process(self):
    self.__loop_counter += 1
    data = self._get_request()
    payload = None
    self._maybe_cancel_original_image()
    if data is not None:
      self.P("**** Request <{}...> at iter {}".format(str(data)[:50], self.__loop_counter), color='d')
      if self.cfg_debug_rest:
        has_img = self.dataapi_image() is not None
        meta = self.dataapi_all_metadata()
        freq = self.actual_plugin_resolution
        qlen = self.input_queue_size
        ale = self.cfg_allow_empty_inputs
        rwi = self.cfg_run_without_image
        self.P("DEBUG-REST: Image avl: {}, p.freq.: {}, q.len: {}, alw.emt: {}, r.wo.i.: {}\nMetadata: {}".format(
          has_img, freq, qlen, ale, rwi,
          meta
          )
        )
      # endif "DEBUG_REST" : true
      try:
        payload = self._on_request(data)
      except Exception as exc:
        self.P("Exception in on-reques: {}".format(exc), color='r')
        raise exc
      finally:
        self.archive_config_keys(keys=['REQUEST'])

      
      if self.cfg_debug_rest:
        if payload is not None:
          im = vars(payload).get('IMG', [])
          has_img = len(im) > 0
          self.P("DEBUG-REST: Payload IMG: {}, default IMG: {}".format(
            im, self.default_image is not None)
          )
        else:
          self.P("DEBUG-REST: on_request payload None", color='r')
      if payload is not None:
        vars(payload)['TIMESTAMP'] = self.__last_request_timestamp
    elif not self.__first_run_executed or self._needs_manifest():
      __elapsed = min(time() - self.__time_of_last_request, time() - self.__time_of_last_manifest)
      self.__time_of_last_manifest = time()
      self.__request_served = False
      # no need for cleanup as certainly it was already run
      payload = self._create_payload(
        info_manifest=self.cfg_info_manifest
      )
      if self.cfg_log_manifest_send:
        self.P("**** Sending manifest after {:.1f}s/{:.1f}s from last activity".format(
          __elapsed, self.cfg_send_manifest_each), color='d'
        )
    else:
      self._maybe_cleanup_instance()

    if not self.__first_run_executed:
      self.P("* * * * REST-like service up and running * * * * ", color='g')
      self.__first_run_executed = True
    return payload
  
  
  