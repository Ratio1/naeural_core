import os
import inspect
import importlib

from PyE2 import BaseDecentrAIObject
class Benchmarker(BaseDecentrAIObject):
  def __init__(self, config, **kwargs):
    self.custom_models_list = []
    
    super().__init__(**kwargs)
    self.read_config(config)
    # self.load_default_models()
    self.load_custom_models()
    self.load_datasets()
    self.load_default_metrics()
    self.load_custom_metrics()
    pass
  
  def read_config(self, config):
    if type(config) == str:
      self.config_dict = self.log.load_json(config)
    elif type(config) == dict:
      self.config_dict = config
    else:
      self.log.P('Invalid config type', c = 'red')
    return
  
  def load_default_models(self):
    for model_path in self.config_dict['MODELS_PATHS']:
      if os.path.isdir(model_path):
        self.load_models_from_dir(model_path)
      elif os.path.isfile(model_path):
        if model_path[-3:] == '.pb':
          self.load_pb_model(model_path)
        if model_path[-3:] == '.h5':
          self.load_h5_model(model_path)
    return
  
  def load_custom_models(self):
    for model_name, model_file in self.config_dict['CUSTOM_MODELS'].items():
      if model_file[-3:] == '.py':
        model_file = model_file[:-3]
      model_file = model_file.replace('/', '.')
      module = importlib.import_module(model_file, package='.')
      classes = inspect.getmembers(module, inspect.isclass)
      _cls = None
      for obj in classes:
        if obj[0] == model_name:
          _cls = obj[1]
          model = _cls(log=self.log)
      self.custom_models_list.append(model)
      
    return
      
  
  def load_datasets(self):
    pass
  
  def load_default_metrics(self):
    pass
  
  def load_custom_metrics(self):
    pass
  
  def benchmark(self):
    pass
  
  def save_results(self):
    pass
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  