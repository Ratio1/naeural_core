import numpy as np


def get_model_size(model):
  n_params = 0
  n_bytes = 0
  for param in model.parameters():
    npar = param.nelement()
    size = npar * param.element_size()
    n_bytes += size
    n_params += npar
  return n_params, n_bytes


def get_dropout_rate(
    value_type='constant',
    dropout=0.1,
    step=0,
    max_step=0
):
  """
  Utility method for getting the dropout rate for a multistep training process.
  Parameters
  ----------
  value_type : str,
    if 'constant' the dropout rate will be constant,
    if 'last' the dropout rate will be 0 until the last step,
    if 'progressive' the dropout rate will increase linearly from 0 to the specified dropout rate.
    otherwise the dropout rate will be 0
  dropout : float, the base dropout rate
  step : int, the current step in the training process
  max_step : int, the maximum number of steps in the training process

  Returns
  -------
  float, the computed dropout rate
  """
  dropout = max(0.0, min(1.0, dropout))
  if value_type == 'constant':
    return dropout
  if value_type == 'last':
    return 0 if step < max_step else dropout
  if value_type == 'progressive':
    return np.linspace(dropout / 10, dropout, max_step + 1)[step]
  return 0


def conv_output_shape(
    h_w, 
    kernel_size=1, 
    stride=1, 
    pad=0, 
    dilation=1, 
    transpose=False,
    **kwargs
  ):
  """
  Utility function for computing output of convolutions
  takes a tuple of (h,w) and returns a tuple of (h,w)

  credits: https://discuss.pytorch.org/t/utility-function-for-calculating-the-shape-of-a-conv-output/11173/6
  """

  if isinstance(h_w, (tuple, list)):
    if len(h_w) == 3:
      h_w = h_w[1:]
  elif isinstance(h_w, int):
    h_w = (h_w, h_w)
  
  if isinstance(kernel_size, int):
    kernel_size = (kernel_size, kernel_size)

  if isinstance(stride, int):
    stride = (stride, stride)

  if 'padding' in kwargs:
    pad = kwargs['padding']
    
  if isinstance(pad, int):
    pad = (pad, pad)

  if isinstance(dilation, int):
    dilation = (dilation, dilation)
    
  h, w = h_w
  kh, kw = kernel_size
  sh, sw = stride
  ph, pw = pad
  dh, dw = dilation
  if not transpose:
    h = (h + 2 * ph - dh * (kh - 1) - 1) // sh + 1
    w = (w + 2 * pw - dw * (kw - 1) - 1) // sw + 1
  else:
    h = (h - 1) * sh - 2 * ph + (kh - 1) + 1    
    w = (w - 1) * sw - 2 * pw + (kw - 1) + 1    
  return h, w