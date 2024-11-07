import cv2
import os
import traceback
from naeural_core import constants as ct
from time import time


def get_rotate_code_from_orientation(cap_orientation):
  rotate_code = None
  if int(cap_orientation) == 270:
    rotate_code = cv2.ROTATE_90_CLOCKWISE
  elif int(cap_orientation) == 180:
    rotate_code = cv2.ROTATE_180
  elif int(cap_orientation) == 90:
    rotate_code = cv2.ROTATE_90_COUNTERCLOCKWISE

  return rotate_code


class _VideoFileMixin(object):

  def __init__(self):
    self._path = None
    self._progress_interval = list(range(0, 101, 10))

    self._cv2_cap = None
    self._cv2_orientation = None
    self._cv2_rotate_code = None
    self._decord_vr = None

    super(_VideoFileMixin, self).__init__()
    return

  @property
  def cfg_notify_download_downstream(self):
    return self.cfg_stream_config_metadata.get('NOTIFY_DOWNLOAD_DOWNSTREAM', False)

  def _maybe_download(self):
    if os.path.exists(str(self.video_file_url)):
      self.P('Found local file.')
      self._path = self.video_file_url

      self._metadata.download = 100
      self._metadata.download_start_date = time()
      self._metadata.download_elapsed_time = time() - self._metadata.download_start_date
      if self.cfg_notify_download_downstream:
        metadata = self._metadata.__dict__.copy()
        self._add_inputs(
          [
            self._new_input(struct_data=metadata, metadata=metadata),
          ]
        )
      self.P('File to be used: {}'.format(self._path))
    else:
      self.P('Download...')
      try:
        self._path = self._download(
          url=self.video_file_url,
          progress=False,
          verbose=False,
          notify_download=self._notify_download_progress,
        )
        self.P('File downloaded in: {}'.format(self._path))
      except:
        msg = "Exception download"
        info = traceback.format_exc()
        self.P(msg + "\n" + info, color='y')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          info=info
        )
      #end try-except
    #endif

    self._path = os.path.abspath(self._path)
    if hasattr(self._metadata, 'path'):
      self._metadata.path = self._path

    return

  def _notify_download_progress(self, message):
    try:
      prc = message.split(':')[1]
      prc = prc.replace('%', '')
      prc = int(float(prc))
      if prc % 10 == 0 and prc in self._progress_interval:
        if prc == 0:
          vars(self._metadata)['download_start_date'] = time()

        vars(self._metadata)['download'] = prc
        vars(self._metadata)['download_elapsed_time'] = time() - self._metadata.download_start_date
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          msg='Download status',
          info='{}'.format(self._metadata.__dict__),
          displayed=True,
        )
        if int(prc) in [10, 30, 70, 100]:
          self.P("Download status: {}%".format(prc))
        self._progress_interval.remove(prc)

        if self.cfg_notify_download_downstream:
          metadata = self._metadata.__dict__.copy()
          self._add_inputs(
            [
              self._new_input(struct_data=metadata, metadata=metadata),
            ]
          )
        # endif
    except Exception as e:
      self.P('Exception while notify progress: {}'.format(str(e)), color='r')
    return

  def _create_cv2_capture(self, path=None):
    path = path or self._path
    
    if self.cfg_use_ffmpeg:
      self.P("Opening capture with CAP_FFMPEG...")
      self._cv2_cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    else:
      self._cv2_cap = cv2.VideoCapture(path)
      
    self.cv2_supports_rotation = True
    try:
      if self.cfg_custom_rotation:
        self._cv2_cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
      self._cv2_orientation = self._cv2_cap.get(cv2.CAP_PROP_ORIENTATION_META)
      self._cv2_rotate_code = get_rotate_code_from_orientation(self._cv2_orientation)
    except Exception as e:
      self.cv2_supports_rotation = False
      msg = "Video File plugin cv2 does not support orientation API"
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        info=str(e),
        displayed=True,
      )
      self.P(msg, color='r')
      
    self._h, self._w = int(self._cv2_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(self._cv2_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    if self.cv2_supports_rotation and self._cv2_rotate_code in [0, 2]:
      self._h, self._w = self._w, self._h

    return

  def _create_decord_video_reader(self, path=None):
    path = path or self._path
    
    try:
      from decord import VideoReader, cpu
    except ModuleNotFoundError:
      msg = "ERROR: Missing decord package renders unusable current DCT!"
      self.P(msg, color='r')
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg=msg,
        displayed=True,
      )
      return

    try:
      self._decord_vr = VideoReader(path, ctx=cpu(0))
    except:
      pass

    return

  def _release_cv2_capture(self):
    if self._cv2_cap is None:
      return

    self._cv2_cap.release()
    del self._cv2_cap
    self._cv2_cap = None
    return

  def _release_decord_video_reader(self):
    if self._decord_vr is None:
      return

    del self._decord_vr
    self._decord_vr = None
    return

  def _reset_cv2_capture(self, path=None):
    self._release_cv2_capture()
    self._create_cv2_capture(path=path)
    return

  def _reset_decord_video_reader(self, path=None):
    self._release_decord_video_reader()
    self._create_decord_video_reader(path=path)
    assert self._decord_vr is not None, "`_create_decord_video_reader` failed - cannot use current DCT"
    return

  def _get_number_of_frames(self):
    # frame_count = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
    # cv2 doesn't always return the true number of frames. in order to
    # be sure that we report the correct number of frames, we will count the number of frames by reading the entire movie

    if self._decord_vr is not None:
      return len(self._decord_vr)
    # TODO:
    #  Find a efficient solution for this !!!!
    i = 0
    while True:
      has_frame = self._cv2_cap.grab()
      if not has_frame:
        break
      i += 1
    # self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return i


