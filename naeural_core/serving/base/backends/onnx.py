import json
from google.protobuf.json_format import MessageToDict
import torch as th
import numpy as np
import onnx
import onnxruntime as ort
from typing import Tuple
from pathlib import Path

from naeural_core.serving.base.backends.model_backend_wrapper import ModelBackendWrapper

def onnx_to_np_dtype(onnx_type : str) -> np.dtype:
  onnx_to_np_dtype_dict = {
    "tensor(bool)": np.bool_,
    "tensor(int)": np.int32,
    'tensor(int32)': np.int32,
    'tensor(int8)': np.int8,
    'tensor(uint8)': np.uint8,
    'tensor(int16)': np.int16,
    'tensor(uint16)': np.uint16,
    'tensor(uint64)': np.uint64,
    "tensor(int64)": np.int64,
    'tensor(float16)': np.float16,
    "tensor(float)": np.float32,
    'tensor(double)': np.float64,
  }
  return onnx_to_np_dtype_dict[onnx_type]


def get_torch_dtype(np_dtype : np.dtype) -> th.dtype:
  """
  Get a torch dtype from a numpy dtype.
  Parameters:
    - np_dtype a numpy dtype
  Returns:
    - the corresponding torch dtype.
  """
  numpy_to_torch_dtype_dict = {
      bool          : th.bool,
      np.bool_      : th.bool,
      np.uint8      : th.uint8,
      np.int8       : th.int8,
      np.int16      : th.int16,
      np.int32      : th.int32,
      np.int64      : th.int64,
      np.float16    : th.float16,
      np.float32    : th.float32,
      np.float64    : th.float64,
      np.complex64  : th.complex64,
      np.complex128 : th.complex128
  }
  if np_dtype not in numpy_to_torch_dtype_dict.keys():
    raise ValueError("No known corresponding torch dtype for {}".format(np_dtype))
  return numpy_to_torch_dtype_dict[np_dtype]

def get_static_shape(shape, batch_size):
  # Replace all dynamic dimensions with batch size
  return [batch_size if isinstance(x, str) else x for x in shape]

# Wrapper around ONNX for inference.
class ONNXModel(ModelBackendWrapper):
  ONNX_METADATA_KEY = 'onnx_metadata'

  def __init__(self):
    self._session = None
    self._max_batch_size = None
    self._batched_input_idx = None
    self._batch_runtime_info = None
    self._metadata = None
    return


  def _get_batch_dependent_arg_idx(self) -> int:
    for i, input in enumerate(self._session.get_inputs()):
      shape = input.shape
      if isinstance(shape[0], str):
        return i
    # No batching?
    raise Exception("No batching dimension found")


  def _get_batch_runtime_state(self, batch_size : int):
    # Return a tuple of (list of (input_name, input_dtype, input_shape),
    # (list of (output shape, output name, numpy dtype, th dtype).
    inputs_info = []
    for input in self._session.get_inputs():
      input_name = input.name
      input_dtype = onnx_to_np_dtype(input.type)
      input_shape = get_static_shape(input.shape, batch_size)
      inputs_info.append((input_name, input_dtype, input_shape))
    #endfor all inputs

    # Produce the list of output tensor information for this batch size.
    outputs = []
    for output in self._session.get_outputs():
      output_shape = get_static_shape(output.shape, batch_size)
      np_dtype = onnx_to_np_dtype(output.type)
      th_dtype = get_torch_dtype(np_dtype)
      outputs.append((output_shape, output.name, np_dtype, th_dtype))
    #endfor outputs
    return inputs_info, outputs


  def load_model(
    self,
    model_path : str,
    max_batch_size : int,
    device : th.device,
    half : bool
  ) -> None:
    """
    Initialize from a path on the disk.

    Parameters
    ----------
      model_path - path to onnx model on disk
      max_batch_size - maximum batch size for the inference
      device - torch device to use.
      half - if True converts model to fp16

    Returns
    -------
    None
    """
    self._max_batch_size = max_batch_size
    self._device_type = device.type
    self._device_id = 0 if device.index is None else device.index
    self._device = device

    # Read the metadata from the model.
    model_onnx = onnx.load(model_path)
    onnx_metadata_dict = MessageToDict(model_onnx, preserving_proto_field_name = True)['metadata_props']
    for meta in onnx_metadata_dict:
      if meta['key'] == ONNXModel.ONNX_METADATA_KEY:
        self._metadata = json.loads(meta['value'])

    model_precision = self._metadata.get('precision')
    if model_precision is not None:
      if (model_precision.lower() == "fp16") != half:
        if half and model_precision.lower() == "fp32":
          #from onnxconverter_common import float16
          from onnxruntime.transformers import float16
          model_fp16 = float16.convert_float_to_float16(model_onnx, disable_shape_infer=True)
          fp16path = str(Path(model_path).with_suffix('.tofp16.onnx'))
          onnx.save(model_fp16, fp16path)
          model_path = fp16path
        else:
          raise RuntimeError('Incompatible precision in ONNX model')
    #endif check onnx precision

    del model_onnx
    model_onnx = None

    providers = ['CPUExecutionProvider']
    if device.type.startswith('cuda'):
      providers.append('CUDAExecutionProvider')

    self._session = ort.InferenceSession(
      model_path,
      providers=providers
    )
    self._input_dtypes = []
    for input in self._session.get_inputs():
      np_dtype = onnx_to_np_dtype(input.type)
      self._input_dtypes.append(np_dtype)

    self._batched_input_idx = self._get_batch_dependent_arg_idx()

    # Pre-construct shapes and output information for all possible batch sizes.
    # This avoids doing some work at inference time.
    self._batch_runtime_info = []
    for batch_size in range(self._max_batch_size + 1):
      self._batch_runtime_info.append(self._get_batch_runtime_state(batch_size))

    return


  def forward(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    """
    Run inference. All model inputs and outputs are torch tensors.
    Arguments are the input tensors in the order specified when the model
    was exported from torch, first inputs and then outputs (see
    create_from_torch).
    Users must pass all input tensors as unnamed arguments.
    Returns a tuple of output tensors, in the order of outputs of the
    original torch model.
    """
    batch_size = args[self._batched_input_idx].shape[0]
    binding = self._session.io_binding()
    input_info, output_info = self._batch_runtime_info[batch_size]

    for idx, (input_name, input_dtype, input_shape) in enumerate(input_info):
      binding.bind_input(
        name=input_name,
        device_type=self._device_type,
        device_id=self._device_id,
        element_type=input_dtype,
        shape=input_shape,
        buffer_ptr=args[idx].contiguous().data_ptr(),
      )
    #endfor all inputs
    outputs = []
    # Allocate and bind output tensors.
    for shape, name, np_dtype, th_dtype in output_info:
      th_output = th.empty(
        size=tuple(shape),
        dtype=th_dtype,
        device=self._device,
        requires_grad=False
      )
      outputs.append(th_output)
      binding.bind_output(
          name=name,
          device_type=self._device_type,
          device_id=self._device_id,
          element_type=np_dtype,
          shape=tuple(shape),
          buffer_ptr=th_output.data_ptr(),
      )
    #endfor all outputs

    if batch_size > 0:
      self._session.run_with_iobinding(binding)
    if len(outputs) == 1:
      return outputs[0]
    return tuple(outputs)

  def get_metadata(self):
    return self._metadata

  def get_device(self) -> th.device:
    return self._device

  def get_input_dtype(self, index : int) -> th.dtype:
    np_dtype = self._input_dtypes[index]
    return get_torch_dtype(np_dtype)

  def __call__(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    return self.forward(*args)
