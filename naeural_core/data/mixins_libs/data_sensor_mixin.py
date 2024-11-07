import importlib
import inspect

from naeural_core import constants as ct

from naeural_core.data_structures import MetadataObject

class _DataSensorMixin(object):
  def __init__(self):
    self._data_helper_name = None
    self._data_helper_params = None
    self._training_data_sent = False
    self.sensor = None
    
    super(_DataSensorMixin, self).__init__()
    return

  def _get_sensor_helper_class(self, module_name):
    full_module_name = ct.SENSORS_LOCATION + '.' + module_name
    module = importlib.import_module(full_module_name)
    classes = inspect.getmembers(module, inspect.isclass)
    _class = None
    for _cls in classes:
      if _cls[0].lower() == module_name.lower().replace('_',''):
        _class = _cls[1]
    return _class


  def _start_sensor(self):
    self._data_helper_name = self.cfg_stream_config_metadata[ct.DATA_HELPER_NAME]
    self._data_helper_params = self.cfg_stream_config_metadata[ct.DATA_HELPER_PARAMS]
    if not isinstance(self._data_helper_params, dict):
      raise ValueError("DATA_HELPER_PARAMS must be a dictionary of params for the Sensor class!")
    
    _class = self._get_sensor_helper_class(self._data_helper_name)
    obj = _class(**self._data_helper_params)
    self.sensor = obj
    self.P("Started sensor '{}':".format(obj.__class__.__name__), color='y')
    for k,v in vars(obj).items():
      self.P("  {} : {}".format(
        k,v if isinstance(v, (int, str)) else type(v)),
        color='y')
    self._sensor_metadata = MetadataObject(
      dataframe_count=self.sensor.get_datastream_len(),
      dataframe_current=None,
    )
    return  
  
  def _get_sensor_data(self):
    _train, _obs = None, None
    if not self._training_data_sent and self.sensor.has_training_data():
      self._train_data = self.sensor.get_train_data()
      _train = self._train_data
      self._training_data_sent = True
    else:
      _obs = self.sensor.get_observation()
    _count = self.sensor.get_position()
    return _obs, _train, _count