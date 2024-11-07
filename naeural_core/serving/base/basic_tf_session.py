from naeural_core import constants as ct
from naeural_core.serving.base.base_serving_process import ModelServingProcess as BaseServingProcess

__VER__ = '0.1.0.0'

_CONFIG = {
  **BaseServingProcess.CONFIG,
  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}

class BasicTFSessionServer(BaseServingProcess):
  """
  This is a premade Tensorflow base serving process. You can subclass it in order
  to obtain advanced versions that preprocess or post-process
  """

  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    super(BasicTFSessionServer, self).__init__(**kwargs)
    return

  @property
  def cfg_gpu_min_memory_mb(self):
    return self.config_model.get(ct.GPU_MIN_MEMORY_MB)

  @property
  def _obsolete_cfg_memory_fraction(self):
    return self.config_model.get(ct.MEMORY_FRACTION)

  @property
  def cfg_graph(self):
    return self.config_model[ct.GRAPH]

  @property
  def cfg_url(self):
    return self.config_model.get(ct.URL, None)

  @property
  def cfg_input_tensors(self):
    return self.config_model.get(ct.INPUT_TENSORS, [])

  @property
  def cfg_output_tensors(self):
    return self.config_model.get(ct.OUTPUT_TENSORS, [])

  def _setup_session(self):
    self._start_timer('setup_session')
    import tensorflow.compat.v1 as tf
    mem_minimal = self.cfg_gpu_min_memory_mb
    cfg_mem_fraction = self._obsolete_cfg_memory_fraction
    total_gpu_mem_gb = self.log.gpu_info()[0]['TOTAL_MEM'] # assume in GB
    total_gpu_mem_mb = total_gpu_mem_gb * 1024

    if mem_minimal is None:
      self.P("{} not found in config. Please use it in place of memory percent.".format(
        ct.GPU_MIN_MEMORY_MB), color='r')
      if cfg_mem_fraction is None:
        raise ValueError("TF serving process must have either memory size im MB or (now obsolete) memory fraction")
      mem_fraction = cfg_mem_fraction
    else:
      self.P("  Model config {}={} MB".format(ct.GPU_MIN_MEMORY_MB, mem_minimal))    
      if cfg_mem_fraction is not None:
        self.P("Found both {} and {}. Using {}".format(
          ct.MEMORY_FRACTION, ct.GPU_MIN_MEMORY_MB, ct.GPU_MIN_MEMORY_MB))
      mem_fraction = mem_minimal / total_gpu_mem_mb
    #endif

    graph_file = self.cfg_graph
    self.P("Preparing '{}' from '{}' using {:.2f} memory fraction".format(
      self.server_name.upper(), graph_file, mem_fraction), color='y')

    url = self.cfg_url
    if url is not None:
      self.log.maybe_download_model(
        url=url,
        model_file=graph_file
      )
    #endif

    graph = self.log.load_graph_from_models(graph_file)
    if graph is None:
      raise ValueError("Graph loading failed for {}".format(graph_file))
    self.graph = graph
    self.P("Preparing session...")
    if mem_fraction is not None:
      gpu_options = tf.GPUOptions(
        per_process_gpu_memory_fraction=mem_fraction
        )
      config = tf.ConfigProto(gpu_options=gpu_options)
      self.sess = tf.Session(graph=self.graph, config=config)
      self.mem_fraction = mem_fraction
    else:
      self.sess = tf.Session(graph=self.graph)
      self.mem_fraction = 1
    self.tf_runoptions = tf.RunOptions(report_tensor_allocations_upon_oom=True)
    self._stop_timer('setup_session')
    return
  
  def _setup_tensors(self):
    self._start_timer('setup_tensors')
    input_tensor_names = self.cfg_input_tensors
    output_tensor_names = self.cfg_output_tensors
    self.P("Preparing input {} and output {} tensors".format(
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

    self._stop_timer('setup_tensors')
    msg = "\n  Status:\n    Inputs:\n{}    Outputs:\n{}    Mem prc: {:.1f}% ".format(
      "".join(["      {}\n".format(x) for x in self.tensor_inputs]), 
      "".join(["      {}\n".format(x) for x in self.tensor_outputs]),
      self.mem_fraction * 100 if hasattr(self, 'mem_fraction') else 100
      )
    return msg[:-1]
  
  def _pre_process_inputs(self, inputs):
    if len(self.input_tensor_names) > 1:
      self.P("Called {} with multi-inputs {} - please define a special server with specific `_preprocess_inputs`".format(
        self.__class__.__name__, self.input_tensor_names))
      raise ValueError("Using Basic TF server with multi-input graph not allowed")    
    dct_inputs = {
        self.input_tensor_names[0] : inputs['DATA']
      }    
    return dct_inputs
  
  def _run_inference(self, input_feed):
    fetches = self.tensor_outputs 
    if len(self.tensor_outputs) == 1:
      fetches = self.tensor_outputs[0]
    
    preds = self.sess.run(
      fetches=fetches,
      feed_dict=input_feed,
      options=self.tf_runoptions      
      )        
    return preds
  
  def _post_process_inference(self, predictions):
    return predictions
  
  def _filter_inference(self, predictions):
    return predictions
  
  def _post_process_outputs(self, predictions):
    return predictions

  def _prepare(self):
    return

  ###
  ### BELOW MANDATORY FUNCTIONS:
  ###
  
  def _startup(self):
    self._setup_session()
    msg = self._setup_tensors()
    self._prepare()
    return msg
  
  def _pre_process(self, inputs):
    prep_inputs = self._pre_process_inputs(inputs)

    if self.tensor_inputs:
      input_feed = {
        self.tensor_inputs[i] : prep_inputs[tensor_name]
        for i, tensor_name in enumerate(self.input_tensor_names)
      }
    else:
      input_feed = prep_inputs

    return input_feed

  
  def _predict(self, prep_inputs):
    preds = self._run_inference(prep_inputs)
    return preds  
  
  
  def _post_process(self, preds):
    self._start_timer(ct.TIMER_POST_PROCESS_INFERENCE)
    post_inference = self._post_process_inference(preds)
    self._stop_timer(ct.TIMER_POST_PROCESS_INFERENCE)
    
    self._start_timer(ct.TIMER_FILTER_RESULTS)
    filtered_inference = self._filter_inference(post_inference)
    self._stop_timer(ct.TIMER_FILTER_RESULTS)
    
    self._start_timer(ct.TIMER_POST_PROCESS_OUTPUTS)
    outputs = self._post_process_outputs(filtered_inference)
    self._stop_timer(ct.TIMER_POST_PROCESS_OUTPUTS)  
    return outputs
    
    
  
  


