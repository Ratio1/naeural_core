import os
import cv2
import numpy as np
import traceback
from naeural_core import constants as ct

from time import sleep
from naeural_core.data.base import DataCaptureThread
from naeural_core.data.mixins_libs import _VideoFileMixin, _VideoConfigMixin

_CONFIG = {
  **DataCaptureThread.CONFIG,
    
  "CAP_RESOLUTION" : ct.CAP_RESOLUTION_NON_LIVE_DEFAULT,
  
  "DISPLAY_STEP"   : 500, # display each 500 frames
  'MAX_RETRIES'           : 2,
  
  "USE_FFMPEG"     : False,

  
  
  'VALIDATION_RULES' : {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

MAX_FAILED_FRAMES = 10

class VideoFileDataCapture(DataCaptureThread, _VideoFileMixin, _VideoConfigMixin):
  CONFIG = _CONFIG
  def __init__(self, **kwargs):
    self._crt_frame = 0
    self._max_no_frame = MAX_FAILED_FRAMES
    self._failed_frames = 0
    self._resize_h, self._resize_w = None, None
    self._h, self._w = None, None

    super(VideoFileDataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    self.last_url = None

    self._metadata.update(
      fps=None,
      frame_h=None,
      frame_w=None,
      frame_count=None,
      frame_current=None,
      download=None,
      total_time=None,
      time_current=None,
      frame_msec=None,
      path=None,
    )
    return
  
  @property 
  def cfg_c_array(self):
    return self.cfg_stream_config_metadata.get('C_ARRAY', True)

  @property
  def cfg_custom_rotation(self):
    return self.cfg_stream_config_metadata.get('CUSTOM_ROTATION', True)

  @property
  def cfg_delete_path(self):
    delete_path = False
    if not os.path.exists(str(self.video_file_url)):
      delete_path = True

    delete_path = self.cfg_stream_config_metadata.get('DELETE_PATH', delete_path)
    return delete_path

  @property
  def video_file_url(self):
    return self.cfg_url

  def _release(self):
    self._release_cv2_capture()
    
    if self.cfg_delete_path:
      self.P("In `_release` removed file '{}'".format(self._path), color='y')
      os.remove(self._path)
    return

  def _init(self):
    self._force_video_stream_config()
    self._maybe_download()
    return

  def _on_config_changed(self):
    if self.cfg_url != self.last_url:
      self.has_connection = False # forces reconnection
      # TODO(AID): maybe change this log to protect the URL of the video?
      self._maybe_download()
      self.P("URL change detected from {} to {}, reconnecting...".format(
        self.last_url, self.cfg_url,
        )
      )
    return

  def _maybe_reconnect(self):
    if self.has_connection:
      return

    self.has_connection = False
    nr_retry = 1
    str_e = None
    self.last_url = self.cfg_url # a cache of current url
    self._crt_frame = 0
    while nr_retry <= self.cfg_max_retries:
      try:
        self.P('Try #{} connecting to: {} (deq size: {})'.format(nr_retry, self._path, len(self._deque)))

        if not os.path.exists(self._path):
          raise Exception('Connection error. Provided url cannot be found: {}'.format(self._path))

        self._create_cv2_capture()

        if self._cv2_cap is None or not self._cv2_cap.isOpened():
          raise Exception('Video Capture could not be initiated. Please check the url: {}'.format(self._path))

        fps = 20
        try:
          fps = int(self._cv2_cap.get(cv2.CAP_PROP_FPS))
        except OverflowError:
          pass

        
        # height = int(self._cv2_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        # width = int(self._cv2_cap.get(cv2.CAP_PROP_FRAME_WIDTH))


        frame_count = self._get_number_of_frames()

        self._reset_cv2_capture()

        frame_msec = 1000 / fps

        # this makes certain kinds of movie to stop returning frames!!!!
        # self._cv2_cap.set(cv2.CAP_PROP_POS_AVI_RATIO, 1)
        # video_time = self._cv2_cap.get(cv2.CAP_PROP_POS_MSEC)
        # self._cv2_cap.set(cv2.CAP_PROP_POS_AVI_RATIO, 0)

        self.P('FPS: {}, Shape (HxW): {}, Frame count: {}'.format(fps, (self._h, self._w), frame_count), color='g')
        self.has_connection = True

        self._metadata.fps = fps
        self._metadata.finished = False
        self._metadata.frame_h = self._h
        self._metadata.frame_w = self._w
        self._metadata.frame_count = frame_count
        self._metadata.frame_msec = frame_msec
        # self._metadata.total_time = video_time

        ### universal video stream code
        self._maybe_configure_frame_crop_resize(
          height=self._h,
          width=self._w,
        )
        ### end universal video stream code

        if self._metadata.download is None:
          self._metadata.download = 0
        
        msg = 'Connected to stream'
        self.P(msg)
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          msg=msg,
          info="{}".format(self._metadata.__dict__),
          displayed=True,
        )
      except:
        str_e = traceback.format_exc()
        self.P('Connect exception for current config {}: \n{}'.format(self.config_data, str_e), color='r')
      # end try-except

      if self.has_connection:
        break
      elif nr_retry == self.cfg_max_retries:
        self.P('Reached maximum number of connect retries. Ending init method.', color='y')
      else:
        sleep(1)

      nr_retry += 1
    # endwhile
    if self.has_connection:
      self.P('Done init')
    else:
      msg = 'Data capture could not be initialized after {} retries'.format(self.cfg_max_retries)
      self.P(msg)
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info=str_e
      )
    return

  def correct_rotation(self, frame, rotateCode):
    return cv2.rotate(frame, rotateCode)

  def _run_data_aquisition_step(self):
    if self._cv2_cap is not None and self._cv2_cap.isOpened():
      self.start_timer('cv2_read')
      has_frame, frame = self._cv2_cap.read()
      self.end_timer('cv2_read')
      if self.cv2_supports_rotation and  self._cv2_rotate_code is not None and self.cfg_custom_rotation:
        self.start_timer('cv2_rotate')
        frame = self.correct_rotation(frame, self._cv2_rotate_code)
        self.end_timer('cv2_rotate')
      if has_frame:
        ### universal video stream code        
        frame = self._maybe_resize_crop(frame)
        ### end universal video stream code
        # data
        frame = frame[:, :, ::-1]

        if self.cfg_c_array:
          self.start_timer('ascontiguousarray')
          frame = np.ascontiguousarray(frame)
          self.end_timer('ascontiguousarray')
        # endif

        # metadata
        self._crt_frame += 1
        
        if (self._crt_frame % self.cfg_display_step) == 0:
          self.P("Getting frame {}/{} ({:.1f}%)".format(
            self._crt_frame, self._metadata.frame_count,
            self._crt_frame / self._metadata.frame_count * 100,
            ),
          color='g'
          )

        self._metadata.frame_current = self._crt_frame
        self._metadata.time_current = self._cv2_cap.get(cv2.CAP_PROP_POS_MSEC)
        
        if self._crt_frame >= self._metadata.frame_count and not self._metadata.finished:
          self._metadata.finished = True
          self.P("Video stream state is now finished")
        
        self.start_timer('add_video_frame')
        self._add_inputs(
          [
            self._new_input(img=frame, metadata=self._metadata.__dict__.copy()),
          ]
        )
        self.end_timer('add_video_frame')

      else:
        self._failed_frames += 1
        if self._max_no_frame > 0:
          self._max_no_frame -= 1
        else:
          self.has_finished_acquisition = True
          self._crt_frame = 0
          if self._metadata.finished:
            self.P("Empty reads {}/{} => `has_finished_acquisition=True` at {}/{}".format(
              self._failed_frames, MAX_FAILED_FRAMES,
              self._metadata.frame_current,self._metadata.frame_count, 
              ), 
            )
          else:
            self.P("Multiple failed reads at {}/{} => `has_finished_acquisition=True`, Fail/Max: {}/{}".format(
              self._metadata.frame_current,self._metadata.frame_count, 
              self._failed_frames, MAX_FAILED_FRAMES), color='r'
            )
    else:
      self.P('Setting has_connection=False')
      self.has_connection = False
    return