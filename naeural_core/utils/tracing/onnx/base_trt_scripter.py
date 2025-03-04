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
from naeural_core.utils.tracing.base_scripter import BaseScripter
from ratio1 import load_dotenv

load_dotenv()

class BaseTensorRTScripter(BaseScripter):
  def __init__(
    self, log, model, model_name, input_shape,
    input_names, output_names,
    model_config=None, preprocess_method=None,
    matching_method=None,
    predict_method=None, use_fp16=False,
    use_amp=False,
    batch_axes=None,
    gold_model=None
  ):
    self.extension = '_trt.onnx'
    self.input_names = input_names
    self.output_names = output_names
    self.batch_axes = batch_axes
    super(BaseTensorRTScripter, self).__init__(
      log, model, model_name, input_shape,
      model_config, preprocess_method,
      matching_method,
      predict_method, use_fp16,
      use_amp, gold_model
    )
    return

  def load(self, fn_path, device, batch_size, use_fp16):
    from naeural_core.serving.base.backends.trt import TensorRTModel
    trt_model = TensorRTModel(self.log)
    trt_model.load_or_rebuild_model(fn_path, use_fp16, batch_size, device)
    config = trt_model._metadata[TensorRTModel.ONNX_METADATA_KEY]

    return trt_model, config

  def convert(self, inputs, config, fn):
    from naeural_core.serving.base.backends.trt import TensorRTModel
    from naeural_core.utils.tracing.onnx.utils import create_from_torch
    import copy
    print('saving to {}'.format(fn))
    device = th.device('cuda')
    export_model = copy.deepcopy(self.model)
    create_from_torch(
      export_model, device, fn, half=self.use_fp16,
      input_names=self.input_names,
      output_names=self.output_names,
      args=inputs,
      batch_axes=self.batch_axes,
      aggressive_shape_inference=True,
      metadata=config
    )
    trt_model = TensorRTModel(self.log)
    trt_model.load_or_rebuild_model(fn, self.use_fp16, 1, device)

    return trt_model
