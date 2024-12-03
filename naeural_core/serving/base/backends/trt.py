import gc
import os
from pathlib import Path

import torch as th
import numpy as np
import json
import tensorrt as trt

from typing import Any, Mapping, Tuple, Sequence, Union

from naeural_core import constants as ct
from naeural_core.serving.base.backends.model_backend_wrapper import ModelBackendWrapper

def get_torch_dtype(np_dtype : np.dtype) -> th.dtype:
  """
  Get a torch dtype from a numpy dtype.
  Parameters:
    - np_dtype a numpy dtype
  Returns:
    - the corresponding torch dype.
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


def check_trt_version(
    trt_version : str,
    cuda_version : str,
    model_device_name : str,
    current_device : th.device):
  """
  Checks the provided version strings and device against the current environment.
  Parameters:
    - trt_version : str, TensorRT version
    - cuda_version : str, CUDA version string
    - model_device_name : str, GPU name
    - current_device : th.device, the checked torch device
  Returns:
    bool, True if this is a compatible configuration,, False otherwise.
  """
  if trt_version != trt.__version__ or cuda_version != th.version.cuda:
    return False
  if model_device_name != th.cuda.get_device_name(current_device):
    return False
  return True

class TRTServingLogger(trt.ILogger):
  """
  A TensorRT-compatible wrapper around the EE logger so that TensorRT
  can provide logs.
  """
  def __init__(self, logger):
    trt.ILogger.__init__(self)
    self._logger = logger
    return

  def log(self, severity : trt.ILogger.Severity, msg : str):
    if severity == trt.ILogger.VERBOSE:
      # Skip verbose messages.
      return
    color = ct.COLORS.SERVING
    self._logger.P('[TRT] ' + msg, color=color)
    return

  def get_base_logger(self):
    return self._logger


class TensorRTModel(ModelBackendWrapper):
  """
  Wrapper around TensorRT for inference.
  For further information see the TensorRT documentation at
  https://docs.nvidia.com/deeplearning/tensorrt/archives/tensorrt-1001/api/python_api/
  """

  # Metadata keys
  METADATA_MAX_BATCH = 'max_batch_size'
  METADATA_INPUTS_KEY = 'inputs'
  METADATA_OUTPUTS_KEY = 'outputs'
  METADATA_ONNX_MD5 = 'onnxmd5' # ONNX md5
  METADATA_ENGINE_MD5 = 'enginemd5' # ONNX md5

  # TensorRT .engine compatibility metadata.
  # We need to check these values against the current environment
  # to ensure that the .engine file can run.
  METADATA_CUDA_VERSION_KEY = 'cudaver' # CUDA version identifier metadata
  METADATA_TRT_VERSION_KEY = 'trtver' # TensorRT identifier metadata
  METADATA_CUDA_DEVICE_KEY = 'cudadev' # GPU device name
  METADATA_PRECISION_KEY = 'precision'

  ONNX_METADATA_KEY = 'onnx_metadata'


  def _clear_internal_state(self):
    self._context = None # TensorRT context
    self._model = None # TensorRT model
    self._metadata = None # Model metadata (JSON)
    self._trt_to_th_idx = None # Permutation from TensorRT to torch order
    self._output_args = None # Tuple of tensor outputs (in torch order)
    self._max_batch_size = None # The maximum batch size the model was built with
    self._current_batch_size = None # The last batch size used for inference
    self._batched_input_idx = None # The index of a batched input
    self._batch_runtime_info = None # Runtime batch-size dependent information
    self._device = None # The device for the model
    return


  def __init__(self, log):
    self._clear_internal_state()
    self._log = TRTServingLogger(log)
    # Make sure the TRT plugins are initialized.
    trt.init_libnvinfer_plugins(None, "")
    return


  def get_device(self):
    return self._device


  def _allocate_outputs(self, batch_size : int) -> Tuple[th.tensor, ...]:
    """
    Allocate output tensors for the current model batch size.
    Parameters:
      batch_size : int, the batch size to use when allocating the output tensors.

    Returns:
      A tuple of torch tensors for the current batch size.
    """
    _, output_types = self._batch_runtime_info[batch_size]
    outputs = []
    for out_shape, dtype in output_types:
      outputs.append(th.empty(
        size=out_shape,
        dtype=dtype,
        device=self._device,
        requires_grad=False
      ).contiguous())
    return tuple(outputs)

  def _set_batch_size(self, batch_size : int):
    """
    Set the current dynamic shape to batch_size.
    We need to call this every time the batch size changes.
    Parameters:
      batch_size: integer, the current batch size
    Returns: None
    """
    self._current_batch_size = batch_size
    shape_info, _ = self._batch_runtime_info[batch_size]
    for idx, name, shape in shape_info:
      # Update all batch-dependent tensor with their new shape.
      if not self._context.set_input_shape(name, shape):
        raise ValueError("Unable to set dynamic shape")

    return


  def _deserialize_model(
    self,
    model_path : str,
    device : th.device):
    """
    Initialize the model and metadata from the TensorRT model serialized
    on disk at the path model_path.
    Parameters:
      model_path : string, the path to the TensorRT model on the disk.
    Returns: None
    """
    with open(model_path, 'rb') as f, trt.Runtime(self._log) as runtime:
      # Set the CUDA device to device so that the model will be loaded to it.
      # It's not recommended to call this function in general, but according to
      # the docs this is the only way to load to a certain device. Only do this
      # if we have more than one device since otherwise we can run into issues
      # with the device index being set to None.
      # See https://docs.nvidia.com/deeplearning/tensorrt/developer-guide/index.html#faq
      if th.cuda.device_count() > 1:
        th.cuda.set_device(device)
      # Now read the actual TensorRT model.
      self._model = runtime.deserialize_cuda_engine(f.read())  # read engine
    #endwith read model from disk
    return


  def _get_torch_args_mapping(self):
    """
    Get the mapping of the input/output arguments to TensorRT order.
    """
    th_arg_positions = {}
    for i, name in enumerate(self._input_names + self._output_names):
      th_arg_positions[name] = i

    num_bindings = self._model.num_io_tensors
    return [
      th_arg_positions[self._model.get_tensor_name(i)]
      for i in range(num_bindings)
    ]


  def _get_batch_dependent_arg_idx(self) -> int:
    """
    Get the index of an input argument whose size is batch-dependent.
    That is the shape of that argument should be batch_size x ...
    """
    for i, name in enumerate(self._input_names):
      shape = self._context.get_tensor_shape(name)
      if shape[0] == -1:
        return i
    # No batching?
    return None


  def _get_batch_runtime_state(self, batch_size : int):
    """
    Get the current runtime state dependent on the batch size

    """
    # Produce a list of bindings indices and shapes
    # for the bindings that need to be set once the
    # batch size changes
    # Here will be cached only the dynamic batches, hence
    # in case of a dynamic shape the engine may change at runtime
    # (e.g. at the warmup stage when the batch size may vary).
    dynamic_shapes = []
    for i, name in enumerate(self._input_names):
      shape = self._context.get_tensor_shape(name)
      if shape[0] != -1:
        continue
      shape = trt.Dims([batch_size] + list(shape[1:]))
      dynamic_shapes.append((i, name, shape))
      #endfor all batch sizes
    #endfor all bindings

    # Produce a list of output tensors shapes for this batch size.
    # There will be cached info for what the allocated memory should be.
    # Thus, we cache the fixed batch sizes too.
    batch_output_types = []
    for name in self._output_names:
      shape = self._context.get_tensor_shape(name)
      shape = tuple([batch_size if size == -1 else size for size in list(shape)])
      np_dtype = trt.nptype(self._model.get_tensor_dtype(name))
      dtype=get_torch_dtype(np_dtype)
      batch_output_types.append((shape, dtype))
    #endfor all output tensors

    return dynamic_shapes, batch_output_types

  def check_engine_file(
    self,
    device : th.device,
    onnx_path,
    engine_metadata_path,
    engine_path,
    half : bool,
    max_batch_size : int
  ) -> None:
    """
    Parameters:
     - device - torch device on which the model will be loaded.
     - onnx_path - path to the onnx model
     - engine_metadata_path - path to the local engine metadata file
         produced by create_from_onnx
     - engine_path - path to the TensorRT engine file
     - max_batch_size - int, maximum batch size to use. If this value
        is different from the one stored in the engine metadata file
        the check will fail and a ValueError will be raised.

    Returns: None

    Raises:
     - ValueError if this configuration cannot be loaded
    """
    if not os.path.isfile(str(engine_metadata_path)):
      raise ValueError("No metadata file")

    try:
      with open(str(engine_metadata_path), 'rt') as f:
        metadata = json.load(f)
    except json.JSONDecodeError as _:
      raise ValueError("Invalid engine config JSON")

    if not self.METADATA_INPUTS_KEY in metadata.keys():
      raise ValueError("Missing input names metadata")

    if not self.METADATA_OUTPUTS_KEY in metadata.keys():
      raise ValueError("Missing input names metadata")

    if not self.METADATA_ONNX_MD5 in metadata.keys():
      raise ValueError("Missing onnx md5 metadata")

    if not self.METADATA_MAX_BATCH in metadata.keys():
      raise ValueError("Missing max_batch_size metadata")

    trt_version = metadata[self.METADATA_TRT_VERSION_KEY]
    cuda_version = metadata[self.METADATA_CUDA_VERSION_KEY]
    device_name = metadata[self.METADATA_CUDA_DEVICE_KEY]

    if max_batch_size != metadata[self.METADATA_MAX_BATCH]:
      raise ValueError("Batch size mismatch")

    if not check_trt_version(trt_version, cuda_version, device_name, device):
      # Bail out here, we have a version mismatch so an engine
      # rebuild is required.
      raise ValueError("Version mismatch")

    import hashlib
    if not os.path.isfile(str(onnx_path)):
      raise ValueError("No ONNX file")
    if not os.path.isfile(str(engine_path)):
      raise ValueError("No .engine file")

    with open(str(onnx_path), 'rb') as f:
      onnx_md5 = hashlib.md5(f.read()).hexdigest()
    with open(str(engine_path), 'rb') as f:
      engine_md5 = hashlib.md5(f.read()).hexdigest()

    if metadata[self.METADATA_ONNX_MD5] != onnx_md5:
      raise ValueError("ONNX MD5 mismatch")
    if metadata[self.METADATA_ENGINE_MD5] != engine_md5:
      raise ValueError("Engine MD5 mismatch")

    if (metadata[self.METADATA_PRECISION_KEY] == "fp16") != half:
        raise ValueError("Precision mismatch")

    return

  @staticmethod
  def _get_engine_and_config_path(
    onnx_path : str,
    half : bool,
    max_batch_size : int
  ) -> Tuple[str, str]:
    """
    Get the .engine and config file path given an ONNX model.

    Parameters:
      - onnx_path, str, path to the .onnx model
      - half : bool, True if engine is built for fp16
      - max_batch_size : int, maximum batch size supported in inference

    Returns:
      Tuple(str, str) containing the .engine and configuration file
      paths.
    """
    onnx_file_basename = os.path.basename(onnx_path)
    onnx_file_path = Path(onnx_path)
    if half:
      precision = 'fp16'
    else:
      precision = 'fp32'

    config_rel_path = os.path.join(precision, 'bs' + str(max_batch_size))

    onnx_conf_path = Path(os.path.join(
      os.path.dirname(str(onnx_file_path)),
      config_rel_path,
      onnx_file_basename
    ))
    engine_path = onnx_conf_path.with_suffix(".engine")
    engine_metadata_path = onnx_conf_path.with_suffix(".engine.json")
    return str(engine_path), str(engine_metadata_path)

  def load_or_rebuild_model(
    self,
    onnx_path : str,
    fp16 : bool,
    max_batch_size : int,
    device : th.device
  ) -> None:
    """
    Loads a TensorRT engine from engine_path if possible.
    If not possible (e.g. due to the file not existing, cuda/tensor-rt
    version mismatches, etc) we rebuild the TensorRT engine from the
    ONNX model located at engine_path

    Parameters:
      onnx_path - string, path of the ONNX model on disk
      fp16: bool, True if the model should be casted to float16
      max_batch_size - the maximum batch size to use in inference
      device - torch device to use.

    Returns:
      None
    """

    onnx_file_path = Path(onnx_path)
    engine_path, engine_metadata_path = self._get_engine_and_config_path(
      onnx_path,
      fp16,
      max_batch_size
    )

    try:
      self.check_engine_file(
        device,
        onnx_file_path,
        engine_metadata_path,
        engine_path,
        fp16,
        max_batch_size
      )

      # Try to load the model from the disk.
      self.load_model(engine_path, engine_metadata_path, device)
      # Model was loaded so there is nothing left to do.
      return
    except ValueError as ve:
      # If check_engine_file passes we except to be able to
      # load the model, so just catch ValueErrors from it,
      self._log.log(severity=trt.ILogger.INFO, msg=str(ve))

    # Clear any kind of left-over internal state from the failed load_model.
    self._clear_internal_state()

    # Reconstruct the TensorRT engine file.
    self._log.log(
      severity=trt.ILogger.INFO,
      msg="Failed to load model, trying to rebuild"
    )
    self._log._logger.P(
      "Building TensorRT engine file, this can take up to an hour",
      color='r'
    )
    TensorRTModel.create_from_onnx(
      onnx_path=onnx_path,
      half=fp16,
      max_batch_size=max_batch_size,
      device=device,
      log=self._log.get_base_logger()
    )
    # There is no need to check for a ValueError here since we
    # expect create_from_onnx to give us something that will pass
    # this check. If this is not the case a crash is appropriate.
    self.check_engine_file(
      device,
      onnx_file_path,
      engine_metadata_path,
      engine_path,
      fp16,
      max_batch_size
    )
    self.load_model(engine_path, engine_metadata_path, device)
    return

  def get_input_dtype(self, idx):
    name = self._input_names[idx]
    np_dtype = trt.nptype(self._model.get_tensor_dtype(name))
    th_dtype = get_torch_dtype(np_dtype)
    return th_dtype

  def load_model(
    self,
    model_path : str,
    metadata_path : str,
    device : th.device
  ) -> None:
    """
    Initialize from a path on the disk. The initialization may raise
    exceptions if the cuda or TensorRT versions mismatch.

    Parameters:
      model_path - path to tensor-rt model on disk
      device - torch device to use.

    Returns: None
    """

    if self._context is not None:
      raise RuntimeError("Model already loaded")

    with open(metadata_path, 'rt') as f:
      self._metadata = json.load(f)

    self._deserialize_model(model_path, device)

    self._context = self._model.create_execution_context()
    self._input_names = self._metadata[TensorRTModel.METADATA_INPUTS_KEY]
    self._output_names = self._metadata[TensorRTModel.METADATA_OUTPUTS_KEY]
    self._max_batch_size = self._metadata[TensorRTModel.METADATA_MAX_BATCH]
    self._trt_to_th_idx = self._get_torch_args_mapping()
    self._device = device

    # Initialize index of the first batch dependent input.
    self._batched_input_idx = self._get_batch_dependent_arg_idx()

    # Pre-construct shapes and outputs for all possible batch sizes.
    # This avoids doing work at inference time.
    self._batch_runtime_info = []
    for batch_size in range(self._max_batch_size + 1):
      self._batch_runtime_info.append(self._get_batch_runtime_state(batch_size))

    # Finally set the current batch size to the maximum batch size.
    self._set_batch_size(self._max_batch_size)
    return


  def forward(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    """
    Run inference. All model inputs and outputs are torch tensors.

    Parameters:
      All parameters are input tensors in the order specified when the model
      was exported from torch. All parameters are unnamed.

    Returns:
      A tuple of output tensors, in the order of outputs of the
      original torch model. Outputs are not captured by the model
      and are owned by the caller. In case of a tuple with a single
      output, the output is returned directly.
    """
    batch_size = self._current_batch_size
    # If the batch size was changed from the last inference
    # we need to let TensorRT know.
    if self._batched_input_idx is not None:
      batch_size = args[self._batched_input_idx].shape[0]
      if batch_size != self._current_batch_size:
        self._set_batch_size(batch_size)
    #endif set batch size if changed

    # Allocate output tensors.
    current_output_tensors = self._allocate_outputs(batch_size)

    # Add the implicit (output) arguments to the list of input arguments.
    # Make the inputs contiguous if they are not since TensorRT requires this.
    # Output tensors have already been allocated in a contiguous fashion on
    # creation.
    args_list = [x.contiguous() for x in args] + list(current_output_tensors)

    # Convert the args list into the actual tensor-rt parameter list.
    # This accounts for the difference in argument ordering between torch
    # and TensorRT.
    parameter_addrs = [
      int(args_list[self._trt_to_th_idx[i]].data_ptr())
      for i in range(self._model.num_io_tensors)
    ]

    if not self._context.execute_v2(parameter_addrs):
      raise Exception("Failed to run TensorRT kernel")

    # Return pre-computed output tensors in original ordering.
    if len(current_output_tensors) == 1:
      return current_output_tensors[0]
    return current_output_tensors


  def __call__(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    """
    Override for the call operator, just goes to forward.
    """
    return self.forward(*args)


  @staticmethod
  def create_from_onnx(
    onnx_path : str,
    half : bool,
    max_batch_size : int,
    device : th.device,
    log,
    workspace_size : int = 4,
    flags : Sequence[trt.NetworkDefinitionCreationFlag] =
      [trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH],
    builder_flags : Sequence[trt.BuilderFlag] = [],
  ) -> None:
    """
    Export an ONNX model to a tensor-rt model on disk.
    The engine model and the metadata file will be written with
    extensions '.engine' and '.engine.json' respectively.
    Input and output names must be valid python identifiers.

    Parameters:
      onnx_path - string, path on disk to the input onnx model
      half - bool, exports the model to fp16 if True
      max_batch_size - int, maximum batch size for the produced
        engine.
      log - the EE logger object
      workspace_size - size of the Tensor-RT workspace (in GB)
        Defaults to 4.
      flags - a list of TensorRT NetworkDefinitionCreationFlag flags.
        Defaults to [NetworkDefinitionCreationFlag.EXPLICIT_BATCH]
      builder_flags - a list of TensorRT BuilderFlag. If half is true
        the FP16 flag is implicitly added. Defaults to [].

    Returns: None
    """

    import onnx
    import hashlib
    from google.protobuf.json_format import MessageToDict

    trt_logger = TRTServingLogger(log)

    path, config_path = TensorRTModel._get_engine_and_config_path(
      onnx_path,
      half,
      max_batch_size
    )
    # Create folders if they don't exist.
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Load the ONNX model from disk just to get the input/output names
    # in the correct order.
    model_onnx = onnx.load(onnx_path)

    with open(onnx_path,'rb') as f:
      onnx_md5 = hashlib.md5(f.read()).hexdigest()

    # Gather input and output names from the onnx model.
    # We need to save these in order in the TensorRT engine
    # metadata.
    def get_onnx_names(onnx_nodes_list):
      ret = []
      for name in onnx_nodes_list:
        message_dict = MessageToDict(name)
        ret.append(message_dict['name'])
      return ret

    output_names = get_onnx_names(model_onnx.graph.output)
    input_names = get_onnx_names(model_onnx.graph.input)

    # Extract the onnx metadata from the onnx model.
    onnx_metadata_dict = MessageToDict(model_onnx, preserving_proto_field_name = True)['metadata_props']
    for meta in onnx_metadata_dict:
      if meta['key'] == TensorRTModel.ONNX_METADATA_KEY:
        onnx_metadata = json.loads(meta['value'])
        break

    model_precision = onnx_metadata.get('precision')
    # TODO: change this to a maybe_change_precision function that will be situated in a utils file
    if model_precision is not None:
      if (model_precision.lower() == "fp16") != half:
        # We're raising a runtime error specifically so that this doesn't
        # get caught by load_or_rebuild_model
        if half and model_precision.lower() == "fp32":
          #from onnxconverter_common import float16
          from onnxruntime.transformers import float16
          model_fp16 = float16.convert_float_to_float16(model_onnx)
          fp16path = str(Path(onnx_path).with_suffix('.tofp16.onnx'))
          onnx.save(model_fp16, fp16path)
          onnx_path = fp16path
        else:
          raise RuntimeError('Incompatible precision in ONNX model')
    #endif check onnx precision

    metadata = {
      TensorRTModel.METADATA_ONNX_MD5 : onnx_md5,
      TensorRTModel.METADATA_INPUTS_KEY : input_names,
      TensorRTModel.METADATA_OUTPUTS_KEY : output_names,
      TensorRTModel.METADATA_MAX_BATCH : max_batch_size,
      TensorRTModel.METADATA_TRT_VERSION_KEY : trt.__version__,
      TensorRTModel.METADATA_CUDA_VERSION_KEY : th.version.cuda,
      TensorRTModel.METADATA_CUDA_DEVICE_KEY : th.cuda.get_device_name(device)
    }
    # We're done with the ONNX model so nuke it now.
    del model_onnx
    model_onnx = None

    # Set up the tensor-rt framework boilerplate.
    builder = trt.Builder(trt_logger)
    config = builder.create_builder_config()

    # Set the workspace scratch memory size. Note that this is only
    # for one layer and the engine can use much more overall in an
    # execution.
    config.set_memory_pool_limit(
      trt.MemoryPoolType.WORKSPACE,
      pool_size=workspace_size * (1 << 30)
    )

    # Build the flag mask for the network.
    flag_mask = 0
    for flag in flags:
      flag_mask |= (1 << int(flag))

    # Load model through tensor-rt's ONNX parser.
    network = builder.create_network(flag_mask)
    parser = trt.OnnxParser(network, trt_logger)
    if not parser.parse_from_file(onnx_path):
      raise RuntimeError(f'Failed to load ONNX file: {onnx_path}')
    if half:
      config.set_flag(trt.BuilderFlag.FP16)
    for config_flag in builder_flags:
      config.set_flag(config_flag)

    # Add the optimization profile.
    profile = builder.create_optimization_profile()
    inputs = [network.get_input(i) for i in range(network.num_inputs)]
    for input in inputs:
      if input.shape[0] != -1:
        # Tensor doesn't have a dynamic batch dimension, ignore.
        continue
      # Add an optimization profile for this input tensor.
      # Optimize for the max batch size case.
      min_shape = [1] + list(input.shape[1:])
      opt_shape = [max_batch_size] + list(input.shape[1:])
      max_shape = [max_batch_size] + list(input.shape[1:])
      profile.set_shape(
        input.name,
        min=min_shape,
        opt=opt_shape,
        max=max_shape
      )
    #endfor all inputs
    config.add_optimization_profile(profile)

    # Empty cache before building the engine.
    gc.collect()
    th.cuda.empty_cache()

    # Write built engine to disk.
    timer_build_id = "trt_engine_build"
    log.start_timer(timer_build_id)
    try:
      if not hasattr(builder, 'build_engine'):
        # This is the TensorRT 10.0 API case where we don't have a build_engine
        with builder.build_serialized_network(network, config) as engine, open(path, "wb") as t:
          t.write(engine)
      else:
        # This is the base case for TensorRT 8.6.1
        with builder.build_engine(network, config) as engine, open(path, 'wb') as t:
          # Write the serialized TensorRT engine.
          t.write(engine.serialize())
      log.stop_timer(timer_build_id)
      log.P("TensorRT model build took {}".format(log.get_timer_mean(timer_build_id)))
    except Exception as e:
      log.stop_timer(timer_build_id)
      raise RuntimeError(f"Failed to build TensorRT engine: {e}")

    with open(path,'rb') as f:
      engine_md5 = hashlib.md5(f.read()).hexdigest()

    metadata[TensorRTModel.METADATA_ENGINE_MD5] = engine_md5
    # Also record the ONNX metadata
    metadata[TensorRTModel.ONNX_METADATA_KEY] = onnx_metadata
    # Record the actual precision of the model
    metadata[TensorRTModel.METADATA_PRECISION_KEY] = 'fp16' if half else 'fp32'

    with open(config_path, 'wt') as f:
      json.dump(metadata, f)
    #endif on status write metadata file
    return
