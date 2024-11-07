import json
import numpy as np
from datetime import datetime

class AbstractSensor:
  def __init__(self, **kwargs):
    self._pos = 0
    self._setup_connection(**kwargs)
    return
  
  def _setup_connection(self, **kwargs):
    raise NotImplementedError()
  
  def _get_single_observation(self):
    raise NotImplementedError()
    
  def _has_training_data(self):
    raise NotImplementedError()
    
  def _get_training_data(self):
    raise NotImplementedError()
    
  def release(self):
    return
  
  def has_training_data(self):
    has_data = self._has_training_data()
    return has_data

  def maybe_reconnect(self):
    return True
  
  def get_observation(self, as_json=False):
    data = self._get_single_observation()
    if as_json:
      if isinstance(data, np.ndarray):
        data = data.tolist()
      elif isinstance(data, dict):
        for k in data:
          if isinstance(data[k], np.ndarray):
            data[k] = data[k].tolist()
          elif isinstance(data[k], datetime):
            data[k] = str(data[k])            
      data = json.dumps(data, indent=4)
    return data
  
  def get_train_data(self):
    if self._has_training_data():
      data = self._get_training_data()
      return data
    return None
  
  def get_training_data(self):
    return self.get_train_data()
  
  
  def get_datastream_len(self):
    return vars(self).get('_datastream_len', None)
  
  def get_position(self):
    return self._pos
   
  
  def __repr__(self):
    str_res = "{}:\n{}".format(
      self.__class__.__name__,
      self.get_observation(as_json=True)
      )
    return str_res