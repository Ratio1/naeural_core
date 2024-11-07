import os
from pathlib import Path
import torch as th
from openvino.runtime import Core
import openvino as ov
import json
import numpy as np

from naeural_core.serving.base.backends.model_backend_wrapper import ModelBackendWrapper

from typing import Tuple

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

class OpenVINOModel(ModelBackendWrapper):
  ONNX_METADATA_KEY = 'onnx_metadata'

  def __init__(self):
    self._model = None
    self._compiled_model = None
    self._metadata = None
    return

  def load_model(
    self,
    model_path : str,
    half : bool
  ) -> None:
    """
    Loads the OpenVINO from disk (in ONNX format).
    If the model cannot be loaded, raises an Exception.

    Parameters
    ----------

    model_path : str
      The path of the model on disk, in ONNX format
    half : bool
      If True loads the model in fp16

    Returns
    -------
    None
    """
    import onnx
    from google.protobuf.json_format import MessageToDict
    # We're only going to run this on the CPU so hard-code it here.
    # We could in theory use other devices however the output seems
    # to always be on the CPU side (i.e. in a numpy array).
    device = 'CPU'

    # Read the metadata from the model.
    model_onnx = onnx.load(model_path)
    onnx_metadata_dict = MessageToDict(model_onnx, preserving_proto_field_name = True)['metadata_props']
    for meta in onnx_metadata_dict:
      if meta['key'] == OpenVINOModel.ONNX_METADATA_KEY:
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

    # Building the OpenVINO model from ONNX is fast so there is no
    # issue with rebuilding on load.
    if not OpenVINOModel.create_from_onnx(onnx_path=model_path):
      raise RuntimeError("Error while creating OpenVINO model")

    core = Core()
    self._model = core.read_model(
      model=model_path,
      weights=Path(model_path).with_suffix('.bin')
    )
    self._compiled_model = core.compile_model(self._model, device_name=device)

    return

  def forward(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    args_dict = {}
    # Set model arguments while ensuring that we're using contiguous tensors
    # and the data is on the CPU.
    for i, _ in enumerate(self._model.inputs):
      args_dict[i] = args[i].cpu().contiguous().numpy()

    request = self._compiled_model.create_infer_request()
    result = request.infer(inputs=args_dict) # Run inference.

    # Convert output into a tuple of torch tensors. Note that we're
    # already running on the CPU so this shouldn't involve any
    # memory copies.
    ret = tuple([th.tensor(result[out]) for out in request.model_outputs])
    if len(ret) == 1:
      return ret[0]
    return ret

  def __call__(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    """
    Override for the call operator, just goes to forward.
    Input and output tensors are in the ONNX order.
    """
    return self.forward(*args)

  @staticmethod
  def create_from_onnx(
    onnx_path : str,
    vino_path : str = None,
    half = False
  ):
    status = True
    if vino_path is None:
      vino_path = str(Path(str(onnx_path)).with_suffix('.xml'))
    #endif

    weights_path = Path(vino_path).with_suffix('.bin')
    try:
      ov_model = ov.convert_model(onnx_path, share_weights=False)
      ov.serialize(ov_model, vino_path)
    except Exception as e:
      status = False

    if not status:
      # Something went wrong, remove all vino files (xml and bin files).
      try:
        os.unlink(vino_path)
      except Exception:
        pass
      try:
        os.unlink(weights_path)
      except Exception:
        pass

    return status

  def get_metadata(self):
    return self._metadata

  def get_device(self) -> th.device:
    return th.device('cpu')

  def get_input_dtype(self, index : int) -> th.dtype:
    np_dtype = self._model.inputs[index].get_element_type().to_dtype().type
    return get_torch_dtype(np_dtype)

  def __call__(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    return self.forward(*args)
