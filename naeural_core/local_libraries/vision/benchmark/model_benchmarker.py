#global dependencies
import os
import inspect  
import importlib
import traceback
import pandas as pd

#local dependencies
from naeural_core import DecentrAIObject

class ModelBenchmarker(DecentrAIObject):
  def __init__(self, log, module_name, class_name, batch_sizes=None, **kwargs):
    super().__init__(log=log, **kwargs)
    assert isinstance(module_name, str), 'Please provide path to module'
    assert isinstance(class_name, str), 'Please provide name of class'
    module_name = module_name.replace('/', '.')
    self._module_name = module_name
    self._class_name = class_name    
    self._kwargs = kwargs
    
    self._batch_sizes = [2**p for p in range(7)]
    if isinstance(batch_sizes, list):
      assert len(batch_sizes) > 0, 'You specified an empty batch sizes list'
      self._batch_sizes = batch_sizes
    return
  
  def startup(self):
    super().startup()    
    self._info = {
      'STEP': [],
      'TOTAL_GB': [],
      'USED_GB': [],
      'TIME': []
      }
    self._cls = None
    return
  
  def _add_info(self, step_name):
    total_gpu_mem = ''
    allocated_gpu_mem = ''
    try:
      gpu_info = self.log.gpu_info()
      total_gpu_mem = gpu_info[0]['TOTAL_MEM']
      allocated_gpu_mem = gpu_info[0]['ALLOCATED_MEM']
    except:
      pass
    timer = self.log.get_timer(step_name)
    total_time = timer.get('END', 0) - timer.get('START', 0)
    
    self._info['STEP'].append('{}_{}'.format(self._class_name, step_name))
    self._info['TOTAL_GB'].append(total_gpu_mem)
    self._info['USED_GB'].append(allocated_gpu_mem)
    self._info['TIME'].append(total_time)
    return
  
  def _start(self):
    step_name = 'START'
    self.log.start_timer(step_name)    
    #no op, just take a snapshot of system status
    self.log.stop_timer(step_name)    
    self._add_info(step_name)
    return
  
  def _import(self):
    step_name = 'IMPORT'
    self.log.start_timer(step_name) 
    _cls = None
    try:
      module = importlib.import_module(self._module_name)
      classes = inspect.getmembers(module, inspect.isclass)
      for obj in classes:
        if obj[0] == self._class_name:
          _cls = obj[1]
          break
      #endfor
      
      if _cls is None:
        raise ValueError('Could not find specified class `{}` in module `{}`'.format(self._class_name, self._module_name))
      
      self._cls = _cls(
        log=self.log,
        **self._kwargs
        )
    except:
      self.P('Exception while loading requested class: {}'.format(traceback.format_exc()))
    finally:
      self.log.stop_timer(step_name)    
      if self._cls is not None:
        self._add_info(step_name)
    return
  
  def _load(self):
    step_name = 'LOAD'
    self.log.start_timer(step_name)
    self._cls.load()
    self.log.stop_timer(step_name)
    self._add_info(step_name)
    return
  
  def _prepare(self):
    step_name = 'PREPARE'
    self.log.start_timer(step_name)
    self._cls.prepare()
    self.log.stop_timer(step_name)
    self._add_info(step_name)
    return
  
  def _draw(self, path_out, inputs, preds):
    self._cls.draw(path_out, inputs, preds)
    return
  
  def _report(self, max_batch_size):
    self.log.p('Creating report ...')
    
    self.log.p('Max batch size: {}'.format(max_batch_size))
    df = pd.DataFrame(self._info)
    self.log.p('\n\n{}'.format(df))
    return df
  
  def run(self, inputs, **kwargs):    
    self._import()
    if self._cls is None:
      self.log.p('Benchmark will not run as requested module/class was not found!')
      return
    
    self._load()
    self._prepare()
    
    max_bs = None
    for bs in self._batch_sizes:
      try:
        step_name = 'PREDICT_BS_{}'.format(bs)
        n_batches = len(inputs) // bs
        if n_batches == 0:
          self.log.p('No batches for bs {}'.format(bs))
          continue
        
        path_out = os.path.join(
          self.log.get_output_folder(),
          self._class_name,
          'BS_{}'.format(bs)
          )
        os.makedirs(path_out, exist_ok=True)
        self.log.p('Infering on bs {}. Number of batches: {}'.format(bs, n_batches))
        for n_batch in range(n_batches):
          _start = n_batch * bs
          _stop = (n_batch + 1) * bs
          batch = inputs[_start:_stop]
          
          self.log.start_timer(step_name)
          preds = self._cls.predict(batch)
          self.log.stop_timer(step_name)          
          
          self._draw(path_out, batch, preds)
        #endfor
        max_bs = bs
        self._add_info(step_name)
      except:
        self.log.p('Exception while running bs {}: {}'.format(bs, traceback.format_exc()))
    #endfor
    
    df = self._report(
      max_batch_size=max_bs
      )
    return df
    
  
    
    
    
    
    
    
    
    
    
    
    
    
    
    
  
    
    
  
  
  