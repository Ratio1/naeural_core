import torch as th
import json
import os

from naeural_core.utils.tracing.base_scripter import BaseScripter
from ratio1 import load_dotenv


load_dotenv()


def load_model_full(
  log, model_name, model_factory, weights, hyperparams,
  weights_filename=None, hyperparams_filename=None,
  device='cpu', return_config=False
):
  """
  Method for loading a model from a given class, with given configuration
  (either as a dictionary or as a .json) hyperparameters.
  Parameters
  ----------
  log - Logger, swiss knife object used in all DecentrAI
  model_name - str, name of the model
  model_factory - class extending torch.nn.Module, class of the given model
  weights - str, local path or url to file with the weights for the model
  device - str or torch.device, device on which the input will be put
  hyperparams - str or dict
  - if dict it will be considered the configuration of the specified model
  - if str it will be considered either a local path or an url to a file containing the hyperparameters
  weights_filename - str or None
  - if weights is an url this has to be the filename to be used when saving the file locally
  hyperparams_filename - str or None
  - if hyperparams is an url this has to be the filename to be used when saving the file locally
  return_config - bool, whether to return the config as a dictionary

  Returns
  -------
  res - (torch.nn.Module, dict) if return_config else torch.nn.Module
  The instantiated model with the weights loaded and if required, the config dictionary of the model.
  """
  log.P(
    f'Attempting to load model {model_name} with config: {hyperparams} and weights at: {weights} on device {device}')
  if weights_filename is None and os.path.isfile(weights):
    weights_filename = os.path.split(weights)[-1]
  elif weights_filename is None:
    raise Exception('`weights_filename` not provided and `weights` is not a local file. Please either provide '
                    'a path to an existing local file or specify `weights_filename`!')
  if isinstance(hyperparams, str) and hyperparams_filename is None and os.path.isfile(hyperparams):
    hyperparams_filename = os.path.split(hyperparams)[-1]
  elif isinstance(hyperparams, str) and hyperparams_filename is None:
    raise Exception('`hyperparams_filename` not provided and `hyperparams` is not a local file. Please either provide '
                    'a path to an existing local file or specify `hyperparams_filename`!')

  download_kwargs = log.config_data.get('MODEL_ZOO_CONFIG', {
      "endpoint": os.environ["EE_MINIO_ENDPOINT"],
      "access_key": os.environ["EE_MINIO_ACCESS_KEY"],
      "secret_key": os.environ["EE_MINIO_SECRET_KEY"],
      "secure": os.environ["EE_MINIO_SECURE"],
      "bucket_name": "model-zoo"
    })

  saved_files, msg = log.maybe_download_model(
    url=weights,
    model_file=weights_filename,
    **download_kwargs,
  )

  hyperparams_files = [None]
  if type(hyperparams) == str:
    hyperparams_files, _ = log.maybe_download_model(
      url=hyperparams,
      model_file=hyperparams_filename,
      **download_kwargs
    )
    hyperparams = None
  model, hyperparams = log.load_model_and_weights(
    model_class=model_factory,
    weights_path=saved_files[0],
    config_path=hyperparams_files[0],
    return_config=True,
    device=device,
    config=hyperparams
  )
  model.eval()

  return (model, hyperparams) if return_config else model

class BaseTorchScripter(BaseScripter):
  def __init__(
    self, log, model, model_name, input_shape,
    model_config=None, preprocess_method=None,
    matching_method=None,
    predict_method=None, use_fp16=False,
    use_amp=False,
    gold_model=None
  ):
    self.extension = '.ths'
    super(BaseTorchScripter, self).__init__(
      log, model, model_name, input_shape,
      model_config, preprocess_method,
      matching_method,
      predict_method, use_fp16,
      use_amp, gold_model)
    return

  def load(self, fn_path, device, batch_size, use_fp16):
    extra_files = {'config.txt': ''}
    ts_model = th.jit.load(
      f=fn_path,
      map_location=device,
      _extra_files=extra_files,
    )
    config = json.loads(extra_files['config.txt'].decode('utf-8'))
    if use_fp16:
      ts_model.half()
    return ts_model, config

  def convert(self, inputs, config, fn):
    if isinstance(inputs, dict):
      traced_model = th.jit.trace_module(self.model, inputs, strict=False)
    else:
      traced_model = th.jit.trace(self.model, inputs, strict=False)

    extra_files = {'config.txt': json.dumps(config)}
    self.log.P(f"  Saving '{fn}'...")
    traced_model.save(fn, _extra_files=extra_files)
    return traced_model

