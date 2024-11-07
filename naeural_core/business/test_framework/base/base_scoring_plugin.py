import abc
import os
import uuid
from naeural_core import DecentrAIObject

class BaseScoringPlugin(DecentrAIObject):

  def __init__(self, log, y_true_src, owner=None, **kwargs):
    self.y_true = None
    self.payload = None
    self.exceptions = None
    self.max_score = None
    self.score_per_obs = None
    self.config = None
    self.owner = owner

    self._y_true_src = y_true_src
    super(BaseScoringPlugin, self).__init__(log=log, **kwargs)
    return

  def startup(self):
    super().startup()
    self.y_true = self._get_y_true(self._y_true_src)
    return

  def _get_y_true(self, y_true_src):
    y_true = None
    delete_file = False
    file_path = None

    if isinstance(y_true_src, str):
      if os.path.isfile(y_true_src):
        file_path = y_true_src
      elif y_true_src.startswith('http'):
        saved_files, _ = self.log.maybe_download(
          url=y_true_src,
          fn='tmp-{}.json'.format(uuid.uuid4()),
          target='output'
        )
        file_path = saved_files[0]
        delete_file = True
      #endif
    else:
      y_true = y_true_src
    # endif

    if file_path is not None:
      try:
        y_true = self.log.load_json(fname=file_path, folder=None)
      except Exception as e:
        self.P("Could not load y_true json from {}:\n{}".format(file_path, e))

      if delete_file:
        os.remove(file_path)
    # endif
    return y_true

  @property
  def cfg_max_score(self):
    return self.config.get('MAX_SCORE', None)

  @property
  def cfg_score_per_obs(self):
    return self.config.get('SCORE_PER_OBS', 5)

  def _refresh(self, payload, config=None):
    self.payload = payload
    self.y_hat = self.payload["Y_HAT"]
    self.exceptions = []
    self.config = config or {}

    self.max_score = self.cfg_max_score

    if self.max_score is not None:
      self.score_per_obs = self.max_score / len(self.y_true)
    else:
      self.score_per_obs = self.cfg_score_per_obs
      self.max_score = self.score_per_obs * len(self.y_true)
    # endif
    return

  @abc.abstractmethod
  def _scoring(self):
    raise NotImplementedError()

  def _time_penalization(self, movie_seconds, processing_secondss):
    thr_1 = 0.02 * movie_seconds
    thr_2 = 0.1 * movie_seconds

    diff = abs(processing_secondss - movie_seconds)

    if diff < thr_1:
      return 0
    elif thr_1 <= diff < thr_2:
      return -5
    else:
      return -100


  def score(self, payload, config=None):
    self._refresh(payload=payload, config=config)

    try:
      s = self._scoring()
    except Exception as e:
      s = None
      msg = "Could not score due to the following error:\n{}".format(e)
      self.exceptions.append(msg)
      self.P(msg, color='r')

    movie_seconds = payload.get('MOVIE_SECONDS', None)
    processing_seconds = payload.get('PROCESSING_SECONDS', None)
    time_penalization = None

    if movie_seconds is not None and processing_seconds is not None:
      time_penalization = self._time_penalization(
        movie_seconds,
        processing_seconds
      )

    return s, time_penalization
