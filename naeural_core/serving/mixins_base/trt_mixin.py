import torch as th
import os

class TensortRTMixin:

  def _prepare_trt_model(self,
    url : str = None,
    fn_model : str = None,
    post_process_classes : bool = False,
    return_config : bool = False,
    **kwargs
  ):
    # Engines/config files are built at
    #   <models-folder>/{backend_name}/model_name{no_prefix}/<precision>/<batch_size>/{model_name}.engine
    #   <models-folder>/{backend_name}/model_name{no_prefix}/<precision>/<batch_size>/{model_name}.json

    allow_fallback = kwargs.pop("allow_backend_fallback", True)
    strict_backend = kwargs.pop("strict_backend", False)

    if not th.cuda.is_available() or th.cuda.device_count() == 0:
      # No CUDA device is available.
      raise RuntimeError("Trying to create TensorRT model without cuda")

    self._last_backend_error = None
    trt_version = None
    trt_major = None
    try:
      import tensorrt as trt
      trt_version = getattr(trt, "__version__", None)
      if trt_version:
        trt_major = int(str(trt_version).split(".")[0])
    except Exception as exc:
      self._last_backend_error = "TensorRT import failed: {}".format(exc)
      if strict_backend or not allow_fallback:
        raise RuntimeError(self._last_backend_error)
      return (None, None) if return_config else None

    device_index = self.dev
    if isinstance(self.dev, th.device):
      device_index = self.dev.index
    if device_index is None and th.cuda.is_available():
      try:
        device_index = th.cuda.current_device()
      except Exception:
        device_index = None

    device_props = None
    if th.cuda.is_available() and device_index is not None:
      try:
        device_props = th.cuda.get_device_properties(device_index)
      except Exception:
        device_props = None

    if device_props is not None and trt_major is not None and device_props.major >= 10 and trt_major < 10:
      self._last_backend_error = "TensorRT {} does not support SM {}.{}".format(
        trt_version, device_props.major, device_props.minor
      )
      if strict_backend or not allow_fallback:
        raise RuntimeError(self._last_backend_error)
      return (None, None) if return_config else None

    from naeural_core.serving.base.backends.trt import TensorRTModel
    if 'batch_size' not in kwargs.keys():
      self.P("Batch size not passed as parameter when preparing model")
      raise ValueError("TensorRT model needs batch size")

    #endif check for batch size
    max_batch_size = kwargs['batch_size']

    self.P("Preparing {} TensorRT model {}...".format(self.server_name, self.version))
    if url is None:
      url = self.get_trt_url
    if fn_model is None:
      fn_model = self.cfg_model_trt_filename

    model_dir = self.download_model_for_backend(url=url, fn_model=fn_model, backend='trt')
    fn_path = os.path.join(model_dir, fn_model)

    model = TensorRTModel(self.log)

    if fn_path is not None:
      self.P("Using ONNX model {} ({:.03f} MB) at `{}` using map_location: {} on python v{}...".format(
          fn_model,
          self.os_path.getsize(fn_path) / 1024 / 1024,
          fn_path,
          self.dev,
          self.python_version()
        ),
        color='y'
      )
    # Just load or rebuild the model.
    self.P("Trying to load TensorRT model from {}".format(fn_path))
    half = self.cfg_fp16
    if half:
      precision = 'fp16'
    else:
      precision = 'fp32'

    engine_folder = os.path.join(model_dir, precision, 'bs' + str(max_batch_size))
    os.makedirs(engine_folder, exist_ok=True)

    try:
      model.load_or_rebuild_model(fn_path, half, max_batch_size, self.dev)
    except RuntimeError as exc:
      self._last_backend_error = str(exc)
      if strict_backend or not allow_fallback:
        raise
      return (None, None) if return_config else None
    config = model._metadata[TensorRTModel.ONNX_METADATA_KEY]

    err_keys = ['torch']
    env_versions = {
      'python': self.python_version(),
      'torch': self.th.__version__,
      'torchvision': self.tv.__version__
    }
    self.check_versions(config, fn_path, env_versions, err_keys)

    if post_process_classes:
      config = self._process_config_classes(config)

    self.P("  Model config:\n{}".format(self.log.dict_pretty_format(config)))
    return (model, config) if return_config else model
