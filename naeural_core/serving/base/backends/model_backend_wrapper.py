import abc
from typing import Tuple
import torch as th

class ModelBackendWrapper(metaclass=abc.ABCMeta):
  """
  A model wrapper for inference, abstracting the backend
  that is actually used (TensorRT, Torch, ONNX, etc). Users
  should interact with the model only through this interface
  to ensure portability to new backends.
  """

  @abc.abstractmethod
  def get_device(self) -> th.device :
    """
    Returns the torch device used by the model.
    """
    raise NotImplementedError("get_device not implemented")

  @abc.abstractmethod
  def get_input_dtype(self, index : int) -> th.dtype:
    """
    Get the torch dtype for a given argument number.

    Parameters:
      - index : int, the number of the argument

    Returns:
      The torch dtype of the argument.
    """
    raise NotImplementedError("get_device not implemented")

  @abc.abstractmethod
  def __call__(self, *args : th.tensor) -> Tuple[th.tensor, ...]:
    """
    Performs inference by invoking the forward method of the model.

    Parameters
    - All inputs are unnamed torch tensor arguments specific to the model.

    Returns
    - Tuple of tensors (specific to the model) if the model has more than
      one ouput. The model itself does not capture these.
      If the model only has one output (a tensor), returns that output
      as is (a pytorch tensor).
    """
    raise NotImplementedError("__call__ not implemented")
