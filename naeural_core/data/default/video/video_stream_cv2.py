"""
TODO: Refactor in order to have a abstract video stream than can:
  - resize & cropping
  - stream splitting (virtual stream) so we can spawn multiple streams from same one
  - unify all video stream under this abstract video stream

"""
import numpy as np
import cv2
import traceback
from naeural_core import constants as ct
from time import sleep

from naeural_core.data.base import DataCaptureThread
from naeural_core.data.mixins_libs import _VideoConfigMixin

_CONFIG = {
  **DataCaptureThread.CONFIG,

  "MAX_RETRIES": 2,

  "AMD_TARGET_DPS": 0,
  "SIMULATE_AMD": False,

  "CAP_PROP_BUFFERSIZE": 0,

  "USE_FFMPEG": False,

  "CONFIGURED_H": -1,
  "CONFIGURED_W": -1,


  'VALIDATION_RULES': {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],

    "AMD_TARGET_DPS": {
      "DESCRIPTION": "For AMD architectures (as of 2023-04-28 with no plans to use also on Intel) forces number of grabs per iteration based on the target required DPS. Set this to 3 for example to (hopefully) get 3 FPS on a 15 FPS camera",
      "TYPE": "int"
    }
  },
}


class VideoStreamCv2DataCapture(DataCaptureThread, _VideoConfigMixin):
  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self._capture = None
    self._crt_frame = 0
    self.__configured_size_error = False

    super(VideoStreamCv2DataCapture, self).__init__(**kwargs)
    return

  def startup(self):
    super().startup()
    self._metadata.update(
      fps=None,
      frame_h=None,
      frame_w=None,
      frame_count=None,
      frame_current=None,
    )
    return

  @property
  def is_hw_error(self):
    return self.__configured_size_error

  def is_intel(self):
    if self.cfg_simulate_amd:
      result = False
    else:
      proc = self.log.get_processor_platform()
      if proc is not None:
        result = "intel" in proc.lower()
      else:
        result = False
    return result

  def _release(self):
    self._release_capture()
    del self._capture
    return

  def _release_capture(self):
    self._capture.release()
    return

  def _init(self):
    self._force_video_stream_config()
    return

  def _open_stream(self):

    if self.cfg_use_ffmpeg:
      self.P("    Opening capture with CAP_FFMPEG...")
      cap = cv2.VideoCapture(self.cfg_url, cv2.CAP_FFMPEG)
    else:
      cap = cv2.VideoCapture(self.cfg_url)

    if self.cfg_cap_prop_buffersize > 0:
      self.P("    Setting cv2.CAP_PROP_BUFFERSIZE={}".format(self.cfg_cap_prop_buffersize))
      cap.set(cv2.CAP_PROP_BUFFERSIZE, self.cfg_cap_prop_buffersize)
    return cap

  def _on_config_changed(self):
    if self.cfg_url != self.last_url:
      self.has_connection = False  # forces reconnection
      self.P("RTSP URI change detected from {} to {}, reconnecting...".format(
        self.last_url, self.cfg_url,
      )
      )
    return

  def _maybe_reconnect(self):
    if self.has_connection:
      return

    self.last_url = self.cfg_url  # a cache of current url
    self.has_connection = False
    nr_retry = 0
    if self.nr_connection_issues == 0:
      msg = "Connecting to url:"
      color = 'g'
    else:
      msg = "Reconnecting ({}) to url:".format(self.nr_connection_issues)
      color = 'r'
    self.P("{} {} (deq size: {})".format(msg, self.cfg_url, self._deque.maxlen), color=color)
    str_e = None
    while nr_retry <= self.cfg_max_retries:
      self.sleep(1)
      nr_retry += 1
      self.nr_connection_issues += 1
      try:
        if self._capture:
          self._release_capture()
        self.P("  Connection retry {}/{} of connect session {}, total retries {}:".format(
          nr_retry, self.cfg_max_retries, self.nr_connection_issues // self.cfg_max_retries, self.nr_connection_issues,
        ))
        self._capture = self._open_stream()

        if self._capture is None or not self._capture.isOpened():
          self.P("    Capture failed.", color='r')
          continue

        has_frame, frame = self._capture.read()
        if not has_frame:
          self.P("    Capture read failed.", color='r')
          continue

        if self.cfg_nr_skip_frames:
          self._capture.set(cv2.CAP_PROP_POS_FRAMES, self.cfg_nr_skip_frames)

        _fps = 20
        try:
          _fps = int(self._capture.get(cv2.CAP_PROP_FPS))
        except:
          pass

        fps = np.clip(_fps, 15, 40)
        buff_size = self._capture.get(cv2.CAP_PROP_BUFFERSIZE)
        height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_count = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_count = max(0, frame_count)
        self.P("""Video stream configuration:
          AMD Simulate/FPS: {}/{}
          FPS={} (reported: {})
          Shape (HxW): {}
          Frame count: {}
          Buff size:   {} ({})""".format(
          self.cfg_simulate_amd, self.cfg_amd_target_dps,
          fps, _fps, (height, width), frame_count, buff_size, buff_size.__class__.__name__,
        ))

        self._metadata.fps = fps
        self._metadata.frame_h = height
        self._metadata.frame_w = width
        self._metadata.buffersize = buff_size

        # universal video stream code
        self._maybe_configure_frame_crop_resize(
          height=height,
          width=width,
        )
        # end universal video stream code

        self._metadata.frame_count = frame_count
        self.has_connection = True
      except:
        str_e = traceback.format_exc()
        self.P('`_maybe_reconnect` exception: {}'.format(str_e), color='r')
      # end try-except

      if self.has_connection:
        self.P("Connection seems to be established.")
        break
    # endwhile

    # random sleep time to de-sync broken streams
    sleep_time = np.random.randint(self.cfg_sleep_time_on_error, self.cfg_sleep_time_on_error * 3)

    if self.has_connection:
      self.reset_received_first_data()
      msg = "Video DCT '{}' successfully connected. Overall {} reconnects.".format(
        self.cfg_name,
        self.nr_connection_issues,
      )
      notification_type = ct.STATUS_TYPE.STATUS_NORMAL
      color = 'g'
    else:
      msg = "Abnormal functioning of Video DCT '{}' failed after {} retries, overall {} reconnects. Sleeping DCT for {:.1f}s".format(
        self.cfg_name, self.cfg_max_retries,
        self.nr_connection_issues,
        sleep_time,
      )
      notification_type = ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING
      color = 'e'
    # endif
    self.P(msg, color=color)
    self._create_notification(
      notif=notification_type,
      msg=msg,
      info=str_e,
      video_stream_info=self.deepcopy(self._metadata.__dict__),
      displayed=True,
    )

    if not self.has_connection:
      # execute sleep after notification delivery
      sleep(sleep_time)
    return

  def _get_stream_fps_max_thr(self):
    return self._metadata.fps * 0.6

  def _get_nr_grabs(self):
    if self.cfg_amd_target_dps > 0:
      n_grabs = int(np.ceil(self.cfg_cap_resolution / self.cfg_amd_target_dps))
    else:
      n_grabs = int(np.ceil(self._metadata.fps / self.cap_resolution)) + 1
    return n_grabs

  def _recalc_cap_resolution(self):
    # _heuristic_cap_resolution recalc for AMD (so far)
    WARM_UP_SEC = 60
    MAX_TIME = 2
    if self.cap_resolution >= self._get_stream_fps_max_thr():
      nr_streams = self.get_nr_parallel_captures() + 5
      read_time = self.get_timer('cv2_read')['MEAN']
      read_count = self.get_timer('cv2_read')['COUNT']
      if read_count > (self.cap_resolution * WARM_UP_SEC):
        total_read_time = read_time * self.cap_resolution * nr_streams
        # TODO: change below to more progressive & adaptive method
        if total_read_time >= MAX_TIME:
          configured_cap_res = self.get_cap_or_forced_resolution()
          self._heuristic_cap_resolution = round(MAX_TIME / (read_time * nr_streams), 1)
          self.P("Total cv2r time @{} load: {:.4f}s({:.4f}s/strm/itr) exceding {}s. Forcing cap {} @ {:.1f} dps, nr_grabs={} (stream fps: {} cap dps:{}/{})".format(
            nr_streams, total_read_time, read_time, MAX_TIME,
            self.cfg_name, self._heuristic_cap_resolution,
            self._get_nr_grabs(),
            self._metadata.fps,
            self.cap_resolution,
            configured_cap_res,
          ),
            color='m')
    return

  def _capture_read(self):
    has_frame, frame = False, None

    # COMMENT: a naive approach will not work in real-life multi-stream scenarios
    # has_frame, frame = self._capture.read()
    # END COMMENT: do not remove this

    if (self.cap_resolution >= self._get_stream_fps_max_thr() and self.cfg_amd_target_dps == 0) or self.is_intel():
      self.start_timer('cv2_read')  # DO NOT REMOVE - needs to "wrap" custom dps reads for _recalc_cap_resolution
      self.start_timer('cv2_read_dps{}'.format(self.cap_resolution))
      has_frame, frame = self._capture.read()
      self.end_timer('cv2_read_dps{}'.format(self.cap_resolution))
      self.end_timer('cv2_read')
    else:
      self.start_timer('cv2_grab_retrv_dps{}'.format(self.cap_resolution))
      nr_grabs = self._get_nr_grabs()
      self.start_timer('cv2_grab_x{}'.format(nr_grabs))
      for _ in range(nr_grabs):
        self.start_timer('cv2_grab')
        try:
          _ = self._capture.grab()
        except:
          break
        self.end_timer('cv2_grab')
      # endfor all grabs
      self.end_timer('cv2_grab_x{}'.format(nr_grabs))
      self.start_timer('cv2_retrieve')
      has_frame, frame = self._capture.retrieve()
      self.end_timer('cv2_retrieve')
      self.end_timer('cv2_grab_retrv_dps{}'.format(self.cap_resolution))
    if not self.is_intel() and self.cfg_amd_target_dps == 0:
      self._recalc_cap_resolution()
    return has_frame, frame

  def _run_data_aquisition_step(self):
    if self.has_connection and self._capture.isOpened():
      has_frame, frame = self._capture_read()
      if has_frame:

        # universal video stream code
        frame = self._maybe_resize_crop(frame)
        # end universal video stream code

        frame = np.ascontiguousarray(frame[:, :, ::-1])  # transform to rgb & contiguous

        # check for HW issues on some nvr/dvr
        if ((self.cfg_configured_h > 0 and self.cfg_configured_h != frame.shape[0])
            or
                (self.cfg_configured_w > 0 and self.cfg_configured_w != frame.shape[1])):
          if not self.__configured_size_error:
            self.__configured_size_error = True
            msg = "ERROR: Video stream configured at {}x{} is decoded at {}x{}".format(
              self.cfg_configured_h, self.cfg_configured_w,
              frame.shape[0], frame.shape[1],
            )
            self._create_notification(
              notif=ct.STATUS_TYPE.STATUS_ABNORMAL_FUNCTIONING,
              stream_name=self.cfg_name,
              msg=msg,
            )
          # endif requires exception notification
        # endif maybe we know specific HW for the video stream

        # metadata
        self._crt_frame += 1
        self._metadata.frame_current = self._crt_frame

        self._add_inputs(
          [
            self._new_input(img=frame, metadata=self._metadata.__dict__.copy()),
          ]
        )
      else:
        self.has_finished_acquisition = True
    else:
      self.P('Capture seems to be closed. Setting `has_connection=False`', color='r')
      self.has_connection = False
    return
