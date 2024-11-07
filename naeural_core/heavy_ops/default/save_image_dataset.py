from naeural_core.heavy_ops.base import BaseHeavyOp
import cv2
import os


FALLEN_TYPE = 'fallen'
DISRESS_TYPE = 'distress'

CONFIG = {
  'IDLE_THREAD_SLEEP_TIME' : 2,
}


class SaveImageDatasetHeavyOp(BaseHeavyOp):

  def __init__(self, **kwargs):
    super(SaveImageDatasetHeavyOp, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    assert self.comm_async

  def _register_payload_operation(self, payload):
    dct = payload.copy()
    payload.pop('_DATASET_IMG', None)
    payload.pop('_DATASET_TLBRS', None)
    payload.pop('_DATASET_EVENT', None)
    payload.pop('_DATASET_VERDICTS', None)
    payload.pop('_DATASET_SUBDIR', None)
    payload.pop('_DATASET_BATCH', None)
    payload.pop('_DATASET_X_EXTENSION', None)
    payload.pop('_DATASET_LABEL_EXTENSION', None)

    if payload.pop('_DATASET_CLEAR_PAYLOAD', False):
      # TODO: this seems to no longer be enough to discard the payload
      # maybe implement clear method in GeneralPayload
      payload.clear()
    return dct

  def save_batch(self, batch, subdir, x_extension, label_extension):
    dir_path = os.path.join(self.log.get_target_folder('output'), 'dataset')
    save_path = os.path.join(dir_path, subdir)
    os.makedirs(save_path, exist_ok=True)
    for (img, label_txt, fn, sub_ds) in batch:
      prefix_path = os.path.join(save_path, sub_ds) if sub_ds is not None else save_path
      os.makedirs(prefix_path, exist_ok=True)
      current_path = os.path.join(prefix_path, fn)
      cv2.imwrite(
        filename=f'{current_path}.{x_extension}',
        img=img[:, :, ::-1]  # flip channels to BGR
      )
      with open(f'{current_path}.{label_extension}', 'w') as f:
        f.write(label_txt)
    # endfor

    return


  def _process_dct_operation(self, dct):
    subdir = dct.pop('_DATASET_SUBDIR', None)
    dataset_batch = dct.get('_DATASET_BATCH', None)
    if dataset_batch is not None:
      x_extension = dct.pop('_DATASET_X_EXTENSION', 'jpg')
      label_extension = dct.pop('_DATASET_LABEL_EXTENSION', 'txt')
      self.save_batch(dataset_batch, subdir, x_extension, label_extension)
      return
    event_type = dct.pop('_DATASET_EVENT', None)
    if event_type is None:
      return

    original_img = dct.pop('_DATASET_IMG', None)
    h, w = original_img.shape[0:2]
    tlbrs = dct.pop('_DATASET_TLBRS', None)
    verdicts = dct.pop('_DATASET_VERDICTS', None)  # available just for fallen

    dt_now = self.log.now_str()
    dir_path = os.path.join(self.log.get_target_folder('output'), 'dataset')
    save_path = os.path.join(dir_path, event_type)
    if subdir is not None:
      save_path = os.path.join(save_path, subdir)

    os.makedirs(save_path, exist_ok=True)

    it = 0

    for (t, l, b, r) in tlbrs:
      # making sure we are in the matrix
      t, l = max(t, 0), max(l, 0)
      b, r = min(b, h - 1), min(r, w - 1)

      # cropping the current person
      current_img = original_img[int(t): int(b), int(l): int(r)]

      if event_type == FALLEN_TYPE:
        current_name = f'{dt_now}_{it}_{verdicts[it]}.jpg'
      else:
        current_name = f'{dt_now}_{it}.jpg'

      current_path = os.path.join(save_path, current_name)

      # saving the current cropping
      cv2.imwrite(
        filename=current_path,
        img=current_img[:, :, ::-1]  # flip channels to BGR
      )

      it += 1
    # endfor

    return

if __name__ == '__main__':
  from naeural_core import Logger
  import numpy as np
  log = Logger(lib_name='CHK', base_folder='.', app_folder='_local_cache', TF_KERAS=False)

  op = SaveImageDatasetHeavyOp(log=log)
  d = log.load_json(fname='...')

  d['IMG'] = np.random.randint(0, 255, size=(100,100,3), dtype=np.uint8)
  d['_H_ORIGINAL_IMAGE'] = np.random.randint(0, 255, size=(100,100,3), dtype=np.uint8)

  op.process_instance_payload(d)
