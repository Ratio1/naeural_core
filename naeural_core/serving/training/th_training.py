# TODO Bleo: WIP
from naeural_core.serving.base import ContinousServingProcess as BaseServingProcess
from PyE2 import _PluginsManagerMixin
from naeural_core.local_libraries.nn.th.training.pipelines.base import BaseTrainingPipeline
from naeural_core.utils.thread_raise import ctype_async_raise
import torch as th
import gc

_CONFIG = {
  **BaseServingProcess.CONFIG,

  'PICKED_INPUT': 'STRUCT_DATA',
  'CLOSE_IF_UNUSED': True,

  'PIPELINE_SIGNATURE': None,
  'PIPELINE_CONFIG': {},

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}


DEFAULT_PIPELINE_CONFIG = {
  'START_ITER': 0,
  'END_ITER': None,
  'BATCH_SIZE': 8,
  'EPOCHS': 10,
  'DEVICE_LOAD_DATA': 'cuda:0',
  'DEVICE_TRAINING': 'cuda:0',
  'PRELOAD_DATA': True,
  "NUM_WORKERS": 0,
  "EXPORT_MODEL": True
}


class THTraining(BaseServingProcess, _PluginsManagerMixin):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._pipeline : BaseTrainingPipeline = None
    self.__done_training = False
    super(THTraining, self).__init__(**kwargs)
    return

  @property
  def th(self):
    return th

  def get_pipeline_config(self):
    pipeline_config = self.cfg_pipeline_config
    return {
      **DEFAULT_PIPELINE_CONFIG,
      **pipeline_config,
    }

  def get_start_iter(self):
    return self.get_pipeline_config().get('START_ITER', 0)

  def get_end_iter(self):
    return self.get_pipeline_config().get('END_ITER', None)

  def _create_pipeline(self, path_to_dataset):
    signature = self.cfg_pipeline_signature
    assert signature is not None, 'Pipeline Signature is None'

    _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
      locations=['naeural_core.local_libraries.nn.th.training.pipelines', 'plugins.serving.pipelines'],
      name=signature,
      suffix='TrainingPipeline',
      search_in_packages=self.ct.PLUGIN_SEARCH.SEARCH_IN_PACKAGES
      # the following line was commented in order to be debugged after the training workshop
      # safety_check=True, # perform safety check
    )

    self._pipeline = _cls_def(
      log=self.log,
      signature=signature,
      config=self.get_pipeline_config(),
      path_to_dataset=path_to_dataset,
    )
    return

  def _on_status(self, inputs):
    return {
      'STATUS'       : self._pipeline.status,
      'METADATA'     : self._pipeline.metadata,
      'HAS_FINISHED' : self._pipeline.grid_has_finished,
    }

  def _process(self):
    self._continous_process_done = True
    while self._pipeline is None:
      self.sleep(1)

    self._pipeline.run(start_iter=self.get_start_iter(), end_iter=self.get_end_iter())
    self.__done_training = True
    return

  def _pre_process(self, inputs):
    data = inputs['DATA']
    stream_name = inputs['STREAM_NAME']
    if len(data) > 1:
      raise ValueError("Multiple training streams are opened: {}".format(stream_name))
    return data[0]

  def _predict(self, prep_inputs):
    if 'dataset_ready' in prep_inputs and self._pipeline is None:
      can_start_training = prep_inputs['dataset_ready']
      if can_start_training:
        self._create_pipeline(path_to_dataset=prep_inputs['dataset_path'])
        return self._on_status(prep_inputs)
      else:
        return {'STATUS' : 'WAITING'}
      # endif
    # endif

    return self._on_status(prep_inputs)

  def _post_process(self, preds):
    return [preds]

  def _shutdown(self):
    self.P('Shutting down continuous thread...')
    if not self.__done_training:
      self.P('Training process has not finished yet, forcing stop...')
      ctype_async_raise(self._continous_thread.ident, self.ct.ForceStopException)
    self._continous_thread.join()
    self.P('Continuous thread has been shut down')
    gc.collect()
    if self.th.cuda.is_available():
      self.th.cuda.empty_cache()
    self.P('Memory has been freed up')
    return
