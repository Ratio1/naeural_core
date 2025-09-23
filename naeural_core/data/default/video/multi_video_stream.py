from naeural_core import constants as ct
from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,


  # List of configs for each video stream source.
  # Example item: {"NAME": "cam1", "URL": "rtsp://...", "BACKEND": "ffmpeg", "CONFIG": {...}, ...}
  'SOURCES': [],

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

  def _discover_video_stream_class(self, src_cfg):
    src_type = src_cfg.get(ct.TYPE)
    backend = src_cfg.get('BACKEND', None)
    if backend and isinstance(backend, str):
      signature = 'VIDEO_STREAM_' + backend.upper()
    else:
      signature = src_type
    try:
      _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
        locations=ct.PLUGIN_SEARCH.LOC_DATA_ACQUISITION_PLUGINS,
        name=signature,
        suffix=ct.PLUGIN_SEARCH.SUFFIX_DATA_ACQUISITION_PLUGINS,
        search_in_packages=ct.PLUGIN_SEARCH.SEARCH_IN_PACKAGES,
        safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_PLUGINS,
        safe_imports=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_IMPORTS,
        safety_check=True,
      )
    except Exception as exc:
      msg = "Failed to resolve child class '{}' for source '{}'".format(signature, src_cfg.get(ct.NAME))
      self.P(msg + ": {}".format(exc), color='r')
      # self._create_notification(
      #   notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
      #   msg=msg,
      #   info=self.log.get_error_info(),
      #   displayed=True,
      # )
      raise
    return _cls_def, _config_dict, signature

  def _start_video_streams(self):
    sources = self.config.get('SOURCES', [])
    for src, idx in sources:
      name = src.get(ct.NAME)
      if not name:
        name = 'src_{}'.format(idx)
        src[ct.NAME] = name
      # end if
      if name in self._video_streams:
        continue
      try:
        _cls, _cfg, signature = self._discover_video_stream_class(src)
        video_stream = _cls(
          log=self.log,
          default_config=_cfg,
          **src,
        )
        video_stream.start()
        self._video_streams[name] = video_stream
        msg = "Started video stream '{}' using class '{}'".format(name, signature)
        self.P(msg)
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_NORMAL,
          msg=msg,
          info="{}".format(src),
          displayed=True,
        )
      except Exception as exc:
        msg = "Failed to start video stream '{}'".format(name)
        self.P(msg + ": {}".format(exc), color='r')
        self._create_notification(
          notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
          msg=msg,
          info=self.log.get_error_info(),
          displayed=True,
        )
        continue
      # end try-except
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