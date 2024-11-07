import os
import pandas as pd
import numpy as np

from time import time
from datetime import timedelta

from naeural_core import constants as ct
from naeural_core.utils.datetime_utils import add_microseconds_to_str_timedelta

class _LimitedDataMixin(object):

  def __init__(self):
    self._limited_data_finished = False    
    self.last_limited_data_counter = -1
    self.last_limited_data_total_counter = -1
    self.last_limited_data_progress = -1
    self.last_limited_data_remaining_time = -1 
    self.last_limited_data_finished_flag = None
    self.is_data_limited = False
    
    self._payload_history = [] # list of payloads for limited streams
    super(_LimitedDataMixin, self).__init__()
    return

  @property
  def is_limited_data_finished(self):
    # TODO: `_limited_data_finished` is set to `True` by `cv` -> move to `base`!
    return self._limited_data_finished

  @property
  def is_data_limited_and_has_frame(self):
    metadata = self.dataapi_all_metadata()
    if metadata is not None:
      total = metadata.get('frame_count', 0)
      if total is not None and total > 0:
        self.is_data_limited = True
        return True
    return False

  @property
  def is_last_data(self):
    if self.is_data_limited_and_has_frame:
      curr = self.limited_data_frame_current
      total = self.limited_data_frame_count
      if curr >= total:
        return True
    return False
  
  @property
  def was_last_data(self):
    if self.last_limited_data_counter > -1:
      curr = self.last_limited_data_counter
      total = self.last_limited_data_total_counter
      if curr >= total:
        return True
    return False  
  
  @property
  def limited_data_finished_flag(self):
    if self.is_data_limited_and_has_frame:
      self.last_limited_data_finished_flag = self.dataapi_all_metadata()['finished']
      return self.last_limited_data_finished_flag
    return None

  @property
  def limited_data_counter(self):
    if self.is_data_limited_and_has_frame:
      self.last_limited_data_counter = self.dataapi_all_metadata()['frame_current']
      return self.last_limited_data_counter
    return

  @property
  def limited_data_frame_current(self):
    return self.limited_data_counter

  @property
  def limited_data_total_counter(self):
    if self.is_data_limited_and_has_frame:
      self.last_limited_data_total_counter = self.dataapi_all_metadata()['frame_count']
      return self.last_limited_data_total_counter
    return

  @property
  def limited_data_frame_count(self):
    return self.limited_data_total_counter

  @property
  def limited_data_progress(self):
    if self.limited_data_counter is not None:
      crt_frame = self.limited_data_counter
      total_frames = self.limited_data_total_counter
      progress = crt_frame / total_frames * 100
      progress = round(progress, 3)
      self.last_limited_data_progress = progress
      return progress
    return -1

  @property
  def limited_data_remaining_time(self):
    res = None
    if self.is_data_limited_and_has_frame:
      diff_time = time() - self.first_process_time
      crt_frame = self.limited_data_counter
      total_frames = self.limited_data_total_counter
      remaining_time = (total_frames - crt_frame) * (diff_time / crt_frame)
      self.last_limited_data_remaining_time = remaining_time
      res = remaining_time
    elif self.last_limited_data_remaining_time > -1:
      res = self.last_limited_data_remaining_time
    return res

  @property
  def limited_data_fps(self):
    res = None
    all_metadata = self.dataapi_all_metadata()    
    if self.is_data_limited_and_has_frame:
      try:
        res = all_metadata['cap_resolution']
      except KeyError:
        res = all_metadata['fps']
    return res

  @property
  def limited_data_seconds_elapsed(self):
    res = None
    if self.is_data_limited_and_has_frame:
      res = np.array(self.limited_data_frame_current) / np.array(self.limited_data_fps) # scalars, but safer with numpy
    return res

  @property
  def limited_data_crt_time(self):
    res = None
    if self.is_data_limited_and_has_frame:
      crt_time = add_microseconds_to_str_timedelta(str(timedelta(seconds=self.limited_data_seconds_elapsed)))
      res = crt_time
    return res

  @property
  def limited_data_duration(self):
    res = None
    if self.is_data_limited_and_has_frame:
      res = np.array(self.limited_data_total_counter) / np.array(self.limited_data_fps) # scalars, but safer with numpy
    return res
  
  @property
  def limited_data_process_fps(self):
    res = 0
    if self.is_data_limited_and_has_frame:
      try:
        elapsed_time = time() - self.first_process_time
        crt_frame = self.limited_data_counter
        res = round(crt_frame / elapsed_time, 2)
      except:
        pass
    return res

  def _patch_limited_data_history(self, payload, debug_objects):
    """
    Patch created especially because one of our implementer did not want to track objects in a finite stream
    """
    if not self.is_data_limited_and_has_frame:
      return payload

    curr_count = self.limited_data_counter
    fps = self.dataapi_all_metadata()['fps'] if self.dataapi_all_metadata()['fps'] is not None else 0
    time_sec = curr_count / fps if fps != 0 else 0
    s_time = str(timedelta(seconds=int(time_sec)))
    # minimal load for each processing step:
    dct_payload = {
      ct.FRAME_CURRENT: curr_count,
      ct.STREAM_TIME: s_time,
      ct.DEBUG_OBJECTS: debug_objects,
      ct.ALERT_HELPER: self.get_alerter_status(),

      ct.DEBUG_OBJECTS_SUMMARY: self.get_debug_objects_summary(debug_objects),
      ct.CONFIDENCE_THRESHOLD: self._get_confidence_threshold(),
    }
    if payload is not None:
      dct_payload[ct.HAS_PAYLOAD] = True
      # add actual payload info in any
      for k, v in vars(payload).items():
        if k not in [ct.IMG, ]:  # maybe other large and not necesary data
          dct_payload[k] = v
    else:
      dct_payload[ct.HAS_PAYLOAD] = False
    self._payload_history.append(dct_payload)
    if self.is_last_data:
      # now we send the results!
      # Note: even if the stream is LIVE eventually the stream plugin will
      #       push the last data in stream in buffer and will stop after the
      #       capture manager will consume it so we ALWAYS receive the last
      #       observation. This way `is_last_data` is safe.
      if payload is None:
        payload = self._create_payload()

      if self._instance_config.get(ct.PROCESSING_RESULTS_CSV, True):
        file_path = os.path.join(
          self.log.get_output_folder(),
          '{}.csv'.format(self.log.now_str())
        )
        pd.DataFrame(self._payload_history).to_csv(file_path)

        # save to dropbox
        _results = {'UPLOAD_FILE' : file_path}
        if _results is None:
          _results = 'Error while uploading csv results'
      else:
        _results = self._payload_history
      vars(payload)[ct.HISTORY] = _results
      self._payload_history = []

    return payload
