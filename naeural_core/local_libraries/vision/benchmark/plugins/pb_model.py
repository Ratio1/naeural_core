#global dependencies
import tensorflow.compat.v1 as tf

#local dependencies
from .abstract_model import AbstractModel

class PbModel(AbstractModel):
  def __init__(self, config_model, **kwargs):
    self.config_model = config_model
    super().__init__(**kwargs)
    return
  
  def _load_graph(self):
    self.log.start_timer('load_graph')    
    graph_file = self.config_model['GRAPH']
    graph = self.log.load_graph_from_models(graph_file)
    if graph is None:
      raise ValueError("Graph loading failed for {}".format(graph_file))
    self.graph = graph
    self.log.stop_timer('load_graph')
    return
  
  def _setup_session(self):
    self.log.start_timer('setup_session')
    mem_fraction = self.config_model.get('MEMORY_FRACTION')
    self.log.P("Preparing session...")
    if mem_fraction is not None:
      gpu_options = tf.GPUOptions(
        per_process_gpu_memory_fraction=mem_fraction
        )
      config = tf.ConfigProto(gpu_options=gpu_options)
      self.sess = tf.Session(graph=self.graph, config=config)
    else:
      self.sess = tf.Session(graph=self.graph)
    self.tf_runoptions = tf.RunOptions(report_tensor_allocations_upon_oom=True)
    self.log.stop_timer('setup_session')
    return
  
  def _setup_tensors(self):
    self.log.start_timer('setup_tensors')
    input_tensor_names = self.config_model.get('INPUT_TENSORS', [])
    output_tensor_names = self.config_model.get('OUTPUT_TENSORS', [])
    self.log.P("Preparing input {} and output {} tensors".format(
      input_tensor_names, output_tensor_names))
    self.input_tensor_names = []
    self.output_tensor_names = []
    
    tensor_outputs = []
    for tname in output_tensor_names:
      tn = tname if ':' in tname else tname + ':0'
      tensor_outputs.append(self.sess.graph.get_tensor_by_name(tn))
      self.output_tensor_names.append(tn)
    self.tensor_outputs = tensor_outputs
    
    tensor_inputs = []
    for tname in input_tensor_names:
      tn = tname if ':' in tname else tname + ':0'
      tensor_inputs.append(self.sess.graph.get_tensor_by_name(tn))
      self.input_tensor_names.append(tn)
    self.tensor_inputs = tensor_inputs

    self.log.stop_timer('setup_tensors')
    msg = "\n  Status:\n    Inputs:\n{}    Outputs:\n{}".format(
      "".join(["      {}\n".format(x) for x in self.tensor_inputs]), 
      "".join(["      {}\n".format(x) for x in self.tensor_outputs])
      )
    return msg[:-1]
  
  def _process_input(self, inputs, **kwargs):
    return inputs
  
  def _predict(self, inputs, **kwargs):
    self.log.start_timer('predict')
    if len(self.tensor_outputs) == 1:
      fetches = self.tensor_outputs[0]
    else:
      fetches = self.tensor_outputs
    
    preds = self.sess.run(
      fetches=fetches,
      feed_dict=inputs,
      options=self.tf_runoptions
      )
    
    self.log.stop_timer('predict')
    return preds  
  
  def init(self, **kwargs):
    return
  
  def load(self, **kwargs):
    self._load_graph()    
    return  
  
  def prepare(self, **kwargs):
    self._setup_session()
    self._setup_tensors()
    return
  
  
  
  
  
  
  
  
  
  
  
  
  