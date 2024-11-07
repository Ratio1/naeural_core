class TorchscriptMixin:

  def _load_torchscript(self, fn_path, device=None):
    """
    Generic method for loading a torchscript and returning both the model generated
    by it and its config.
    Parameters
    ----------
    fn_path - path of the specified torchscript

    Returns
      (model, config) where model is the loaded torchscript model and config is the
        model json config.
    -------

    """
    extra_files = {'config.txt': ''}
    dct_config = None
    model = self.th.jit.load(
      f=fn_path,
      map_location=self.dev,
      _extra_files=extra_files,
    )
    model.eval()
    self.P("Done loading model on device {}".format(
      self.dev,
    ))
    try:
      dct_config = self.json.loads(extra_files['config.txt'].decode('utf-8'))
    except Exception as exc:
      self.P("Could not load in-model config '{}': {}. In future this will stop the model loading: {}".format(
        fn_path, exc, extra_files
      ), color='r')
    return model, dct_config

  def _prepare_ts_model(self, url=None, fn_model=None, post_process_classes=False, return_config=False, **kwargs):
    """
      Do not call directly, use prepare_model instead.
    """
    self.P("Preparing {} torchscript graph model {}...".format(self.server_name, self.version))
    if url is None:
      url = self.get_url
    if url is None:
      raise ValueError("No URL specified for model")
    if fn_model is None:
      fn_model = self.get_saved_model_fn()
    self.download(url, fn_model)
    fn_path = self.log.get_models_file(fn_model)
    model, config = None, None
    if fn_path is None:
      raise ValueError(f"Could not download model file from {url}. {fn_model} not found.")

    self.P("Loading torchscript model {} ({:.03f} MB) at `{}` using map_location: {} on python v{}...".format(
        fn_model,
        self.os_path.getsize(fn_path) / 1024 / 1024,
        fn_path,
        self.dev,
        self.python_version()
      ),
      color='y'
    )
    model, config = self._load_torchscript(fn_path, device=self.dev)
    if self.cfg_fp16:
      model.half()
    err_keys = ['torch']
    env_versions = {
      'python': self.python_version(),
      'torch': self.th.__version__,
      'torchvision': self.tv.__version__
    }
    self.check_versions(config, fn_path, env_versions, err_keys)
    if post_process_classes:
      config = self._process_config_classes(config)
    # endif post_process_classes
    self.P("  Model config:\n{}".format(self.log.dict_pretty_format(config)))
    return (model, config) if return_config else model

