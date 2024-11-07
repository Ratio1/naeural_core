"""
TODO: delete
"""
import os
from naeural_core import constants as ct


class _PersistenceSerializationMixin(object):
  def __init__(self):
    self.__persistence_fld = None
    super(_PersistenceSerializationMixin, self).__init__()
    return
  
  
  @property
  def _persistence_fld(self):
    return self.__persistence_fld
  
  
  @property
  def _cache_folder(self):
    return "" ## will save in data-root folder
  
  
  @property
  def plugin_id(self):
    """
    Returns the instance id of the current plugin.
    WARNING: This should be overwridden in the plugin class to return the correct id.

    Returns
    -------
    str
      the instance id.
      
    Example
    -------
      ```      
      instance_id = self.instance_id
      ```    
    """
    str_name = self.__class__.__name__
    str_name = self.sanitize_name(str_name)
    return str_name  


  def _maybe_setup_persistence_fld(self):
    if self.__persistence_fld is not None:
      return self.__persistence_fld       
    self.__persistence_fld = '{}/{}'.format(self._cache_folder, self.plugin_id.lower())
    full_path = os.path.join(self.log.get_data_folder(), self.__persistence_fld)
    os.makedirs(full_path, exist_ok=True)
    return self.__persistence_fld


  def _helper_persistence_serialization_save(self, obj, subfolder_path=None, verbose=True):
    self._maybe_setup_persistence_fld()
    if subfolder_path is None:
      subfolder_path = self._persistence_fld

    self.log.save_pickle_to_data(
      data=obj,
      fn='object.pickle',
      subfolder_path=subfolder_path,
      verbose=verbose
    )

    self.log.save_data_json(
      data_json={'SERIALIZATION_SIGNATURE' : self.cfg_serialization_signature},
      fname='metadata.json',
      subfolder_path=subfolder_path,
      verbose=verbose
    )
    return
    
  def _helper_persistence_serialization_load(self, subfolder_path=None, default=None, verbose=True):
    self._maybe_setup_persistence_fld()
    if subfolder_path is None:
      subfolder_path = self._persistence_fld

    dct_metadata = self.log.load_data_json(
      fname='metadata.json',
      subfolder_path=subfolder_path,
      verbose=verbose
    ) or {}

    loaded_serialization_signature = dct_metadata.get('SERIALIZATION_SIGNATURE', None)

    if self.cfg_serialization_signature is not None:
      if loaded_serialization_signature != self.cfg_serialization_signature:
        self.P("Loaded serialization signature differs from the current one", color='y')
        return default

    obj = self.log.load_pickle_from_data(
      fn='object.pickle',
      subfolder_path=subfolder_path,
      verbose=verbose
    ) or default

    return obj

  def _helper_persistence_serialization_update(self, update_callback, subfolder_path=None, verbose=True):
    self._maybe_setup_persistence_fld()
    if subfolder_path is None:
      subfolder_path = self._persistence_fld

    self.log.update_pickle_from_data(
      fn='object.pickle',
      update_callback=update_callback,
      subfolder_path=subfolder_path,
      verbose=verbose,
      force_update=True
    )
    self.log.save_data_json(
      data_json={'SERIALIZATION_SIGNATURE' : self.cfg_serialization_signature},
      fname='metadata.json',
      subfolder_path=subfolder_path,
      verbose=verbose
    )
    return
  
  def _helper_persistence_serialization_save_json(self, obj, subfolder_path=None, verbose=True):
    self._maybe_setup_persistence_fld()
    if subfolder_path is None:
      subfolder_path = self._persistence_fld

    self.log.save_data_json(
      data_json=obj,
      fname='data.json',
      subfolder_path=subfolder_path,
      verbose=verbose
    )
    return  
  
  def _helper_persistence_serialization_load_json(self, subfolder_path=None, default={}, verbose=True):
    self._maybe_setup_persistence_fld()
    if subfolder_path is None:
      subfolder_path = self._persistence_fld

    data = self.log.load_data_json(
      fname='data.json',
      subfolder_path=subfolder_path,
      verbose=verbose
    ) or default

    return data  
  
  
  
  def persistence_serialization_save(self, obj, verbose=True):
    self._helper_persistence_serialization_save(
      obj=obj,
      verbose=verbose
    )
    return

  def persistence_serialization_save_to_serving(self, obj, name, verbose=True):
    subfolder_path = os.path.join(ct.CACHE_SERVING, name.lower())
    self._helper_persistence_serialization_save(
      obj=obj,
      subfolder_path=subfolder_path,
      verbose=verbose
    )
    return

  def persistence_serialization_load(self, default=None, verbose=True):
    return self._helper_persistence_serialization_load(
      default=default,
      verbose=verbose
    )

  def persistence_serialization_load_from_serving(self, name, default=None, verbose=True):
    subfolder_path = os.path.join(ct.CACHE_SERVING, name.lower())
    return self._helper_persistence_serialization_load(
      subfolder_path=subfolder_path,
      default=default,
      verbose=verbose
    )

  def persistence_serialization_update(self, update_callback, verbose=True):
    self._helper_persistence_serialization_update(
      update_callback=update_callback,
      verbose=verbose
    )
    return

  def persistence_serialization_update_serving(self, update_callback, name, verbose=True):
    subfolder_path = os.path.join(ct.CACHE_SERVING, name.lower())
    self._helper_persistence_serialization_update(
      update_callback=update_callback,
      subfolder_path=subfolder_path,
      verbose=verbose
    )
    return
  
  
  def cacheapi_save_pickle(self, obj, verbose=True):
    """
    Save object to the current plugin instance cache folder

    Parameters
    ----------
    obj : any
        the picklable object to be saved
    verbose : bool, optional
        show information during process, by default True
    """
    self.persistence_serialization_save(obj, verbose=verbose)
    return
  
  
  def cacheapi_load_pickle(self, default=None, verbose=True):
    """
    Loads object from the current plugin instance cache folder

    Parameters
    ----------
    default : any, optional
        default value, by default None
    verbose : bool, optional
         show information during process, by default True

    Returns
    -------
    any
        the loaded object
    """
    return self.persistence_serialization_load(default=default, verbose=verbose)
  
  
  
  def cacheapi_save_json(self, obj, verbose=False):
    """
    Save object json to the current plugin instance cache folder

    Parameters
    ----------
    obj : any
        the json-able object to be saved
        
    verbose : bool, optional
        show information during process, by default True
    """
    self._helper_persistence_serialization_save_json(obj, verbose=verbose)
    return
  
  def cacheapi_load_json(self, default={}, verbose=False):
    """
    Loads object json from the current plugin instance cache folder

    Parameters
    ----------
    default : any, optional
        default value, by default {}
    verbose : bool, optional
         show information during process, by default True

    Returns
    -------
    any
        the loaded object
    """
    return self._helper_persistence_serialization_load_json(default=default, verbose=verbose)
  




