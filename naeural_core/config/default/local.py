import os
from naeural_core import Logger
from naeural_core.config.base import BaseConfigRetrievingPlugin

class _LocalConstants:
  PATH = 'PATH'
  IS_RELATIVE_PATH = 'IS_RELATIVE_PATH'

_CONFIG = {
  **BaseConfigRetrievingPlugin.CONFIG,
  'VALIDATION_RULES': {
    **BaseConfigRetrievingPlugin.CONFIG['VALIDATION_RULES'],
  },
}

class LocalConfigRetriever(BaseConfigRetrievingPlugin):
  CONFIG = _CONFIG
  def __init__(self, log : Logger, **kwargs):
    super(LocalConfigRetriever, self).__init__(log=log, prefix_log='[LocalCR]', **kwargs)
    return
  
  def _connect(self, **kwargs):
    self.P("  'Fake' connect - locally accessing file...")
    return
  

  def __create_full_path(self, endpoint):
    if isinstance(endpoint, dict):
      path, is_relative_path = endpoint['PATH'], endpoint['IS_RELATIVE_PATH']
    elif isinstance(endpoint, str):
      path = endpoint
      is_relative_path = False
    else:
      raise ValueError('Unknown endpoint type. Supported types: dict, str')
    if is_relative_path:
      path = os.path.join(self.log.get_base_folder(), path)
    return path

  def _get_app_configuration(self, endpoint):
    path = self.__create_full_path(endpoint)
    config_app = self.log.load_json(path, folder=None, verbose=True)
    return config_app

  def _get_streams_configurations(self, endpoint):
    assert isinstance(endpoint, dict), "`endpoint` must be dict, received({}): {}".format(
      type(endpoint), endpoint,
    )
    path = self.__create_full_path(endpoint)
    lst_streams = [self.log.load_json(os.path.join(path, fn), folder=None, verbose=False) for fn in os.listdir(path)]
    return lst_streams
