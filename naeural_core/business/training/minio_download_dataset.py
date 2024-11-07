from naeural_core.business.base import BasePluginExecutor as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,
  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },

  'ALLOW_EMPTY_INPUTS' : True,

  'DATASET_OBJECT_NAME' : None,
  'DATASET_LOCAL_PATH'  : None,

  'PLUGIN_LOOP_RESOLUTION' : 1/5,
}

class MinioDownloadDatasetPlugin(BasePlugin):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self._downloaded = False
    super(MinioDownloadDatasetPlugin, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    assert self.cfg_dataset_object_name is not None
    assert self.cfg_dataset_local_path is not None
    return

  @property
  def cfg_dataset_object_name(self):
    return self._instance_config['DATASET_OBJECT_NAME']

  @property
  def cfg_dataset_local_path(self):
    return self._instance_config['DATASET_LOCAL_PATH']

  def _process(self):
    if not self._downloaded:
      self.global_shmem['file_system_manager'].download(
        uri=self.cfg_dataset_object_name,
        local_file_path=self.cfg_dataset_local_path
      )
      self._downloaded = True
      self.cmdapi_stop_current_stream()
    #endif

    return
