"""
Unified Video Stream with dynamic backend selection

This plugin acts like any other plugin, but it dynamically selects the backend.

This means that the user can create this plugin, specify the desired backend in the config,
as well as the config for the backend, and the plugin will automatically select the correct
backend and initialize it. After this, this plugin will act like the backend plugin.
"""

from ratio1 import _PluginsManagerMixin

from naeural_core import DecentrAIObject
from naeural_core import constants as ct
from naeural_core.data.base import DataCaptureThread

_CONFIG = {
  **DataCaptureThread.CONFIG,

  'OVERWRITE_DEFAULT_PLUGIN_CONFIG': False,
  'DEFAULT_PLUGINS': {
    'VIDEO_LOSS_01': [{
    }],
  },

  'BACKEND': 'ffmpeg',

  'VALIDATION_RULES': {
    **DataCaptureThread.CONFIG['VALIDATION_RULES'],

  },
}


class VideoStreamDataCapture(DecentrAIObject, _PluginsManagerMixin):
  CONFIG = _CONFIG

  def __init__(self, *args, **kwargs):
    super(VideoStreamDataCapture, self).__init__(*args, **kwargs)

    default_config = kwargs.pop('default_config', {})
    upstream_config = kwargs.get('upstream_config', {})
    str_backend = upstream_config.get('BACKEND', default_config.get('BACKEND'))

    if str_backend is None:
      raise ValueError("Backend not specified")

    if not isinstance(str_backend, str):
      raise ValueError("Backend must be a string")

    signature = 'VIDEO_STREAM_' + str_backend.upper()

    _module_name, _class_name, _cls_def, _config_dict = self._get_module_name_and_class(
      locations=ct.PLUGIN_SEARCH.LOC_DATA_ACQUISITION_PLUGINS,
      name=signature,
      suffix=ct.PLUGIN_SEARCH.SUFFIX_DATA_ACQUISITION_PLUGINS,
      search_in_packages=ct.PLUGIN_SEARCH.SEARCH_IN_PACKAGES,
      safe_locations=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_PLUGINS,
      safe_imports=ct.PLUGIN_SEARCH.SAFE_LOC_DATA_ACQUISITION_IMPORTS,
      safety_check=True,
    )

    kwargs['default_config'] = _config_dict
    self.backend = _cls_def(*args, **kwargs)
    return

  def __getattr__(self, name):
    # if the backend is not initialized, we return the attribute from this class
    if 'backend' not in self.__dict__:
      if name not in self.__dict__:
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
      return self.__dict__[name]
    return getattr(self.backend, name)

  def __setattr__(self, name, value):
    # If the attribute is backend, set it directly on the instance
    if 'backend' not in self.__dict__:
      super().__setattr__(name, value)
    else:
      # Otherwise, set the attribute on the backend
      setattr(self.backend, name, value)
