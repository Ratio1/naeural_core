from naeural_core.heavy_ops.base import BaseHeavyOp
from time import time

from naeural_core.utils.debug_save_img import save_images_and_payload_to_output

CONFIG = {
  'IDLE_THREAD_SLEEP_TIME' : 2,
}


class DebugSaveHeavyOp(BaseHeavyOp):

  def __init__(self, **kwargs):
    self._last_archive_ts = None
    self._lst_files = []
    self._file_system_manager = None
    super(DebugSaveHeavyOp, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    assert self.comm_async
    self._file_system_manager = self.shmem['file_system_manager']
    return

  def _register_payload_operation(self, payload):
    """
    this plugin does not need to modify sent payload - only use a copy 
    """
    dct = payload.copy()
    payload.pop('_H_RELATIVE_PATH', None)
    payload.pop('_H_ORIGINAL_IMAGE', None)
    payload.pop('_H_ARCHIVE_M', 60)
    payload.pop('_H_UPLOAD', False)
    return dct

  def _process_dct_operation(self, dct):
    if self._last_archive_ts is None:
      self._last_archive_ts = time()

    relative_path = dct.pop('_H_RELATIVE_PATH', None)
    if relative_path is None:
      return

    np_orig_img = dct.pop('_H_ORIGINAL_IMAGE', None)
    np_witness_img = dct.pop('IMG', None)
    archive_minutes = dct.pop('_H_ARCHIVE_M', 60)
    perform_upload = dct.pop('_H_UPLOAD', False)
    
    atime = save_images_and_payload_to_output(
      log=self.log, 
      relative_path=relative_path,
      np_witness_img=np_witness_img, 
      np_orig_img=np_orig_img, 
      dct_payload=dct, 
      file_system_manager=self._file_system_manager,
      last_archive_time=self._last_archive_ts,
      archive_each_minutes=archive_minutes,
      upload_nr_imgs=None, 
      perform_upload=perform_upload,      
    )
    
    if atime is not None:
      self._last_archive_ts = atime
    return

# if __name__ == '__main__':
#   from naeural_core import Logger
#   import numpy as np
#   log = Logger(lib_name='CHK', base_folder='.', app_folder='_local_cache', TF_KERAS=False)
#
#   op = DebugSaveHeavyPOp(log=log)
#   d = log.load_json(fname='...')
#
#   d['IMG'] = np.random.randint(0, 255, size=(100,100,3), dtype=np.uint8)
#   d['_H_ORIGINAL_IMAGE'] = np.random.randint(0, 255, size=(100,100,3), dtype=np.uint8)
#
#   op.process_instance_payload(d)
