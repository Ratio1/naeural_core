import platform
import torch as th
import torchvision as tv

from naeural_core.serving.base.backends.model_backend_wrapper import ModelBackendWrapper
from naeural_core.serving.base.base_serving_process import ModelServingProcess as BaseServingProcess
from naeural_core.serving.mixins_base.th_utils import _ThUtilsMixin
from naeural_core.serving.mixins_base.ths_mixin import TorchscriptMixin
from naeural_core.serving.mixins_base.trt_mixin import TensortRTMixin
from naeural_core.serving.mixins_base.onnx_mixin import ONNXMixin
from naeural_core.serving.mixins_base.openvino_mixin import OpenVINOMixin

from naeural_core.utils.plugins_base.plugin_base_utils import NestedDotDict

BACKEND_PRIORITY = NestedDotDict({
  'ARM64_CPU' : ['onnx', 'openvino', 'ths'],
  'ARM64_GPU' : ['trt', 'ths'],
  'AMD64_CPU' : ['openvino', 'onnx', 'ths'],
  'AMD64_GPU' : ['trt', 'ths'],
})

BACKEND_REQUIRES_CPU = ['onnx', 'openvino']


_CONFIG = {
  **BaseServingProcess.CONFIG,

  "IMAGE_HW"                    : None,
  "WARMUP_COUNT"                : 3,
  "USE_AMP"                     : False,
  "USE_FP16"                    : None,
  "DEFAULT_DEVICE"              : None,
  "URL"                         : None,
  "DEBUG_TIMERS"                : False,
  "MAX_BATCH_FIRST_STAGE"       : None,
  "MAX_BATCH_SECOND_STAGE"      : None,

  "MODEL_WEIGHTS_FILENAME"      : None,
  "MODEL_CLASSES_FILENAME"      : None,

  "MODEL_ONNX_FILENAME"         : None,
  "ONNX_URL"                    : None,
  "MODEL_TRT_FILENAME"          : None,
  "TRT_URL"                     : None,
  "BACKEND"                     : None,

  "SECOND_STAGE_MODEL_WEIGHTS_FILENAME" : None,
  "SECOND_STAGE_MODEL_CLASSES_FILENAME" : None,

  "CUDNN_BENCHMARK"             : False,

  'VALIDATION_RULES': {
    **BaseServingProcess.CONFIG['VALIDATION_RULES'],
  },
}


