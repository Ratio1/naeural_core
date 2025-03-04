import sys
import torch as th
import torchvision as tv
import time
import json
import os
import gc
import numpy as np
import abc

from naeural_core.local_libraries.nn.th.utils import th_resize_with_pad
from ratio1 import load_dotenv


load_dotenv()


class BaseScripter(metaclass=abc.ABCMeta):
  """
  This class will be used in order to convert already existent pytorch models to
  various formats via torch tracing.
  """

  def __init__(
    self, log, model, model_name, input_shape,
    model_config=None, preprocess_method=None,
    matching_method=None,
    predict_method=None, use_fp16=False,
    use_amp=False,
    gold_model=None
  ):
    """
    Parameters
    ----------
    log - Logger, swiss knife object used in all DecentrAI
    model - torch.nn.Module, the instantiated model to be converted
    model_name - str, name of the model
    input_shape - list, expected input shape by the model
    model_config - dict, configuration of the converted model
    preprocess_method - method, method of preprocessing the input in case it is needed
    - for the default value of this see self.preprocess_callback
    matching_method - method, method for checking if the output of the model matches the
      output of the traced model
    - for the default value of this see self.matching_callback
    predict_method - method, method of running inferences given model and inputs
    - for the default value of this see self.default_predict
    use_fp16 - bool, whether to use fp16 when tracing a model
    use_amp - bool, whether to use amp when tracing a model
    gold_model - Torchscript model, if not None we will use this for validation
    """
    self.model = model
    self.model_name = model_name
    self.model_config = {} if model_config is None else model_config
    self.log = log
    self.input_shape = input_shape
    self.preprocess_method = preprocess_method if preprocess_method is not None else self.preprocess_callback
    self.matching_method = matching_method if matching_method is not None else self.matching_callback
    self.predict_method = predict_method if predict_method is not None else self.default_predict
    self.use_amp = use_amp
    self.use_fp16 = use_fp16
    self.gold_model = gold_model
    self.model.is_gold = False
    if self.use_fp16:
      self.model.half()
      if self.gold_model is not None:
        self.gold_model.half()
        self.gold_model.is_gold = True
    return

  def clear_cache(self):
    gc.collect()
    th.cuda.empty_cache()
    return

  def start_timer(self, name):
    self.log.start_timer(name, section='tracing')
    return

  def stop_timer(self, name):
    self.log.stop_timer(name, section='tracing')
    return

  def default_predict(self, model, inputs):
    return model(inputs)

  def __predict(self, model, inputs):
    if self.use_amp:
      with th.cuda.amp.autocast(enabled=self.use_amp):
        with th.no_grad():
          return self.predict_method(model, inputs)
    else:
      with th.no_grad():
        return self.predict_method(model, inputs)
    # endif use_amp

  def preprocess_callback(self, inputs, device='cpu', normalize=True, **kwargs):
    """
    This default callback will deal with resizing images, hence the majority of the models used in DecentrAI are
    computer vision models at the moment.
    Parameters
    ----------
    inputs - list, list of inputs on which to trace/test the model
    device - str or torch.device, device on which the input will be put

    Returns
    -------
    res - the data ready to enter the model
    """
    h, w = self.input_shape[:2]
    self.log.P("  Resizing from {} to {}".format([x.shape for x in inputs], (h, w)))
    results = th_resize_with_pad(
      img=inputs,
      h=h,
      w=w,
      device=device,
      normalize=normalize,
      return_original=False,
      half=self.use_fp16
    )
    if len(results) < 3:
      prep_inputs, lst_original_shapes = results
    else:
      prep_inputs, lst_original_shapes, lst_original_images = results
    return prep_inputs

  def convert_to_batch_size(self, inputs, batch_size):
    if hasattr(inputs, 'shape') and len(inputs.shape) == len(self.input_shape):
      # here inputs will be a single input
      inputs = [inputs for _ in range(batch_size)]
    else:
      # here inputs will be a list of inputs
      if hasattr(inputs, '__len__') and len(inputs) != batch_size:
        inputs = [inputs[i % len(inputs)] for i in range(batch_size)]
      # endif inputs of different length than the batch size needed
    # endif inputs is a single input
    return inputs

  def model_timing(self, model, model_name, inputs, nr_warmups=20, nr_tests=20, **kwargs):
    """
    Method for timing the speed of a certain model on given inputs.
    Parameters
    ----------
    model - torch.nn.Module, the model to be checked
    model_name - str, name of the timed model
    inputs - any, input data on which the model will be traced
    nr_warmups - int, how many inferences to make for warm up
    nr_test - int, how many inferences to make for timings

    Returns
    -------
    res - float, average time of inference for a batch
    """
    self.log.restart_timer(model_name)
    # warmup
    self.log.P(f"  Warming up ({nr_warmups} inferences)...")
    for _ in range(nr_warmups):
      print('.', flush=True, end='')
      preds = self.__predict(model, inputs)
    print('')

    # timing
    self.log.P(f"  Predicting ({nr_tests} inferences)...")
    for _ in range(nr_tests):
      print('.', flush=True, end='')
      self.start_timer(model_name)
      preds = self.__predict(model, inputs)
      self.stop_timer(model_name)
    print('')
    return self.log.get_timer_mean(model_name)

  def maybe_numpy(self, x):
    if isinstance(x, th.Tensor):
      return x.detach().cpu().numpy()
    return x

  def matching_callback(self, output1, output2, atol=0.0001, **kwargs):
    """
    Method for validating that 2 outputs are matching
    Parameters
    ----------
    output1 - any
    output2 - any
    atol - float, absolute tolerance for the comparison

    Returns
    -------
    True if outputs are matching, False otherwise
    """
    if isinstance(output1, tuple) and len(output1) == 1:
      output1 = output1[0]
    if isinstance(output2, tuple) and len(output2) == 1:
      output2 = output2[0]

    if type(output1) != type(output2):
      self.log.P('TYPE IS DIFFERENT! {} {} '.format(type(output1), type(output2)))
      return False
    if isinstance(output1, (list, tuple)):
      return len(output1) == len(output2) and all([
        np.allclose(self.maybe_numpy(output1[i]), self.maybe_numpy(output2[i]), atol=atol)
        for i in range(len(output1))]
      )
    if isinstance(output1, th.Tensor):
      output1 = output1.detach().cpu().numpy()
      output2 = output2.detach().cpu().numpy()

    if (isinstance(output1, np.ndarray) and np.any(output1 != output2)) or (not isinstance(output1, np.ndarray) and output1 != output2):
      try:
        self.log.P('Absolute error is {}'.format(np.max(np.abs(output1 - output2))))
        self.log.P('Mean error is {}'.format(np.average(np.abs(output1 - output2))))
        self.log.P('Std error is {}'.format(np.std(np.abs(output1 - output2))))
        return np.allclose(output1, output2, atol=atol)
      except Exception as e:
        self.log.P(f'Error! {e}')
        return False
    # endif outputs are different
    return True

  @abc.abstractmethod
  def load(self, fn_path, device, batch_size, use_fp16):
    """
    Loads the traced model from disk to memory, possibly converting it to float16.

    Parameters
    ----------
    fn_path: str, path on disk to the traced model

    device: th.device, the torch device to load the model to

    batch_size: maximum supported batch size for inference

    use_fp16: if True, converts the loaded model to f16

    Returns
    -------
    Tuple[model, dict] - a tuple containing the model and the model
      configuration
    """
    pass

  @abc.abstractmethod
  def convert(self, inputs, config, model_fn):
    """
    Traces the pytorch model of the scripter, saving it to disk.

    Parameters
    ----------

    inputs - data for tracing (FIXME: what's the exact type??)

    config : dict, model config

    model_fn : str, path where the model should be saved on disk

    Returns
    -------
    Union[ModelBackendWrapper, torch.jit.ScriptModule] - the converted model,
    loaded from disk
    """
    pass

  def test(
      self, ts_path, model, inputs, batch_size=2, device='cpu',
      nr_warmups=20, nr_tests=20, skip_preprocess=False,
      use_fp16=False, **kwargs
  ):
    """
    Method for testing the traced model at the same time with the model in order to
    validate that the outputs are the same and to check the speed difference between the 2.
    Parameters
    ----------
    ts_path - str, path to the traced model
    model - torch.nn.Module, the model to be checked
    inputs - any, input data on which the model will be traced
    batch_size - int, Warning: the batch_size for testing and the batch
    size on which the model was traced should be different in order to
    further check that the model was traced correctly
    device - str or torch.device
    skip_preprocess - bool, whether to skip the preprocessing of the input
    nr_warmups - int, how many inferences to make for warm up
    nr_test - int, how many inferences to make for timings
    use_fp16 - bool, whether to convert both the model and the traced model to
    float16 before testing

    Returns
    -------
    res - (ok, timings), where:
    ok - bool, True if the outputs coincide, False otherwise
    timings - dict, the timers of both the model and the traced version
    """
    str_prefix = f'[bs={batch_size}]'
    self.log.P(f"{str_prefix}Testing the graph from {ts_path} for {self.model_name} with bs={batch_size} on device {device} with use_fp16={use_fp16}")
    ts_model, config = self.load(ts_path, device, batch_size=batch_size, use_fp16=use_fp16)
    ts_input_shape = config.get('input_shape')
    assert list(ts_input_shape) == list(self.input_shape), \
        f'Error! The input shape of the model and the .ths do not coincide! ' \
        f'The specified shape for the model was {self.input_shape}, while the ' \
        f'input shape in the .ths config is {ts_input_shape}'

    if not skip_preprocess:
      self.log.P(f'{str_prefix}Preprocessing...')
      inputs = self.convert_to_batch_size(inputs=inputs, batch_size=batch_size)
      inputs = self.preprocess_method(inputs=inputs, device=device, **kwargs)
    else:
      self.log.P(f'{str_prefix}Skipping preprocessing')
    # endif skip_preprocess

    if model.is_gold:
      self.log.P("Running with gold model")
    else:
      self.log.P("Running without gold model")

    suffix_str = ""
    if use_fp16:
      model.half()
      inputs = inputs.half()
      suffix_str = "[FP16]"
    # endif use_fp16

    self.log.P(f'{str_prefix}Inferences...')
    model_output = self.__predict(model, inputs)
    ts_output = self.__predict(ts_model, inputs)

    self.log.P(f'{str_prefix}Validating...')
    ok = self.matching_method(model_output, ts_output, **kwargs)

    self.log.P(f'{str_prefix}Timing...')
    model_time = self.model_timing(
      model=model, model_name=self.model_name, inputs=inputs,
      nr_warmups=nr_warmups, nr_tests=nr_tests, **kwargs
    )
    self.clear_cache()
    ts_time = self.model_timing(
      model=ts_model, model_name=self.model_name + '_ts', inputs=inputs,
      nr_warmups=nr_warmups, nr_tests=nr_tests, **kwargs
    )
    self.clear_cache()
    timing_dict = {
      f'{self.model_name}_python': model_time,
      f'{self.model_name}_ts': ts_time,
      f'{self.model_name}_python_per_input': model_time / batch_size,
      f'{self.model_name}_ts_per_input': ts_time / batch_size,
    }
    self.log.P(f'{str_prefix}Model outputs are{"" if ok else " not"} matching!!{suffix_str}', color='g' if ok else 'r')
    total_gain = model_time - ts_time
    model_time_per_input = model_time / batch_size
    ts_time_per_input = ts_time / batch_size
    self.log.P(f'{str_prefix}Time for normal model: {model_time:.05f}s [{model_time_per_input:.05f} per input]{suffix_str}')
    self.log.P(f'{str_prefix}Time for traced model: {ts_time:.05f}s [{ts_time_per_input:.05f} per input]{suffix_str}')
    self.log.P(
      f'{str_prefix}Speed gain of {total_gain:.05f}s per batch and {total_gain / batch_size:.05f}s per input! ({total_gain/model_time*100:.02f}%){suffix_str}',
      color='g' if total_gain > 0 else 'r'
    )
    return ok, timing_dict

  def generate(
      self, inputs, batch_size=1, device='cpu', to_test=False,
      nr_warmups=20, nr_tests=20, no_grad_tracing=True,
      test_batch_size=None, test_fp16=False, **kwargs
  ):
    """
    Method for generating the traced model on a given device, batch size and inputs.
    Parameters
    ----------
    inputs - any, input data on which the model will be traced
    batch_size - int
    device - str or torch.device
    to_test - bool, whether to test the model after tracing
    nr_warmups - int, how many inferences to make for warm up, relevant only if to_test=True
    nr_test - int, how many inferences to make for timings, relevant only if to_test=True
    no_grad_tracing - bool, whether to apply th.no_grad() when tracing the model
    test_batch_size - int or None, the batch size on which to test the model
    - if None it will be 2 x batch_size
    - relevant only if to_test=True
    test_fp16 - bool, whether to test the model in fp16

    Returns
    -------
    res - path of the saved traced model
    """
    self.log.P(f"Generating traced model for {self.model_name} on {device} with batch_size={batch_size} "
               f"and the following config:")
    self.log.P(f"{self.model_config}")
    config = {
      **self.model_config,
      'input_shape': self.input_shape,
      'python': sys.version.split()[0],
      'torch': th.__version__,
      'torchvision': tv.__version__,
      'device': device,
      'optimize': False,
      'date': time.strftime('%Y%m%d', time.localtime(time.time())),
      'model': self.model_name,
    }
    original_inputs = inputs
    inputs = self.convert_to_batch_size(inputs=original_inputs, batch_size=batch_size)
    inputs = self.preprocess_method(inputs=inputs, device=device, **kwargs)

    self.log.P("  Scripting...")
    self.model.to(device)

    save_dir = os.path.join(self.log.get_models_folder(), 'traces')
    os.makedirs(save_dir, exist_ok=True)
    model_fn = self.model_name + f'{"_fp16" if self.use_fp16 else ""}_bs{batch_size}'
    model_fn = model_fn + self.extension
    fn = os.path.join(save_dir, model_fn)

    traced_model = self.convert(inputs, config, fn)

    self.log.P("  Forwarding using traced...")
    output = self.__predict(traced_model, inputs)

    loaded_model, config = self.load(fn, device=device, batch_size=batch_size, use_fp16=test_fp16)
    self.log.P("  Loaded config with {}".format(list(config.keys())))

    prep_inputs_test = self.convert_to_batch_size(inputs=original_inputs, batch_size=2 * batch_size)
    prep_inputs_test = self.preprocess_method(inputs=prep_inputs_test, device=device, **kwargs)

    self.log.P("  Running forward...")
    preds = self.__predict(loaded_model, prep_inputs_test)
    self.log.P(f"  Done running forward. Ouput:\n{preds}")

    if to_test:
      test_batch_size = 2 * batch_size if test_batch_size is None else test_batch_size
      if self.gold_model is not None:
        self.gold_model.to(device)

      self.log.P('Starting validation phase...')
      if self.gold_model is None:
        self.log.P("Starting test without gold model")
      else:
        self.log.P("Starting test WITH gold model")

      test_kwargs = {
        'ts_path': fn,
        'model': self.model if self.gold_model is None else self.gold_model,
        'inputs': prep_inputs_test,
        'batch_size': test_batch_size,
        'device': device,
        'nr_warmups': nr_warmups,
        'nr_tests': nr_tests,
        'skip_preprocess': True,
        **kwargs
      }
      self.test(**test_kwargs)
      if test_fp16:
        self.test(use_fp16=True, **test_kwargs)
    # endif to_test

    return fn

