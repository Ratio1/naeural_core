import os
import zipfile
from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,
  
  'FORCE_DOWNLOAD'          : False,
  'LIVE_FEED'               : False,
  'RECONNECTABLE'           : 'YES',
  
  
  'VALIDATION_RULES' : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}


class MinioDatasetDataCapture(DataCaptureThread):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self._file_system_manager = None
    self._heuristic_cap_resolution = None
    self._send_pings = False
    self.checked_dataset = False
    self.dataset_status = "Checking for dataset"
    super(MinioDatasetDataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    # safe to use config now
    self._setup_minio()
    self._heuristic_cap_resolution = 1/10
    return

  @property
  def cfg_dataset_object_name(self):    
    if self.cfg_url is not None:
      # if available naturally url contains the object name
      return self.cfg_url
    # else get from stream config metadata
    return self.cfg_stream_config_metadata.get('DATASET_OBJECT_NAME')

  @property
  def parent_fld(self):
    return self.os_path.join(self.log.get_target_folder('data'), 'TRAINING_DATASETS')
  
  def _setup_minio(self):
    # TODO: check for self.cfg_stream_config_metadata for override minio config !
    if 'MINIO_CONFIG' in self.cfg_stream_config_metadata:
      self._custom_minio = self.cfg_stream_config_metadata['MINIO_CONFIG']
    else:
      self._file_system_manager = self.shmem['file_system_manager']
      if self._file_system_manager.file_system.signature.lower() != "minio":
        self.P("Errors will occur because the configured file system on this box is not minio!", color='e')
    return

  @property
  def ds_local_path(self):
    res = self.os_path.join(self.parent_fld, self.cfg_dataset_object_name)
    return res if not res.endswith('.zip') else res[:-4]

  def _list_minio_files(self):
    res = None
    if self._file_system_manager is not None:
      res = self._file_system_manager.file_system.list_objects()
    else:
      # TODO: implement
      pass
    return res
  
  def _download_minio_file(self, uri, local_file_path):
    res = None
    if self._file_system_manager is not None:
      res = self._file_system_manager.download(
        uri=uri,
        local_file_path=local_file_path
      )
    else:
      # TODO: implement
      pass
    return res

  def _init(self):
    return

  def _release(self):
    return

  def _maybe_reconnect(self):
    return

  def _run_data_aquisition_step(self):
    if not self.checked_dataset:
      self.P(f"Checking if dataset {self.cfg_dataset_object_name} is available...")
    if not self.checked_dataset and not self._is_dataset_available():
      self.P(f"Dataset {self.cfg_dataset_object_name} not available.")
      self.add_ds_inputs(ready=False)
    else:
      self.checked_dataset = True
      if not self._send_pings:
        self.process_found_dataset()
        self._send_pings = True
      else:
        self._add_ping_inputs()
      #endif
    #endif
    return

  def process_found_dataset(self):
    if self.os_path.isdir(self.ds_local_path) and not self.cfg_force_download:
      self.P(f"Dataset already available at {self.ds_local_path}")
      self.dataset_status = "Dataset available"
      self.add_ds_inputs(ready=True)
      return
    # endif dataset already available

    self.dataset_status = "Downloading dataset"
    os.makedirs(self.parent_fld, exist_ok=True)
    dataset_path = self.ds_local_path + '.zip'
    cloud_path = self.cfg_dataset_object_name
    if not cloud_path.endswith('.zip'):
      cloud_path += '.zip'
    self.P(f"Downloading dataset {cloud_path} to {dataset_path}")
    self._download_minio_file(
      uri=cloud_path,
      local_file_path=dataset_path
    )
    self.P(f"Downloaded dataset {self.cfg_dataset_object_name} to {dataset_path}. Unzipping...")
    self.dataset_status = "Unzipping dataset"
    target_path = self._unzip(dataset_path, self.ds_local_path)
    self.P(f"Unzipped dataset {self.cfg_dataset_object_name} to {target_path}.")
    os.remove(dataset_path)
    self.dataset_status = "Dataset available"
    self.add_ds_inputs(ready=True)
    return

  def add_ds_inputs(self, ready=False):
    self._add_inputs([
      self._new_input(struct_data={
        'dataset_ready': ready,
        'dataset_path': self.ds_local_path if ready else None,
        'dataset_status': self.dataset_status
      })
    ])

  def _add_ping_inputs(self):
    self._add_inputs(
      [
        self._new_input(struct_data={
          'ping': True,
          'dataset_ready': True,
          'dataset_status': self.dataset_status,
          'dataset_path': self.ds_local_path
        })
      ]
    )
    return

  def _is_dataset_available(self):
    target_path = self.os_path.join(self.parent_fld, self.cfg_dataset_object_name)
    if self.os_path.isdir(target_path) and not self.cfg_force_download:
      return True
    inventory = self._file_system_manager.file_system.list_objects()
    return self.cfg_dataset_object_name in inventory or self.cfg_dataset_object_name + '.zip' in inventory

  def _unzip(self, path_zip_file, dir_name=None):
    if dir_name is None:
      dir_name, ext = self.os_path.splitext(path_zip_file)
    with zipfile.ZipFile(path_zip_file, "r") as zip_ref:
      zip_ref.extractall(dir_name)
    return dir_name