class UnifiedFirstStage(
  BaseServingProcess,
  _ThUtilsMixin,
  TorchscriptMixin,
  TensortRTMixin,
  ONNXMixin,
  OpenVINOMixin,
):

  CONFIG = _CONFIG

  def __init__(self, **kwargs):
    self.default_input_shape = None
    self.class_names = None
    self.graph_config = {}
    self._platform_backend_priority = None
    self._str_dev = None

    super(UnifiedFirstStage, self).__init__(**kwargs)
    return

  @property
  def th(self):
    return th

  @property
  def tv(self):
    return tv

  @property
  def cfg_input_size(self):
    return self.cfg_image_hw

  @property
  def cfg_fp16(self):
    # Use the USE_FP16 value if that was specified in the config.
    # If it wasn't specified and we're using the CPU don't use
    # fp16 as that will crash pytorch. If we're using CUDA
    # default to fp16.
    if self.cfg_use_fp16 is not None:
      return self.cfg_use_fp16
    if self.dev.type == 'cpu':
      return False
    return True

  @property
  def th_dtype(self):
    ### TODO: Maybe we have uint as input
    return th.float16 if self.cfg_fp16 else th.float32

  @property
  def get_model_weights_filename(self):
    # This is done in order for us to be able to have more control over the weights filename
    return self.cfg_model_weights_filename

  @property
  def get_model_classes_filename(self):
    return self.cfg_model_classes_filename

  @property
  def get_model_trt_filename(self):
    return self.cfg_model_trt_filename

  @property
  def get_trt_url(self):
    return self.cfg_trt_url

  @property
  def get_model_onnx_filename(self):
    return self.cfg_model_onnx_filename

  @property
  def get_onnx_url(self):
    return self.cfg_onnx_url

  @property
  def get_url(self):
    return self.cfg_url

  def get_device_string(self):
    """
    Get the device string for the torch device used by this serving.

    Parameters
    ----------
    None

    Returns
    str - the torch device string used
    """
    return self._str_dev

  def get_saved_model_fn(self):
    fn_saved_model = self.get_model_weights_filename if self.get_model_weights_filename is not None else self.server_name + '_weights.pt'
    return fn_saved_model

  def get_saved_classes_fn(self):
    fn_saved_classes = self.get_model_classes_filename if self.get_model_classes_filename is not None else self.server_name + '_classes.txt'
    return fn_saved_classes

  def has_device(self, dev=None):
    if dev is None:
      dev = th.device(self.get_device_string())
    elif isinstance(dev, str):
      dev = th.device(dev)
    dev_idx = 0 if dev.index is None else dev.index
    if dev.type == 'cpu' or (th.cuda.is_available() and th.cuda.device_count() > dev_idx):
      return True
    return False

  def _stop_timer(self, tmr, periodic=False):
    if self.cfg_debug_timers:
      th.cuda.synchronize()
    return super()._stop_timer(tmr, periodic=periodic)

  def _get_platform_backend_order(self):
    """
    Produces a list of supported backends, ordered by preference (from
    most preferable to least), given the current platform and torch device.

    Parameters
    ----------
    None

    Returns
    -------
    ret - Sequence[str] - a list of backend names to be used
    """
    machine = platform.machine().lower()
    # Check for x86/x86_64/amd64
    is_x86 = machine.startswith('x86_64') or machine.startswith('amd64')
    # Check for arm{32|64} or aarch{32|64}
    is_arm64 = machine.startswith('arm64') or machine.startswith('aarch64')
    is_cpu = (self.dev.type == 'cpu')
    if is_cpu:
      if is_x86:
        return BACKEND_PRIORITY.AMD64_CPU
      if is_arm64:
        return BACKEND_PRIORITY.ARM64_CPU

    if is_x86:
      return BACKEND_PRIORITY.AMD64_GPU
    if is_arm64:
      return BACKEND_PRIORITY.ARM64_GPU
    # TODO: we might have more platforms and this code is a bit too specific.
    raise ValueError('Unexpected platform')

  def _setup_device_string(self):
    """
    Initializes the device string of this serving based on the
    configuration and platform. If DEFAULT_DEVICE was explicitly
    set in the config, we use that. If a backend that needs the
    CPU was explicitly and DEFAULT_DEVICE was not specified, we
    will use the CPU device. Otherwise, if the platform has a
    CUDA device we will use cuda:0 or fall back to the CPU.

    Parameters
    ----------
    None

    Returns
    -------
    None
    """
    has_cuda = (th.cuda.is_available() and th.cuda.device_count() > 0)
    if self.cfg_default_device is not None:
      self._str_dev = self.cfg_default_device.lower()
      return
    elif has_cuda:
      self.P("Machine support CUDA, using cuda:0 as the default device", color='b')
      self._str_dev = 'cuda:0'
    else:
      self.P("Machine does not support CUDA, falling back to CPU", color='r')
      self._str_dev = 'cpu'
    #endif select default device

    if self.cfg_backend is not None:
      if self.cfg_backend in BACKEND_REQUIRES_CPU:
        self._str_dev = 'cpu'
        self.P("Forcing device to CPU for backend", c='r')
    #endif force device for backend
    return

  def _setup_model(self):
    self.P("Model setup initiated for {}".format(self.__class__.__name__))

    # Determine the device string based on configs and platform.
    self._setup_device_string()

    dev = th.device(self.get_device_string())
    if not self.has_device(dev):
      raise ValueError('Current machine does not have compute device {}'.format(dev))
    self.dev = dev

    if self.cfg_use_amp and self.cfg_fp16:
      self.P('Using both AMP and FP16 is not usually recommended.', color='r')

    # Now that we've chosen the device we can determine the default
    # backend order for this platform.
    self._platform_backend_priority = self._get_platform_backend_order()

    # record gpu info status pre-loading
    lst_gpu_status_pre = None
    if 'cuda' in self.get_device_string():
      lst_gpu_status_pre = self.log.gpu_info(mb=True)

    self.P("Prepping classes if available...")
    fn_classes = self.get_saved_classes_fn()
    url_classes = self.config_model.get(self.const.URL_CLASS_NAMES)
    if fn_classes is not None:
      self.download(
        url=url_classes,
        fn=fn_classes,
      )
      self.class_names = self.log.load_models_json(fn_classes)

    if self.class_names is None:
      self.P("WARNING: based class names loading failed. This is not necessarily an error as classes can be loaded within models.")

    if self.get_onnx_url is None and self.get_trt_url is None:
      # weights preparation - only for non-dynamic models
      # FIXME: this _seems_ to be only needed for torch (not ths) models and doesn't
      # match the rest of the code. Likely should be refactored to a _prepare_pytorch_model
      # and code should be pushed to users.
      if self.get_url is not None:
        self.P("Prepping model weights or graph...")
        model_weights = self.get_saved_model_fn()
        self.download(
          url=self.get_url,
          fn=model_weights,
        )
        # TODO: maybe use fn_weights as the output of self.download from above
        self.P("Loading model...")
        self.th_model = self._get_model(config=model_weights)
    else:
      backend_model_map={
        'ths' : (self.get_saved_model_fn(), self.get_url),
        'trt' : (self.get_model_trt_filename, self.get_trt_url),
        'onnx' : (self.get_model_onnx_filename, self.get_onnx_url),
        'openvino' : (self.get_model_onnx_filename, self.get_onnx_url),
      }
      self.th_model = self._get_model(config=backend_model_map)

    # Move the model to the required device if necessary.
    self._model_to(self.dev)

    # now load second stage model
    self._second_stage_model_load()

    # now warmup all models
    self._model_warmup()

    # record gpu info status post-loading
    taken, dev_nr = 0, 0
    if lst_gpu_status_pre is not None:
      dev_nr = 0 if dev.index is None else dev.index
      lst_gpu_status_post = self.log.gpu_info(mb=True)
      try:
        free_pre = lst_gpu_status_pre[dev_nr]['FREE_MEM']
        free_post = lst_gpu_status_post[dev_nr]['FREE_MEM']
        taken = free_pre - free_post
      except:
        self.P("Failed gpu check on {} PRE:{}, POST:{}\n {}\n>>>> END: GPU INFO ERROR".format(
          dev_nr, lst_gpu_status_pre, lst_gpu_status_post,
          self.trace_info()), color='error'
        )

    curr_model_dev = self._get_model_device()

    msg = "Model {} prepared on device '{}'{}:".format(
      self.server_name, curr_model_dev,
      " using {:.0f} MB GPU GPU {}".format(taken, dev_nr) if taken > 0 else "",
      )
    for k in self.config_model:
      msg = msg + '\n  {:<17} {}'.format(k+':', self.config_model[k])
    self.P(msg, color='g')
    return msg

  def _th_device_eq(self, device1, device2):
    """
    Compares two torch devices for equality.

    Parameters
    ----------

    device1: th.device, left hand side of the comparison
    device2: th.device, right hand side of the comparison

    Returns
    -------
    True iff the devices are equal, false otherwise
    """
    if ((device1.type != device2.type) or  # cpu vs cuda
        ((device1.type == device2.type) and
         (device1.index != device2.index)) # cuda but on different index
        ):
      return False
    return True

  def _get_model_device(self, model=None):
    """
    Returns the torch device assiciated with the model. If the model is
      None then the fist stage serving model is used.

    Parameters
    ----------

    model: torchscript, torch model or a ModelBackendWrapper.

    Returns
    -------

    ret - th.device - the corresponding torch device where the model is located.
    """
    if model is None:
      model = self.th_model
    if isinstance(model, ModelBackendWrapper):
      return model.get_device()
    return next(model.parameters()).device

  def _get_input_dtype(self, index, model=None):
    """
    Returns the torch dype for an input of the given model. If the model is
      None then the fist stage serving model is used.

    Parameters
    ----------

    index: int, the index of the paramter being queried.
    model: torchscript, torch model or a ModelBackendWrapper - The model
      for which we're querying the input parameter type. If the model is
      None then the fist stage serving model is used.

    Returns
    -------

    ret - th.dtype, the associated type of the input
    """
    if model is None:
      model = self.th_model
    if isinstance(model, ModelBackendWrapper):
      return model.get_input_dtype(index)
    return next(model.parameters()).dtype

  def _model_to(self, device, model=None):
    """
    Move a model to the specified torch device, converting it to fp16 if
    required (the serving configuration has USE_FP16 set to True). If the
    model is None then the fist stage serving model is used.

    Parameters
    ----------

    device: th.device, the device to move the model to

    model: torchscript, torch model or a ModelBackendWrapper - The model
      to move or conver. If the model is None then the fist stage serving
      model is used.

    Returns
    -------
    None
    """
    if model is None:
      model = self.th_model
    model_dev = self._get_model_device(model)
    if isinstance(model, ModelBackendWrapper):
      # The should already be on the correct device. If it's not
      # then something has gone horribly wrong.
      if not self._th_device_eq(model_dev, device):
        raise ValueError("Backend model of type {} on {} should already be on device: {}".format(
            model.__class__.__name__,
            model_dev,
            device
          )
        )
      return
    #endif backend model
    if not self._th_device_eq(model_dev, device):
      self.P("Model '{}' loaded & placed on '{}' -  moving model to device:'{}'".format(
        self.__class__.__name__, model_dev, device), color='y')
      model.to(device)
    model.eval()
    if self.cfg_fp16:
      model.half()
    return

  def _process_config_classes(self, config):
    config['names'] = {int(k): v for k, v in config.get('names', {}).items()}
    class_names = self._class_dict_to_list(config['names'])
    if len(class_names) > 0:
      self.P("Loading class names : {} classes ".format(len(class_names)))
      self.class_names = class_names
    return config

  def download_model_for_backend(self, url, fn_model, backend):
    """
    Downloads a model for a backend from the specifed url to:

       <models-folder>/backend/fn_model{no_suffix}/fn_model

    This will additionally create folders if needed.

    Parameters
    ----------
      url - str, The URL used for download
      fn_model - str, the downloaded model file
      backend - the name of the backend

    Returns
    -------
    ret - str - the path to the the downloaded model folder
    """
    if url is None:
      raise ValueError(f"No URL specified for model {fn_model} for backend {backend}")
    from pathlib import Path
    import os

    fn_model_no_suffix = str(Path(fn_model).with_suffix(''))
    relative_backend_folders = os.path.join(backend, fn_model_no_suffix)
    model_dir = os.path.join(self.log.get_models_folder(), relative_backend_folders)
    # Make sure folders are in fact there.
    os.makedirs(model_dir, exist_ok=True)
    fn_path = os.path.join(model_dir, fn_model)
    download_path = os.path.join(relative_backend_folders, fn_model)
    self.download(url, download_path)
    if not os.path.isfile(fn_path):
      raise ValueError(f"Could not download model file from {url}. {fn_model} not found.")
    return model_dir

  def prepare_model(
    self,
    backend_model_map : dict = None,
    forced_backend : str = None,
    post_process_classes : bool = False,
    return_config : bool = False,
    **kwargs
  ):
    """
    Creates the optimal model for this platform according
    to the backend_model_map parameter. The model will be downloaded
    (if required), saved to disk (if required) and loaded.
    Adds the model configuration (dict) to serving graph_config
    dictionary, with the key being the selected model filename.

    Raises a RuntimeError if no model was selected.

    Examples:
      model, config, fn_model = self.prepare_model(
        backend_model_map={
          'ths' : (self.get_saved_model_fn(), self.get_url),
          'trt' : (self.get_model_trt_filename, self.get_trt_url),
          'onnx' : (self.get_model_onnx_filename, self.get_onnx_url),
          'openvino' : (self.get_model_onnx_filename, self.get_onnx_url),
        },
        post_process_classes=True,
        return_config=True
      )
      # Creates a torchscript model.
      model = self.prepare_model(
        backend_model_map={
          'ths' : (self.get_saved_model_fn(), self.get_url),
          'trt' : (self.get_model_trt_filename, self.get_trt_url),
        },
        forced_backend='ths',
        post_process_classes=True,
        return_config=False
      )

    Parameters
    ----------
    backend_model_map : dict
      Keys are backend names and values are tuples of (filename, url)

    forced_backend : str | None
      If not None the method will use the provided backend instead of
      selecting one according to the platform configuration.

    post_process_classes : bool
      Updates the returned configuration, changing keys from strings
      to integers

    return_config : bool
      If True, returns a tuple of (model, model configuration, filename)
      If False, only returns the model.

    Returns
    -------
    If return_config is False:
      res - torchscript model | BackendModelWrapper
    If return_config is True:
      res - tuple of (torchscript model | BackendModelWrapper,
                      model configuration,
                      selected model filename)
    """

    backend_order = self._platform_backend_priority
    if forced_backend is not None:
      backend_order = [forced_backend]

    for backend in backend_order:
      if backend == 'trt':
        load_method = self._prepare_trt_model
      if backend == 'onnx':
        load_method = self._prepare_onnx_model
      if backend == 'openvino':
        load_method = self._prepare_openvino_model
      if backend == 'ths':
        load_method = self._prepare_ts_model

      if backend_model_map is not None:
        # If the backend model map was passed don't fall back to first stage
        # defaults. This is to support the case where we want to load a second
        # stage model, and for this case falling back to the first stage model
        # would be wrong.
        fns = backend_model_map.get(backend)
        if fns is None:
          self.P("No backend map entry found for backend {}, skipping".format(backend))
          continue
        fn_model, fn_url = fns
        if fn_model is None:
          self.P("No model found for backend {}, skipping".format(backend))
          continue
      #endif check for non-default backend model map

      self.P("Loading for backend {}".format(backend))
      model, config = load_method(
        url=fn_url,
        fn_model=fn_model,
        post_process_classes=post_process_classes,
        return_config=True,
        **kwargs
      )

      if model is not None:
        return (model, config, fn_model) if return_config else model
      #endif check if model loaded
    #endfor backends in order

    raise RuntimeError("Could not prepare model")
    return

  def get_input_shape(self):
    return (3, *self.cfg_input_size) if self.cfg_input_size is not None else None

  @staticmethod
  def __model_call_callback(th_inputs, model):
    """
    This is the default method for calling a model with the given inputs.
    Parameters
    ----------
    th_inputs - the inputs to be passed to the model
    model - the model to be called

    Returns
    -------
    res - the result of the model call
    """
    return model(th_inputs)

  def get_model_call_method(self, model_call_method=None):
    """
    This method returns the model call method to be used for calling the model.
    Parameters
    ----------
    model_call_method - the model call method to be used for calling the model

    Returns
    -------
    model_call_method - the model call method to be used for calling the model
    """
    if model_call_method is None:
      # If the method is not provided a custom method will be checked
      # and if it is not found the default method will be used
      default_method = getattr(self, 'model_call', None)
      return default_method if callable(default_method) else self.__model_call_callback
    return model_call_method

  def _forward_pass(self, th_inputs, model=None, model_call_method=None, debug=None, debug_str='', autocast=True):
    model = self.th_model if model is None else model
    debug = self._full_debug if debug is None else debug
    model_call_method = self.get_model_call_method(model_call_method=model_call_method)
    # endif model not provided
    if autocast:
      with th.cuda.amp.autocast(enabled=self.cfg_use_amp):
        with th.no_grad():
          if debug:
            self.P("  Forward pass {}, dev '{}' with {}:{}".format(
              debug_str, th_inputs.device, th_inputs.shape, th_inputs.dtype
            ))
          th_preds = model_call_method(model=model, th_inputs=th_inputs)
        # end no_grad
      # end autocast
    else:
      with th.no_grad():
        if debug:
          self.P("  Forward pass {}, no autocast on dev '{}' with {}:{}".format(
            debug_str, th_inputs.device, th_inputs.shape, th_inputs.dtype
          ))
        th_preds = model_call_method(model=model, th_inputs=th_inputs)
      # end no_grad
    # endif autocast or not
    return th_preds

  # TODO: maybe add support for randint?
  def model_warmup_helper(
      self, model=None, input_shape=None, warmup_count=None,
      max_batch_size=None, model_dtype=None, model_device=None,
      model_name=None, model_call_method=None
  ):
    """
    This method is used to warm up the model.
    Parameters
    ----------
    model - the model to be warmed up
    input_shape - the shape of the input to be used for the model after warmup
    warmup_count - the number of forward passes per batch size to be used for warmup
    max_batch_size - the maximum batch size to be used for the model after warmup
    model_dtype - the dtype of the input to be used for the model after warmup
    model_device - the device of the input to be used for the model after warmup
    model_name - the name of the model

    Returns
    -------
    None
    """
    model = self.th_model if model is None else model
    input_shape = self.get_input_shape() if input_shape is None else input_shape
    warmup_count = self.cfg_warmup_count if warmup_count is None else warmup_count
    max_batch_size = self.cfg_max_batch_first_stage if max_batch_size is None else max_batch_size
    model_name = self.__class__.__name__ if model_name is None else model_name

    if model is not None:
      if model_dtype is None or model_device is None:
        model_dtype = self._get_input_dtype(0, model=model)
        model_device = self._get_model_device(model=model)
      # endif model_dtype or model_device
      model_dev_type = model_device.type
      if input_shape is not None:
        self.P(f"Warming up model {model_name} with {input_shape}/{model_dtype} on device '{model_device}'"
               f" for MAX_BATCH_SIZE {max_batch_size} and WARMUP_COUNT {warmup_count}...")
        for wbs in range(1, max_batch_size + 1):
          shape = (wbs, *input_shape)
          th_warm = th.rand(
            *shape,
            device=model_device,
            dtype=model_dtype
          )
          for warmup_pass in range(1, warmup_count + 1):
            _ = self._forward_pass(
              th_warm, model=model, model_call_method=model_call_method,
              autocast='cuda' in model_dev_type and self.cfg_use_amp,
              debug=self._full_debug, debug_str=str(warmup_pass)
            )
          # endfor warmup_pass
        # endfor warmup_batch_size
      self.P("Model {} warmed up and ready for inference".format(model_name))
    else:
      self.P("ERROR: Model of {} not found".format(model_name))
    return

  def _model_warmup(self):
    shape = self.get_input_shape()
    th.backends.cudnn.benchmark = self.cfg_cudnn_benchmark

    self.model_warmup_helper(
      model=self.th_model,
      input_shape=shape,
      warmup_count=self.cfg_warmup_count,
      max_batch_size=self.cfg_max_batch_first_stage
    )

    self._second_stage_model_warmup()
    return

  def _class_dict_to_list(self, dct_classes):
    keys = sorted(list(dct_classes.keys()))
    result = [dct_classes[x] for x in keys]
    return result

  def ver_element_to_int(self, ver_element):
    """
    Method for getting rid of non-numeric suffixes from version elements.
    For example if our current version is '0.15.2+cpu' and the minimum version is '0.15.2' we want to
    still be able to tell that the current version is greater than or equal to the minimum version.
    Parameters
    ----------
    ver_element - the version element to be converted to int

    Returns
    -------
    int - the numeric value of the version element or 0 if the version element does not start with a digit.
    """
    ver_element_numeric = self.re.sub('\D.*', '', ver_element)
    return int(ver_element_numeric) if ver_element_numeric.isnumeric() else 0

  def ver_to_int(self, version):
    ver_elements = version.split('.')
    int_ver_elements = [self.ver_element_to_int(x) for x in ver_elements[:3]]
    weights = [1000000, 1000, 1]
    int_ver = sum(int_ver_elements[i] * weights[i] for i in range(min(len(weights), len(int_ver_elements))))

    return int_ver

  def check_version(self, min_ver, curr_ver):
    return self.ver_to_int(min_ver) <= self.ver_to_int(curr_ver)

  def valid_version(self, ver_str):
    return '.' in ver_str

  def check_versions(self, model_config, fn_path, env_versions, err_keys):
    versions = {key: model_config[key] if key in model_config.keys() else 'Unspecified' for key in env_versions.keys()}

    err_check = not all([
      self.check_version(min_ver=versions[vkey], curr_ver=env_versions[vkey]) and self.valid_version(versions[vkey])
      for vkey in err_keys
    ])
    warn_check = not all([
      self.valid_version(versions[vkey]) and self.check_version(min_ver=versions[vkey], curr_ver=env_versions[vkey])
      for vkey in env_versions.keys()
    ])

    if err_check:
      err_msg = f'ERROR! Model from {fn_path} has versions above current environment versions!' \
                f'[Model versions:{versions} > Env versions: {env_versions}]'
      self.P(err_msg, color='e')
      raise Exception(err_msg)
    elif warn_check:
      warn_msg = f'WARNING! Model from {fn_path} has versions above current environment versions or has ' \
                 f'unspecified/invalid versions! [Model versions:{versions} > Env versions: {env_versions}]'
      self.P(warn_msg, color='r')
    else:
      self.P(f'Graph version check passed! [{versions} <= {env_versions}]')
    # endif version_check
    return

  def _get_model(self, config):
    """
    Get the model associated with configuration config.

    Parameters
    ----------
    config : str | dict
      If config is a dict, keys are backend names and values are tuples
        of (filename, url).
      Current valid backend names are:
        'ths'      - torchscript backend
        'onnx'     - ONNX backend
        'openvino' - OpenVINO backend
        'trt'      - TensorRT backend
      If config is a string, this is the torchscript file name.
        Note this is used for backwards compatibility and is deprecated.
        Please use a dict parameter.

    Returns
    -------
    res - torch/torchscript model | ModelBackendWrapper
    """
    raise NotImplementedError()

  def _pre_process_images(self, images):
    raise NotImplementedError()

  # second stage methods
  def _second_stage_model_load(self):
    # this method defines default behavior for second stage model load
    return

  def _second_stage_model_warmup(self):
    # this method defines default behavior for second stage model warmup
    return

  def _second_stage_process(self, th_preds, th_inputs=None, **kwargs):
    # this function processed post forward and should be used in subclasses
    # also here you we can add second/third stage classifiers/regressors (eg mask detector)
    return th_preds

  def _second_stage_classifier(self, first_stage_out, th_inputs):
    # override this to add second stage classifier/regressor fw pass
    # for this to be executed in model prep set `self.has_second_stage_classifier = True`
    return None

  # end second stage methods

  @staticmethod
  def _th_resize(self, lst_images, target_size):
    return

  ###
  ### BELOW MANDATORY (or just overwritten) FUNCTIONS:
  ###

  def _get_inputs_batch_size(self, inputs):
    """
    Extract the batch size of the inputs. This method is can be overwritten when inputs are dictionaries,
    because `len(inputs)` would simply be the number of keys in the dict.

    Example of use case: in `th_cqc`, the input is a kwarg dict with images and anchors, so the batch size
    should be the len of those inputs, not the number of inputs (which is constantly 2).

    Parameters
    ----------
    inputs : Any
        The input of the serving model

    Returns
    -------
    int
        The batch size of the input
    """
    return len(inputs) # not the greatest method but it works

  def _startup(self):
    msg = self._setup_model()
    return msg

  def _pre_process(self, inputs):
    """
    This method does only the basic data extraction from upstream payload and
    has replaced:
      ```
      def _pre_process(self, inputs):
        prep_inputs = th.tensor(inputs, device=self.dev, dtype=self.th_dtype)
        return prep_inputs
      ```


    Parameters
    ----------
    inputs : list
      list of batched numpy images.

    Raises
    ------
    ValueError
      DESCRIPTION.

    Returns
    -------
    TYPE
      DESCRIPTION.

    """
    lst_images = None
    if isinstance(inputs, dict):
      lst_images = inputs.get('DATA', None)
    elif isinstance(inputs, list):
      lst_images = inputs

    if lst_images is None or len(lst_images) == 0:
      msg = "Unknown or None `inputs` received: {}".format(inputs)
      self.P(msg, color='error')
      raise ValueError(msg)
    return self._pre_process_images(lst_images)

  def _aggregate_batch_predict(self, lst_results):
    if len(lst_results) == 1:
      return lst_results[0]
    else:
      if isinstance(lst_results[0], th.Tensor):
        return th.cat(lst_results, dim=0)
      else:
        raise NotImplementedError(
          "Please implement `_aggregate_batch_predict` method that can aggregate model output type: {}".format(
            type(lst_results[0]))
        )
    return

  def _batch_predict(self, prep_inputs, model, batch_size, aggregate_batch_predict_callback=None, **kwargs):
    lst_results = []
    start_time = self.time()

    if isinstance(prep_inputs, dict):
      last_dim = None
      for key in prep_inputs.keys():
        assert isinstance(prep_inputs[key], (list, th.Tensor, self.np.ndarray)), 'Invalid input type {}'.format(type(prep_inputs[key]))
        assert last_dim is None or last_dim == len(prep_inputs[key]), 'Inconsistent input dim: {} vs {}'.format(last_dim, prep_inputs[key])
        last_dim = len(prep_inputs[key])
      #endfor
      real_batch_size = last_dim
    else:
      real_batch_size = len(prep_inputs)
    #endif

    if batch_size is None:
      self.maybe_log_phase('_model_predict[bs=None]', start_time, done=False)
      if isinstance(prep_inputs, dict):
        return model(**prep_inputs, **kwargs)
      else:
        return model(prep_inputs, **kwargs)
      #endif
    #endif

    self.maybe_log_phase(f'_model_predict[bs={real_batch_size} mbs={batch_size} and bn={self.np.ceil(real_batch_size / batch_size).astype(int)}]', start_time, done=False)
    num_batches = self.np.ceil(real_batch_size / batch_size).astype(int)
    for i in range(num_batches):
      if isinstance(prep_inputs, dict):
        batch = {}
        for key in prep_inputs.keys():
          batch[key] = prep_inputs[key][i * batch_size: (i+1) * batch_size]
        # endfor
        th_inferences = model(**batch, **kwargs)
      else:
        batch = prep_inputs[i * batch_size:(i+1) * batch_size]
        th_inferences = model(batch, **kwargs)
      # endif

      lst_results.append(th_inferences)
    # endfor batches
    self.maybe_log_phase(f'_model_predict[bs={real_batch_size} mbs={batch_size} and bn={self.np.ceil(real_batch_size / batch_size).astype(int)}]', start_time)
    if aggregate_batch_predict_callback is None:
      return self._aggregate_batch_predict(lst_results)
    else:
      return aggregate_batch_predict_callback(lst_results)

  def _predict(self, prep_inputs):
    ### TODO: This no longer works with multiple inputs
    if isinstance(prep_inputs, dict):
      kwargs_first_stage = prep_inputs.get('kwargs_first_stage', {})
      kwargs_second_stage = prep_inputs.get('kwargs_second_stage', {})
      inputs = prep_inputs.get('inputs', None)
      if inputs is None:
        raise ValueError('Prepocess returned dict without inputs')
    elif not isinstance(prep_inputs, (list, th.Tensor, self.np.ndarray)):
      raise ValueError('Preprocess returned invalid type {}'.format(type(prep_inputs)))
    else:
      inputs = prep_inputs
      kwargs_first_stage = {}
      kwargs_second_stage = {}
    #endif

    start_time = self.time()
    bs = self._get_inputs_batch_size(inputs)
    with th.cuda.amp.autocast(enabled=self.cfg_use_amp):
      with th.no_grad():
        str_predict_timer = 'fwd_b{}_{}'.format(str(bs), str(self.cfg_max_batch_first_stage))
        self.log.start_timer(self.predict_server_name + '_' + str_predict_timer)
        self.maybe_log_phase('_batch_process', start_time, done=False)
        th_x_stage1 = self._batch_predict(
          prep_inputs=inputs,
          model=self.th_model,
          batch_size=self.cfg_max_batch_first_stage,
          **kwargs_first_stage
        )
        self.maybe_log_phase('_batch_predict', start_time)
        self.log.stop_timer(self.predict_server_name + '_' + str_predict_timer)

        self._start_timer('stage2')
        self.maybe_log_phase('_second_stage_process', start_time, done=False)
        th_x_stage2 = self._second_stage_process(
          th_preds=th_x_stage1,
          th_inputs=inputs,
          **kwargs_second_stage
        )
        self.maybe_log_phase('_second_stage_process', start_time)

        self._stop_timer('stage2')
    return th_x_stage2

  def _post_process(self, preds):
    if isinstance(preds, (list, tuple)):
      # lets unpack
      outputs = []
      for pred in preds:
        if isinstance(pred, th.Tensor):
          outputs.append(pred.cpu().numpy())
    elif isinstance(preds, th.Tensor):
      outputs = preds.cpu().numpy()
    else:
      raise ValueError("Unknown model preds output of type {}".format(type(preds)))
    return outputs

  def _clear_model(self):
    if hasattr(self, 'th_model') and self.th_model is not None:
      self.P("Deleting current loaded model for {} ...".format(self.__class__.__name__))
      curr_model_dev = self._get_model_device(self.th_model)
      if 'cuda' in curr_model_dev.type.lower():
        lst_gpu_status_pre = self.log.gpu_info(mb=True)

      del self.th_model
      self.th_utils.clear_cache()

      freed = 0
      idx = None
      if 'cuda' in curr_model_dev.type.lower():
        lst_gpu_status_post = self.log.gpu_info(mb=True)
        try:
          idx = 0 if curr_model_dev.index is None else curr_model_dev.index
          freed = lst_gpu_status_post[idx]['FREE_MEM'] - lst_gpu_status_pre[idx]['FREE_MEM']
        except:
          self.P("Failed gpu check on {} PRE:{}, POST:{}, {}".format(
            idx, lst_gpu_status_pre[idx], lst_gpu_status_post[idx],
            self.trace_info()), color='error'
          )

      self.P("Cleanup finished. Freed {:.0f} MB on GPU {}".format(freed, idx))
    return

  def _shutdown(self):
    self._clear_model()
    return

  def forward(self, inputs):
    prep_inputs = self._pre_process(inputs)
    fwd = self.th_model(prep_inputs)
    return fwd
