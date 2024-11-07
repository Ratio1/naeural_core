import torch as th
import os

class OpenVINOMixin:
  def _prepare_openvino_model(self,
    url : str = None,
    fn_model : str = None,
    post_process_classes : bool = False,
    return_config : bool = False,
    **kwargs
  ):
    """
    Creates an OpenVINO backend model, possibliy downloading it. OpenVINO
    uses ONNX as the file format, so it will use the ONNX file when loading
    the model.

    Parameters
    ---------
    url: str
      The URL where we can download the ONNX model from. If None, the
      URL value will be picked as the default URL of the serving (specified
      by ONNX_URL)

    fn_model : str
      The filename of the OpenVINO model. If None, the default ONNX filename value
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
      res - the OpenVINOModel that was loaded

    if return_config is True
      res - tuple of (OpenVINOModel, config) where the OpenVINOModel is the model that
        was loaded/prepared, config is the configuration of the model (dict)
    """

    from naeural_core.serving.base.backends.ovino import OpenVINOModel
    if 'batch_size' not in kwargs.keys():
      self.P("batch size not passed as parameter when preparing model")
      raise ValueError("No batch size parameter")
    #endif check for batch size

    self.P("Preparing {} OpenVINO model {}...".format(self.server_name, self.version))
    if url is None:
      url = self.get_onnx_url
    if fn_model is None:
      fn_model = self.cfg_model_onnx_filename

    if fn_model is None:
      # No ONNX file was specified in the configuration so bail out.
      self.P('OpenVINO model not specified')
      raise ValueError("OpenVINO model not specified")

    if not self._th_device_eq(self.dev, th.device('cpu')):
      self.P("Incompatible device for OpenVINO backend: {}".format(self.dev))
      raise ValueError("Incompatible device for OpenVINO ")
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
    model = OpenVINOModel()
    self.P("Trying to load from {}".format(fn_path))
    model.load_model(fn_path, half=self.cfg_fp16)
    config = model.get_metadata()

    err_keys = ['torch']
    # torch and torchvisions versions may not affect anything here since
    # the model is already traced (as onnx).
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
