#global dependencies
import os
import numpy as np
import tensorflow as tf

#local dependencies
from .abstract_model import AbstractModel

class Tfodapi2SavedModel(AbstractModel):
  def __init__(self, config_model, **kwargs):
    self.config_model = config_model
    super().__init__(**kwargs)
    return
  
  def _load_graph(self):
    self.log.start_timer('load_graph')    
    graph_file = self.config_model['GRAPH']
    graph = tf.saved_model.load(graph_file)
    if graph is None:
      raise ValueError("Graph loading failed for {}".format(graph_file))
    self.graph = graph
    self.log.stop_timer('load_graph')
    return
  
  def _process_input(self, inputs, **kwargs):
    return inputs
  
  def _predict(self, inputs, **kwargs):
    self.log.start_timer('predict')
    preds = self.graph.predict(inputs)
    self.log.stop_timer('predict')
    return preds  
  
  def init(self, **kwargs):
    return
  
  def load(self, **kwargs):
    self._load_graph()    
    return    
  
  def prepare(self, **kwargs):
    return
  

    
    
    
  
  
  
  
  
  
  
  
  
  