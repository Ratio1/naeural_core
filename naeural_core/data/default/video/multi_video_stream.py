from naeural_core import constants as ct
from naeural_core.data.base import DataCaptureThread
from naeural_core.data.default.video.video_stream_cv2 import VideoStreamCv2DataCapture

_CONFIG = {
  **DataCaptureThread.CONFIG,


  # Dict of configs for each video stream source.
  # Example: {"cam1": {"NAME": "Camera 1", "URL": "rtsp://...", "CONFIG": {...}},
  #           "cam2": {"NAME": "Camera 2", "URL": "rtsp://...", "CONFIG": {...}},
  #           ...}
  # All streams use VideoStreamCv2DataCapture (OpenCV backend)
  'SOURCES': {},

  'VALIDATION_RULES': {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],
  },
}

class MultiVideoStreamDataCapture(DataCaptureThread):
  CONFIG = _CONFIG

  def __init__(self, *args, **kwargs):
    super(MultiVideoStreamDataCapture, self).__init__(*args, **kwargs)
    self._video_streams = {}
    self._start_video_streams()
    return

  def _init(self):
    return

  def fn_loop_stage_callback(self, *args, **kwargs):
    # Log when this callback is called and what data is passed
    self.P("fn_loop_stage_callback called with args: {}, kwargs: {}".format(args, kwargs), color='y')
    
    # Log additional details if there are any interesting arguments
    if args:
      self.P("  - Number of positional arguments: {}".format(len(args)), color='y')
      for i, arg in enumerate(args):
        self.P("  - arg[{}]: {} (type: {})".format(i, arg, type(arg).__name__), color='y')
    
    if kwargs:
      self.P("  - Number of keyword arguments: {}".format(len(kwargs)), color='y')
      for key, value in kwargs.items():
        self.P("  - {}: {} (type: {})".format(key, value, type(value).__name__), color='y')
    
    pass

  def _start_video_streams(self):
    sources = self.config.get('SOURCES', {})
    self.P("Starting video streams for {} sources".format(len(sources)))
    for src_name, src in sources.items():
      self.P("Starting video stream for source '{}'".format(src_name))
      if src_name in self._video_streams:
        continue
      
      # Ensure the source has a NAME field for cfg_name
      if ct.NAME not in src:
        src[ct.NAME] = src_name
        self.P("Added NAME field '{}' to source config".format(src_name))
      
      try:
        video_stream = VideoStreamCv2DataCapture(
          log=self.log,
          default_config=VideoStreamCv2DataCapture.CONFIG,
          upstream_config=src,
          environment_variables=self._environment_variables if hasattr(self, '_environment_variables') else {},
          shmem=self.shmem,
          fn_loop_stage_callback=self.fn_loop_stage_callback,
          signature='VIDEO_STREAM_CV2',
        )
        video_stream.start()
        self._video_streams[src_name] = video_stream
        msg = "Started video stream '{}' using VideoStreamCv2DataCapture".format(src_name)
        self.P(msg)
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          msg=msg,
          info="{}".format(src),
          displayed=True,
        )
      except Exception as exc:
        msg = "Failed to start video stream '{}'".format(src_name)
        self.P(msg + ": {}".format(exc), color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          info=self.log.get_error_info(),
          displayed=True,
        )
        continue
      # end try-except
    # end for
    return

  def _stop_video_streams(self):
    for name in self._video_streams.keys():
      video_stream = self._video_streams.pop(name)
      try:
        video_stream.stop(join_time=3)
      except Exception as exc:
        msg = "Failed to stop video stream '{}'".format(name)
        self.P(msg + ": {}".format(exc), color='r')
        # self._create_notification(
        #   notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        #   msg=msg,
        #   info=self.log.get_error_info(),
        #   displayed=True,
        # )
        continue
      # end try-except
    return

  def _read_latest_imgs_from_video_streams(self):
    imgs = []
    for name, video_stream in self._video_streams.items():
      try:
        dct = video_stream.get_data_capture()
        inputs = dct.get('INPUTS', [])
        # pick last image if present
        for item in reversed(inputs):
          if item.get('TYPE') == 'IMG' and item.get('IMG') is not None:
            imgs.append(item.get('IMG'))
            break
      except Exception:
        self.P("Error reading from child '{}'".format(name), color='r')
        continue
    return imgs

  def connect(self):
    # No external connection; manage video streams instead
    self._start_video_streams()
    return True

  def _release(self):
    self._stop_video_streams()
    return

  def data_step(self):
    self.P("Running data acquisition step...")
    imgs = self._read_latest_imgs_from_video_streams()
    self.P("Read {} images from {} video streams".format(len(imgs), len(self._video_streams)))
    self.P("Image shapes: {}".format([im.shape if im is not None else None for im in imgs]))
    if len(imgs) == 0:
      return

    try:
      self._add_img_input(imgs)
    except Exception as exc:
      self.P("Failed to enqueue fused frame: {}".format(exc), color='r')
    return