import abc
import json

from naeural_core import DecentrAIObject
from naeural_core import Logger
from naeural_core import constants as ct


_CONFIG = {
  'VALIDATION_RULES' : {

  }
}

class BaseConfigRetrievingPlugin(DecentrAIObject):

  CONFIG = _CONFIG
  __metaclass__ = abc.ABCMeta

  def __init__(self, log : Logger, config, **kwargs):
    self.config = config
    super(BaseConfigRetrievingPlugin, self).__init__(log=log, **kwargs)
    return
  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.MAIN
    super().P(s, prefix=False, color=color, **kwargs)
    return    

  def startup(self):
    super().startup()
    self.config_data = self.config
    return

  def retrieve(self, app_config_endpoint=None, streams_configs_endpoint=None):
    config_app, lst_config_streams = None, None
    dct_config_streams = None
    if app_config_endpoint is not None:
      self.log.start_timer('get_app_configuration')
      try:
        self.P("  Running `_get_app_configuration`...")
        config_app = self._get_app_configuration(app_config_endpoint)
        self.P("App config - Successfully retrieved from endpoint '{}'".format(app_config_endpoint), color='g')
      except Exception as e:
        msg = "App config - ERROR! Could not retrieve from endpoint '{}'".format(app_config_endpoint)
        self.P(msg, color='r')
        self._create_notification(notif=ct.STATUS_TYPE.STATUS_EXCEPTION, msg=msg, autocomplete_info=True)
      #end try-except
      self.log.stop_timer('get_app_configuration', skip_first_timing=False)
    #endif

    if streams_configs_endpoint is not None:
      try:
        self.log.start_timer('get_streams_configurations')
        lst_config_streams = self._get_streams_configurations(streams_configs_endpoint)
        self.log.stop_timer('get_streams_configurations', skip_first_timing=False)
        self.P("Streams configs - Successfully retrieved from endpoint '{}'".format(streams_configs_endpoint), color='g')
      except Exception as e:
        msg = "Streams configs - ERROR! Could not retrieve from endpoint '{}'".format(streams_configs_endpoint)
        self.P(msg, color='r')
        self._create_notification(notif=ct.STATUS_TYPE.STATUS_EXCEPTION, msg=msg, autocomplete_info=True)
      #end try-except

      if lst_config_streams:
        good_streams, good_streams_fnames = self._keep_good_streams(lst_config_streams)
        if len(good_streams_fnames) > 0:
          self.P("  New streams: {}".format("; ".join(good_streams_fnames)), color='g')
        lst_config_streams = good_streams
        dct_config_streams = {s['NAME'] : s for s in lst_config_streams}
      #endif
    #endif

    return config_app, dct_config_streams

  def connect(self, **kwargs):
    try:
      self._connect(**kwargs)
      self.P("Config retriever {} connection succeeded".format(self.__class__.__name__), color='g')
    except Exception as e:
      msg = "Config retriever '{}' connection failed".format(self.__class__.__name__)
      self.P(msg, color='r')
      self._create_notification(notif=ct.STATUS_TYPE.STATUS_EXCEPTION, msg=msg, autocomplete_info=True)
    #end try-except

    return



  def _keep_good_streams(self, lst_config_streams):
    good_streams, bad_streams = [], []
    fnames = []
    for i,config_s in enumerate(lst_config_streams):
      name = config_s.get('NAME', None)
      if name is None:
        bad_streams.append(config_s)
      else:
        good_streams.append(config_s)
        fnames.append(name + '.txt')
    #endfor

    if len(bad_streams) > 0:
      msg = "ERROR! {}/{} retrieved streams are bad configured - have no 'NAME'. Therefore they will not be started".format(
        len(bad_streams), len(lst_config_streams)
      )
      info = json.dumps(bad_streams)
      self.P(msg + "\n" + info, color='r')
      self._create_notification(notif=ct.STATUS_TYPE.STATUS_EXCEPTION, msg=msg, info=info)
    #endif

    return good_streams, fnames



  ### START - MUST BE OVERWRITTEN
  def _connect(self, **kwargs):
    """
    This method should be implemented when the plugin has to create a connection to an API / DB etc.

    Parameters:
    ----------
    kwargs:
      Connection arguments

    Returns:
    --------
    None
    """
    raise NotImplementedError()
  ### END - MUST BE OVERWRITTEN

  ### START - MUST BE IMPLEMENTED
  @abc.abstractmethod
  def _get_app_configuration(self, endpoint):
    """
    Parameters:
    -----------
    endpoint
      The endpoint from where the app configuration is retrieved.
      The type of this parameter depends on the plugin

    Returns:
    --------
    config_app : dict
      The application configuration that will be saved on the local device data storage (_local_cache/_data).
      The reason why we return dict is that the public function `retrieve` handles saving.
    """
    raise NotImplementedError()


  @abc.abstractmethod
  def _get_streams_configurations(self, endpoint):
    """
    Parameters:
    -----------
    endpoint
      The endpoint from where the streams configurations are retrieved.
      The type of this parameter depends on the plugin

    Returns:
    --------
    lst_config_streams : List[dict]
      A list containing the streams configurations that will be saved on the local device data storage  (_local_cache/_data).
      The reason why we return List[string] is that the public function `retrieve` handles saving.
    """
    raise NotImplementedError()


  ### END - MUST BE IMPLEMENTED
