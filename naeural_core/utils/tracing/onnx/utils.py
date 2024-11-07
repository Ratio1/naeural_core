import os

import torch as th
import json

from typing import Any, Mapping, Tuple, Sequence, Union

def create_from_torch(
  model: Union[th.nn.Module, th.jit.ScriptModule, th.jit.ScriptFunction],
  device : th.device,
  path : str,
  half : bool,
  input_names : Sequence[str],
  output_names : Sequence[str],
  args : Union[Tuple[Any, ...], th.Tensor],
  batch_axes : Mapping[str, Sequence[int]] | None = None,
  aggressive_shape_inference : bool = False,
  metadata = None
):
  """
  Export a torch or torchscript(this does not work for the moment
  because of a bug in pytorch) model to a onnx model on disk.
  Input and output names must be valid python identifiers.

  Parameters:
    model - the torch model
    path  - string, path on disk where the model is saved
    half - bool, export the model to fp16
    input_names - list of str, names to assign to inputs
    output_names - list of str, names to assign to outputs
    args - Can have one of 3 formats:
      - tensor
      - tuple of tensors
      - tuple of tensors except the last element which can be a dict
      Note that this matches the format of args in th.onnx.export
    batch_axes - Dictionary to specify the batch axes. Keys are input and
      output names. Values are a list of dynamic dimensions for the tensor.
      If None the first dimension of all input and output tensors is
      considered dynamic. Defaults to None.
    aggressive_shape_inference - Bool, run aggressive shape inference.
      Defaults to False.
    metadata - dict, will be added as metadata

  Returns: bool, True if successful, False otherwise.
  """

  import onnxsim
  import onnx
  from google.protobuf.json_format import MessageToDict

  if not isinstance(args, tuple):
    args = (args,)
  # Split arguments into positional and keyword arguments for model forward.
  exec_args = args
  exec_kwargs = {}
  if isinstance(args, tuple):
    if len(args) > 0 and isinstance(args[-1], dict):
      exec_kwargs = args[-1]
      exec_args = args[:-1]
    #endif last item is dict
  #endif tuple args

  # Make sure all used names are valid python identifiers,
  # otherwise we will have issues at inference time.
  for arg in input_names + output_names:
    if not isinstance(arg, str):
      return False
    if not arg.isidentifier():
      return False
  #endfor check names

  # Convert the model to the required precision and prepare it for
  # tracing.
  model.eval()
  model.to(device=device)
  if half:
    model = model.half()
  else:
    model = model.float()

  with th.no_grad():
    # Execute once otherwise we get trace-related issues.
    _ = model(*exec_args, **exec_kwargs)

  status = True
  try:
    if batch_axes is None:
      batch_axes = {}
      for name in input_names + output_names:
        batch_axes[name] = {0 : 'batch_size' }
    #endif init batch_axes
    with th.no_grad():
      th.onnx.export(
        model,
        args,
        path,
        verbose=False,
        do_constant_folding=True,
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=batch_axes # Set batch as a dynamic axis
      )
    model_onnx = onnx.load(path)  # load onnx model
    # Make sure we have all inputs and outputs.
    if len(model_onnx.graph.input) != len(input_names) or len(model_onnx.graph.output) != len(output_names):
      raise ValueError('Not all input and output names specified')

    # This is essentially an optimization pipeline.
    # Do a simplification first, then run shape inference, then do more
    # simplifications.
    model_onnx, check = onnxsim.simplify(model_onnx) # simplify onnx model
    if aggressive_shape_inference:
      model_onnx = onnx.shape_inference.infer_shapes(model_onnx, True, False, True)
      model_onnx, check = onnxsim.simplify(model_onnx) # simplify onnx model
    assert check, 'Simplified ONNX model could not be validated'
    # Make sure the model is still valid.
    onnx.checker.check_model(model_onnx)  # check onnx model

    # There should be only one dynamic dimension.
    # If there is more than one either the user got the dynamic axes wrong
    # or there was an issue with shape inference.
    dyn_dims = set()
    for output in model_onnx.graph.output:
      output_dict = MessageToDict(output)
      type = output_dict.get('type')
      if type is None:
        continue
      tensor_type = type.get('tensorType')
      if tensor_type is None:
        continue
      shape = tensor_type.get('shape')
      if shape is None:
        continue
      dims = shape.get('dim')
      if dims is None:
        continue
      for dim in dims:
        param = dim.get('dimParam')
        if param is None:
          continue
        dyn_dims.add(param)
      #endfor all dims
    #endfor all outputs

    if len(dyn_dims) > 1:
      raise Exception("Error, multiple dynamic dimensions (perhaps shape inference issue?)")

    if metadata is None:
      metadata = {}

    # Set various metadata required at runtime.
    if metadata.get('precision') is None:
      if half:
        metadata['precision'] = 'fp16'
      else:
        metadata['precision'] = 'fp32'
    #endif precision metadata
    if metadata.get('input_names') is None:
      metadata['input_names'] = input_names
    if metadata.get('output_names') is None:
      metadata['output_names'] = output_names

    m1 = model_onnx.metadata_props.add()
    m1.key = 'onnx_metadata'
    m1.value = json.dumps(metadata)

    # Finally save the onnx model to disk.
    onnx.save(model_onnx, path)
  except Exception as e:
    print(str(e))
    os.unlink(path)
    status = False

  return status