if __name__ == '__main__':

  from naeural_core import Logger
  from naeural_core import DecentrAIObject

  log = Logger(lib_name='SER', base_folder='.', app_folder='_local_cache', TF_KERAS=False)

  class Base(DecentrAIObject, _PersistenceSerializationMixin):

    def __init__(self, load_prev_ser, ser_sign=None, **kwargs):
      self._load_prev_ser = load_prev_ser
      self._ser_sign = ser_sign
      self._instance_config = None
      super(Base, self).__init__(**kwargs)

    @property
    def unique_identification(self):
      return self._instance_config['STREAM'], self._instance_config['SIGNATURE'], self._instance_config['INSTANCE_ID']

    def startup(self):
      super().startup()
      self._instance_config = {
        'STREAM' : 'buc',
        'SIGNATURE' : 'camera_tampering_02',
        'INSTANCE_ID' : 'buc_ct_001',
        'LOAD_PREVIOUS_SERIALIZATION' : self._load_prev_ser,
        'SERIALIZATION_SIGNATURE' : self._ser_sign
      }
      self.P("In base startup")


  class Plu(Base):

    def __init__(self, **kwargs):
      super(Plu, self).__init__(**kwargs)
      self.obj = self.persistence_serialization_load()
      return

    def startup(self):
      super().startup()


  ##### Mode I - load if exists
  p = Plu(log=log, load_prev_ser=True, ser_sign=None)
  p.persistence_serialization_save({'a' : 1, 'b' : 2})
  log.P("\n")

  p = Plu(log=log, load_prev_ser=True, ser_sign=None)
  p.persistence_serialization_save({'a' : 100, 'b' : 100})
  log.P("\n")

  p = Plu(log=log, load_prev_ser=True, ser_sign='2123')
  p.persistence_serialization_save({'c': 1, 'd' : 2})
  log.P("\n")

  p = Plu(log=log, load_prev_ser=True, ser_sign='2123')
  log.P("\n")

  p = Plu(log=log, load_prev_ser=True, ser_sign=None)
  p.persistence_serialization_save({'c' : 100, 'd': 100})
  log.P("\n")

  p = Plu(log=log, load_prev_ser=True, ser_sign='2123')
  log.P("\n")

  p = Plu(log=log, load_prev_ser=True, ser_sign=None)
  log.P("\n")
