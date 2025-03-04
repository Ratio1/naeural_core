from naeural_core import constants as ct

from naeural_core import Logger
from ratio1 import _PluginsManagerMixin
from naeural_core import DecentrAIObject

class Manager(DecentrAIObject, _PluginsManagerMixin):

  def __init__(self, log : Logger, **kwargs):
    self._dct_subalterns = {}
    self.plugin_locations_cache = {}
    super(Manager, self).__init__(log=log, **kwargs)
    return
  
  def _create_notification(
    self, 
    notif, 
    msg, 
    info=None, 
    stream_name=None, 
    autocomplete_info=False, 
    notif_code=None, 
    notif_tag=None, 
    **kwargs
  ):
    kwargs.pop('ct', None)
    return super()._create_notification(
      notif=notif, notif_code=notif_code, msg=msg, 
      info=info, stream_name=stream_name, 
      autocomplete_info=autocomplete_info, 
      notif_tag=notif_tag, ct=ct, 
      **kwargs
    )
  
  def P(self, s, color=None, **kwargs):
    if color is None or (isinstance(color,str) and color[0] not in ['e', 'r']):
      color = ct.COLORS.MAIN
    super().P(s, prefix=False, color=color, **kwargs)
    return      

  def add_subaltern(self, subaltern_key, obj):
    self._dct_subalterns[subaltern_key] = obj

  def get_subaltern(self, subaltern_key):
    return self._dct_subalterns.get(subaltern_key)

  def get_all_subalterns(self):
    return self._dct_subalterns

  def print_all_subalterns(self):
    self.P("{} subalterns:".format(self.__class__.__name__))
    str_subalterns = [" * {} : {}".format(k, v) for k,v in self._dct_subalterns.items()]
    for s in str_subalterns:
      self.P(s)

    return

  def get_manager_notifications(self):
    return self.get_notifications()

  def get_subaltern_notifications(self, subaltern_key):
    subaltern = self._dct_subalterns[subaltern_key]
    if isinstance(subaltern, DecentrAIObject):
      return subaltern.get_notifications()

    return

  def get_all_subalterns_notifications(self):
    notifications = []
    for k in self._dct_subalterns:
      crt_notifications = self.get_subaltern_notifications(k) or []
      notifications += crt_notifications
    return notifications

  def _get_module_name_and_class(self, locations, name, suffix=None, verbose=1, safety_check=True, safe_locations=None, safe_imports=None):
    if safe_locations is None:
      self.P("  Warning: no safe location provided", color='r')
    self.P("Attempting to load {} plugin '{}'".format(
      self.__class__.__name__, name,
    ))
    if name.lower() in self.plugin_locations_cache:
      _module_name, _class_name, _cls_def, _config_dict = self.plugin_locations_cache[name.lower()]
      self.P(f'Attempting to load plugin {name} from cache.')
    else:
      _module_name, _class_name, _cls_def, _config_dict = super()._get_module_name_and_class(
        locations=locations,
        name=name,
        suffix=suffix,
        verbose=verbose,
        safety_check=safety_check,
        safe_locations=safe_locations,
        search_in_packages=ct.PLUGIN_SEARCH.SEARCH_IN_PACKAGES,
        safe_imports=safe_imports,
      )
      self.plugin_locations_cache[name.lower()] = _module_name, _class_name, _cls_def, _config_dict
    # endif name in cache

    if _cls_def is None and verbose >= 1:
      self._create_notification(
        notif=ct.STATUS_TYPE.STATUS_EXCEPTION,
        msg="Cannot understand plugin '{}'".format(name)
      )
    #endif

    return _module_name, _class_name, _cls_def, _config_dict
