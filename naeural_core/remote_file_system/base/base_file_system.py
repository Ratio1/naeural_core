from naeural_core import DecentrAIObject
from naeural_core import Logger
import abc

_CONFIG = {
  'VALIDATION_RULES' : {

  }
}

class BaseFileSystem(DecentrAIObject):
  CONFIG = _CONFIG
  def __init__(self, log: Logger, signature, config, **kwargs):
    self.signature = signature
    self.config = config
    super(BaseFileSystem, self).__init__(log=log, **kwargs)
    return

  def startup(self):
    super().startup()
    self.config_data = self.config
    return

  @abc.abstractmethod
  def upload(self, file_path, target_path):
    # TODO: maybe remove abstract and create & run a thread with _upload (abstract) target
    # also: put run_threaded FLAG !!!! default=False (plugins WILL be threads)
    # in main_loop use run_threaded=True (needed not to block main loop)
    raise NotImplementedError

  @abc.abstractmethod
  def download(self, uri, local_file_path):
    raise NotImplementedError

  def _http_download(self, url, local_file_path):
    # TODO: maybe review the case with multiple files downloaded
    saved_files, _ = self.log.maybe_download(
      url=url, fn=local_file_path, target=None,
      print_progress=False,
      force_download=True
    )

    if len(saved_files) == 1:
      return saved_files[0]

    return

