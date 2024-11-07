from naeural_core.business.base import BasePluginExecutor as BasePlugin

__VER__ = '0.1.0.0'

_CONFIG = {
  **BasePlugin.CONFIG,

  'ALLOW_EMPTY_INPUTS': True,

  'DATASET_OBJECT_NAME': None,
  'DATASET_LOCAL_PATH': None,
  'DELETE_AFTER_ZIP': False,
  'INCLUDE_DIR_IN_ZIP': False,

  'IS_RAW': False,

  'PLUGIN_LOOP_RESOLUTION': 1/10,


  'VALIDATION_RULES': {
    **BasePlugin.CONFIG['VALIDATION_RULES'],
  },

}


class MinioUploadDatasetPlugin(BasePlugin):
  CONFIG = _CONFIG

  def on_init(self):
    super(MinioUploadDatasetPlugin, self).on_init()
    self.__uploaded = False
    return

  def startup(self):
    super().startup()
    assert self.cfg_dataset_object_name is not None
    assert self.cfg_dataset_local_path is not None
    return

  def _process(self):
    if not self.__uploaded:
      local_path = self.cfg_dataset_local_path
      # In case the local path is a directory, zip it if it is not already zipped
      if self.os_path.isdir(local_path) and not self.os_path.exists(f'{local_path}.zip'):
        zip_path = self.diskapi_zip_dir(local_path, include_dir=self.cfg_include_dir_in_zip)
        if self.cfg_delete_after_zip:
          self.diskapi_delete_directory(local_path)
        local_path = zip_path
      # endif local_path is a directory and not zipped
      # In case the local path is a directory and the zip file exists, use the zip file
      local_path = local_path if local_path.endswith('.zip') else f'{local_path}.zip'
      target_path = self.cfg_dataset_object_name
      target_path = target_path if target_path.endswith('.zip') else f'{target_path}.zip'
      if self.os_path.exists(local_path):
        self.upload_file(
          file_path=local_path,
          target_path=target_path,
          force_upload=True,
        )
        self.__uploaded = True
        self.cmdapi_stop_current_stream()
      # endif local path exists
    # endif dataset uploaded
    # Report upload status if final dataset
    if not self.cfg_is_raw and self.__uploaded:
      self.add_payload_by_fields(
        is_final_dataset_status=not self.cfg_is_raw,
        uploaded=self.__uploaded
      )
    # endif report

    return
