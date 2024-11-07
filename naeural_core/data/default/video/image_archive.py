#local dependencies
import os
import re
import cv2
import numpy as np

import zipfile

from time import time, sleep
from collections import deque

#global dependencies
from naeural_core import constants as ct

from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,
  "DELETE_ZIP": True,
  'LOG_PROGRESS_PERIOD': 2000,
  'START_IMAGE_ITER': 0,
  'END_IMAGE_ITER': 0,
  'VALIDATION_RULES' : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

class ImageArchiveDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._path = None
    self._progress_interval = list(range(0, 101, 5))
    self._files = deque()
    self._crt_frame = 0
    self._total_files = 0
    
    super(ImageArchiveDataCapture, self).__init__(**kwargs)
    return
  
  def startup(self):
    super().startup()
    # Following lines have been commented due to limitation of DCT use-cases!
    # assert self.is_keepalive or not self.is_reconnectable
    # assert not self.cfg_live_feed

    self._metadata.update(
      fps=None,
      frame_h=None,
      frame_w=None,
      frame_count=None,
      frame_current=None,
      download=None,
      total_time=None,
      time_current=None,
      frame_msec=None
    )
    return
  
  @property
  def cfg_c_array(self):
    return self.cfg_stream_config_metadata.get('C_ARRAY', True)

  @property
  def cfg_notify_download_downstream(self):
    return self.cfg_stream_config_metadata.get('NOTIFY_DOWNLOAD_DOWNSTREAM', False)

  def _notify_download_progress(self, message):    
    try:
      prc = message.split(':')[1]
      prc = prc.replace('%', '')
      prc = int(float(prc))
      if prc % 5 == 0 and prc in self._progress_interval:
        if prc == 0:
          self._metadata.download_start_date = time()
        
        self._metadata.download = prc
        self._metadata.download_elapsed_time = time() - self._metadata.download_start_date
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          msg='Download status',
          info="{}".format(self._metadata.__dict__)
        )
        self._progress_interval.remove(prc)
        
        if self.cfg_notify_download_downstream:
          metadata = self._metadata.__dict__.copy()
          self._add_inputs(
            [
              self._new_input(struct_data=metadata, metadata=metadata),
            ]
          )
    except Exception as e:
      self.P('Exception while notify progress: {}'.format(str(e)), color='r')
    return
  
  def _download_local(self):
    # TODO: unzip file
    self.P(' Found local file.')
    self._path = self.cfg_url
    self._metadata.download = 100
    self._metadata.download_start_date = time()
    self._metadata.download_elapsed_time = time() - self._metadata.download_start_date
    self._create_notification(
      notif=ct.STATUS_TYPE.STATUS_NORMAL,
      msg='Download status',
      info='{}'.format(self._metadata.__dict__)
    )
    if self.cfg_notify_download_downstream:
      metadata = self._metadata.__dict__.copy()
      self._add_inputs(
        [
          self._new_input(struct_data=metadata, metadata=metadata),
        ]
      )
    extract_path = os.path.splitext(self._path)[0]
    
    if self._path.endswith('.zip'):
      with zipfile.ZipFile(self._path, 'r') as fh:
        fh.extractall(extract_path)
      sleep(2)
      if self.cfg_delete_zip:
        os.remove(self._path)
      self._path = extract_path
    self.P(' Path to be used: {}'.format(self._path))
    return

  def _download_cloud(self):
    self.P(' Download...')
    try:
      self._path = self._download(
        url=self.cfg_url,
        progress=False,
        verbose=False,
        notify_download=self._notify_download_progress,
        unzip=True
        )
      self.P(' File downloaded in: {}'.format(self._path))
    except:
      msg = "Exception download"
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        autocomplete_info=True
      )
    return
  
  def _natural_sort(self, l):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)
  
  def _load_files(self):
    l = []
    for subdir, dirs, files in os.walk(self._path):
      for file in files:
        l.append(os.path.join(subdir, file))
    # endfor os.walk
    self.P('Found {} files in {}'.format(len(l), self._path))
    l = self._natural_sort(l)
    nr_skip_start = self.cfg_start_image_iter
    nr_skip_end = self.cfg_end_image_iter
    if nr_skip_end == 0:
      nr_skip_end = len(l)
    for fn in l[nr_skip_start:nr_skip_end]:
      self._files.append(fn)
    self.P(f'Skipped {nr_skip_start + len(l) - nr_skip_end}({nr_skip_start}+{len(l) - nr_skip_end}) files ({len(self._files)} remaining).')
    self._total_files = len(self._files)
    return
  
  def _init(self):
    self.P("Using '{}' as data source".format(self.cfg_url))
    if os.path.exists(self.cfg_url):
      self.P("  Found '{}' in local".format(self.cfg_url))
      self._download_local()
    else:
      self.P("  '{}' not found in local. Assuming URL".format(self.cfg_url))
      self._download_cloud()
    return
  
  #abstract methods implementation
  def _release(self):
    """Cleaning all capture storage"""
    del self._files
    return
  
  def _maybe_reconnect(self):    
    """Nothing to do for this plugin"""
    if self.has_connection:
      return
    
    self._load_files()
    self._metadata.frame_count = len(self._files)
    self.has_connection = True
    return
  
  def _run_data_aquisition_step(self):
    """Reading images one at a time"""
    if self._files:
      fn = self._files.popleft()
      frame = cv2.imread(fn)
      # frame = np.ascontiguousarray(frame[:,:,::-1])
      frame = frame[:,:,::-1]
      if self.cfg_c_array:
        self.start_timer('ascontiguousarray')
        frame = np.ascontiguousarray(frame)
        self.end_timer('ascontiguousarray')
      #endif

      #metadata
      self._crt_frame+= 1

      if self._crt_frame % self.cfg_log_progress_period == 0:
        self.P(f'So far added {self._crt_frame}/{self._total_files} to inputs queue.')

      height, width, _ = frame.shape
      self._metadata.frame_h = height
      self._metadata.frame_w = width
      self._metadata.frame_current = self._crt_frame
      self._metadata.subdir = os.path.relpath(os.path.dirname(fn), start=self._path)

      self._add_inputs(
        [
          self._new_input(img=frame, metadata=self._metadata.__dict__.copy()),
        ]
      )
    else:
      self._crt_frame = 0
      self.has_connection = False
      self.has_finished_acquisition = True
    return