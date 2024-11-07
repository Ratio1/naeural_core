#global dependencies
import os
import cv2
import numpy as np
import pandas as pd

from time import time

#local dependencies
from naeural_core import constants as ct

DUMP_TIME_SECS = 60 * 1

### TODO: TO BE DELETED
class _ExecutionAuditMixin(object):
  def __init__(self):
    self._audit_fld = None
    self._data = self._empty_data()
    self._last_dump_time = time()
    super(_ExecutionAuditMixin, self).__init__()
    return


  @property
  def cfg_audit_dump_time(self):
    return self._instance_config.get('AUDIT_DUMP_SECS', DUMP_TIME_SECS)

  def _empty_data(self):
    dct = {
      'TIMESTAMP' : [],
      'TYPE'      : [],
      'MSG'       : [],
    }
    return dct

  def _maybe_setup_audit_fld(self):
    if self._audit_fld is not None:
      return
    stream_name, signature, instance_id = self.unique_identification
    self._audit_fld = os.path.join(ct.AUDIT_PLUGINS, signature, stream_name, instance_id)
    self.log.check_folder_output(self._audit_fld)
    return

  def _dump_audit_data(self):
    self._maybe_setup_audit_fld()
    df = pd.DataFrame(self._data)
    self.log.save_dataframe(
      df,
      fn='{}.csv'.format(self.log.now_str()),
      folder='output',
      subfolder_path=os.path.join(self._audit_fld, self.log.file_prefix),
      verbose=False
    )

    self._last_dump_time = time()
    self._data = self._empty_data()
    return

  def _maybe_dump_audit_data(self):
    if time() - self._last_dump_time < DUMP_TIME_SECS:
      return

    self._dump_audit_data()
    return

  def _handle_images(self, img):
    if img is None:
      return

    lst_img = img
    if not isinstance(img, list):
      lst_img = [img]

    #check all images are type np.ndarray
    is_ok = all([isinstance(x, np.ndarray) for x in lst_img])
    if not is_ok:
      self.P('Please be sure to provide np.ndarray objects in order to save them while audit your plugin execution!', color='y')
      return

    self._maybe_setup_audit_fld()
    names = []
    fld = self.log.check_folder_output(os.path.join(self._audit_fld, self.log.file_prefix, 'imgs'))
    for img in lst_img:
      if img.dtype != np.uint8:
        if img.dtype == 'float' and 0 <= img.min() <= 1 and 0 <= img.max() <= 1:
          img = img * 255.0
        # endif
        img = img.astype(np.uint8)
      #endif
      name = '{}.png'.format(self.log.now_str())
      names.append(name)
      cv2.imwrite(os.path.join(fld, name), img)
    #endfor
    return names

  def _handle_custom_information(self, **kwargs):
    #check if custom information was added in the message
    if len(kwargs) == 0:
      return

    for k, v in kwargs.items():
      if k not in self._data:
        self._data[k] = [''] * (len(self._data['TIMESTAMP']) - 1)
      self._data[k].append(v)
    #endfor
    return

  def _handle_data_fill(self):
    #check if all columns have the same number of records
    nr_max = max(len(v) for v in self._data.values())
    cols_to_fill = [k for k,v in self._data.items() if len(v) < nr_max]
    for col in cols_to_fill:
      self._data[col].append('')
    #endfor
    return

  def _add_message(self, msg, msg_type, img=None, **kwargs):
    if not self.cfg_enable_audit:
      return

    self._data['TIMESTAMP'].append(self.log.now_str())
    self._data['TYPE'].append(msg_type)
    self._data['MSG'].append(msg)

    names = self._handle_images(img)
    if names is not None:
      kwargs['IMG'] = names

    self._handle_custom_information(**kwargs)
    self._handle_data_fill()

    self._maybe_dump_audit_data()
    return

  def audit_log(self, msg, img=None, **kwargs):
    self._add_message(msg=msg, msg_type='log', img=img, **kwargs)
    return

  def audit_warning(self, msg, img=None, **kwargs):
    self._add_message(msg=msg, msg_type='warning', img=img, **kwargs)
    return

  def audit_error(self, msg, img=None, **kwargs):
    self._add_message(msg=msg, msg_type='error', img=img, **kwargs)
    return

  def audit_dump_audit_data(self):
    self._dump_audit_data()
    return


if __name__ == '__main__':
  from naeural_core import Logger
  from naeural_core import DecentrAIObject

  log = Logger(lib_name='SER', base_folder='.', app_folder='_local_cache', TF_KERAS=False)

  class Base(DecentrAIObject, _ExecutionAuditMixin):
    def __init__(self, **kwargs):
      self._instance_config = None
      super(Base, self).__init__(**kwargs)

    @property
    def unique_identification(self):
      return self._instance_config['STREAM'], self._instance_config['SIGNATURE'], self._instance_config['INSTANCE_ID']

    def startup(self):
      super().startup()
      self._instance_config = {
        'STREAM': '<STREAM_NAME>',
        'SIGNATURE': '<PLUGIN_SIGNATURE>',
        'INSTANCE_ID': '<PLUGIN_INSTANCE_ID>',
        'ENABLE_AUDIT': True
      }
      self.P("In base startup")
      return


  class Plugin(Base):
    def __init__(self, **kwargs):
      super(Plugin, self).__init__(**kwargs)
      return

    def startup(self):
      super().startup()
      return


  p = Plugin(log=log)
  p.audit_log('Test 1')
  p.audit_warning('Test 2')
  p.audit_error('Test 3')
  p.audit_log('Test4', a=1, b=2)
  p.audit_warning('Test5')
  p.audit_log('Test6', a=1, b=2, abcde='sdfsdf', kkkkkkk=12312312)
  p.audit_log('Test7', c=4, d=4, e=5)
  p.audit_log('Test8')
  p.audit_log('Test9', img=np.random.randint(0, 255, (400, 400, 3), dtype=np.uint8))
  p.audit_warning('Test10')
  p.audit_dump_audit_data()