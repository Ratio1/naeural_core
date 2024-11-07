#global dependencies
import abc

#local dependencies
from naeural_core.serving.base.base_serving_process import ModelServingProcess as BaseServingProcess
from naeural_core import constants as ct

_CONFIG = {
  **BaseServingProcess.CONFIG,
  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

class BasicTFFunctionServer(BaseServingProcess):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self.default_input_shape = None
    super(BasicTFFunctionServer, self).__init__(**kwargs)
    return      
  
  @property
  def cfg_gpu_min_memory_mb(self):
    return self.config_model.get(ct.GPU_MIN_MEMORY_MB)
  
  @property
  def cfg_url(self):
    return self.config_model.get(ct.URL, None)
  
  @property
  def cfg_graph(self):
    return self.config_model[ct.GRAPH]
  
  def limit_gpu_memory(self, memory_size_mb):
    # assume running on device 0
    import tensorflow as tf
    gpu = tf.config.experimental.list_physical_devices('GPU')[0]
    cfgs = [tf.config.experimental.VirtualDeviceConfiguration(memory_limit=memory_size_mb)]
    tf.config.experimental.set_virtual_device_configuration(gpu, cfgs)
    return

  ###
  ### BELOW MANDATORY (or just overwritten) FUNCTIONS:
  ###

  
  
  def _startup(self):
    msg = self._setup_model()
    return msg  

  @abc.abstractmethod
  def _setup_model(self):
    raise NotImplementedError()