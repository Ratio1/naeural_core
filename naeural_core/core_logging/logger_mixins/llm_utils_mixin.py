import torch as th

AVAILABLE_WEIGHTS_SIZES = [4, 8, 16, 32]
DEFAULT_WEIGHTS_SIZE = 16

class _LlmUtilsMixin(object):
  """
  Mixin for LLM utilitary methods that are attached to `libraries.logger.Logger`.

  This mixin cannot be instantiated because it is built just to provide some additional
  functionalities for `libraries.logger.Logger`

  In this mixin we can use any attribute/method of the Logger.
  """

  def __init__(self):
    super(_LlmUtilsMixin, self).__init__()
    return

  def validate_weights_size(self, weights_size):
    """
    Validate the weights size.
    If the weights size is not in the available weights sizes and is not None, the default weights size is returned.
    If the weights size is None, it is returned as is. That means that the model will be loaded without quantization.
    Parameters
    ----------
    weights_size : int
        the weights size

    Returns
    -------
    int
        the validated weights size
    """
    if weights_size is None:
      return weights_size
    if weights_size not in AVAILABLE_WEIGHTS_SIZES:
      return DEFAULT_WEIGHTS_SIZE
    return weights_size

  def get_model_load_config(
      self, model_name, token, has_gpu=True, weights_size=None, device_map="auto",
      cache_dir=None, use_flash_attention=False
  ):
    """
    Creates the `model.from_pretrained` load config including the BitsAndBytes configuration.
    Parameters
    ----------
    model_name : str
      The model name
    token : str
      The Hugging Face token
    has_gpu : bool
      If the machine has a GPU
    weights_size : int
      The weights size
    device_map : str
      The device map
    cache_dir : str
      The cache directory
    use_flash_attention : bool
      If the Flash Attention should be used

    Returns
    -------
    tuple[dict, dict]
      the two configurations for the model and the quantization.
    """
    cache_dir = cache_dir or self.get_models_folder()
    weights_size = self.validate_weights_size(weights_size)
    torch_dtype = "auto"
    load_in_8bit = (weights_size == 8)
    load_in_4bit = (weights_size == 4)
    quantization_params = None
    self.P("Preparing model load config for {}...".format(model_name))
    self.P("  Model weights size: {}".format(weights_size))
    self.P("  Has GPU: {}".format(has_gpu))
    if has_gpu and weights_size is not None:
      if weights_size in [4, 8]:
        # We're using 4 or 8 bit quantization and we need to prepare
        # the bitsandbytes quantization arguments.
        quantization_params = {
          "load_in_8bit": load_in_8bit,
          "load_in_4bit": load_in_4bit,
        }
        if load_in_8bit:
          torch_dtype = None
          quantization_params['llm_int8_threshold'] = 6.0
        else:
          quantization_params = {
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_use_double_quant": True,
            "bnb_4bit_compute_dtype": th.bfloat16,
            **quantization_params
          }
      else:
        # No BitsAndBytes quantization is used, thus the model is loaded in 16-bit or 32-bit precision.
        torch_dtype = th.float16
        if weights_size == 32:
          torch_dtype = th.float32
          self.P("WARNING: fp32 requested for a model that is usually fp16. This may cause OOM errors and if not will not yield better performance!", color='r')
        # endif 32 bits
      # endif 4/8 bits (quantization) or 16/32 bits (no quantization)
    # endif no config provided

    model_params = {
      'cache_dir': cache_dir,
      'token': token,
      'low_cpu_mem_usage': True,
      'torch_dtype': torch_dtype,
      'device_map': device_map
    }
    if use_flash_attention:
      model_params['attn_implementation'] = 'flash_attention_2'
    return model_params, quantization_params

