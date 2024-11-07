import abc

from threading import Thread
from naeural_core import constants as ct

from naeural_core.serving.base.base_serving_process import ModelServingProcess as BaseServingProcess

__VER__ = '0.1.0.0'

_CONFIG = {
  **BaseServingProcess.CONFIG,
  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

class ContinousServingProcess(BaseServingProcess):

  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    if False and kwargs['inprocess']:
      raise ValueError("ContinousServingProcess cannot run inprocess")
    self._continous_process_done = False
    self._continous_thread = None
    super(ContinousServingProcess, self).__init__(**kwargs)
    return

  def _startup(self):
    self._continous_thread = Thread(
      target=self.__continous_process,
      args=(),
      name=ct.THREADS_PREFIX + self.__class__.__name__,
      daemon=True,      
    )
    self._continous_thread.start()
    return

  def _pre_process(self, inputs):
    return inputs

  def _predict(self, inputs):
    dummy_results = self._on_status(inputs)
    return dummy_results

  def _post_process(self, preds):
    result = preds.tolist()
    return result
  
  def __continous_process(self):
    while not self._continous_process_done:
      # if self._process is single pass (such as a training process) it must set _continous_process_done to True
      self._process()
    return

  # below must be defined in child class
  def _on_status(self, inputs):
    return inputs

  @abc.abstractmethod
  def _process(self):
    raise NotImplementedError()

  