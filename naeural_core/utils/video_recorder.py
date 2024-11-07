import cv2
import os

from naeural_core import DecentrAIObject
from naeural_core import Logger

class VideoRecorder(DecentrAIObject):
  """
  Class that will be initialized in specific plugins implementations (not base plugin!) for recording live input (frames)
  that causes errors in a stateful plugin.

  Steps for using:
  1. initilize
  2. call `maybe_start` and `write` whenever the input data comes
  3. call `stop` when the recording is done (in the most cases, when an Exception occurs)
  """

  def __init__(self, log : Logger, name, **kwargs):
    self._recording = None
    self._name = name

    self.__force_stopped = False
    super(VideoRecorder, self).__init__(log=log, prefix_log='[VREC]', **kwargs)
    return

  def startup(self):
    super().startup()
    return

  def maybe_start(self, height, width):
    if self._recording is not None:
      return

    fld = os.path.join(
      self.log.get_output_folder(),
      'recordings'
    )
    if not os.path.exists(fld):
      os.makedirs(fld)

    path = os.path.join(
      fld,
      '{}_{}.avi'.format(self.log.now_str(), self._name)
    )

    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    fps = 25
    out = cv2.VideoWriter(path, fourcc, fps, (width, height))
    self._recording = {
      'CODEC': out,
      'PATH': path
    }
    self.P('Started recording {} in {}'.format(self._name, path))
    return

  def stop(self):
    if self.__force_stopped:
      self.P("Recording {} was already force stopped".format(self._name))
      return

    self._recording['CODEC'].release()
    self.P('Stopped recording {}. Video: {}'.format(
      self._name,
      self._recording['PATH']
    ))

    return

  def write(self, frame):
    self._recording['CODEC'].write(frame)

    size_gb = os.path.getsize(self._recording['PATH']) / (2**30)
    thr_gb = 10.0

    if size_gb >= thr_gb:
      self.P("Force stop the recording as it occupies more than {}GB".format(thr_gb))
      self.stop()
      self.__force_stopped = True
    #endif
    return

