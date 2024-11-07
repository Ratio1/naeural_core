import torch as th
import os

class ONNXMixin:
  def _has_valid_onnx_precision(self, config : dict) -> bool:
    """
    Checks if the precision for this model is compatible
    with ONNX serving.

    Parameters
    ----------
    config: dict, the config of the model

    Returns
    -------
    bool - True if the model precision allows us to run with the
      ONNX serving
    """
    precision = config.get('precision').lower()
    if self.cfg_fp16 and precision is not None and precision != "fp16":
      return False
    return True

  def _prepare_onnx_model(self,
    url : str = None,
    fn_model : str = None,
    post_process_classes : bool = False,
    return_config : bool = False,
    **kwargs
  ):
    """
    Creates an ONNX backend model, possibliy downloading it.

    Parameters
    ---------
    url: str
      The URL where we can download the ONNX model from. If None, the
      URL value will be picked as the default URL of the serving (specified
      by ONNX_URL)

    fn_model : str
      The filename of the ONNX model. If None, the default ONNX filename value
      will be used (specified by MODEL_ONNX_URL)

    post_process_classes : bool
      If True updates the returned configuration, changing keys from strings
      to integers

    return_config : bool
      If True, returns a tuple of (model, model configuration, filename)
      If False, only returns the model.

    Returns
    -------
    if return_config is False
      res - the ONNXModel that was loaded

    if return_config is True
      res - tuple of (ONNXModel, config) where the ONNXModel is the model that
        was loaded/prepared, config is the configuration of the model (dict)
    """
    from naeural_core.serving.base.backends.onnx import ONNXModel
    if 'batch_size' not in kwargs.keys():
      self.P("batch size not passed as parameter when preparing model")
      raise ValueError("Batch size required for ONNX model")
    #endif check for batch size
    max_batch_size = kwargs['batch_size']

    self.P("Preparing {} ONNX model {}...".format(self.server_name, self.version))
    if url is None:
      url = self.cfg_onnx_url
    if fn_model is None:
      fn_model = self.cfg_model_onnx_filename

    if fn_model is None:
      self.P('ONNX model not specified')
      # No ONNX file was specified in the configuration so bail out.
      raise ValueError("ONNX model not available")
    #endif check model path

    if not self._th_device_eq(self.dev, th.device('cpu')):
      self.P("Incompatible device for ONNX backend: {}".format(self.dev))
      raise ValueError("Trying to use ONNX backend with incompatible device")
    #endif check torch device type

    model_dir = self.download_model_for_backend(url=url, fn_model=fn_model, backend='onnx')
    fn_path = os.path.join(model_dir, fn_model)

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
    model = ONNXModel()
    self.P("Trying to load from {}".format(fn_path))
    model.load_model(fn_path, max_batch_size, self.dev, self.cfg_fp16)
    config = model.get_metadata()

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
